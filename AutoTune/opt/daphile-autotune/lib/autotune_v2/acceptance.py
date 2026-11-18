"""Independent acceptance boundary for a measured proposal, before any commit."""
import copy,math
from .paired_search import combination_candidates,combine,SearchRefused,SYNERGY_PROTOCOL,SYNERGY_MEMBER_BASIS
from .adaptive_plan import qualification_group
from .accounting import issues as accounting_issues
try:from measurement_contract import choose,compare,metric,optimization_issues,BRACKET_PROTOCOL
except ImportError:from ..measurement_contract import choose,compare,metric,optimization_issues,BRACKET_PROTOCOL


def observation_spans(row,count,require_complete=True):
    if not isinstance(row,dict) or not isinstance(row.get('measurements'),list) or len(row['measurements'])!=count:
        raise ValueError('Exact bounded observation pairs required')
    if row.get('comparison_protocol')!=BRACKET_PROTOCOL:
        raise ValueError('Current A/B/A restored-baseline protocol required')
    spans=[]
    for pair in row['measurements']:
        if not isinstance(pair,dict) or set(pair)!={'before','after','restored_baseline'}:raise ValueError('Complete restored-baseline observation block required')
        for observation in (pair['before'],pair['after'],pair['restored_baseline']):
            if not isinstance(observation,dict):raise ValueError('Observation object required')
            if require_complete and optimization_issues(observation)+accounting_issues(observation):raise ValueError('Complete current-version measurements required')
            start,end=(observation.get('observation_monotonic_'+k) for k in ('start','end'))
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or start>=end:
                raise ValueError('Real finite observation intervals required')
            spans.append((start,end))
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('Reused or overlapping observations')
    return spans


def checked_pairs(row,count=3):
    if not isinstance(row,dict) or row.get('issues')!=[]:
        raise ValueError('Complete issue-free qualification pairs required')
    spans=observation_spans(row,count)
    applied=row.get('spec',{}).get('stage')!='reference'
    flags=row.get('application_observations')
    if (not isinstance(flags,list) or len(flags)!=count or any(flag is not applied for flag in flags)
            or row.get('application_observed') is not applied):
        raise ValueError('Each independent observation must prove its own control application state')
    if row.get('application_observed') is True and any(
            compare(pair[key],pair['after']) for pair in row['measurements']
            for key in ('before','restored_baseline')):
        raise ValueError('Applied trial regressed against a bracketing baseline')
    return spans


def combination_rows(search):
    """Return normal attempts only; the final alias can instead identify a probe."""
    combined=search.get('combined_verification')
    if 'combination_trials' not in search:
        synergy=search.get('neutral_synergy')
        if isinstance(synergy,dict) and combined==synergy.get('qualification'):return []
        return [combined] if combined else []
    rows=search['combination_trials']
    if not isinstance(rows,list) or len(rows)>7 or any(not isinstance(row,dict) for row in rows):
        raise ValueError('At most seven explicit combination attempts required')
    return rows


def _screen_promoted(row):
    """Recompute the screening threshold here, never trusting a report label."""
    pair=row['measurements'][0]
    a,b,c=(metric(pair[key]) for key in ('before','after','restored_baseline'))
    if any(value is None for value in (a,b,c)):raise ValueError('Complete screening metrics required')
    return (all(b['latency']<=value['latency'] for value in (a,c)) and
        (any(all(b[key]<value[key]*.9 for value in (a,c))
             for key in ('cpu','context','interrupts','wakeups','latency') if all(key in v for v in (a,b,c)))
         or all(b['thermal']<=value['thermal']-2 for value in (a,c))))


def _unpromoted_member(row):
    spans=checked_pairs(row,1)
    spec=row.get('spec',{})
    if spec.get('stage')=='reference' or spec.get('applicable') is False or _screen_promoted(row):
        raise ValueError('Synergy members must be individually unpromoted applied controls')
    pair=row['measurements'][0];candidate=pair['after'];b=metric(candidate)
    objectives={'cpu','context','interrupts','wakeups','latency','thermal'}
    for key in ('before','restored_baseline'):
        baseline=pair[key];a=metric(baseline)
        if set(a)!=objectives or set(b)!=objectives or any(b[name]>a[name] for name in objectives):
            raise ValueError('Synergy member has incomplete metrics or observed regression')
        peak_a,peak_b=baseline['max_cpu_percent'],candidate['max_cpu_percent']
        if set(peak_a)!=set(peak_b) or 'cpu' not in peak_a:
            raise ValueError('Synergy CPU peak coverage changed')
        if any(type(value) not in (int,float) or not math.isfinite(value) or value<0
               for peaks in (peak_a,peak_b) for value in peaks.values()):
            raise ValueError('Synergy CPU peak observation invalid')
        if (peak_b['cpu']>peak_a['cpu'] or max(v for k,v in peak_b.items() if k!='cpu')>
                max(v for k,v in peak_a.items() if k!='cpu')):
            raise ValueError('Synergy member CPU peak regressed')
        sensors_a=baseline['telemetry']['temperatures'];sensors_b=candidate['telemetry']['temperatures']
        if set(sensors_a)!=set(sensors_b) or any(sensors_b[name]['peak_c']>sensors_a[name]['peak_c'] or
                sensors_b[name]['headroom_c']<sensors_a[name]['headroom_c'] for name in sensors_a):
            raise ValueError('Synergy member thermal observation regressed')
    return spans


