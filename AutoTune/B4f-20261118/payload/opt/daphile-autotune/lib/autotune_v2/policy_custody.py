"""V2 monitor custody: physical EX writes, read-only idle, authentic cleanup.

The durable parent-authored envelope identifies a dead monitor even if it dies
between its next real EX admission and its per-operation journal. It is a hint
and a denial boundary, never an authority substitute for original policy undo.
"""
import ctypes,errno,fcntl,hashlib,json,os,re,select,signal,stat,subprocess,sys,time,uuid
from pathlib import Path
from . import common_activity as common
import audio_activity_lease as kernel

SCHEMA='autotune-v2-source-bound-policy-monitor-v4'
ARM_FD='DAPHILE_AUTOTUNE_POLICY_ARM_FD'
ARM_NONCE='DAPHILE_AUTOTUNE_POLICY_ARM_NONCE'
ARM_PARENT='DAPHILE_AUTOTUNE_POLICY_ARM_PARENT'
ENVELOPE='v2-policy-monitor.json'
ADMISSION='autotune-v2-policy-denial-context-v4'


def need(ok,why):kernel.need(ok,why)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sources():
    base=Path(__file__).resolve().parent
    result={name:sha((base/name).read_bytes()) for name in ('policy_custody.py','persistence.py','common_activity.py')}
    result['provider_sha256']=sha(Path(kernel.__file__).read_bytes())
    return result


def policy_identity(saved):
    need(saved.get('kind')=='autotune-v2-policy-v1' and isinstance(saved.get('id'),str)
         and re.fullmatch('[a-f0-9]{32}',saved['id']) and isinstance(saved.get('run_id'),str),
         'EXACT_ORIGINAL_V2_POLICY_IDENTITY')
    return dict(policy_id=saved['id'],run_id=saved['run_id'],selection_sha256=sha(canonical(saved['selected'])),
                binding_sha256=sha(canonical(saved['binding'])),origin_sha256=sha(canonical(saved['origin'])))


def argv(root):
    return [sys.executable,'-I','-B',str(Path(__file__).resolve().parents[2]/'bin/dat'),
            '--state',str(root),'policy-restore','serve-v2']


