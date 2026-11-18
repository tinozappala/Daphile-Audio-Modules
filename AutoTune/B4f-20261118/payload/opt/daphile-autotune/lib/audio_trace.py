"""Per-instance event filters for verified audio/USB IRQ thread identities.

Filter contract: https://docs.kernel.org/trace/events.html#setting-filters
"""
import re
from pathlib import Path


def identities(targets,proc=Path('/proc')):
    """Read only the verified targets, without enumerating unrelated tasks."""
    def ticks(pid):
        try:
            tail=(proc/str(pid)/'stat').read_text().rsplit(')',1)[1].split()
            return tail[19] if tail[0] not in ('Z','X') else None
        except (OSError,IndexError):return None
    result={}
    for tid,start in targets.items():
        if type(tid)!=int or tid<=0 or not start:raise RuntimeError('Invalid audio target identity')
        try:
            task=proc/str(tid)
            status=(task/'status').read_text()
            owner=int(re.search(r'^Tgid:\s*(\d+)\s*$',status,re.M)[1])
            parent_start=ticks(owner);observed=ticks(tid)
            name=(task/'comm').read_text().strip()
            if parent_start and observed==start and ticks(tid)==observed and ticks(owner)==parent_start:
                result[tid]={'pid':owner,'process_start':parent_start,'thread_start':observed,'name':name}
        except (OSError,TypeError,ValueError):pass
    if set(result)!=set(targets):raise RuntimeError('Audio target exited or changed before tracing')
    return result


def filter_expression(event,tids):
    if not tids or len(tids)>64 or any(type(t)!=int or not 0<t<2**31 for t in tids):raise ValueError('Invalid bounded audio thread set')
    fields=('prev_pid','next_pid') if event=='sched_switch' else ('pid',)
    if event not in ('sched_switch','sched_wakeup','sched_wakeup_new','sched_process_exit'):raise ValueError('Unexpected audio event')
    return ' || '.join('('+field+' == '+str(tid)+')' for tid in sorted(tids) for field in fields)


def apply_filters(path,known,targets):
    if any(t not in known or known[t]['thread_start']!=start for t,start in targets.items()):raise RuntimeError('Audio thread identity changed before tracing')
    for event in ('sched_switch','sched_wakeup','sched_wakeup_new','sched_process_exit'):
        target=path/'events/sched'/event
        fields=('prev_pid','next_pid') if event=='sched_switch' else ('pid',)
        description=(target/'format').read_text()
        if any(not re.search(r'\b'+field+r'\s*;',description) for field in fields):raise RuntimeError('Required scheduler event field absent')
        (target/'filter').write_text(filter_expression(event,targets)+'\n')
    return {tid:known[tid] for tid in targets}

__release_signature__ = 'Tino Zappala'
