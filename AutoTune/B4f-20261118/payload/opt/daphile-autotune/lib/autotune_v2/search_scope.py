"""Describe the limits of a native V2 search without granting acceptance.

These fields explain evidence already gathered. They never authorize a trial,
commit, or restore, and do not convert an attempted qualification into a pass.
"""
import collections
import copy
import math
from .adaptive_plan import family
from .accounting import issues as accounting_issues
try:from measurement_contract import optimization_issues,observation_coverage
except ImportError:from ..measurement_contract import optimization_issues,observation_coverage


def count(value):
    return value if type(value) is int and value >= 0 else None


def trial_rows(search):
    rows=[search.get('reference')]
    for key in ('screening','qualification','combination_trials'):
        value=search.get(key)
        if isinstance(value,list):rows.extend(value)
    rows.append(search.get('combined_verification'))
    synergy=search.get('neutral_synergy')
    if isinstance(synergy,dict):rows.extend(synergy.get(key) for key in ('screening','qualification'))
    return [row for row in rows if isinstance(row,dict)]


def row_family(row):
    spec=row.get('spec')
    if not isinstance(spec,dict) or spec.get('stage') in ('reference','combined'):return None
    try:return family(spec)
    except ValueError:return None


def complete_observation(value):
    try:return isinstance(value,dict) and not optimization_issues(value) and not accounting_issues(value)
    except (AttributeError,KeyError,TypeError,ValueError):return False


def measurement_coverage(rows):
    """Describe current complete evidence, deduplicating final-result aliases."""
    observed={};conflicts=set();invalid=0
    for row in rows:
        pairs=row.get('measurements')
        if not isinstance(pairs,list):continue
        for pair in pairs:
            for key in ('before','after','restored_baseline'):
                value=pair.get(key) if isinstance(pair,dict) else None
                span=tuple(value.get('observation_monotonic_'+key) for key in ('start','end')) if isinstance(value,dict) else ()
                if (len(span)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in span)
                        or not 0<=span[0]<span[1]):
                    invalid+=1;continue
                if span in observed and observed[span]!=value:conflicts.add(span)
                else:observed[span]=value
    complete=[value for span,value in observed.items() if span not in conflicts and complete_observation(value)]
    total=len(observed)+invalid
    scopes=collections.Counter(value['scheduler_trace']['measurement_scope'] for value in complete)
    roles=collections.Counter(row['role'] for value in complete for row in value['scheduler_trace']['target_roles'])
    signatures={observation_coverage(value) for value in complete}
    consistent=bool(complete) and len(complete)==total and len(signatures)==1
    common_scope=next(iter(scopes)) if consistent else None
    if common_scope=='verified-audio-and-usb-irq':
        label='maximum observed runnable-to-run delay of verified audio and physical USB IRQ threads'
    elif common_scope=='verified-audio':
        label='maximum observed runnable-to-run delay of the verified audio output thread'
    elif complete:
        label='observed runnable-to-run delay with mixed or incomplete measurement coverage'
    else:label='unavailable; no current complete scheduler observation'
    return dict(observation_count=total,current_complete_observation_count=len(complete),
        incomplete_observation_count=total-len(complete),conflicting_observation_windows=len(conflicts),
        complete_observed_scopes=dict(sorted(scopes.items())),complete_observed_role_counts=dict(sorted(roles.items())),
        consistent_coverage=consistent,common_scope=common_scope,
        roles_covered_every_observation=sorted(roles) if consistent else [],
        usb_irq_covered_every_observation=common_scope=='verified-audio-and-usb-irq',
        label=label,acceptance_authority=False),observed,conflicts


def family_coverage(search,rows,observed,conflicts):
    planning=search.get('planning') or {}
    available={name for name in planning.get('available_families',[]) if isinstance(name,str)}
    attempted=set();measured=set();by_name={}
    for row in rows:
        group=row_family(row);spec=row.get('spec',{})
        if group is None:continue
        by_name[spec.get('name')]=group
        if row.get('measurements'):attempted.add(group)
        if row.get('application_observed') is not True:continue
        for pair in row.get('measurements',[]):
            value=pair.get('after') if isinstance(pair,dict) else None
            if not complete_observation(value):continue
            span=(value['observation_monotonic_start'],value['observation_monotonic_end'])
            if span in observed and span not in conflicts:measured.add(group)
    for spec in planning.get('screening',[]):
        if not isinstance(spec,dict):continue
        group=row_family(dict(spec=spec))
        if group:by_name[spec.get('name')]=group
    deferred=collections.Counter();unclassified=0
    for row in search.get('deferred') or []:
        if not isinstance(row,dict):continue
        group=row.get('family') or row_family(dict(spec=row.get('spec'))) or by_name.get(row.get('name'))
        if isinstance(group,str):deferred[group]+=1
        else:unclassified+=1
    return dict(supported_control_families=sorted(available|measured),
        support_basis='current native inventory eligibility or actual observed application; full qualification is separate',
        attempted_control_families=sorted(attempted),observed_control_families=sorted(measured),
        deferred_candidate_families=dict(sorted(deferred.items())),unclassified_deferred_entries=unclassified,
        families=[dict(name=name,eligible_for_screening=name in available,attempted=name in attempted,
            observed_in_complete_candidate_measurement=name in measured,deferred_candidates=deferred[name])
            for name in sorted(available|attempted|measured|set(deferred))],acceptance_authority=False)


