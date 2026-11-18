"""Restore an observed IRQ delivery CPU as well as its requested mask.

Writing a broad original mask alone can leave the interrupt on the trial CPU.
The original effective mask is a journaled, bounded intermediate, never guessed.
"""
import copy,time


def snapshot(change,read,numbers):
    requested=set(numbers(change['before']));effective=read('/proc/irq/'+str(change['irq'])+'/effective_affinity_list')
    observed=set(numbers(effective)) if effective else set()
    if not observed or not observed<=requested:raise RuntimeError('Original IRQ effective route unavailable')
    return ','.join(str(n) for n in sorted(observed))


def restore(change,write,value,read,numbers):
    requested=set(numbers(change['before']));effective=set(numbers(change['effective_before']))
    if not effective or not effective<=requested or not change.get('rollback_irq_hint_attempted'):raise RuntimeError('Owned original IRQ delivery snapshot required')
    hint=','.join(str(n) for n in sorted(effective));temporary=copy.deepcopy(change);temporary['after']=hint
    now=read('/proc/irq/'+str(change['irq'])+'/effective_affinity_list')
    if set(numbers(now))!=effective:
        write(temporary,hint)
    write(change,change['before'])
    end=time.monotonic()+3;stable=0
    while time.monotonic()<end:
        observed=set(numbers(read('/proc/irq/'+str(change['irq'])+'/effective_affinity_list')))
        stable=stable+1 if set(numbers(value(change)))==requested and observed==effective else 0
        if stable>=2:return
        time.sleep(.02)
    raise RuntimeError('Original IRQ delivery CPU did not return')

__release_signature__ = 'Tino Zappala'
