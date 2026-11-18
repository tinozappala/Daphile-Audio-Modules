"""Balanced search with fresh repeated qualification and combined-policy proof.

The adapter owns every temporary trial and its restoration. This coordinator
never applies a persistent setting. All combinations of the at most three
qualified control-group winners are considered, but only fresh complete measurements
within the remaining budget can support a proposal. This is not a global search.
One additional compatible pair may probe two individually unpromoted control
groups without observed regression. Its screening never counts as one of the
three fresh qualification blocks.
"""
import collections,copy,itertools,json,math,time
from .adaptive_plan import plan,family,qualification_group
from .reference_state import identity
from .accounting import issues as accounting_issues
try:from measurement_contract import choose,compare,metric,optimization_issues,BRACKET_PROTOCOL
except ImportError:from ..measurement_contract import choose,compare,metric,optimization_issues,BRACKET_PROTOCOL


class SearchRefused(RuntimeError):pass
class RecoveryRequired(SearchRefused):pass

SYNERGY_PROTOCOL='unpromoted-pair-v1'
SYNERGY_MEMBER_BASIS='not-promoted-without-observed-regression'
OBJECTIVES={'cpu','context','interrupts','wakeups','latency','thermal'}


def screening_metrics(row):
    """A complete applied screening block, independent of claimed labels."""
    if (not isinstance(row,dict) or row.get('issues')!=[]
            or row.get('comparison_protocol')!=BRACKET_PROTOCOL
            or row.get('application_observed') is not True
            or not isinstance(row.get('application_observations'),list)
            or len(row['application_observations'])!=1 or row['application_observations'][0] is not True
            or not isinstance(row.get('spec'),dict) or row['spec'].get('stage')=='reference'
            or row['spec'].get('applicable') is False):return None
    samples=row.get('measurements')
    if (not isinstance(samples,list) or len(samples)!=1 or not isinstance(samples[0],dict)
            or set(samples[0])!={'before','after','restored_baseline'}):return None
    pair=samples[0]
    values=[pair[key] for key in ('before','after','restored_baseline')]
    if any(optimization_issues(value)+accounting_issues(value) for value in values):return None
    if any(compare(pair[key],pair['after']) for key in ('before','restored_baseline')):return None
    metrics=tuple(metric(value) for value in values)
    return metrics if all(metrics) else None


def screening_promising(row):
    metrics=screening_metrics(row)
    if metrics is None:return False
    a,b,c=metrics
    if any(b['latency']>baseline['latency'] for baseline in (a,c)):return False
    return (any(all(b[key]<baseline[key]*.9 for baseline in (a,c))
                for key in ('cpu','context','interrupts','wakeups','latency') if key in a and key in b and key in c)
            or all(b['thermal']<=baseline['thermal']-2 for baseline in (a,c)))


def unpromoted_without_regression(row):
    """Observed non-regression is not a statistical equivalence test.

    Require all six objectives, busiest-core and total CPU peaks, and each
    thermal sensor's observed peak/margin to be no worse than either A. This
    optional probe is deliberately stricter than ordinary finalist screening.
    """
    metrics=screening_metrics(row)
    if metrics is None or screening_promising(row):return False
    a,b,c=metrics
    if any(set(value)!=OBJECTIVES for value in metrics):return False
    if any(b[key]>baseline[key] for baseline in (a,c) for key in OBJECTIVES):return False
    pair=row['measurements'][0];after=pair['after']
    for key in ('before','restored_baseline'):
        baseline=pair[key];peaks_a=baseline['max_cpu_percent'];peaks_b=after['max_cpu_percent']
        if set(peaks_a)!=set(peaks_b) or 'cpu' not in peaks_a:return False
        if any(type(value) not in (int,float) or not math.isfinite(value) or value<0
               for peaks in (peaks_a,peaks_b) for value in peaks.values()):return False
        if (peaks_b['cpu']>peaks_a['cpu'] or max(v for k,v in peaks_b.items() if k!='cpu')>
                max(v for k,v in peaks_a.items() if k!='cpu')):return False
        sensors_a=baseline['telemetry']['temperatures'];sensors_b=after['telemetry']['temperatures']
        if set(sensors_a)!=set(sensors_b):return False
        if any(sensors_b[name]['peak_c']>sensors_a[name]['peak_c'] or
               sensors_b[name]['headroom_c']<sensors_a[name]['headroom_c'] for name in sensors_a):return False
    return True


def unpromoted_pair(screening,normal_pool):
    """Choose at most one pair, deterministically in original screening order."""
    eligible=[row for row in screening if unpromoted_without_regression(row)]
    for first,second in itertools.combinations(eligible,2):
        try:
            if qualification_group(first['spec'])==qualification_group(second['spec']):continue
            spec=combine([first['spec'],second['spec']])
        except (ValueError,SearchRefused):continue
        # Two labels must add two real sets of controls, and may not repeat a
        # normal combination already planned from independently winning groups.
        if any(spec==combine([row['spec']]) for row in (first,second)):continue
        if any(spec==row['spec'] for row in normal_pool):continue
        return dict(spec=spec,members=[first['spec']['name'],second['spec']['name']])
    return None


