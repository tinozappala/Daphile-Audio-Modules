"""Automatic application contains only the policy actually selected by trials."""


def decide(h, selected=None, baseline=None):
    changes=[]
    if selected is not None:
        if not isinstance(selected,dict) or selected.get('name') not in ('current','combined'):
            raise RuntimeError('Automatic policy must come from measured search')
        allowed={'name','stage','output','irq','policy','priority','clock','ceiling','governor','frequencies','features','coalesce'}
        if set(selected)-allowed:raise RuntimeError('Unknown automatic policy coordinate')
        if selected.get('name')=='current' and set(selected)-{'name','stage'}:
            raise RuntimeError('Reference policy cannot contain changes')
        if selected.get('name')=='combined' and len(selected)>2:
            import search
            changes=search.changes(h,selected)
    keys=set()
    for c in changes:
        key=(c['parameter'],c.get('path'),c.get('policy'),c.get('tid'),c.get('service'))
        if key in keys:raise RuntimeError('Duplicate automatic control target')
        keys.add(key)
    return dict(profile='measured-automatic',hardware_id=h['hardware_id'],changes=changes,
                warnings=['Only measured search selections are applied; other controls retain their current values.'],
                sound_quality_claim=False)


def measured_gain(before,after):
    """A fresh final pair must meet the selector's complete numeric contract."""
    try:from measurement_contract import confirmation_issues
    except ImportError:from .measurement_contract import confirmation_issues
    return not confirmation_issues(before,after)


def application_issues(before,after,changes):
    """Compare actual changes; independently qualify an unchanged reference.

    Two real observations are still required. Variation between them cannot be
    attributed to an application when no setting was written, and conveys no
    improvement claim.
    """
    try:from measurement_contract import confirmation_issues,observation_pair_issues
    except ImportError:from .measurement_contract import confirmation_issues,observation_pair_issues
    if not isinstance(changes,list):raise ValueError('Observed change list required')
    return confirmation_issues(before,after) if changes else observation_pair_issues(before,after)


def shortlist(h,inventory,experimental=False):
    """Bounded calibration covers CPU power as well as audio placement.

    Only governors the native Settings implementation supports are selected.
    Every omitted coordinate remains explicit; three paired repeats are kept.
    """
    candidates=inventory['candidates'];reference=next(c for c in candidates if c['stage']=='reference')
    owner=h['dac'].get('owner_pid')
    thread=next((t for p in h.get('squeezelite',[]) for t in p.get('threads',[]) if t['tid']==owner),{})
    current={c.get('cpufreq',{}).get('scaling_governor') for c in h.get('cpu',{}).get('cores',[])}
    governors=[c for c in candidates if c.get('governor') in ('ondemand','performance') and current!={c['governor']}]
    governors.sort(key=lambda c:(c['governor']!='ondemand',c['name']))
    scheduling=[c for c in candidates if c['stage']=='scheduling']
    import re
    irq=(h['dac'].get('usb') or {}).get('irq')
    entry=next((r for r in h.get('irq',[]) if str(r.get('irq'))==str(irq)),{})
    parts=re.split(r'(?:fasteoi|edge|level)\s+',entry.get('description',''),maxsplit=1)
    owners=[s.strip() for s in parts[-1].split(',')] if len(parts)==2 else []
    independent=bool(owners and all(re.fullmatch(r'(?:ehci|xhci|ohci|uhci)_hcd:usb[0-9]+',s) for s in owners))
    if not independent:
        # A shared IRQ excludes affinity trials, but the verified output thread
        # can still compare its own scheduler without moving the controller.
        original=scheduling;scheduling=[c for c in original if 'output' not in c and 'irq' not in c]
        seen={(c.get('policy'),c.get('priority')) for c in scheduling}
        for c in original:
            pair=(c.get('policy'),c.get('priority'))
            if c.get('policy') is None or pair in seen or pair==(thread.get('policy'),thread.get('priority')):continue
            seen.add(pair);scheduling.append({'name':'scheduler-only-'+str(pair[0])+'-'+str(pair[1]),'stage':'scheduling','coordinate':'scheduling','policy':pair[0],'priority':pair[1]})
    scheduling.sort(key=lambda c:(c.get('priority')!=thread.get('priority',60),c.get('output')==thread.get('affinity'),c.get('policy')!=thread.get('policy'),c['name']))
    selected=[reference]
    if governors:selected.append(governors[0])
    if scheduling:selected.append(scheduling[0])
    fallback=[c for c in candidates if c['stage']=='energy' and ('ceiling' in c or 'clock' in c)] if experimental else [c for c in candidates if c['stage']=='network']
    for c in fallback:
        if len(selected)>=3:break
        if c not in selected:selected.append(c)
    return dict(candidates=selected,deferred_candidates=[c['name'] for c in candidates if c not in selected],
                scope='CPU governor and verified audio placement, three paired repetitions per candidate; other coordinates explicitly deferred to extended diagnostics.',exhaustive=False)

__release_signature__ = 'Tino Zappala'
