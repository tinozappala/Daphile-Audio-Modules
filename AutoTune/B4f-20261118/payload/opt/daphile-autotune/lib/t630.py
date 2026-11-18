"""Explicit T630/USB runtime plan; never changes boot options or enables itself."""
import re
from detection import numbers, read

def eligibility(h):
    reasons = []
    if (h.get('system') or {}).get('product_name') != 'HP t630 Thin Client':
        reasons.append('HP t630 Thin Client required')
    cpu = h.get('cpu') or {}; cores = cpu.get('cores') or []
    topology = {(c.get('topology', {}).get('physical_package_id'), c.get('topology', {}).get('core_id')) for c in cores}
    if (cpu.get('logical_cpus') != 4 or cpu.get('physical_cores') != 4 or cpu.get('smt') is not False
            or {c['cpu'] for c in cores} != {0, 1, 2, 3} or numbers(cpu.get('online')) != [0, 1, 2, 3]
            or len(topology) != 4 or any(None in x for x in topology)):
        reasons.append('Four distinct online physical cores without SMT required')
    d = h.get('dac') or {}; u = d.get('usb') or {}
    if (d.get('status') != 'verified-active' or not __import__('usb_target').identified(__import__('usb_target').identity(u))
           ):
        reasons.append('Verified active USB DAC with serial required')
    irq = u.get('irq')
    entry = next((x for x in h.get('irq') or [] if str(x['irq']) == str(irq)), None)
    suffix = re.split(r'(?:fasteoi|edge|level)\s+', (entry or {}).get('description', ''), maxsplit=1)
    owners = [x.strip() for x in suffix[-1].split(',')] if len(suffix) == 2 else []
    threads = [t for p in h.get('squeezelite', []) for t in p.get('threads', [])
               if (t.get('name') or '').startswith('irq/' + str(irq) + '-')]
    if (not u.get('controller') or not entry or not owners or not threads
            or not all(re.fullmatch(r'(?:ehci|xhci|ohci|uhci)_hcd:usb[0-9]+', x) for x in owners)
            or not all(re.search(r'ehci|xhci|ohci|uhci', t['name']) for t in threads)):
        reasons.append('Verified USB-only IRQ and its kernel thread required')
    return dict(eligible=not reasons, reasons=reasons, scope='explicit runtime trial; no boot writes',
                housekeeping_cpus=[0, 1], usb_irq_cpu=2, output_cpu=3)

def plan(h):
    check = eligibility(h)
    if not check['eligible']: raise RuntimeError('; '.join(check['reasons']))
    from advanced import plan as generic_plan, ticks
    changes, warnings = generic_plan(h, 'audio-rt')
    for c in changes:
        if c['parameter'] == 'usb_irq_affinity': c['after'] = '2'
        if c['parameter'] == 'thread_affinity' and c['role'] == 'usb-irq': c['after'] = [2]
    d = h['dac']
    # Include already-correct output/IRQ targets through the generic plan's ownership checks;
    # only verified audio/LMS processes are moved to housekeeping, never arbitrary user tasks.
    for p in h.get('squeezelite', []):
        role = p.get('runtime_role')
        if role not in ['audio-player', 'player-control', 'lms', 'scanner']: continue
        for t in p.get('threads', []):
            if t['tid'] == d['owner_pid']: continue
            if t.get('affinity') == [0, 1]: continue
            start = ticks(t['tid'])
            if not start: continue
            changes.append(dict(host_uuid=read('/sys/class/dmi/id/product_uuid'),
                                parameter='thread_affinity', before=t['affinity'], after=[0, 1],
                                identity=dict(d['identity']), classification='CONTROLLED TRIAL', reversible=True,
                                tid=t['tid'], start=start, role='housekeeping', name=t['name'],
                                process_pid=p['pid'], executable=p['executable'], runtime_role=role))
    return [c for c in changes if c['before'] != c['after']], warnings + [
        'T630 fixed runtime allocation: housekeeping 0-1, verified USB IRQ 2, output 3',
        'Existing boot isolation is observed, not enabled, removed or transferred to another machine']

__release_signature__ = 'Tino Zappala'