def synergy_rows(search,reference,pool):
    """Reconstruct the single optional probe from its actual individual screens.

    The producer's member labels, 'qualified' status and chosen policy are not
    authority. Membership, distinct targets, composed controls, screening and
    three additional observations are all checked independently here.
    """
    if 'neutral_synergy' not in search:return []
    value=search['neutral_synergy']
    if (not isinstance(value,dict) or value.get('protocol')!=SYNERGY_PROTOCOL
            or value.get('member_basis')!=SYNERGY_MEMBER_BASIS):
        raise ValueError('Known single unpromoted-pair protocol required')
    spec=value.get('spec');screened=value.get('screening');qualified=value.get('qualification')
    if spec is None:
        if (value.get('members')!=[] or screened is not None or qualified is not None
                or value.get('status')!='not-planned'):
            raise ValueError('Unplanned synergy cannot contain measurement claims')
        return []
    names=value.get('members')
    if (not isinstance(spec,dict) or not isinstance(names,list) or len(names)!=2
            or any(not isinstance(name,str) or not name for name in names) or len(set(names))!=2):
        raise ValueError('Exactly two distinct observed synergy members required')
    sources=[];member_spans=[]
    for name in names:
        rows=[row for row in search.get('screening',[]) if isinstance(row,dict) and row.get('spec',{}).get('name')==name]
        if len(rows)!=1:raise ValueError('Exactly one original screen required for each synergy member')
        row=rows[0];member_spans+=_unpromoted_member(row);sources.append(row['spec'])
        if any(other.get('spec')==row['spec'] for other in search.get('qualification',[])):
            raise ValueError('Individually promoted finalist cannot become an unpromoted synergy member')
    if qualification_group(sources[0])==qualification_group(sources[1]):
        raise ValueError('Synergy must join two distinct actual control groups')
    try:expected=combine(sources)
    except SearchRefused as error:raise ValueError('Incompatible synergy controls') from error
    if expected!=spec or any(expected==combine([source]) for source in sources) or any(expected==row['spec'] for row in pool):
        raise ValueError('Synergy controls differ from two independent unpromoted groups')
    if screened is None:
        if (qualified is not None or value.get('status')!='deferred'
                or value.get('reason')!='insufficient-unpromoted-synergy-budget'):
            raise ValueError('Unscreened synergy cannot be qualified')
        return []
    if not isinstance(screened,dict) or screened.get('spec')!=spec:
        raise ValueError('Exact composed screening controls required')
    screen_spans=observation_spans(screened,1,require_complete=screened.get('issues')==[])
    if max(end for _,end in member_spans)>screen_spans[0][0]:
        raise ValueError('Composed screening must follow its individual evidence')
    if screened.get('issues'):
        if qualified is not None or value.get('status')!='screening-rejected':
            raise ValueError('Rejected synergy screening cannot qualify')
        return []
    checked_pairs(screened,1)
    if not _screen_promoted(screened):
        if qualified is not None or value.get('status')!='screened-no-benefit':
            raise ValueError('Synergy qualification needs fresh promising composed screening')
        return []
    if qualified is None:
        if (value.get('status')!='deferred'
                or value.get('reason')!='insufficient-fresh-synergy-qualification-budget'):
            raise ValueError('Composed screening alone cannot establish synergy')
        return []
    if (not isinstance(qualified,dict) or qualified.get('spec')!=spec
            or qualified.get('unpromoted_members')!=names):
        raise ValueError('Exact composed qualification controls and provenance required')
    pairs=qualified.get('measurements')
    if not isinstance(pairs,list) or not 0<=len(pairs)<=3:raise ValueError('At most three fresh synergy qualification blocks required')
    if not qualified.get('issues'):
        qualification_spans=checked_pairs(qualified)
    else:qualification_spans=observation_spans(qualified,len(pairs),require_complete=False)
    if qualification_spans and screen_spans[-1][1]>qualification_spans[0][0]:
        raise ValueError('Synergy qualification must be additional to its screening')
    accepted=choose([reference,qualified])['status']=='measured-winner'
    if (value.get('status')=='qualified') is not accepted:
        raise ValueError('Claimed synergy qualification differs from independent evidence')
    if not accepted and value.get('status') not in ('not-qualified','deferred'):
        raise ValueError('Incomplete synergy qualification status')
    return [qualified] if pairs else []


