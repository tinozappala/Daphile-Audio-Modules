"""Describe the limits of a native V2 search without granting acceptance.

These fields explain evidence already gathered. They never authorize a trial,
commit, or restore, and do not convert an attempted qualification into a pass.
"""
import collections
import copy


def count(value):
    return value if type(value) is int and value >= 0 else None


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
    return {
        'schema_version': 1,
        'method': 'bounded-paired-native-v2',
        'counts': counts,
        'available_control_families': copy.deepcopy(planning.get('available_families') or []),
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
        'latency_metric': 'maximum observed runnable-to-run delay of verified audio and USB IRQ threads',
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
