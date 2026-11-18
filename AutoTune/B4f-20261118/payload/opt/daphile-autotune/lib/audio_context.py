"""Read-only physical PCM observations for optional processors.

The legacy expiring provider observation remains readable. When it is absent,
kernel PCM ownership, process membership, device FDs and bounded configuration
reads can establish the same read-only observation without another producer.
Neither route grants permission to retune an external processor's threads.
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


def _bounded_entries(path,limit):
    result=[]
    for item in Path(path).iterdir():
        result.append(item)
        if len(result)>limit:raise RuntimeError('Observation directory exceeds bound')
    return result


def _file_observation(path,limit):
    """Read one small root-owned regular file, checking replacement races."""
    path=Path(path)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=0 or before.st_mode&0o022
                or not 0<=before.st_size<=limit):
            raise RuntimeError('Bounded root-owned configuration required')
        raw=stream.read(limit+1);after=os.fstat(stream.fileno())
    def same(value):
        return (value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
    if len(raw)>limit or same(before)!=same(after) or same(before)!=same(path.stat()):
        raise RuntimeError('Configuration changed while observed')
    return raw


def configuration_context(arguments):
    """Hash configuration arguments and their bounded regular JSON siblings.

    Live filter editors can replace saved profiles without changing the engine
    startup configuration. Read the small JSON state beside that configuration
    too; do not inspect logs, caches, descendants or executable contents.
    """
    if len(arguments)>128:raise RuntimeError('Processor argument count exceeds bound')
    configs=[]
    for argument in arguments[1:]:
        path=Path(argument)
        if path.is_absolute() and path.suffix.lower() in ('.json','.yaml','.yml','.toml','.conf'):
            configs.append(path)
    if not 1<=len(configs)<=8:raise RuntimeError('Bounded processor configuration argument required')
    parents={path.parent for path in configs}
    entries={parent:_bounded_entries(parent,64) for parent in parents}
    selected=set(configs)
    for siblings in entries.values():
        selected.update(path for path in siblings if path.suffix.lower()=='.json')
    if len(selected)>32:raise RuntimeError('Processor configuration count exceeds bound')
    digest=hashlib.sha256();total=0
    for path in sorted(selected,key=str):
        raw=_file_observation(path,2*1024*1024);total+=len(raw)
        if total>4*1024*1024:raise RuntimeError('Processor configuration size exceeds bound')
        name=os.fsencode(path)
        digest.update(len(name).to_bytes(4,'big'));digest.update(name)
        digest.update(len(raw).to_bytes(8,'big'));digest.update(raw)
    for parent,before in entries.items():
        # Atomic profile creation/deletion is a context change too.
        old={path.name for path in before if path.suffix.lower()=='.json'}
        new={path.name for path in _bounded_entries(parent,64) if path.suffix.lower()=='.json'}
        if old!=new:raise RuntimeError('Processor configuration set changed')
    return digest.hexdigest()


def _root_tgid(tid,proc):
    status=(proc/str(tid)/'status').read_text()
    tgid=re.findall(r'^Tgid:\s*(\d+)\s*$',status,re.M)
    uid=re.findall(r'^Uid:\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',status,re.M)
    if len(tgid)!=1 or uid!=[('0','0','0','0')]:raise RuntimeError('Root PCM process credentials required')
    return int(tgid[0])


def _member_start(tgid,tid,proc):
    fields=(proc/str(tgid)/'task'/str(tid)/'stat').read_text().rsplit(') ',1)[1].split()
    if fields[0] in ('Z','X') or not fields[19].isdigit():raise RuntimeError('Live PCM thread required')
    return fields[19]


def _character_device(path):
    value=Path(path).stat()
    if not stat.S_ISCHR(value.st_mode) or value.st_rdev<=0:raise RuntimeError('PCM character device required')
    return value.st_rdev


def kernel_owner(card,pcm,tid,proc=Path('/proc'),snd=Path('/dev/snd'),usb=()):
    """Observe one actual external physical PCM owner; never authorize a write."""
    from physical_audio import identity,identified
    proc=Path(proc);snd=Path(snd)
    coordinates=(card.get('card'),pcm.get('device'),pcm.get('subdevice'))
    if any(type(value) is not int or value<0 for value in coordinates):raise RuntimeError('Exact PCM coordinates required')
    if not identified(identity(card,usb)) or type(tid) is not int or tid<=1:raise RuntimeError('Physical ALSA PCM owner required')
    number,device,subdevice=coordinates
    endpoint=proc/'asound'/('card'+str(number))/('pcm'+str(device)+'p')/('sub'+str(subdevice))
    def running():
        status=(endpoint/'status').read_text()
        owner=re.findall(r'^owner_pid\s*:\s*(\d+)\s*$',status,re.M)
        if owner!=[str(tid)] or not re.search(r'^state:\s*RUNNING\s*$',status,re.M):
            raise RuntimeError('Physical PCM is not running under this owner')
        params=dict(re.findall(r'^([\w]+):\s*(.+)$',(endpoint/'hw_params').read_text(),re.M))
        if params!=pcm.get('hw'):raise RuntimeError('Physical PCM parameters changed')
    running()
    tgid=_root_tgid(tid,proc);before=process(tgid,proc);start=_member_start(tgid,tid,proc)
    rdev=_character_device(snd/('pcmC'+str(number)+'D'+str(device)+'p'))
    matched=None
    for fd in _bounded_entries(proc/str(tgid)/'fd',256):
        try:
            if fd.name.isdigit() and _character_device(fd)==rdev:matched=fd;break
        except (OSError,RuntimeError):continue
    if matched is None:raise RuntimeError('PCM process does not hold the physical device')
    raw=(proc/str(tgid)/'cmdline').read_bytes()
    if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=before['cmd']:
        raise RuntimeError('Processor command changed or exceeds bound')
    context=configuration_context([os.fsdecode(argument) for argument in raw.split(b'\0') if argument])
    if (_root_tgid(tid,proc)!=tgid or _member_start(tgid,tid,proc)!=start or process(tgid,proc)!=before):
        raise RuntimeError('Physical PCM owner changed while observed')
    if (_character_device(matched)!=rdev
            or _character_device(snd/('pcmC'+str(number)+'D'+str(device)+'p'))!=rdev):
        raise RuntimeError('Physical PCM device FD changed while observed')
    running()
    return dict(role='pcm-processor',process_pid=tgid,process_start=before['start'],thread_start=start,
        context_sha256=context,read_only=True,observation='kernel-physical-pcm')


def select(cards,usb):
    from physical_audio import identity,identified
    permitted=owners();candidates=[]
    for card in cards:
        ident=identity(card,usb)
        if not identified(ident):continue
        for pcm in card['pcms']:
            found=re.search(r'owner_pid\s*:\s*(\d+)',pcm.get('status') or '')
            running=re.search(r'^state:\s*RUNNING\s*$',pcm.get('status') or '',re.M)
            if pcm.get('active') and found and running:
                tid=int(found[1]);observation=permitted.get(tid)
                if observation is not None:observation=dict(role='pcm-processor',**observation)
                else:
                    try:observation=kernel_owner(card,pcm,tid,usb=usb)
                    except (OSError,RuntimeError,ValueError,KeyError,TypeError,IndexError,AttributeError):continue
                candidates.append((card,pcm,tid,observation,ident))
    if len(candidates)!=1:return dict(status='unknown' if not candidates else 'ambiguous',candidates=len(candidates))
    card,pcm,pid,observation,ident=candidates[0]
    matches=[u for u in usb if card.get('usb_path') and u['path']==card['usb_path']]
    device=matches[0] if len(matches)==1 else None
    return dict(status='verified-active',card=card['card'],alsa_id=card['id'],device=pcm['device'],owner_pid=pid,
        pcm=pcm,usb=device,identity=ident,owner_contract=observation,transport='usb' if device else 'alsa-physical')

__release_signature__ = 'Tino Zappala'