def describe(search, workload):
    planning = search.get('planning') or {}
    combinations = search.get('combination_coverage') or {}
    synergy = search.get('neutral_synergy')
    synergy = synergy if isinstance(synergy, dict) else {}
    synergy_screen = synergy.get('screening')
    synergy_qualification = synergy.get('qualification')
    synergy_attempted = (isinstance(synergy_screen, dict)
                         and isinstance(synergy_screen.get('measurements'), list)
                         and len(synergy_screen['measurements']) == 1)
    synergy_repeated = (isinstance(synergy_qualification, dict)
                        and isinstance(synergy_qualification.get('measurements'), list)
                        and bool(synergy_qualification['measurements']))
    synergy_qualified = (synergy_repeated and synergy.get('status') == 'qualified'
                         and len(synergy_qualification['measurements']) == 3
                         and synergy_qualification.get('issues') == []
                         and synergy_qualification.get('application_observed') is True)
    deferred = search.get('deferred') or []
    reasons = collections.Counter(row.get('reason', 'unspecified') for row in deferred if isinstance(row, dict))
    # The acceptance boundary independently recomputes winners. Separate
    # control groups (e.g. two cpufreq policies) from their public family.
    # Neither accepted count is inferred from the number of attempted trials.
    counts = {
        'screening_attempts': len(search.get('screening') or []),
        'qualification_attempts': len(search.get('qualification') or []),
        'qualified_control_groups': count(search.get('qualified_candidates')),
        'qualified_families': count(search.get('qualified_families')),
        'combination_candidates': count(combinations.get('compatible_candidates')),
        'combination_attempts': len(search.get('combination_trials') or []),
        'complete_combination_comparisons': count(combinations.get('complete_paired_candidates')),
        'accepted_combination_comparisons': count(combinations.get('accepted_candidates')),
        'unpromoted_synergy_attempts': int(synergy_attempted),
        'unpromoted_synergy_qualification_attempts': int(synergy_repeated),
        'unpromoted_synergy_qualified': int(synergy_qualified),
        'deferred_entries': len(deferred),
        'excluded_before_planning': len(search.get('inventory_deferred') or []),
    }
    controlled = workload.get('scope') == 'owned-native-pcm'
    rows=trial_rows(search)
    measurements,observed,conflicts=measurement_coverage(rows)
    return {
        'schema_version': 2,
        'method': 'bounded-paired-native-v2',
        'counts': counts,
        'available_control_families': copy.deepcopy(planning.get('available_families') or []),
        'control_family_coverage': family_coverage(search,rows,observed,conflicts),
        'measurement_coverage': measurements,
        'placement_coverage': copy.deepcopy(search.get('placement_coverage') or search.get('inventory_placement_coverage') or planning.get('placement_coverage')),
        'deferral_reasons': dict(sorted(reasons.items())),
        'controlled_workload': {
            'declared': controlled,
            'source_kind': 'local-generated-digital-silence' if controlled else 'unavailable',
            'rate_hz': count(workload.get('rate')) if controlled else None,
            'bits': count(workload.get('bits')) if controlled else None,
            'channels': count(workload.get('channels')) if controlled else None,
            'sample_rate_sweep': False,
            'codec_or_network_stress_qualified': False,
        },
        'latency_metric': measurements['label'],
        'observation_conditions': 'short instrumented A/B/A windows with repeated qualification',
        'comparison_protocol': search.get('comparison_protocol'),
        'comparison_order': 'original baseline, candidate, newly observed restored original baseline',
        'restored_baseline_required': True,
        'randomized_comparisons': False,
        'causal_effect_statistically_established': False,
        'neutral_candidate_synergies_explored': synergy_attempted,
        'unpromoted_synergy': {
            'member_basis': 'not-promoted-without-observed-regression',
            'maximum_pairs': 1,
            'groups_per_pair': 2,
            'screening_blocks': 1,
            'additional_qualification_blocks': 3,
            'member_statistical_equivalence_proven': False,
            'all_unpromoted_pairs_tested': False,
            'status': synergy.get('status', 'unavailable'),
            'reason': synergy.get('reason'),
        },
        'ranking_method': 'mean-relative-observed-metrics after conservative safety and benefit gates',
        'ranking_reference_competes': True,
        'ranking_reference_score': 1.0,
        'pareto_objectives': 'median and observed maximum of every declared metric, including absolute temperature',
        'initial_task_state_observation_required': True,
        'unfinished_runnable_intervals_refused': True,
        'latency_worst_case_proven': False,
        'end_to_end_audio_latency_measured': False,
        'energy_joules_measured': False,
        'analog_output_measured': False,
        'bit_transparency_measured': False,
        'sound_quality_claim': False,
        'alsa_buffer_optimized_by_this_workflow': False,
        'global_optimum_proven': False,
        'complete_optimization': False,
        'all_qualified_subsets_tested': combinations.get('all_qualified_subsets_tested') is True,
        'subset_scope': 'Compatible subsets of at most three repeatedly qualified control-group winners',
        'acceptance_authority': False,
    }


def summary(scope):
    values = scope['counts']
    accepted = values['accepted_combination_comparisons']
    if accepted is None and values['combination_attempts'] == 0:accepted = 0
    return {
        'screened': values['screening_attempts'],
        'qualified': values['qualified_control_groups'],
        'qualified_families': values['qualified_families'],
        'qualification_attempted': values['qualification_attempts'],
        'deferred': values['deferred_entries'],
        'combinations_tested': values['combination_attempts'] + values['unpromoted_synergy_attempts'],
        'combinations_accepted': (accepted + values['unpromoted_synergy_qualified']) if accepted is not None else None,
        'complete_optimization': False,
    }

__release_signature__ = 'Tino Zappala'
