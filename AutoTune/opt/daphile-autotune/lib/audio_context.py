"""Read-only, expiring ownership contract for optional PCM processors.

The core knows no module names, paths, executables or private APIs. A root
provider may identify live process/thread owners. ALSA must independently show
that exactly one of those threads owns the actual active USB PCM. This grants
observation only, never permission to retune an external processor's threads.
"""
import hashlib,json,math,os,re,stat,time,uuid
from pathlib import Path

ROOT=Path('/run/daphile-audio-context')
FIELDS={'schema','boot_id','issued','expires','provider','owners'}
IDENTITY={'pid','start','cmd'}


def process(pid,proc=Path('/proc')):
    if type(pid) is not int or not 1<pid<2**31:raise ValueError('Bounded process required')
    path=proc/str(pid)
    before=(path/'stat').read_text().rsplit(') ',1)[1].split()
    raw=(path/'cmdline').read_bytes()
    after=(path/'stat').read_text().rsplit(') ',1)[1].split()
    if before[0] in ('Z','X') or after[0] in ('Z','X') or before[19]!=after[19] or not raw:
        raise RuntimeError('Process exited or changed')
    if path.stat().st_uid!=0:raise RuntimeError('Root PCM owner required')
    return dict(pid=pid,start=after[19],cmd=hashlib.sha256(raw).hexdigest())


def validated(record,boot,now,identity,threads):
    """Pure contract validation; tests supply explicit process observations."""
    if not isinstance(record,dict) or set(record)!=FIELDS or type(record['schema']) is not int or record['schema']!=1:
        raise ValueError('Known audio ownership schema required')
    if str(uuid.UUID(record['boot_id']))!=record['boot_id'] or record['boot_id']!=boot:raise ValueError('Different boot')
    if any(type(record[k]) not in (int,float) or not math.isfinite(record[k]) for k in ('issued','expires')):
        raise ValueError('Finite ownership lifetime required')
    if not record['issued']<=now<record['expires']<=record['issued']+30:raise ValueError('Expired audio owner')
    def check(value):
        if not isinstance(value,dict) or set(value)!=IDENTITY or type(value['pid']) is not int or not 1<value['pid']<2**31:
            raise ValueError('Exact process identity required')
        if not isinstance(value['start'],str) or not re.fullmatch('[0-9]+',value['start']):raise ValueError('Process start required')
        if not isinstance(value['cmd'],str) or not re.fullmatch('[a-f0-9]{64}',value['cmd']):raise ValueError('Command fingerprint required')
        if identity(value['pid'])!=value:raise RuntimeError('Owner process changed')
    check(record['provider'])
    owners=record['owners']
    if not isinstance(owners,list) or len(owners)>8:raise ValueError('Bounded owner list required')
    result={};seen=set()
    for item in owners:
        if not isinstance(item,dict) or set(item)!={'process','threads','role','context_sha256'} or item['role']!='pcm-processor':raise ValueError('Read-only PCM role required')
        if not isinstance(item['context_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',item['context_sha256']):raise ValueError('Processor configuration fingerprint required')
        check(item['process']);pid=item['process']['pid']
        if pid in seen:raise ValueError('Duplicate process owner')
        seen.add(pid);tasks=item['threads']
        if not isinstance(tasks,list) or not 1<=len(tasks)<=64:raise ValueError('Bounded thread list required')
        actual=threads(pid)
        for thread in tasks:
            if not isinstance(thread,dict) or set(thread)!={'tid','start'} or type(thread['tid']) is not int or not 1<thread['tid']<2**31:
                raise ValueError('Exact thread identity required')
            if not isinstance(thread['start'],str) or not re.fullmatch('[0-9]+',thread['start']):raise ValueError('Thread start required')
            if thread['tid'] in result or actual.get(thread['tid'])!=thread['start']:raise RuntimeError('Thread exited or changed group')
            result[thread['tid']]=dict(process_pid=pid,process_start=item['process']['start'],thread_start=thread['start'],context_sha256=item['context_sha256'])
    # Close exit/reuse races across the bounded thread reads.
    check(record['provider'])
    for item in owners:check(item['process'])
    return result


def thread_starts(pid,proc=Path('/proc')):
    result={}
    for path in (proc/str(pid)/'task').glob('[0-9]*'):
        fields=(path/'stat').read_text().rsplit(') ',1)[1].split()
        if fields[0] not in ('Z','X'):result[int(path.name)]=fields[19]
    return result


def owners(root=ROOT):
    """An absent/stale/invalid contract grants no additional ownership."""
    try:
        root=Path(root)
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid!=0 or stat.S_IMODE(root.stat().st_mode)&0o022:return {}
        fd=os.open(root/'owners.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:
            meta=os.fstat(stream.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_size>65536:return {}
            raw=stream.read(65537)
        if len(raw)>65536:return {}
        return validated(json.loads(raw),Path('/proc/sys/kernel/random/boot_id').read_text().strip(),time.monotonic(),process,thread_starts)
    except (OSError,RuntimeError,ValueError,KeyError,TypeError,IndexError,AttributeError):return {}


def select(cards,usb):
    permitted=owners();candidates=[]
    for card in cards:
        if not card.get('usb_path'):continue
        for pcm in card['pcms']:
            found=re.search(r'owner_pid\s*:\s*(\d+)',pcm.get('status') or '')
            running=re.search(r'^state:\s*RUNNING\s*$',pcm.get('status') or '',re.M)
            if pcm.get('active') and found and running and int(found[1]) in permitted:
                candidates.append((card,pcm,int(found[1])))
    if len(candidates)!=1:return dict(status='unknown' if not candidates else 'ambiguous',candidates=len(candidates))
    card,pcm,pid=candidates[0]
    matches=[u for u in usb if u['path']==card['usb_path']]
    from usb_target import identity
    if len(matches)!=1 or not identity(matches[0]):return dict(status='unknown',candidates=0)
    return dict(status='verified-active',card=card['card'],alsa_id=card['id'],device=pcm['device'],owner_pid=pid,
        pcm=pcm,usb=matches[0],identity=identity(matches[0]),owner_contract=dict(role='pcm-processor',**permitted[pid]))

__release_signature__ = 'Tino Zappala'
