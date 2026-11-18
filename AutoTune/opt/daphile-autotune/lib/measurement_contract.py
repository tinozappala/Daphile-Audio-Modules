"""Finite observations and conservative candidate selection for AutoTune.

Passive observation may lack scheduler tracing. Optimization requires a finite
latency measurement, because CPU savings cannot establish latency safety.
"""
import copy
import math
import statistics
try:
    from thermal_policy import issues as thermal_issues, CRITICAL_RESERVE_C
except ImportError:
    from .thermal_policy import issues as thermal_issues, CRITICAL_RESERVE_C
try:
    from trace_feed import boundary_status
except ImportError:
    from .trace_feed import boundary_status

# User accepted the real 8.375 C headroom (displayed as 8.4 C).
MINIMUM_THERMAL_HEADROOM_C = CRITICAL_RESERVE_C
BRACKET_PROTOCOL = 'a-b-a-restored-v1'
ACTIVITY_TOLERANCE = {'cpu': 2., 'context': 50., 'interrupts': 50.,
                      'latency': 0., 'wakeups': 25.}
METRIC_NAMES = frozenset(ACTIVITY_TOLERANCE) | {'thermal'}


def finite_nonnegative(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def measurement_issues(m, require_latency=False):
    if not isinstance(m, dict):
        return ['Measurement unavailable']
    issues = []
    if m.get('kernel_log_observable') is not True:
        issues.append('Kernel log observation incomplete')
    xrun = m.get('observed_alsa_xrun_states')
    if type(xrun) is not int or xrun < 0:
        issues.append('XRUN observation unavailable')
    elif xrun:
        issues.append('Observed audio error')
    errors = m.get('new_kernel_errors')
    if not isinstance(errors, list):
        issues.append('Kernel error observation unavailable')
    elif errors:
        issues.append('Observed kernel error')
    for key in ('duration', 'interrupts_per_second', 'context_switches_per_second'):
        if not finite_nonnegative(m.get(key)) or (key == 'duration' and m[key] <= 0):
            issues.append('Invalid ' + key)
    cpu = m.get('cpu_percent')
    if not isinstance(cpu, dict) or not finite_nonnegative(cpu.get('cpu')) or cpu['cpu'] > 100:
        issues.append('CPU observation unavailable or invalid')
    elif any(not finite_nonnegative(value) or value > 100 for value in cpu.values()):
        issues.append('Per-core CPU observation invalid')
    if m.get('kernel_events_truncated') is True:
        issues.append('Kernel error observation truncated')
    for key in ('max_scheduling_latency', 'wakeups_per_second'):
        value = m.get(key)
        if value is not None and not finite_nonnegative(value):
            issues.append('Invalid ' + key)
    if require_latency and not finite_nonnegative(m.get('max_scheduling_latency')):
        issues.append('Measured scheduling latency required for optimization')
    if m.get('instrumented') is True:
        trace = m.get('scheduler_trace', {})
        requested = m.get('instrumentation_requested_seconds')
        if (not isinstance(trace, dict) or trace.get('status') != 'observed'
                or trace.get('max_scheduling_latency_measured') is not True
                or not finite_nonnegative(requested) or requested <= 0
                or not finite_nonnegative(trace.get('duration')) or trace['duration'] < requested
                or not finite_nonnegative(m.get('duration')) or m['duration'] < requested):
            issues.append('Scheduler instrumentation incomplete for requested window')
        if not isinstance(m.get('network_counter_errors'), list):
            issues.append('Network observation unavailable')
        if m.get('nas_status') not in ('pass', 'not-applicable'):
            issues.append('NAS observation unavailable or failed')
    if m.get('network_counter_errors'):
        issues.append('Observed network counter error')
    if 'nas_status' in m and m['nas_status'] not in ('pass', 'not-applicable'):
        issues.append('NAS observation unavailable or failed')
    if 'telemetry' in m:
        telemetry=m['telemetry']
        if not isinstance(telemetry,dict) or telemetry.get('status')!='observed' or not isinstance(telemetry.get('temperatures'),dict) or not telemetry['temperatures']:
            issues.append('CPU thermal observation incomplete')
        else:
            for row in telemetry['temperatures'].values():issues.extend(thermal_issues(row))
    return issues


def compare(before, after):
    reasons = (['Baseline: ' + x for x in measurement_issues(before)]
               + ['Candidate: ' + x for x in measurement_issues(after)])
    if reasons:
        return reasons
    a, b = before['cpu_percent']['cpu'], after['cpu_percent']['cpu']
    if b > a + max(10, a * .5):
        reasons.append('CPU activity above conservative threshold')
    a, b = before.get('max_scheduling_latency'), after.get('max_scheduling_latency')
    if (a is None) != (b is None):
        reasons.append('Scheduling latency observations are not comparable')
    elif a is not None and b > a:
        reasons.append('Measured scheduling latency regression')
    a, b = before.get('wakeups_per_second'), after.get('wakeups_per_second')
    if (a is None) != (b is None):
        reasons.append('Wakeup observations are not comparable')
    elif a is not None and b > a * 1.5 + 25:
        reasons.append('Measured wakeup regression')
    if before.get('instrumented') is True or after.get('instrumented') is True:
        reasons += ['Baseline: '+x for x in optimization_issues(before)]
        reasons += ['Candidate: '+x for x in optimization_issues(after)]
        if reasons:return reasons
        if set(before['cpu_percent']) != set(after['cpu_percent']):
            reasons.append('CPU average coverage changed')
        peak_a,peak_b=before['max_cpu_percent'],after['max_cpu_percent']
        if set(peak_a)!=set(peak_b):reasons.append('CPU peak coverage changed')
        else:
            # Affinity changes can move the same work from one CPU to another.
            # Compare the busiest core's pressure, independent of its CPU ID;
            # optimization_issues still checks EVERY core's absolute85% limit.
            worst_a=max(v for k,v in peak_a.items() if k!='cpu')
            worst_b=max(v for k,v in peak_b.items() if k!='cpu')
            if worst_b>worst_a+max(5,worst_a*.15):
                reasons.append('Busiest-core CPU peak regression')
        a=before['telemetry']['temperatures'];b=after['telemetry']['temperatures']
        if set(a)!=set(b):reasons.append('Thermal sensor coverage changed')
        elif any(b[k]['headroom_c']<a[k]['headroom_c']-2 for k in a):
            reasons.append('Thermal operating margin regression')
    return reasons


def metric(m):
    if optimization_issues(m):
        return None
    values = {'cpu': m['cpu_percent']['cpu'],
              'context': m['context_switches_per_second'],
              'interrupts': m['interrupts_per_second'],
              'latency': m['max_scheduling_latency']}
    if m.get('wakeups_per_second') is not None:
        values['wakeups'] = m['wakeups_per_second']
    values['thermal']=max(row['peak_c'] for row in m['telemetry']['temperatures'].values())
    return values


def _complete_metrics(values):
    if not isinstance(values, dict) or set(values) != METRIC_NAMES:
        return False
    if any(not finite_nonnegative(values[key]) for key in ACTIVITY_TOLERANCE):
        return False
    temperature = values['thermal']
    return type(temperature) in (int, float) and -20. <= temperature <= 130.


def metric_safety_issues(reference, candidate):
    """Shared numeric bounds, independent of the number of observed windows.

    Inputs are complete metrics produced by metric(), or its observed
    medians/extrema. Temperature stays in absolute Celsius, never a ratio.
    """
    if not _complete_metrics(reference) or not _complete_metrics(candidate):
        return ['Complete comparable metric coverage required']
    issues = []
    if candidate['latency'] > reference['latency']:
        issues.append('Measured scheduling latency regression')
    for key in sorted(ACTIVITY_TOLERANCE.keys() - {'latency'}):
        if candidate[key] > reference[key] * 1.15 + ACTIVITY_TOLERANCE[key]:
            issues.append('Measured ' + key + ' exceeds selection regression limit')
    if candidate['thermal'] > reference['thermal'] + 2.:
        issues.append('Measured temperature exceeds absolute selection margin')
    return issues


def benefit_metrics(reference, candidate):
    """Indicators exceeding the published effect margin for one comparison."""
    if not _complete_metrics(reference) or not _complete_metrics(candidate):
        return set()
    result = {key for key, tolerance in ACTIVITY_TOLERANCE.items()
              if candidate[key] < reference[key] * .9 - tolerance / 2.}
    if candidate['thermal'] <= reference['thermal'] - 2.:
        result.add('thermal')
    return result


def relative_score(reference, candidate):
    """Exact positive-reference ratios; 0/0 neutral, positive/0 inadmissible."""
    if not _complete_metrics(reference) or not _complete_metrics(candidate):
        return None
    ratios = []
    for key in sorted(ACTIVITY_TOLERANCE):
        a, b = reference[key], candidate[key]
        if not finite_nonnegative(a) or not finite_nonnegative(b):
            return None
        if a == 0:
            if b != 0:return None
            ratios.append(1.)
        else:
            try:value = b / a
            except (OverflowError, ZeroDivisionError):return None
            if not finite_nonnegative(value):return None
            ratios.append(value)
    # Divide before adding: a finite score need not overflow a partial sum.
    try:score = math.fsum(value / len(ratios) for value in ratios)
    except OverflowError:return None
    return score if finite_nonnegative(score) else None


def ranking_key(score, medians, maxima):
    """The same objective and tie order for repeated selection and holdout."""
    return (score, medians['latency'], maxima['latency'],
            medians['thermal'], maxima['thermal'])


def observation_pair_issues(before, after):
    """Two complete, distinct, ordered observations, without a gain claim."""
    issues = (['Baseline: ' + issue for issue in optimization_issues(before)]
              + ['Final observation: ' + issue for issue in optimization_issues(after)])
    if issues:return issues
    if before['observation_monotonic_end'] > after['observation_monotonic_start']:
        issues.append('Final observations overlap, were reused or are reversed')
    return issues


def confirmation_issues(before, after):
    """Qualify one fresh final A/B holdout; this never invents A/B/A repeats.

    A prior repeated proposal remains independently necessary. A final write
    additionally needs a real effect, every shared safety bound and an
    objective/tie result no worse than the fresh reference scored as 1.
    """
    issues = observation_pair_issues(before, after)
    if issues:return issues
    issues.extend(compare(before, after))
    a, b = metric(before), metric(after)
    issues.extend(metric_safety_issues(a, b))
    if not benefit_metrics(a, b):
        issues.append('Final holdout has no independently observed benefit')
    score = relative_score(a, b)
    if score is None:
        issues.append('Final relative objective unavailable or positive over zero reference')
    elif ranking_key(score, b, b) > ranking_key(1., a, a):
        issues.append('Final relative objective or tie-break is worse than current')
    return list(dict.fromkeys(issues))


def optimization_issues(m, *, require_current_boundary=True):
    issues=measurement_issues(m,require_latency=True)
    if not isinstance(m,dict):return issues
    trace=m.get('scheduler_trace')
    if m.get('instrumented') is not True or not isinstance(trace,dict):
        return issues+['Optimization requires scheduler instrumentation with provenance']
    if require_current_boundary:
        issues.extend(boundary_status(m)['issues'])
    if trace.get('trace_clock')!='mono' or trace.get('wakeups_measured') is not True:
        issues.append('Comparable scheduler clock/wakeup observation required')
    if not finite_nonnegative(m.get('wakeups_per_second')):
        issues.append('Finite measured target wakeup rate required')
    if trace.get('measurement_scope')!='verified-audio-and-usb-irq' or trace.get('unobserved_target_tids')!=[] or not trace.get('target_tids'):
        issues.append('Verified audio and IRQ latency attribution incomplete')
    spans=[trace.get('monotonic_start'),trace.get('monotonic_end'),m.get('observation_monotonic_start'),m.get('observation_monotonic_end')]
    if not all(finite_nonnegative(v) for v in spans) or not spans[0]<=spans[2]<spans[3]<=spans[1]:
        issues.append('Scheduler interval does not enclose the observation')
    if m.get('network_inventory_comparable') is not True:issues.append('Network counter coverage incomplete')
    for key in ('buffer_loss_count','lost_event_markers','unparsed_lines','clock_order_errors'):
        if type(trace.get(key))!=int or trace[key]!=0:issues.append('Scheduler integrity unavailable or invalid: '+key)
    if type(trace.get('identity_unverified_threads'))!=int or trace['identity_unverified_threads']!=0:issues.append('Scheduler thread identities incomplete')
    telemetry=m.get('telemetry',{})
    if not isinstance(telemetry,dict) or telemetry.get('status')!='observed':
        return issues+['Complete CPU, thermal, memory and PCM telemetry required']
    samples=telemetry.get('sample_count');gap=telemetry.get('max_sample_gap_seconds')
    if type(samples) is not int or samples<2:
        issues.append('At least two actual CPU, thermal and PCM samples required')
    if not finite_nonnegative(gap) or not 0<gap<=3:
        issues.append('CPU, thermal and PCM sampling gap must be positive and at most three seconds')
    elif type(samples) is int and samples>=2 and finite_nonnegative(m.get('duration')) and (samples-1)*gap+.25<m['duration']:
        issues.append('Telemetry sample count and gaps cannot cover the observed duration')
    online=telemetry.get('online');cores=set()
    try:
        for group in online.split(','):
            ends=[int(n) for n in group.split('-')]
            if len(ends) not in (1,2) or min(ends)<0 or max(ends)>4096 or ends[0]>ends[-1]:raise ValueError()
            cores.update(range(ends[0],ends[-1]+1))
    except (ValueError,TypeError,AttributeError):issues.append('Online CPU coverage unavailable')
    average=m.get('cpu_percent',{});peak=m.get('max_cpu_percent',{})
    if not cores or not isinstance(average,dict) or not isinstance(peak,dict) or any('cpu'+str(c) not in average or 'cpu'+str(c) not in peak for c in cores):
        issues.append('Per-core CPU coverage incomplete')
    if not isinstance(peak,dict) or any(not finite_nonnegative(v) or v>85 for k,v in peak.items() if k!='cpu'):
        issues.append('Per-core CPU peak exceeds 85 percent operating margin')
    sensors=telemetry.get('temperatures')
    if not isinstance(sensors,dict) or not sensors:issues.append('CPU thermal margin unavailable')
    memory=telemetry.get('minimum_available_memory_kib')
    if type(memory)!=int or memory<131072:issues.append('Available memory below 128 MiB operating reserve')
    if telemetry.get('selected_pcm_running_every_sample') is not True:issues.append('Selected PCM observation incomplete')
    if type(trace.get('per_cpu_buffers_checked'))!=int or trace['per_cpu_buffers_checked']<len(cores):issues.append('Scheduler CPU buffer coverage incomplete')
    return issues


def _metric_comparisons(row):
    """Keep each candidate window once, with every required baseline.

    Archived A/B rows remain readable only when they do not claim the new
    protocol or contain an unlabelled restored baseline. A/B/A evidence is
    indivisible: a missing third window never silently downgrades to A/B.
    """
    bracketed = 'comparison_protocol' in row
    if bracketed and row['comparison_protocol'] != BRACKET_PROTOCOL:
        return None
    measurements = row.get('measurements', [])
    if not isinstance(measurements, list) or any(not isinstance(sample, dict) for sample in measurements):
        return None
    if not bracketed and any('restored_baseline' in sample for sample in measurements):
        return None
    comparisons = []
    for sample in measurements:
        baselines = [metric(sample.get('before'))]
        if bracketed:
            baselines.append(metric(sample.get('restored_baseline')))
        candidate = metric(sample.get('after'))
        if candidate is None or any(baseline is None for baseline in baselines):
            return None
        comparisons.append((baselines, candidate))
    return comparisons


def choose(rows):
    scope = dict(
        metric_scope={'latency': 'observed_scheduler_runnable_to_run_ms',
                      'cpu': 'sampled_system_cpu_percent',
                      'context': 'system_context_switches_per_second',
                      'interrupts': 'system_interrupts_per_second',
                      'wakeups': 'traced_target_wakeups_per_second',
                      'thermal': 'sampled_cpu_peak_celsius'},
        ranking_method='mean-relative-observed-metrics',
        ranking_normalization='exact-positive-reference-ratio-zero-zero-neutral',
        ranking_reference_score=1.0,
        energy_measured=False, sound_quality_claim=False)
    valid = []
    for row in rows:
        pairs = _metric_comparisons(row)
        unchanged_reference = (row['spec'].get('name') == 'current'
                               and row['spec'].get('stage') == 'reference'
                               and row.get('application_observed') is False)
        if row['spec'].get('name')=='current' and not unchanged_reference:
            continue
        if row.get('application_observed') is not True and row['spec']['name'] != 'current':
            continue
        applications=row.get('application_observations')
        if applications is not None and (not isinstance(applications,list) or pairs is None
                or len(applications)!=len(pairs) or any(type(v) is not bool or v is not (not unchanged_reference) for v in applications)):
            continue
        # A verified no-change reference records natural variation. It still
        # needs every complete, safe observation, but cannot have a regression
        # attributed to a write that did not occur. Candidate comparisons and
        # the later reference median/peak guards remain unchanged.
        if row.get('issues') or pairs is None or len(pairs) < 3:
            continue
        baseline_names = ('before', 'restored_baseline') if 'comparison_protocol' in row else ('before',)
        if not unchanged_reference and any(compare(sample[key],sample['after'])
                for sample in row['measurements'] for key in baseline_names):
            continue
        samples = [b for _, b in pairs]
        common = set.intersection(*(set(x) for x in samples))
        row = copy.deepcopy(row)
        row['metrics'] = {k: statistics.median(x[k] for x in samples) for k in common}
        row['ranges'] = {k: [min(x[k] for x in samples), max(x[k] for x in samples)] for k in common}
        valid.append(row)
    reference = next((x for x in valid if x['spec']['name'] == 'current'), None)
    if not reference:
        return {'status': 'inconclusive', 'reason': 'Three complete paired reference windows including latency required',
                'winner': None, 'ranking_metrics': [], 'ranking_weights': {}, **scope}
    candidates = []
    for row in valid:
        if row is reference or not set(reference['metrics']) <= set(row['metrics']):
            continue
        common = set(reference['metrics'])
        pairs = _metric_comparisons(row)
        if any(not common <= set(a) & set(b) for baselines, b in pairs for a in baselines):
            continue
        # No numerical tolerance or CPU/activity score may buy worse latency.
        # Strict peak AND median guards avoid hiding a systematic shift in noise.
        latency_safe = (row['ranges']['latency'][1] <= reference['ranges']['latency'][1]
                        and row['metrics']['latency'] <= reference['metrics']['latency']
                        and all(b['latency'] <= a['latency'] for baselines, b in pairs for a in baselines))
        reference_max = {k: reference['ranges'][k][1] for k in common}
        reference_min = {k: reference['ranges'][k][0] for k in common}
        candidate_max = {k: row['ranges'][k][1] for k in common}
        no_regression = not metric_safety_issues(reference_max, candidate_max)
        paired_safe = all(not metric_safety_issues(a, b) for baselines, b in pairs for a in baselines)
        thermal_safe = row['ranges']['thermal'][1]<=reference['ranges']['thermal'][1]+2 and row['metrics']['thermal']<=reference['metrics']['thermal']+2
        better = bool(benefit_metrics(reference_min, candidate_max))
        # One and the same indicator must improve in every repeat against
        # BOTH baselines. Averaging A values could hide monotonic load drift.
        paired_better = bool(set.intersection(*(benefit_metrics(a, b)
                               for baselines, b in pairs for a in baselines)))
        if latency_safe and thermal_safe and no_regression and paired_safe and better and paired_better:
            # Celsius has an arbitrary zero. Keep its absolute safety and
            # improvement tests, but never mix temperature ratios into ranking.
            # A zero baseline has no finite relative regression allowance.
            # Zero/zero is neutral; a positive tiny baseline keeps its actual
            # unit-free ratio instead of acquiring an arbitrary .001 floor.
            score = relative_score(reference['metrics'], row['metrics'])
            if score is None:continue
            row['relative_activity'] = score
            candidates.append(row)
    # Do not select a dominated point merely because equal normalized scores
    # retain input order. Temperature is a valid absolute objective even though
    # a ratio of Celsius values is meaningless. Protect both the median and the
    # worst observed value of every metric when constructing the frontier.
    def dominates(a,b):
        pairs=[(a['metrics'][k],b['metrics'][k]) for k in reference['metrics']]
        pairs += [(a['ranges'][k][1],b['ranges'][k][1]) for k in reference['metrics']]
        return all(x<=y for x,y in pairs) and any(x<y for x,y in pairs)
    reference['relative_activity']=1.0
    alternatives=[reference]+candidates
    frontier=[r for r in alternatives if not any(other is not r and dominates(other,r) for other in alternatives)]
    best = min(frontier,key=lambda x:(*ranking_key(x['relative_activity'], x['metrics'],
        {k:v[1] for k,v in x['ranges'].items()}),x['spec']['name'])) if frontier else reference
    ranking_metrics = sorted(set(reference['metrics']) - {'thermal'})
    return {'status': 'measured-winner' if best is not reference else 'retain-current',
            'winner': best['spec'], 'metrics': best['metrics'],
            'relative_activity':best['relative_activity'],
            'ranking_metrics': ranking_metrics,
            'ranking_weights': {key: 1 / len(ranking_metrics) for key in ranking_metrics},
            'pareto_frontier': [r['spec'] for r in frontier],
            'pareto_objectives': {key:['median','observed-maximum'] for key in sorted(reference['metrics'])},
            'reason': 'Repeated improvement with no measured latency increase; conservative retention under uncertainty',
            **scope}

__release_signature__ = 'Tino Zappala'