def process(pid):
    fields=kernel.read_bounded('/proc/'+str(pid)+'/stat',8192).rsplit(b') ',1)[1].split()
    status=kernel.read_bounded('/proc/'+str(pid)+'/status',65536)
    uid=re.findall(rb'(?m)^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$',status)
    gid=re.findall(rb'(?m)^Gid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$',status)
    need(uid==gid==[(b'0',)*4],'POLICY_EXACT_ROOT_PROCESS')
    return dict(pid=pid,start=fields[19].decode(),parent=int(fields[1]),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def dead(pid,start,boot):
    """Actual PIDFD exit (including zombies), or genuine birth/boot mismatch."""
    need(type(pid)is int and pid>0 and isinstance(start,str),'POLICY_EXACT_PROCESS_BIRTH')
    if boot!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():return True
    try:watch=os.pidfd_open(pid)
    except ProcessLookupError:return True
    try:
        if select.select([watch],[],[],0)[0]:return True
        try:actual=process(pid)
        except FileNotFoundError:
            need(select.select([watch],[],[],0)[0],'POLICY_PROCESS_OBSERVATION_RACE')
            return True
        return actual['start']!=start
    finally:os.close(watch)


def admission_context(root,api,role):
    """Denial/recovery binding only; original policy remains the undo authority."""
    need(role in ('policy-arm','policy-cycle','policy-undo'),'POLICY_EXACT_ADMISSION_ROLE')
    saved=api.engine.load(Path(root)/'v2-policy.json')
    return dict(schema=ADMISSION,role=role,root=str(Path(root)),sources=sources(),
                policy=policy_identity(saved),host_uuid=Path('/sys/class/dmi/id/product_uuid').read_text().strip(),
                boot_id=api.engine.boot_id())


def start_owned(root,api):
    from . import persistence as policy
    if common.current()is not None:return start(root,api)
    saved=policy.load(root,'v2-policy.json',api)
    if saved.get('kind')!=policy.KIND or saved.get('enabled')is not True:return dict(status='disabled',audio_started=False)
    # Read-only admission checks confer no authority; repeat under physical EX.
    policy.current_journal(root,policy.load(root,'automatic.json',api),api)
    context=admission_context(root,api,'policy-arm')
    return common.run_owned(root,lambda:start(root,api),role='policy-arm',denial_context=context)


def getfd(watch,slot):
    libc=ctypes.CDLL(None,use_errno=True);libc.syscall.restype=ctypes.c_long
    fd=int(libc.syscall(438,watch,slot,0))
    if fd<0:raise OSError(ctypes.get_errno(),'actual pidfd_getfd refused')
    os.set_inheritable(fd,False);return fd


def no_common_fd(pid,watch,anchor):
    paths=list(Path('/proc',str(pid),'fd').iterdir());need(len(paths)<=4096,'POLICY_FD_CENSUS_BOUND')
    for path in paths:
        duplicate=None
        try:
            duplicate=getfd(watch,int(path.name))
            info=os.fstat(duplicate)
            if stat.S_ISREG(info.st_mode):
                image=kernel.image(duplicate)
                need(image[:2]!=anchor[:2] or image[-1]!=anchor[-1],'POLICY_BOOTSTRAP_EX_COPY_NOT_CLOSED')
        except OSError as error:
            if error.errno not in (errno.EBADF,errno.ENOENT,errno.ESRCH):raise
        finally:
            if duplicate is not None:os.close(duplicate)


def envelope(root,api):
    row=common.read_state(root,ENVELOPE)
    if row is None:return None
    need(row.get('schema')==SCHEMA and row.get('root')==str(Path(root)) and row.get('sources')==sources(),
         'POLICY_DURABLE_ENVELOPE_SOURCE')
    saved=api.engine.load(Path(root)/'v2-policy.json')
    need(row['policy']==policy_identity(saved),'POLICY_DURABLE_ENVELOPE_IDENTITY')
    need(row.get('boot_id')==api.engine.boot_id() and type(row.get('pid'))is int and row['pid']>1
         and isinstance(row.get('start'),str),'POLICY_DURABLE_ENVELOPE_BOOT_OWNER')
    return row


def live_monitor(root,api):
    row=common.read_state(root,ENVELOPE)
    if row is None:return None
    if dead(row['pid'],row['start'],row['boot_id']):return None
    row=envelope(root,api)
    watch=os.pidfd_open(row['pid'])
    try:
        actual=process(row['pid'])
        need(actual['start']==row['start'] and actual['boot_id']==row['boot_id'] and
             kernel.namespaces(row['pid'])==kernel.namespaces() and not select.select([watch],[],[],0)[0],
             'POLICY_ACTUAL_LIVE_MONITOR')
        command=kernel.read_bounded('/proc/'+str(row['pid'])+'/cmdline',16384)
        need(command==b'\0'.join(map(os.fsencode,argv(root)))+b'\0'
             and sha(command)==row['argv_sha256'] and
             os.path.realpath('/proc/'+str(row['pid'])+'/exe')==os.path.realpath(sys.executable),
             'POLICY_ACTUAL_MONITOR_IMAGE_AND_ARGV')
        return row
    finally:os.close(watch)


def monitor_lock(root,create=False):
    if create:common.require_exclusive()
    flags=os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC|(os.O_CREAT if create else 0)
    fd=os.open(Path(root)/'v2-policy-monitor.lock',flags,0o600)
    s=os.fstat(fd);kernel.attributes(fd)
    need(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1
         and stat.S_IMODE(s.st_mode)==0o600 and s.st_size==0,'POLICY_PRIVATE_MONITOR_LOCK')
    return fd


def arm_child(root,api):
    """Verify actual original parent/OFD/source, close witnesses BEFORE ACK."""
    from . import persistence as policy
    root=Path(root);witness=kernel.inherited('EX');parentfd=copyfd=lock=None
    ready=int(os.environ.pop(ARM_FD));nonce=os.environ.pop(ARM_NONCE);parent=int(os.environ.pop(ARM_PARENT))
    try:
        need(re.fullmatch('[a-f0-9]{32}',nonce) and parent==os.getppid(),'POLICY_ORIGINAL_ARM_PARENT')
        row=common.read_state(root,'operation-common-custody.json')
        need(row and row['source']==common.source_identity() and row['root']==str(root)
             and row['holder']['pid']==parent and row['token']==witness.token,
             'POLICY_PARENT_SOURCE_OPERATION')
        parentfd=os.pidfd_open(parent);actual=process(parent)
        need(int(actual['start'])==row['holder']['start'] and not select.select([parentfd],[],[],0)[0]
             and row['holder']['boot']==kernel.owner()['boot'] and
             row['holder']['namespaces']==list(kernel.namespaces()),'POLICY_PARENT_KERNEL_IDENTITY')
        command=kernel.read_bounded('/proc/'+str(parent)+'/cmdline',16384)
        need(sha(command)==row['process_argv_sha256'] and
             command.split(b'\0')[-len(row['argv'])-1:]==[*map(os.fsencode,row['argv']),b'']
             and os.path.realpath('/proc/'+str(parent)+'/exe')==os.path.realpath(sys.executable),
             'POLICY_PARENT_ACTUAL_COMMAND')
        source=Path(row['argv'][0]);info=source.lstat()
        need(stat.S_ISREG(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022 and
             sha(source.read_bytes())==row['process_script_sha256'],'POLICY_PARENT_ACTUAL_SOURCE')
        copyfd=getfd(parentfd,row['slot']);kernel.kernel_locked(copyfd,'EX')
        need(kernel.image(copyfd)==kernel.image(witness.fd)==row['lease'],'POLICY_PARENT_SAME_PHYSICAL_EX')
        saved=policy.load(root,'v2-policy.json',api);record=policy.load(root,'automatic.json',api)
        policy.current_journal(root,record,api,allow_running_origin=True)
        lock=monitor_lock(root);fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        snapshot=dict(schema=SCHEMA,nonce=nonce,pid=os.getpid(),start=api.engine.start_ticks(os.getpid()),
                      boot_id=api.engine.boot_id(),policy=policy_identity(saved),sources=sources())
        os.close(copyfd);copyfd=None;witness.close_copy();witness=None
        for key in (kernel.ENV_FD,kernel.ENV_TOKEN):os.environ.pop(key,None)
        need(not select.select([parentfd],[],[],0)[0] and process(parent)==actual,'POLICY_PARENT_EXITED_DURING_ARM')
        raw=canonical(snapshot)+b'\n';need(os.write(ready,raw)==len(raw),'POLICY_READY_SHORT_WRITE')
        os.close(ready);ready=None
        # Parent persists the envelope under its genuine original EX. We do
        # nothing persistent until that source-bound publication exists.
        until=time.monotonic()+10
        while time.monotonic()<until:
            row=common.read_state(root,ENVELOPE)
            if row and row.get('pid')==snapshot['pid'] and row.get('start')==snapshot['start'] and row.get('nonce')==nonce:
                need(row['sources']==sources() and row['policy']==snapshot['policy'],'POLICY_PARENT_ENVELOPE_CHANGED')
                result=lock;lock=None;return result
            need(not select.select([parentfd],[],[],0)[0],'POLICY_PARENT_DIED_BEFORE_ENVELOPE')
            time.sleep(.02)
        raise RuntimeError('POLICY_PARENT_ENVELOPE_NOT_PUBLISHED')
    finally:
        if copyfd is not None:os.close(copyfd)
        if witness is not None:witness.close_copy()
        if ready is not None:os.close(ready)
        if parentfd is not None:os.close(parentfd)
        if lock is not None:os.close(lock)


def start(root,api):
    from . import persistence as policy
    kernel.root();root=Path(root);lease=common.require_exclusive()
    saved=policy.load(root,'v2-policy.json',api)
    if saved.get('kind')!=policy.KIND or saved.get('enabled')is not True:return dict(status='disabled',audio_started=False)
    policy.current_journal(root,policy.load(root,'automatic.json',api),api,allow_running_origin=True)
    previous=common.read_state(root,ENVELOPE)
    if previous is not None:
        need(type(previous.get('pid'))is int and isinstance(previous.get('start'),str)
             and isinstance(previous.get('boot_id'),str),'POLICY_PREVIOUS_MONITOR_OWNER_UNKNOWN')
        alive=not dead(previous['pid'],previous['start'],previous['boot_id'])
        if alive:
            need(live_monitor(root,api)is not None,'POLICY_FOREIGN_LIVE_MONITOR')
            return dict(status='already-running',audio_started=False)
        # A new parent genuinely admitted from C may replace dead historical
        # metadata. A borrower of that old orphan family may NOT overwrite it.
        member=json.loads(kernel._LIVE[lease]['member'])['owner']
        need(not(member['pid']==previous['pid'] and str(member['start'])==previous['start']
                 and member['boot']==previous['boot_id']),'POLICY_OLD_ORPHAN_ENVELOPE_MUST_REMAIN')
    lock=monitor_lock(root,create=True);os.close(lock)
    read,write=os.pipe2(os.O_CLOEXEC);nonce=uuid.uuid4().hex;child=watch=None
    env=lease.child_environment();env.update({ARM_FD:str(write),ARM_NONCE:nonce,ARM_PARENT:str(os.getpid())})
    try:
        with (root/'v2-policy-monitor.log').open('ab') as log:
            child=subprocess.Popen(argv(root),stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                start_new_session=True,close_fds=True,pass_fds=(lease.fd,write),env=env)
        os.close(write);write=None;watch=os.pidfd_open(child.pid)
        need(select.select([read,watch],[],[],10)[0] and not select.select([watch],[],[],0)[0],
             'POLICY_READONLY_ARM_FAILED')
        raw=os.read(read,16385);need(0<len(raw)<=16384 and raw.endswith(b'\n'),'POLICY_ARM_FRAME')
        ack=json.loads(raw);need(ack==dict(schema=SCHEMA,nonce=nonce,pid=child.pid,start=api.engine.start_ticks(child.pid),
             boot_id=api.engine.boot_id(),policy=policy_identity(saved),sources=sources()),'POLICY_ARM_ACTUAL_READY_IDENTITY')
        actual=process(child.pid);need(actual['start']==ack['start'] and actual['parent']==os.getpid(),
                                     'POLICY_ARM_ACTUAL_CHILD_LINEAGE')
        no_common_fd(child.pid,watch,kernel.image(lease.fd))
        command=kernel.read_bounded('/proc/'+str(child.pid)+'/cmdline',16384)
        need(command==b'\0'.join(map(os.fsencode,argv(root)))+b'\0' and not select.select([watch],[],[],0)[0],
             'POLICY_ARM_SOURCE_CHILD_STILL_LIVE')
        row=dict(ack,root=str(root),argv_sha256=sha(command),parent_operation_token=lease.token,
                 host_uuid=Path('/sys/class/dmi/id/product_uuid').read_text().strip())
        with api.engine.locked(root):api.engine.atomic(root/ENVELOPE,row)
        return dict(status='armed-readonly-v2-policy-monitor',pid=child.pid,audio_started=False,
                    bootstrap_common_copies_closed=True,idle_common_lease=False)
    except BaseException:
        if child is not None and child.poll()is None:
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        raise
    finally:
        os.close(read)
        if write is not None:os.close(write)
        if watch is not None:os.close(watch)


def drain(root,api):
    """Existing root stop authority, PIDFD-only; no metadata/kernel writes."""
    kernel.root();root=Path(root);row=live_monitor(root,api)
    if row is None:return dict(status='inactive',metadata_written=False)
    watch=os.pidfd_open(row['pid'])
    try:
        need(process(row['pid'])['start']==row['start'],'POLICY_DRAIN_BIRTH_CHANGED')
        # If called during original calibration error cleanup, the caller's
        # EX excludes any new monitor cycle; the bootstrap witness was closed.
        if common.current()is not None:no_common_fd(row['pid'],watch,kernel.image(common.require_exclusive().fd))
        signal.pidfd_send_signal(watch,signal.SIGTERM)
        need(select.select([watch],[],[],210)[0],'POLICY_MONITOR_DRAIN_INCOMPLETE')
        return dict(status='drained',metadata_written=False,real_pidfd_exit=True)
    finally:os.close(watch)


def cycle(root,api,state):
    """Original current/reapply decisions; every effect is inside actual EX."""
    from . import persistence as policy
    common.require_exclusive();root=Path(root)
    if not state['running']:return
    row=envelope(root,api)
    need(row and row['pid']==os.getpid() and row['start']==api.engine.start_ticks(os.getpid()),
         'POLICY_CYCLE_ORIGINAL_MONITOR_ENVELOPE')
    try:
        saved=policy.load(root,'v2-policy.json',api);record=policy.load(root,'automatic.json',api)
        journal=policy.current_journal(root,record,api)
        h=api.scan();report=dict(status='waiting-for-compatible-pcm',audio_started=False,playlist_write_commands=0)
        if h['dac'].get('status')=='verified-active':
            target=policy.compatible(root,saved,h,api)
            owner=(api.engine.boot_id(),h['dac']['owner_pid'],api.engine.start_ticks(h['dac']['owner_pid']))
            state['seen']=state['seen']+1 if state['pending']==owner else 1;state['pending']=owner
            departed=journal['boot_id']!=api.engine.boot_id() or any(c.get('tid') and api.engine.start_ticks(c['tid'])!=c.get('start') for c in journal['changes'])
            if not departed:
                for change in journal['changes']:
                    api.engine.guard(change,h)
                    need(api.engine.setting_value(change)==change['after'],'POLICY_MANUAL_RUNTIME_VALUE_PRESERVED')
                if state['claim']is None:state['claim']=api.coordinate(h,target,'verified-policy')
                state['claim'].renew(h);report['status']='policy-current'
            elif policy.activity(root,saved['activity_id'],api).get('status')!='completed':
                report['status']='awaiting-original-acceptance'
            elif not policy.pcm_observable(h,target,api):
                if state['claim']:state['claim'].close();state['claim']=None
                report['status']='waiting-for-playing-pcm'
            elif state['seen']>=3 and owner not in state['attempted']:
                if state['claim']:state['claim'].close();state['claim']=None
                state['attempted'].add(owner);report.update(policy.reapply(root,saved,record,h,api))
                if report['status']=='already-current-observed':state['running']=False
            else:report['status']='awaiting-fresh-identity-verification'
        elif state['claim']:state['claim'].close();state['claim']=None
        api.engine.atomic(root/'v2-policy-status.json',dict(report,time=time.time(),boot_id=api.engine.boot_id()))
    except (OSError,RuntimeError,ValueError,KeyError,TypeError) as error:
        if state['claim']:state['claim'].close();state['claim']=None
        api.engine.atomic(root/'v2-policy-status.json',dict(status='invalidated-no-retry',
            reason=type(error).__name__+': '+str(error),audio_started=False,playlist_write_commands=0,time=time.time()))
        state['running']=False
    finally:
        # SIGTERM only changes in-memory running. Cleanup writes are legal
        # here because the same real cycle EX is still held. Idle exit below
        # never tries to acquire EX against a stopping/restoring parent.
        if not state['running'] and state['claim']:
            state['claim'].close();state['claim']=None


def serve(root,api):
    kernel.root();root=Path(root)
    state=dict(running=True,claim=None,attempted=set(),pending=None,seen=0)
    def end(*_):state['running']=False
    signal.signal(signal.SIGTERM,end);signal.signal(signal.SIGINT,end)
    lock=arm_child(root,api)
    try:
        while state['running']:
            # This preliminary read grants nothing. Invalid/disabled policy
            # exits read-only; existing claim becomes invalid on actual death.
            from . import persistence as policy
            saved=policy.load(root,'v2-policy.json',api);record=policy.load(root,'automatic.json',api)
            if saved.get('enabled')is not True or record.get('status')!='configured':break
            if not state['running']:break
            # Signal-atomic transition into an admitted cycle. A signal while
            # RO preloading exits without any operation journal. Once blocked,
            # admission is an active cycle (bounded readonly proof + custody);
            # delivery resumes before the first policy/control/claim write.
            mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGTERM,signal.SIGINT})
            try:
                if not state['running']:break
                def admitted():
                    signal.pthread_sigmask(signal.SIG_SETMASK,mask)
                    return cycle(root,api,state)
                common.run_owned(root,admitted,role='policy-cycle',
                                 denial_context=admission_context(root,api,'policy-cycle'))
            except (kernel.Busy,kernel.Refused):
                # SH, orphan D or a busy calibration: no claim/status/policy
                # write. A prior verified claim expires in<=60s, not renewed.
                pass
            finally:signal.pthread_sigmask(signal.SIG_SETMASK,mask)
            for _ in range(5):
                if not state['running']:break
                time.sleep(1)
    finally:
        # Never remove durable envelope. It is required for real orphan
        # cleanup if death happens after acquire but before operation journal.
        # An idle process has no EX and therefore performs NO cleanup writes.
        os.close(lock)


