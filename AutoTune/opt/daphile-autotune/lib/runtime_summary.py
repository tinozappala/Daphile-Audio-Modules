"""Small read-only view of an existing discovery snapshot; no new probes/SET."""
import datetime,re

SCHEDULERS={0:'Normale',1:'FIFO',2:'Round Robin',3:'Batch',5:'Idle',6:'Deadline'}


def cpus(value):
    if isinstance(value,list):
        return sorted(set(value)) if all(type(n) is int and 0<=n<4096 for n in value) else None
    if not isinstance(value,str) or not re.fullmatch(r'[0-9]+(?:-[0-9]+)?(?:,[0-9]+(?:-[0-9]+)?)*',value):return None
    result=[]
    for part in value.split(','):
        ends=list(map(int,part.split('-')))
        if ends[0]>ends[-1] or ends[-1]>=4096:return None
        result.extend(range(ends[0],ends[-1]+1))
    return sorted(set(result))


def positive(value):
    if type(value) is int and value>0:return value
    if isinstance(value,str) and re.fullmatch('[1-9][0-9]*',value):return int(value)
    return None


def diagnostic_hold(path='/mnt/hdd/daphile-autotune/usb-port-diagnostic-hold.json'):
    """Read only a bounded, root-owned diagnostic marker; never infer OFF on error."""
    import json,os,stat
    if not hasattr(os,'O_NOFOLLOW'):return None
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError:return False
    except OSError:return None
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or stat.S_IMODE(info.st_mode)!=0o600 or info.st_size>4096:return None
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(4097)
        if len(raw)>4096:return None
        r=json.loads(raw)
        return r.get('enabled') if type(r) is dict and r.get('schema')==1 and type(r.get('enabled')) is bool else None
    except (OSError,ValueError):return None
    finally:os.close(fd)


def current_summary(hardware):
    result=summary(hardware)
    result['usb_diagnostic_hold']=diagnostic_hold()
    return result


def summary(hardware):
    cpu=hardware.get('cpu') or {};dac=hardware.get('dac') or {};usb=dac.get('usb') or {}
    online=cpus(cpu.get('online'));isolated=cpus(cpu.get('isolated'))
    if cpu.get('isolated')=='':isolated=[]
    cores=[]
    for c in cpu.get('cores') or []:
        if type(c.get('cpu')) is not int or online is not None and c['cpu'] not in online:continue
        f=c.get('cpufreq') or {}
        cores.append(dict(cpu=c['cpu'],governor=f.get('scaling_governor'),
            current_khz=positive(f.get('scaling_cur_freq')),minimum_khz=positive(f.get('scaling_min_freq')),maximum_khz=positive(f.get('scaling_max_freq'))))
    number=positive(usb.get('irq'));irqs=[]
    if number:
        for row in hardware.get('irq') or []:
            if positive(row.get('irq'))!=number:continue
            counts=row.get('counts');counts=counts if isinstance(counts,list) and all(type(n) is int and n>=0 for n in counts) else None
            # Frozen discovery does not retain /proc/interrupts column labels.
            # Only attribute its columns when all online CPUs are contiguous;
            # otherwise keep counters unavailable rather than invent CPU IDs.
            if counts is not None and online!=list(range(len(counts))):counts=None
            irqs.append(dict(number=number,description=row.get('description'),allowed_cpus=cpus(row.get('affinity')),
                effective_cpus=cpus(row.get('effective')),counts_since_boot=counts,
                count_scope='Cumulative per-CPU counters for this interrupt since boot; not events per second'))
    threads=[];seen=set();owner=dac.get('owner_pid')
    for process in hardware.get('squeezelite') or []:
        for row in process.get('threads',[]):
            tid=row.get('tid');name=row.get('name') or ''
            is_irq=number is not None and re.match(r'^irq/'+str(number)+r'-',name) is not None
            if tid!=owner and not is_irq:continue
            if type(tid) is not int or tid<=0 or tid in seen:continue
            seen.add(tid);policy=row.get('policy');priority=row.get('priority')
            threads.append(dict(role='usb-irq' if is_irq else 'audio-output',tid=tid,name=name,
                scheduler=SCHEDULERS.get(policy,'Codice '+str(policy)) if type(policy) is int else None,
                policy=policy if type(policy) is int else None,priority=priority if type(priority) is int and priority>=0 else None,
                allowed_cpus=cpus(row.get('affinity'))))
    return dict(kind='runtime-summary',read_only=True,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        cpu=dict(online=online,isolated=isolated,physical_cores=cpu.get('physical_cores'),cores=cores),
        dac=dict(product=(dac.get('identity') or {}).get('product') or usb.get('product'),controller=usb.get('controller')),
        interrupts=irqs,threads=sorted(threads,key=lambda t:(t['role']!='audio-output',t['tid'])),
        values_are_snapshot=True,cpu_usage_measured=False,interrupt_rate_measured=False)

__release_signature__ = 'Tino Zappala'