def _all_probe_spans(search,normal_trials):
    """A probe may not reuse any earlier screening, finalist or normal trial."""
    spans=observation_spans(search['reference'],3)
    for row in search.get('screening',[]):spans+=observation_spans(row,1,require_complete=False)
    for row in list(search.get('qualification',[]))+normal_trials:
        pairs=row.get('measurements') if isinstance(row,dict) else None
        if not isinstance(pairs,list) or not 1<=len(pairs)<=3:raise ValueError('Bounded earlier observations required')
        spans+=observation_spans(row,len(pairs),require_complete=False)
    value=search['neutral_synergy']
    screened=value.get('screening')
    if screened is not None:
        extra=observation_spans(screened,1,require_complete=False)
        if spans and max(end for _,end in spans)>extra[0][0]:
            raise ValueError('Optional synergy must follow the earlier search evidence')
        spans+=extra
    qualified=value.get('qualification')
    if qualified is not None:spans+=observation_spans(qualified,len(qualified['measurements']),require_complete=False)
    return spans


def proposal(search):
    if (search.get('status')!='measured-proposal' or search.get('settings_applied') is not False
            or search.get('comparison_protocol')!=BRACKET_PROTOCOL):
        raise ValueError('Unapplied measured proposal required')
    reference=search['reference'];spans=checked_pairs(reference)
    if reference.get('application_observed') is not False:raise ValueError('Reference changed a control')
    winners=[];groups={}
    for row in search.get('qualification',[]):
        # Failed finalists remain evidence; only independently accepted winners
        # can contribute controls to the eventual combined policy.
        if row.get('issues') or len(row.get('measurements',[]))!=3:continue
        checked_pairs(row)
        groups.setdefault(qualification_group(row['spec']),[]).append(row)
    for rows in groups.values():
        decision=choose([reference]+rows)
        if decision.get('status')=='measured-winner':
            winner=next(r for r in rows if r['spec']==decision['winner'])
            if winner.get('application_observed') is not True:raise ValueError('Finalist was not actually applied')
            screens=[r for r in search.get('screening',[]) if r.get('spec')==winner['spec']]
            if len(screens)!=1 or screens[0].get('application_observed') is not True:
                raise ValueError('Independent original screening required')
            spans+=checked_pairs(screens[0],1)+checked_pairs(winner)
            winners.append(winner)
    pool=combination_candidates(reference,winners) if winners else []
    combined=search['combined_verification']
    normal_trials=combination_rows(search)
    if not isinstance(normal_trials,list) or len(normal_trials)>len(pool):
        raise ValueError('Bounded independent combination trials required')
    seen=[]
    for trial in normal_trials:
        members=next((row for row in pool if row['spec']==trial.get('spec')),None)
        if members is None or trial['spec'] in seen:
            raise ValueError('Combined controls differ from qualified families or repeat a candidate')
        if 'combination_trials' in search and trial.get('qualified_members')!=members['members']:
            raise ValueError('Combined member evidence differs from qualified families')
        seen.append(trial['spec'])
        if trial.get('application_observed') is not True:
            raise ValueError('Combined policy was not actually applied')
        if not trial.get('issues'):
            spans+=checked_pairs(trial)
        else:
            # A measured regression can stop a combination early. Preserve its
            # windows and reject invalid/reused provenance without promoting
            # the failed trial or losing a separately qualified alternative.
            pairs=trial.get('measurements',[])
            if not isinstance(pairs,list) or not 1<=len(pairs)<=3:
                raise ValueError('Failed combination has no bounded observation evidence')
            spans+=observation_spans(trial,len(pairs),require_complete=False)
    extra_trials=synergy_rows(search,reference,pool)
    trials=normal_trials+extra_trials
    if not trials:raise ValueError('No repeatedly qualified composed controls')
    if sum(row==combined for row in trials)!=1:
        raise ValueError('Chosen combination must identify one actual attempted trial')
    decision=choose([reference]+trials)
    expected=decision.get('winner')
    if (decision.get('status')!='measured-winner' or expected!=search.get('proposed_policy')
            or combined.get('spec')!=expected or not any(row==combined for row in trials)):
        raise ValueError('Combined policy lacks repeated independent benefit')
    if isinstance(search.get('neutral_synergy'),dict) and search['neutral_synergy'].get('spec') is not None:
        spans=_all_probe_spans(search,normal_trials)
    spans.sort()
    if any(a[1]>b[0] for a,b in zip(spans,spans[1:])):raise ValueError('Qualification reused an observation window')
    return copy.deepcopy(expected)

__release_signature__ = 'Tino Zappala'