def _claim_dead(root,api,row):
    """Sufficient semantic invalidity by actual PID/start/boot, not JSON flags."""
    common.require_exclusive()
    path=Path('/run/daphile-audio-policy/autotune-rt.json')
    if not path.exists() and not path.is_symlink():return True
    need(not path.is_symlink(),'POLICY_INDIRECT_RT_CLAIM')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        s=os.fstat(fd);kernel.attributes(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and
             stat.S_IMODE(s.st_mode)==0o600 and s.st_size<=16384,'POLICY_EXACT_RT_CLAIM_METADATA')
        raw=os.pread(fd,16385,0);claim=json.loads(raw)
        from native_coordination import FIELDS
        need(set(claim)==FIELDS and claim['schema']==1,'POLICY_RT_CLAIM_SCHEMA')
        terminated=dead(claim['pid'],claim['start'],claim['boot_id'])
        expired=(type(claim['issued'])in(int,float) and type(claim['expires'])in(int,float) and
                 claim['issued']<=claim['expires']<=claim['issued']+60 and claim['expires']<=time.monotonic())
        need(terminated or expired,'POLICY_LIVE_RT_CLAIM_REMAINS')
        if terminated and claim['pid']==row['pid'] and claim['start']==row['start'] and claim['boot_id']==row['boot_id']:
            # Native Perl checks start ticks, so an unreaped zombie could
            # otherwise still defer RT until expiry. Remove only this exact
            # dead family claim under the original native mutex + real EX.
            parent=kernel.directory(str(path.parent));mutex=None
            try:
                mutex=os.open('lock',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                meta=os.fstat(mutex);kernel.attributes(mutex)
                need(stat.S_ISREG(meta.st_mode) and meta.st_uid==0 and not meta.st_mode&0o022 and
                     meta.st_nlink==1 and meta.st_size==0,'POLICY_EXACT_NATIVE_RT_MUTEX')
                fcntl.flock(mutex,fcntl.LOCK_EX|fcntl.LOCK_NB)
                actual=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
                need((actual.st_dev,actual.st_ino,actual.st_mode,actual.st_uid,actual.st_gid,actual.st_nlink,actual.st_size)==
                     (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size) and
                     os.pread(fd,16385,0)==raw,'POLICY_DEAD_RT_CLAIM_CAS_CHANGED')
                need(dead(row['pid'],row['start'],row['boot_id']),'POLICY_RT_OWNER_NOT_DEAD')
                os.unlink(path.name,dir_fd=parent);os.fsync(parent)
            finally:
                if mutex is not None:os.close(mutex)
                os.close(parent)
        else:
            # A foreign record is never deleted. Its actual old boot/birth
            # mismatch or elapsed native deadline must independently deny it.
            need(expired or claim['boot_id']!=api.engine.boot_id() or
                 api.engine.start_ticks(claim['pid'])!=claim['start'],'POLICY_FOREIGN_ZOMBIE_CLAIM_NOT_EXPIRED')
        return True
    finally:os.close(fd)


def recovery_candidate(root):
    """Only detect a possible source-bound monitor family; never a grant."""
    parent=kernel.layout();group=None
    try:
        group,rows=kernel.members(parent)
        if len(rows)!=1:return False
        member=next(iter(rows.values()));context=member.get('denial_context',{})
        return (member['mode']=='EX' and context.get('schema')==ADMISSION and
                context.get('root')==str(Path(root)) and context.get('sources')==sources())
    finally:
        if group is not None:os.close(group)
        os.close(parent)


def reapply_owned(root,saved,record,hardware,api):
    """Seal the real accepted preimage before the unchanged reapply algorithm."""
    from . import persistence as policy
    lease=common.require_exclusive();root=Path(root)
    with api.engine.locked(root):
        journal=policy.current_journal(root,record,api)
        need(policy.load(root,'v2-policy.json',api)==saved,'POLICY_REAPPLY_PREIMAGE_CHANGED')
        member=json.loads(kernel._LIVE[lease]['member'])
        api.engine.atomic(root/'policy-reapply-before.json',dict(schema='autotune-owned-reapply-preimage-v4',
            root=str(root),sources=sources(),boot_id=api.engine.boot_id(),token=lease.token,member=member,
            member_sha256=sha(kernel._LIVE[lease]['member']),policy=policy_identity(saved),
            saved=saved,record=record,journal=journal))
    return policy._policy_reapply_without_common(root,saved,record,hardware,api)


def failed_reapply_journal(root,record,api,*,expected_token=None):
    """Read-only original undo proof, never a fabricated committed journal.

    A completed trial rollback leaves transaction.json rolled-back and restores
    the captured previous_profile in current.json. The old three-file equality
    no longer exists. A pre-write source-bound snapshot plus the REAL owned
    replacement/rollback receipts proves only undo of that original policy.
    This exception is unavailable to qualification, monitor renewal or reapply.
    """
    from . import persistence as policy
    root=Path(root);snapshot=common.read_state(root,'policy-reapply-before.json')
    need(snapshot and snapshot.get('schema')=='autotune-owned-reapply-preimage-v4' and
         snapshot.get('root')==str(root) and snapshot.get('sources')==sources() and
         snapshot.get('boot_id')==api.engine.boot_id(),'POLICY_EXACT_REAPPLY_PREIMAGE_REQUIRED')
    if expected_token is not None:need(snapshot['token']==expected_token,'POLICY_SAME_ORPHAN_REAPPLY_TOKEN')
    member=snapshot['member'];owner=member['owner'];context=member.get('denial_context',{})
    need(member['mode']=='EX' and member['token']==snapshot['token'] and
         sha(canonical(member)+b'\n')==snapshot['member_sha256'] and
         context.get('schema')==ADMISSION and context.get('role')=='policy-cycle' and
         context.get('root')==str(root) and context.get('sources')==sources() and
         context.get('policy')==snapshot['policy'] and context.get('boot_id')==api.engine.boot_id() and
         owner['boot']==kernel.owner()['boot'] and owner['namespaces']==list(kernel.namespaces()),
         'POLICY_REAPPLY_SOURCE_MEMBER_SEAL')
    need(dead(owner['pid'],str(owner['start']),api.engine.boot_id()),'POLICY_REAPPLY_WRITER_STILL_LIVE')
    saved=policy.load(root,'v2-policy.json',api)
    need(policy_identity(saved)==snapshot['policy']==policy_identity(snapshot['saved']) and
         record==snapshot['record'] and record.get('status')=='configured',
         'POLICY_REAPPLY_ORIGINAL_SELECTION_CHANGED')
    old=snapshot['journal'];trial=policy.load(root,'transaction.json',api)
    need(trial.get('id')!=old['id'] and trial.get('previous_profile')==old and
         trial.get('profile')in('automatic-policy-reapply','automatic') and
         trial.get('boot_id')==api.engine.boot_id() and trial.get('parent_pid')==owner['pid'] and
         trial.get('parent_start')==str(owner['start']) and
         trial.get('status')in('pending','rollback-incomplete','rolled-back','committed'),
         'POLICY_EXACT_OWNED_REPLACEMENT_REQUIRED')
    activity=policy.load(root,'activity-session.json',api)
    need(activity.get('kind')=='policy-revalidation-v2' and activity.get('owner')==owner['pid'] and
         activity.get('start')==str(owner['start']) and activity.get('boot_id')==api.engine.boot_id() and
         activity.get('restore_playback')is False and activity.get('id')!=snapshot['saved']['activity_id'] and
         trial.get('session_owner')==dict(kind='activity',id=activity.get('id')),
         'POLICY_EXACT_REAPPLY_ACTIVITY_REQUIRED')
    recovery=activity.get('recovery_process')
    if recovery:need(dead(recovery['pid'],recovery['start'],api.engine.boot_id()),'POLICY_REAPPLY_RECOVERY_STILL_LIVE')
    current=policy.load(root,'current.json',api);last=policy.load(root,'last-known-good.json',api)
    for candidate in (current,last):
        need(candidate==old or (candidate.get('id')==trial['id'] and
             candidate.get('parent_pid')==owner['pid'] and candidate.get('parent_start')==str(owner['start']) and
             candidate.get('boot_id')==api.engine.boot_id() and candidate.get('previous_profile')==old and
             candidate.get('session_owner')==trial['session_owner']),
             'POLICY_FOREIGN_REPLACEMENT_HISTORY')
    # These are the unchanged original identity/owner/activity checks, applied
    # to an actual pre-write snapshot for UNDO only. No JSON is relabeled.
    return policy.validate_owned_policy_journal(root,record,api,snapshot['saved'],old,for_restore=True)


def undo_failed_reapply(root,record,api,journal,*,expected_token=None):
    """Original two-level rollback, with real control readback at both levels."""
    common.require_exclusive();root=Path(root)
    old=failed_reapply_journal(root,record,api,expected_token=expected_token)
    need(old==journal,'POLICY_UNDO_RECEIPT_CHANGED')
    need(old.get('boot_id')==api.engine.boot_id(),'POLICY_HISTORICAL_CONTROLS_MUST_NOT_BE_REPLAYED')
    trial=api.engine.load(root/'transaction.json')
    hardware=api.scan()
    if trial['status']!='rolled-back':
        result=api.engine.rollback(root,trial,hardware)
        need(result['status']=='rolled-back','POLICY_REAPPLY_ROLLBACK_INCOMPLETE')
    else:need(trial.get('parameter_restoration_complete')is True and not trial.get('rollback_errors'),
              'POLICY_REAPPLY_ROLLBACK_UNPROVED')
    need(api.engine.load(root/'current.json')==old,'POLICY_REAPPLY_PREVIOUS_PROFILE_NOT_RESTORED')
    # Original rollback accepts only before/after/owned intermediate values and
    # performs its mandatory second readback of every surviving original control.
    result=api.engine.rollback(root,old,hardware)
    need(result['status']=='rolled-back','POLICY_ORIGINAL_POLICY_ROLLBACK_INCOMPLETE')
    activity=common.read_state(root,'activity-session.json')
    need(activity.get('status')in('completed','recovered'),'POLICY_ORIGINAL_ACTIVITY_RECOVERY_REQUIRED')
    return result


def prepare_failed_reapply_undo(root,api):
    """Original activity recovery BEFORE workflow takes its native state lock."""
    common.require_exclusive();root=Path(root)
    rows=[common.read_state(root,name) for name in ('transaction.json','current.json','last-known-good.json')]
    saved=common.read_state(root,'v2-policy.json')
    if rows[0] and all(row==rows[0] for row in rows) and rows[0].get('status')=='committed' and \
       rows[0].get('profile')=='automatic' and saved and saved.get('transaction_id')==rows[0].get('id'):return
    snapshot=common.read_state(root,'policy-reapply-before.json')
    if snapshot is None:return
    record=common.read_state(root,'automatic.json')
    failed_reapply_journal(root,record,api)
    activity=common.read_state(root,'activity-session.json')
    if activity.get('status')not in('completed','recovered'):
        import session
        session.recover(root,activity)
    need(common.read_state(root,'activity-session.json').get('status')in('completed','recovered'),
         'POLICY_ORIGINAL_REAPPLY_ACTIVITY_NOT_RECOVERED')


def retire_drained_claim(root,api):
    common.require_exclusive()
    row=common.read_state(root,ENVELOPE)
    if row is not None:
        need(dead(row['pid'],row['start'],row['boot_id']),'POLICY_DRAINED_MONITOR_NOT_DEAD')
        _claim_dead(root,api,row)


def undo_pending(root):
    row=common.read_state(root,'policy-undo-before.json')
    return row is not None and row.get('status')!='restored-verified'


def prepare_undo(root,api):
    from . import persistence as policy
    root=Path(root);lease=common.require_exclusive()
    record=policy.load(root,'automatic.json',api)
    if record.get('status')=='retained-current':return
    journal=policy.current_journal(root,record,api,for_restore=True)
    need(journal.get('boot_id')==api.engine.boot_id() and
         record.get('binding',{}).get('host_uuid')==api.scan()['system']['product_uuid'],
         'POLICY_CURRENT_BOOT_HOST_UNDO_ONLY')
    saved=policy.load(root,'v2-policy.json',api)
    if saved.get('transaction_id')!=journal['id']:
        saved=common.read_state(root,'policy-reapply-before.json')['saved']
    prior=common.read_state(root,'policy-undo-before.json')
    need(prior is None or prior.get('status')=='restored-verified','POLICY_PREVIOUS_UNDO_NOT_CLOSED')
    with api.engine.locked(root):
        api.engine.atomic(root/'policy-undo-before.json',dict(schema='autotune-owned-policy-undo-v4',
            status='prepared',root=str(root),sources=sources(),token=lease.token,
            member_sha256=sha(kernel._LIVE[lease]['member']),member=json.loads(kernel._LIVE[lease]['member']),
            policy=policy_identity(saved),record=record,saved=saved,journal=journal,boot_id=api.engine.boot_id()))


def undo_receipt(root,api):
    from . import persistence as policy
    root=Path(root);lease=common.require_exclusive();row=common.read_state(root,'policy-undo-before.json')
    need(row and row.get('schema')=='autotune-owned-policy-undo-v4' and row.get('status')in('prepared','restored-verified') and
         row.get('root')==str(root) and row.get('sources')==sources() and row.get('token')==lease.token and
         row.get('member_sha256')==sha(kernel._LIVE[lease]['member']) and
         row.get('member')==json.loads(kernel._LIVE[lease]['member']) and row.get('boot_id')==api.engine.boot_id(),
         'POLICY_EXACT_DURABLE_UNDO_REQUIRED')
    saved=policy.load(root,'v2-policy.json',api);record=policy.load(root,'automatic.json',api)
    need(policy_identity(saved)==row['policy']==policy_identity(row['saved']),'POLICY_UNDO_SELECTION_CHANGED')
    mutable={'status','settings_changed','restore_available','finished','message'}
    need({k:v for k,v in record.items() if k not in mutable}==
         {k:v for k,v in row['record'].items() if k not in mutable} and
         (record==row['record'] or (record.get('status')=='restored' and record.get('settings_changed')is False and
                                  record.get('restore_available')is False)), 'POLICY_UNDO_AUTOMATIC_REPLACED')
    policy.validate_owned_policy_journal(root,row['record'],api,row['saved'],row['journal'],for_restore=True)
    need(row['journal'].get('boot_id')==api.engine.boot_id() and
         row['record'].get('binding',{}).get('host_uuid')==api.scan()['system']['product_uuid'],
         'POLICY_UNDO_PREIMAGE_CURRENT_BOOT_HOST')
    return row


def finish_undo(root,api):
    root=Path(root);row=undo_receipt(root,api);tx=common.read_state(root,'transaction.json')
    need(tx and tx.get('id')==row['journal']['id'] and tx.get('status')=='rolled-back' and
         tx.get('parameter_restoration_complete')is True and not tx.get('rollback_errors'),
         'POLICY_ORIGINAL_UNDO_NOT_VERIFIED')
    need(same_owned_journal(tx,row['journal']),'POLICY_UNDO_IMMUTABLE_JOURNAL_CHANGED')
    need(common.read_state(root,'current.json')is None,'POLICY_UNDONE_CURRENT_POINTER_NOT_DETACHED')
    hardware=api.scan()
    for change in tx['changes']:
        if change.get('tid') and api.engine.start_ticks(change['tid'])!=change.get('start'):continue
        api.engine.guard(change,hardware,restoring=True)
        need(api.engine.setting_value(change)==change['before'],'POLICY_UNDO_FINAL_CONTROL_READBACK')
    saved=common.read_state(root,'v2-policy.json');record=common.read_state(root,'automatic.json')
    need(saved.get('enabled')is False and record.get('status')=='restored','POLICY_UNDO_TERMINAL_NOT_COMPLETE')
    row.update(status='restored-verified',finished_monotonic=time.monotonic(),
               actual_transaction_sha256=sha(canonical(tx)),calibration_continuation=False)
    api.engine.atomic(root/'policy-undo-before.json',row)


def resume_undo(root,api):
    """Finish only this operation's real original undo, never reset a marker."""
    from . import persistence as policy,workflow
    root=Path(root);row=undo_receipt(root,api)
    if common.read_state(root,'transaction.json').get('id')!=row['journal']['id']:
        prepare_failed_reapply_undo(root,api)
    with api.owner_lock(root),api.engine.locked(root):
        tx=common.read_state(root,'transaction.json')
        if tx.get('id')!=row['journal']['id']:
            undo_failed_reapply(root,row['record'],api,row['journal'],expected_token=row['token'])
        else:
            need(tx.get('status')in('committed','pending','rollback-incomplete','rolled-back'),
                 'POLICY_UNDO_TRANSACTION_TERMINAL_UNKNOWN')
            # Exact original transaction receipt; rollback/readback is idempotent.
            need(same_owned_journal(tx,row['journal']),'POLICY_UNDO_ORIGINAL_IMMUTABLE_JOURNAL_CHANGED')
            check_undo_pointer(root,row)
            result=api.engine.rollback(root,tx,api.scan())
            need(result['status']=='rolled-back','POLICY_UNDO_RECOVERY_INCOMPLETE')
        saved=common.read_state(root,'v2-policy.json');saved['enabled']=False
        api.engine.atomic(root/'v2-policy.json',saved)
        record=common.read_state(root,'automatic.json')
        record.update(status='restored',settings_changed=False,restore_available=False,finished=time.time(),
            message='Impostazioni precedenti ripristinate. La playlist attuale non è stata modificata.')
        api.engine.atomic(root/'automatic.json',record)
        detach_undone_pointer(root,api,row)
        finish_undo(root,api)
        return workflow.public(record)


def same_undo_changes(current,original):
    if not isinstance(current,list) or len(current)!=len(original):return False
    from detection import numbers
    for actual,before in zip(current,original):
        ignore={'rollback_irq_hint_attempted','rollback_irq_write_verified','accepted_intermediate'}
        if {k:v for k,v in actual.items() if k not in ignore}!={k:v for k,v in before.items() if k not in ignore}:return False
        allowed=list(before.get('accepted_intermediate',[]))
        if before.get('parameter')=='usb_irq_affinity' and before.get('effective_before'):
            hint=','.join(str(n) for n in sorted(set(numbers(before['effective_before']))))
            if hint not in allowed:allowed.append(hint)
        if any(v not in allowed for v in actual.get('accepted_intermediate',[])):return False
        if any(v not in actual.get('accepted_intermediate',[]) for v in before.get('accepted_intermediate',[])):return False
        if any(k in actual and actual[k]is not True for k in ('rollback_irq_hint_attempted','rollback_irq_write_verified')):return False
    return True


def same_owned_journal(current,original):
    if not isinstance(current,dict) or current.get('status')not in('committed','rollback-incomplete','rolled-back'):return False
    mutable={'status','parameter_restoration_complete','rollback_errors','changes'}
    if {k:v for k,v in current.items() if k not in mutable}!={k:v for k,v in original.items() if k not in mutable}:return False
    if 'parameter_restoration_complete'in current and type(current['parameter_restoration_complete'])is not bool:return False
    if 'rollback_errors'in current and not isinstance(current['rollback_errors'],list):return False
    return same_undo_changes(current.get('changes'),original.get('changes',[]))


def original_policy_pointer(row):
    """The canonical initial V2 transaction starts with no active predecessor."""
    current=row['journal'];origin=row['saved']['origin'].get('transaction_id',current['id'])
    seen=set()
    for _ in range(128):
        need(isinstance(current,dict) and current.get('id')not in seen and current.get('profile')=='automatic' and
             current.get('status')=='committed' and current.get('selected_policy')==row['saved']['selected'],
             'POLICY_EXACT_CANONICAL_HISTORY_REQUIRED')
        seen.add(current['id'])
        if current['id']==origin:
            need(current.get('previous_profile')is None,'POLICY_INITIAL_PREDECESSOR_NOT_EMPTY')
            return None
        link=current.get('workflow_v2',{});previous=current.get('previous_profile')
        need(link.get('protocol')=='policy-revalidation-v1' and link.get('policy_id')==row['saved']['id'] and
             isinstance(previous,dict) and previous.get('id')==link.get('previous_transaction_id'),
             'POLICY_REVALIDATION_HISTORY_LINK_CHANGED')
        current=previous
    raise RuntimeError('POLICY_HISTORY_BOUND')


def check_undo_pointer(root,row):
    original_policy_pointer(row)
    actual=common.read_state(root,'current.json')
    need(actual in (None,row['journal'],row['journal'].get('previous_profile')),'POLICY_FOREIGN_CURRENT_POINTER_PRESERVED')


def detach_undone_pointer(root,api,row=None):
    """Controls already restored; detach only their captured historical pointer.

    Full accepted history remains in this durable receipt/last-known-good. A
    successful reapply's previous_profile is historical after its merged
    ORIGINAL before-values have been restored; it must not remain active.
    """
    common.require_exclusive();root=Path(root);row=row or undo_receipt(root,api)
    tx=common.read_state(root,'transaction.json')
    need(same_owned_journal(tx,row['journal']) and tx.get('status')=='rolled-back' and
         tx.get('parameter_restoration_complete')is True and not tx.get('rollback_errors'),
         'POLICY_POINTER_DETACH_REQUIRES_ACTUAL_UNDO')
    check_undo_pointer(root,row)
    actual=common.read_state(root,'current.json')
    if actual is not None:
        need(actual==row['journal'].get('previous_profile'),'POLICY_ORIGINAL_ROLLBACK_POINTER_NOT_READY')
        directory=common.safe_root(root)
        try:os.unlink('current.json',dir_fd=directory);os.fsync(directory)
        finally:os.close(directory)


def recover_monitor(root):
    """Canonical root pending cleanup for one exact dead monitor EX family."""
    from . import persistence as policy,workflow
    import engine
    kernel.root();root=Path(root);api=policy.native_api()
    activity=common.read_state(root,'activity-session.json')
    if activity and activity.get('recovery_process'):
        r=activity['recovery_process'];need(dead(r['pid'],r['start'],engine.boot_id()),'POLICY_ORIGINAL_RECOVERY_STILL_LIVE')
    parent=kernel.layout();group=None;descriptor=None;lease=None;borrowed=[]
    try:
        with kernel.admission(parent):
            group,rows=kernel.members(parent)
            need(len(rows)==1 and kernel.deny(parent)==b'D\n','POLICY_ONE_ORIGINAL_ORPHAN_REQUIRED')
            token,member=next(iter(rows.items()))
            context=member.get('denial_context',{});owner=member['owner']
            need(member['mode']=='EX' and context.get('schema')==ADMISSION and
                 context.get('role')in('policy-arm','policy-cycle','policy-undo') and context.get('root')==str(root) and
                 context.get('sources')==sources() and context.get('boot_id')==engine.boot_id() and
                 context.get('host_uuid')==Path('/sys/class/dmi/id/product_uuid').read_text().strip() and
                 owner['boot']==kernel.owner()['boot'] and owner['namespaces']==list(kernel.namespaces()),
                 'POLICY_EXACT_SOURCE_HOST_MEMBER')
            row=dict(pid=owner['pid'],start=str(owner['start']),boot_id=context['boot_id'],policy=context['policy'])
            need(dead(row['pid'],row['start'],row['boot_id']),'POLICY_ORIGINAL_MONITOR_STILL_LIVE')
        os.close(group);group=None
        try:descriptor=kernel.take(parent,'lease.lock','EX')
        except kernel.Busy:
            for leaf in ('activity-common-custody.json','transaction-common-custody.json'):
                try:fd,_=common.custodian_fd(root,leaf,passive=True,expected_token=token)
                except common.CustodianGone:continue
                borrowed.append(fd)
            need(borrowed,'POLICY_NO_PASSIVE_AUTHENTIC_CUSTODIAN')
            descriptor=borrowed.pop()
        lease=kernel.Lease.inherit(descriptor,token,'EX');descriptor=None
        value=dict(lease=lease,root=root,guards=[],role='manual-policy-monitor-recovery')
        scope=common._CURRENT.set(value)
        try:
            started=time.monotonic()
            engine.atomic(root/'manual-common-recovery.json',dict(schema='explicit-monitor-cleanup-v4',status='running',
                maximum_seconds=300,original_monitor=dict(pid=row['pid'],start=row['start'],boot_id=row['boot_id']),
                original_token=token,owner=kernel.owner(),started_monotonic=started,
                calibration_continuation=False,calibration_budget_changed=False))
            with common.manual_recovery_deadline():
                # Pending original trial/activity first, preserving its own
                # authority. This can restore a pre-reapply committed policy.
                pending=common.read_state(root,'transaction.json')
                if pending and pending.get('status')in('pending','rollback-incomplete'):
                    engine._core_restore_without_common('pending',root)
                current=common.read_state(root,'automatic.json')
                saved=common.read_state(root,'v2-policy.json')
                need(policy_identity(saved)==row['policy'],'POLICY_ORPHAN_FOREIGN_REPLACEMENT')
                if undo_pending(root):
                    resume_undo(root,api)
                elif current.get('status')=='configured':
                    # Original completed-policy authority, controls and full
                    # current-journal/readback are mandatory, not a D reset.
                    workflow.restore(root)
                elif current.get('status')!='restored':
                    raise RuntimeError('POLICY_ORPHAN_OWNED_UNDO_NOT_AVAILABLE')
                tx=common.read_state(root,'transaction.json')
                need(tx and tx.get('status')=='rolled-back','POLICY_ORIGINAL_UNDO_NOT_ROLLED_BACK')
                hardware=api.scan()
                for change in tx.get('changes',[]):
                    if change.get('tid') and engine.start_ticks(change['tid'])!=change.get('start'):continue
                    engine.guard(change,hardware,restoring=True)
                    need(engine.setting_value(change)==change['before'],'POLICY_ORIGINAL_CONTROL_READBACK')
                need(policy.load(root,'v2-policy.json',api).get('enabled')is False,'POLICY_CONTINUATION_NOT_DISABLED')
                _claim_dead(root,api,row)
                need(common.clean_state(root) and common.wait_guards(value),'POLICY_CLEANUP_NOT_QUIESCENT')
            engine.atomic(root/'manual-common-recovery.json',dict(schema='explicit-monitor-cleanup-v4',
                status='original-policy-restored-monitor-quiescent',maximum_seconds=300,original_token=token,
                started_monotonic=started,finished_monotonic=time.monotonic(),
                calibration_continuation=False,calibration_budget_changed=False))
            lease.retire_after_quiescence()
            return dict(status='original-policy-restored-monitor-quiescent',policy_controls_restored=True,
                        playback_commands=0,calibration_qualified=False,calibration_continuation=False)
        finally:
            lease.close_copy();common._CURRENT.reset(scope)
    finally:
        if group is not None:os.close(group)
        if descriptor is not None:os.close(descriptor)
        for fd in borrowed:os.close(fd)
        os.close(parent)


__release_signature__ = 'Tino Zappala'
