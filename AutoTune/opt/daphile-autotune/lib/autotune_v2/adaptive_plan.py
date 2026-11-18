"""Budgeted, balanced screening of actual candidate families.

Screening is not qualification or permission to apply a candidate. Repeated
paired verification and final combined verification retain their own reserve.
The native V2 workflow uses this plan; the budget can defer otherwise valid
coordinates and therefore never establishes exhaustive optimization.
"""
import collections,json,math

def family(candidate):
    if candidate.get('stage')=='reference':return 'reference'
    if candidate.get('stage')=='scheduling':
        coordinate=candidate.get('coordinate')
        if coordinate=='priority' and 'output' not in candidate and candidate.get('policy') is not None:return 'priority'
        if coordinate=='placement' and 'output' in candidate and candidate.get('policy') is None:return 'placement'
        if coordinate not in (None,'combined','scheduling'):raise ValueError('Inconsistent scheduling coordinate')
        return 'scheduling'
    mapping={'governor':'governor','ceiling':'idle','clock':'clock',
             'frequency_policy':'frequency','features':'network-offloads','coalesce':'network-coalescing'}
    found=[name for key,name in mapping.items() if key in candidate]
    if len(found)!=1:raise ValueError('Candidate has no single supported control family')
    return found[0]

def qualification_group(candidate):
    """Derive mutually exclusive finalists from actual control targets.

    A public family describes the kind of control, not necessarily its target.
    Frequency values on one CPU policy compete; values on distinct policies
    can coexist. Declared screen/qualification labels cannot change this key.
    """
    group=family(candidate)
    if group=='frequency':
        policy=candidate.get('frequency_policy')
        if not isinstance(policy,str) or not policy:raise ValueError('Frequency policy target required')
        return (group,policy)
    return (group,)

def plan(inventory,remaining_seconds,screen_window=10,transition_allowance=10,
         recovery_reserve=120,final_reserve=60,finalists=3,repetitions=3,observation_windows=2):
    values=(remaining_seconds,screen_window,transition_allowance,recovery_reserve,final_reserve)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in values):raise ValueError('Finite measured time budget required')
    if screen_window<10 or recovery_reserve<120 or final_reserve<60 or type(finalists) is not int or finalists<1 or type(repetitions) is not int or repetitions<3:raise ValueError('Verification or restoration reserve cannot be removed')
    if type(observation_windows) is not int or observation_windows not in (2,3):raise ValueError('Known paired or restored-baseline observation protocol required')
    candidates=inventory.get('candidates')
    if not isinstance(candidates,list):raise ValueError('Observed candidate inventory required')
    names=[c.get('name') for c in candidates if isinstance(c,dict)]
    if len(names)!=len(candidates) or any(not isinstance(n,str) or not n for n in names) or len(set(names))!=len(names):raise ValueError('Unique candidate names required')
    references=[c for c in candidates if c.get('stage')=='reference']
    if len(references)!=1:raise ValueError('Exactly one current reference required')
    # One screening block and its transition allowance. Current V2 supplies
    # three windows for A/B/restored-A; historical callers may estimate A/B.
    # Every finalist
    # still needs >=3 complete paired blocks; reserve that time beforehand.
    pair_seconds=observation_windows*screen_window+transition_allowance
    reserve=recovery_reserve+final_reserve+finalists*repetitions*pair_seconds
    capacity=max(0,int((remaining_seconds-reserve)//pair_seconds))
    queues=collections.OrderedDict();deferred=[];seen=set()
    for candidate in candidates:
        if candidate is references[0]:continue
        try:group=qualification_group(candidate)[0]
        except ValueError:
            deferred.append({'name':candidate['name'],'reason':'unsupported-control-family'});continue
        if candidate.get('applicable') is False:
            deferred.append({'name':candidate['name'],'family':group,'reason':'hardware-not-applicable'});continue
        signature=json.dumps({k:v for k,v in candidate.items() if k not in ('name','stage','coordinate','applicable')},sort_keys=True)
        if signature in seen:
            deferred.append({'name':candidate['name'],'family':group,'reason':'same-control-values-already-planned'});continue
        seen.add(signature);queues.setdefault(group,collections.deque()).append(candidate)
    # Preserve breadth inside the public frequency family as well: a short
    # budget should encounter each independent policy before a second value
    # of the first policy. This changes order only, never the number of slots.
    if 'frequency' in queues:
        policies=collections.OrderedDict()
        for candidate in queues['frequency']:
            policies.setdefault(qualification_group(candidate),collections.deque()).append(candidate)
        queues['frequency']=collections.deque()
        while any(policies.values()):
            for queue in policies.values():
                if queue:queues['frequency'].append(queue.popleft())
    # Clocksource experiments follow the native CPU/scheduler coordinates.
    # A small time budget must not be exhausted on alternate timers before
    # checking the CPU controls actually exposed in POWER.
    priority={'governor':0,'priority':1,'placement':2,'scheduling':3,'idle':4,'frequency':5,'clock':6,'network-offloads':7,'network-coalescing':8}
    queues=collections.OrderedDict(sorted(queues.items(),key=lambda row:priority.get(row[0],99)))
    selected=[]
    while len(selected)<capacity and any(queues.values()):
        for group,queue in queues.items():
            if queue and len(selected)<capacity:selected.append(dict(queue.popleft(),screen_family=group))
    for group,queue in queues.items():
        deferred.extend({'name':c['name'],'family':group,'reason':'reserved-time-for-repeated-verification-and-recovery'} for c in queue)
    return dict(status='screening-planned' if selected else 'insufficient-screening-budget',
                reference=dict(references[0]),screening=selected,deferred=deferred,
                available_families=list(queues),screened_candidates=0,qualified_candidates=0,
                maximum_screening_seconds=len(selected)*pair_seconds,reserved_seconds=reserve,
                observation_windows=observation_windows,
                repetitions_required=repetitions,settings_applied=False,
                exhaustive_screening=not deferred,complete_optimization=False)

__release_signature__ = 'Tino Zappala'
