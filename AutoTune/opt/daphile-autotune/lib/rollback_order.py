"""Restore IRQ request masks before their kernel-coupled thread affinities."""


def ordered(changes):
    rows=list(reversed(changes))
    return [c for c in rows if c['parameter']=='usb_irq_affinity']+[c for c in rows if c['parameter']!='usb_irq_affinity']


def owned_irq_restore_transition(change,current,changes,value,read,numbers):
    if change.get('parameter')!='thread_affinity' or change.get('role')!='usb-irq':return False
    owners=[c for c in changes if c.get('parameter')=='usb_irq_affinity' and c.get('write_attempted') and c.get('rollback_irq_write_verified') is True
        and c.get('identity')==change.get('identity') and c.get('host_uuid')==change.get('host_uuid') and (change.get('name') or '').startswith('irq/'+str(c.get('irq'))+'-')]
    if len(owners)!=1:return False
    owner=owners[0];requested=set(numbers(owner['before']));effective=set(numbers(read('/proc/irq/'+str(owner['irq'])+'/effective_affinity_list')))
    return bool(requested and effective and effective<=requested and set(current)==requested and set(numbers(value(owner)))==requested)


def same_value(change,current,expected,numbers):
    if change['parameter']=='usb_irq_affinity':return set(numbers(current))==set(numbers(expected)) and bool(numbers(expected))
    return current==expected

__release_signature__ = 'Tino Zappala'