def combine(winners):
    result={'name':'combined','stage':'combined'}
    for spec in winners:
        for key,value in spec.items():
            if key in ('name','stage','coordinate','screen_family','qualification_group','applicable','frequency_policy','frequency'):continue
            if key in result and result[key]!=value:raise SearchRefused('Conflicting selected control families')
            result[key]=copy.deepcopy(value)
        if 'frequency' in spec:
            policy=spec['frequency_policy'];frequencies=result.setdefault('frequencies',{})
            if policy in frequencies and frequencies[policy]!=spec['frequency']:raise SearchRefused('Conflicting frequency policy')
            frequencies[policy]=spec['frequency']
    return result


def combination_candidates(reference,winners):
    """Enumerate atomic, compatible subsets without inventing their benefit.

    At most three control groups can reach repeated qualification, hence at most
    seven nonempty subsets exist. A coupled coordinate remains indivisible:
    conflicting values are never silently overwritten or stripped. Ranking
    individual evidence only determines trial order; every eventual winner
    still needs its own three fresh combined-policy pairs.
    """
    if len(winners)>3:raise SearchRefused('At most three qualified control-group winners supported')
    pending=list(winners);ranked=[]
    while pending:
        decision=choose([reference]+pending)
        if decision['status']!='measured-winner':
            raise SearchRefused('Combination member lacks repeated independent benefit')
        index=next(i for i,row in enumerate(pending) if row['spec']==decision['winner'])
        ranked.append(pending.pop(index))
    subsets=[]
    for size in range(1,len(ranked)+1):
        for indexes in itertools.combinations(range(len(ranked)),size):
            try:spec=combine([ranked[i]['spec'] for i in indexes])
            except SearchRefused:continue
            subsets.append((indexes,spec))
    # Try maximal compatible sets first, preferring sets containing the best
    # individually measured candidate. Smaller subsets remain available when
    # interactions regress, or one member alone outperforms the larger set.
    def order(item):
        indexes,_=item
        maximal=not any(set(indexes)<set(other) for other,_ in subsets)
        return (not maximal,indexes[0],-len(indexes),indexes)
    result=[];seen=set()
    for indexes,spec in sorted(subsets,key=order):
        key=json.dumps(spec,sort_keys=True,separators=(',',':'),allow_nan=False)
        if key in seen:continue
        seen.add(key)
        result.append(dict(spec=spec,members=[ranked[i]['spec']['name'] for i in indexes]))
    return result


def run(inventory,adapter,budget,seconds=10,progress=lambda _:None):
    if type(seconds) is not int or seconds<10:raise ValueError('Complete observation windows required')
    baseline=identity(adapter.binding())
    report=dict(status='running',settings_applied=False,qualified_candidates=0,qualified_families=0,
                comparison_protocol=BRACKET_PROTOCOL,
                reference=None,screening=[],qualification=[],deferred=[],proposed_policy=None,
                complete_optimization=False,sound_quality_claim=False)
    report['inventory_deferred']=copy.deepcopy(inventory.get('deferred_candidates',[]))
    report['deferred']=copy.deepcopy(report['inventory_deferred'])
    reserve=180;block_seconds=3*seconds+10;last_observation_end=None
    def publish():progress(copy.deepcopy(report))
    def pair(spec):
        nonlocal block_seconds,last_observation_end
        # Estimates can select work, but only the current measured deadline
        # may authorize the next operation. Recovery/final reserves stay intact.
        if budget.remaining()<reserve+block_seconds:raise SearchRefused('Verification and restoration time reserved')
        if identity(adapter.binding())!=baseline:raise SearchRefused('Context changed before trial')
        started=time.monotonic()
        try:result=adapter.pair(copy.deepcopy(spec),seconds)
        except BaseException:
            report['status']='recovery-required'
            raise
        block_seconds=max(block_seconds,math.ceil(time.monotonic()-started)+3)
        if result.get('restoration_complete') is not True:raise RecoveryRequired('Trial restoration incomplete')
        if identity(adapter.binding())!=baseline:raise SearchRefused('Context changed after restoration')
        if result.get('comparison_protocol')!=BRACKET_PROTOCOL:
            raise SearchRefused('A/B/A restored-baseline comparison protocol required')
        observations={k:result.get(k) for k in ('before','after','restored_baseline')}
        for value in observations.values():
            if not isinstance(value,dict):raise SearchRefused('Three actual restored-baseline observations required')
            start,end=(value.get('observation_monotonic_'+key) for key in ('start','end'))
            if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end))
                    or start>=end or (last_observation_end is not None and start<last_observation_end)):
                raise SearchRefused('Reused, unordered or invalid observation intervals')
            last_observation_end=end
        issues=[k+': '+issue for k,value in observations.items() for issue in optimization_issues(value)+accounting_issues(value)]
        # Natural variation of an unchanged reference is evidence about noise,
        # not a regression caused by a setting which was never written.
        if not issues and spec.get('stage')!='reference':
            issues=[key+': '+issue for key in ('before','restored_baseline')
                    for issue in compare(observations[key],observations['after'])]
        changed=result.get('application_observed')
        expected=spec.get('stage')!='reference'
        if type(changed) is not bool or changed is not expected:
            issues.append('Every trial must explicitly observe '+('applied candidate controls' if expected else 'unchanged reference controls'))
        return observations,issues,changed
    def repeated(spec,optional_budget=False):
        row=dict(spec=copy.deepcopy(spec),measurements=[],issues=[],application_observed=False,application_observations=[],
                 comparison_protocol=BRACKET_PROTOCOL)
        for index in range(3):
            if optional_budget and budget.remaining()<reserve+(3-index)*block_seconds:
                row['issues'].append('Fresh synergy qualification deferred by measured remaining budget')
                break
            observed,issues,changed=pair(spec)
            row['measurements'].append(observed);row['issues'].extend(issues)
            row['application_observations'].append(changed)
            row['application_observed']=all(v is True for v in row['application_observations'])
            if issues:break
        return row
    try:
        references=[s for s in inventory['candidates'] if s.get('stage')=='reference']
        if len(references)!=1:raise ValueError('Exactly one reference required')
        reference=repeated(references[0]);report['reference']=reference;publish()
        if reference['issues'] or len(reference['measurements'])!=3 or reference['application_observed']:
            raise SearchRefused('Reference incomplete, unstable or unexpectedly changed')
        combined_seconds=3*block_seconds
        control_groups=set()
        for spec in inventory['candidates']:
            try:group=qualification_group(spec)
            except ValueError:continue
            if group[0]!='reference' and spec.get('applicable') is not False:control_groups.add(group)
        # Reserve full verification for fewer finalists when that permits
        # screening more distinct families within the real remaining time.
        finalists_limit=1
        for count in (3,2,1):
            if budget.remaining()>=reserve+combined_seconds+(count*3+len(control_groups))*block_seconds:
                finalists_limit=count;break
        planned=plan(inventory,budget.remaining(),screen_window=seconds,transition_allowance=block_seconds-3*seconds,
                     final_reserve=60+combined_seconds,finalists=finalists_limit,observation_windows=3)
        planned.update(finalists_reserved=finalists_limit,estimated_pair_seconds=block_seconds)
        report['planning']=planned;report['deferred'].extend(copy.deepcopy(planned['deferred']))
        promising=[]
        for spec in planned['screening']:
            current_reserved=reserve+3*block_seconds*(finalists_limit+1)
            if budget.remaining()<current_reserved+block_seconds:
                report['deferred'].append({'name':spec['name'],'reason':'measured-budget-reserved'});continue
            observed,issues,changed=pair(spec)
            row=dict(spec=spec,measurements=[observed],issues=issues,application_observed=changed,
                     application_observations=[changed],comparison_protocol=BRACKET_PROTOCOL)
            report['screening'].append(row)
            if screening_promising(row):
                candidate=metric(observed['after']);promising.append((candidate['latency'],candidate['cpu'],row))
            publish()
        # Give each promising control target a finalist before a second value
        # of one target. Distinct CPU frequency policies can be combined even
        # though they share the public frequency family. Screening is no proof.
        groups=collections.OrderedDict()
        for _,__,row in sorted(promising,key=lambda value:(value[0],value[1])):
            groups.setdefault(qualification_group(row['spec']),[]).append(row)
        finalists=[]
        while len(finalists)<finalists_limit and any(groups.values()):
            for rows in groups.values():
                if rows and len(finalists)<finalists_limit:finalists.append(rows.pop(0))
        combined_seconds=3*block_seconds
        for screened in finalists:
            if budget.remaining()<reserve+combined_seconds+3*block_seconds:
                report['deferred'].append({'name':screened['spec']['name'],'reason':'insufficient-fresh-paired-verification-budget'});continue
            qualified=repeated(screened['spec']);report['qualification'].append(qualified);publish()
        groups=collections.defaultdict(list)
        for row in report['qualification']:groups[qualification_group(row['spec'])].append(row)
        winners=[]
        for rows in groups.values():
            decision=choose([reference]+rows)
            if decision['status']=='measured-winner':
                winners.append(next(row for row in rows if row['spec']==decision['winner']))
        report['qualified_candidates']=len(winners)
        report['qualified_families']=len({family(row['spec']) for row in winners})
        pool=combination_candidates(reference,winners) if winners else []
        if winners:
            report['combination_plan']=copy.deepcopy(pool)
            report['combination_trials']=[]
            for index,item in enumerate(pool):
                if budget.remaining()<reserve+3*block_seconds:
                    report['deferred'].extend(dict(name='combined',spec=copy.deepcopy(row['spec']),
                        members=list(row['members']),reason='insufficient-fresh-combination-budget') for row in pool[index:])
                    break
                combined=repeated(item['spec'])
                combined['qualified_members']=list(item['members'])
                report['combination_trials'].append(combined)
                publish()
            trials=report['combination_trials']
            report['combination_coverage']=dict(compatible_candidates=len(pool),tested_candidates=len(trials),
                deferred_candidates=len(pool)-len(trials),all_qualified_subsets_tested=len(trials)==len(pool),
                complete_paired_candidates=sum(not row['issues'] and len(row['measurements'])==3 for row in trials),
                accepted_candidates=sum(choose([reference,row])['status']=='measured-winner' for row in trials),
                scope='Compatible subsets of at most three repeatedly qualified control-group winners; not all parameter combinations')
        # An individually unpromoted coordinate is not known to be equivalent
        # to current. Only a fresh composed screening can justify spending the
        # three additional A/B/A blocks on this one optional pair.
        synergy=dict(protocol=SYNERGY_PROTOCOL,member_basis=SYNERGY_MEMBER_BASIS,
            members=[],spec=None,screening=None,qualification=None,
            status='not-planned',reason='no-compatible-unpromoted-pair')
        report['neutral_synergy']=synergy
        item=unpromoted_pair(report['screening'],pool)
        if item is not None:
            synergy.update(copy.deepcopy(item))
            if budget.remaining()<reserve+4*block_seconds:
                synergy.update(status='deferred',reason='insufficient-unpromoted-synergy-budget')
                report['deferred'].append(dict(name='combined',members=list(item['members']),
                    spec=copy.deepcopy(item['spec']),reason=synergy['reason']))
            else:
                synergy.update(status='screening',reason=None);publish()
                observed,issues,changed=pair(item['spec'])
                screened=dict(spec=copy.deepcopy(item['spec']),measurements=[observed],issues=issues,
                    application_observed=changed,application_observations=[changed],comparison_protocol=BRACKET_PROTOCOL)
                synergy['screening']=screened
                if issues:
                    synergy.update(status='screening-rejected',reason='composed-screening-issues')
                elif not screening_promising(screened):
                    synergy.update(status='screened-no-benefit',reason='no-fresh-composed-screening-benefit')
                elif budget.remaining()<reserve+3*block_seconds:
                    synergy.update(status='deferred',reason='insufficient-fresh-synergy-qualification-budget')
                    report['deferred'].append(dict(name='combined',members=list(item['members']),
                        spec=copy.deepcopy(item['spec']),reason=synergy['reason']))
                else:
                    synergy.update(status='qualifying');publish()
                    qualified=repeated(item['spec'],optional_budget=True)
                    qualified['unpromoted_members']=list(item['members'])
                    synergy['qualification']=qualified
                    decision=choose([reference,qualified])
                    accepted=decision['status']=='measured-winner'
                    synergy.update(status='qualified' if accepted else 'not-qualified',
                        reason=None if accepted else 'fresh-composed-qualification-did-not-establish-benefit')
                    if len(qualified['measurements'])<3 and any('budget' in issue for issue in qualified['issues']):
                        synergy.update(status='deferred',reason='insufficient-fresh-synergy-qualification-budget')
                        report['deferred'].append(dict(name='combined',members=list(item['members']),
                            spec=copy.deepcopy(item['spec']),reason=synergy['reason']))
                publish()
        trials=list(report.get('combination_trials',[]))
        if synergy['qualification'] is not None and synergy['qualification']['measurements']:
            trials.append(synergy['qualification'])
        if trials:
            decision=choose([reference]+trials);report['combined_decision']=decision
            if decision['status']=='measured-winner':
                report['proposed_policy']=decision['winner']
                report['combined_verification']=next(row for row in trials if row['spec']==decision['winner'])
            else:report['combined_verification']=trials[0]
        report['status']='measured-proposal' if report['proposed_policy'] else 'retained-current'
        # Honest scope: unscreened or unqualified families remain explicit.
        report['complete_optimization']=False
        return report
    except BaseException as error:
        if isinstance(error,RecoveryRequired) or report['status']=='recovery-required':report['status']='recovery-required'
        else:report['status']='refused'
        report['error']=type(error).__name__+': '+str(error)
        raise
    finally:publish()

__release_signature__ = 'Tino Zappala'
