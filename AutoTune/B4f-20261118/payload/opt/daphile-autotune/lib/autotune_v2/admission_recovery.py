"""Bounded negative admission facts; no JSON grant and no generic DENY reset."""
import hashlib,json,os,stat,time
from pathlib import Path
import audio_activity_lease as kernel
from . import common_activity as common

SCHEMA='autotune-writeahead-admission-denial-v4'
ROLES=('automatic-run','native-tune','native-trial')
WAL=('activity-session.json','automatic.json','transaction.json','current.json','last-known-good.json',
     'controlled-workload.json','v2-policy.json','policy-undo-before.json','policy-reapply-before.json',
     'ab-session.json','search-session.json','usb-session.json')


def source():
    return dict(common=common.source_identity(),admission_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                provider_sha256=hashlib.sha256(Path(kernel.__file__).read_bytes()).hexdigest())


def snapshot(root,*,without_automatic=False):
    root=Path(root)
    if not root.exists():
        parent=kernel.directory(str(root.parent));os.close(parent)
        return hashlib.sha256(json.dumps({name:None for name in WAL if not(without_automatic and name=='automatic.json')},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    directory=common.safe_root(root);rows={}
    try:
        for name in WAL:
            if without_automatic and name=='automatic.json':continue
            fd=None
            try:
                fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory)
                s=os.fstat(fd);kernel.attributes(fd)
                kernel.need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022 and
                            s.st_nlink==1 and s.st_size<=4*1024**2,'ADMISSION_PRIVATE_WAL_METADATA')
                raw=os.pread(fd,4*1024**2+1,0)
                kernel.need(len(raw)==s.st_size,'ADMISSION_WAL_READ_BOUND')
                rows[name]=hashlib.sha256(raw).hexdigest()
            except FileNotFoundError:rows[name]=None
            finally:
                if fd is not None:os.close(fd)
    finally:os.close(directory)
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def context(root,role):
    kernel.need(common.clean_state(root),'PREEXISTING_STATE_RECOVERY_REQUIRED_BEFORE_NEW_ADMISSION')
    return dict(schema=SCHEMA,root=str(Path(root)),role=role,source=source(),prestate_sha256=snapshot(root),
                prestate_without_automatic_sha256=snapshot(root,without_automatic=True),preclean=True,
                boot=kernel.owner()['boot'])


def member(root):
    parent=kernel.layout();group=None
    try:
        group,rows=kernel.members(parent)
        if len(rows)!=1:return None
        token,row=next(iter(rows.items()));ctx=row.get('denial_context',{})
        if row['mode']!='EX' or ctx.get('schema')!=SCHEMA:return None
        kernel.need(ctx.get('root')==str(Path(root)) and ctx.get('source')==source() and
                    ctx.get('role')in ROLES and ctx.get('boot')==kernel.owner()['boot'] and
                    row['owner']['boot']==kernel.owner()['boot'] and
                    row['owner']['namespaces']==list(kernel.namespaces()),'EXACT_SOURCE_ADMISSION_MEMBER')
        return token,row,ctx
    finally:
        if group is not None:os.close(group)
        os.close(parent)


def recover_no_work(root):
    """Only unchanged WAL at an audited write-ahead entry can prove no work.

    The dedicated root pending request supplies cleanup authority. The actual
    EX/member/dead-owner plus byte-identical WAL and original full clean prove
    that no new audio, RT claim or policy/control effects were reached. Once
    WAL differs, this path refuses to retire and original recovery is required.
    """
    found=member(root)
    if found is None:return None
    token,row,ctx=found
    unchanged=snapshot(root)==ctx['prestate_sha256']
    preactivity=False
    if not unchanged:
        automatic=common.read_state(root,'automatic.json')
        active_pointer=any((common.read_state(root,name) or {}).get('status')in('pending','rollback-incomplete','committed')
                           for name in ('transaction.json','current.json'))
        if not(ctx['role']=='automatic-run' and ctx.get('preclean')is True and
               not active_pointer and
               snapshot(root,without_automatic=True)==ctx.get('prestate_without_automatic_sha256') and
               automatic and automatic.get('kind')=='automatic-configuration-v2' and
               automatic.get('status')=='running' and automatic.get('phase')=='preflight' and
               automatic.get('settings_changed')is False and automatic.get('owner_pid')==row['owner']['pid'] and
               automatic.get('owner_start')==str(row['owner']['start']) and automatic.get('boot_id')==row['owner']['boot'] and
               not any(k in automatic for k in ('activity_session_id','transaction_id','search','selected_policy','results'))):return None
        import calibration_budget
        kernel.need(calibration_budget.validate(automatic),'ORIGINAL_PREFLIGHT_BUDGET_REQUIRED')
        preactivity=True
    from .policy_custody import dead
    kernel.need(dead(row['owner']['pid'],str(row['owner']['start']),row['owner']['boot']),
                'ADMISSION_ORIGINAL_OWNER_STILL_LIVE')
    parent=kernel.layout();fd=lease=None
    try:
        try:fd=kernel.take(parent,'lease.lock','EX')
        except kernel.Busy:return None
        lease=kernel.Lease.inherit(fd,token,'EX');fd=None
        if preactivity:
            kernel.need(snapshot(root,without_automatic=True)==ctx['prestate_without_automatic_sha256'] and
                        common.read_state(root,'automatic.json')==automatic,'PREFLIGHT_CANCEL_CAS_CHANGED')
            automatic.update(status='cancelled-before-activity-no-effects',phase='cancelled',settings_changed=False,
                restore_available=False,calibration_qualified=False,playback_restored=False,
                finished=time.time(),message='Prova interrotta prima della preparazione audio. Riproduzione attuale conservata.')
            import engine
            engine.atomic(Path(root)/'automatic.json',automatic)
        else:kernel.need(snapshot(root)==ctx['prestate_sha256'],'ADMISSION_NO_WORK_WAL_CHANGED')
        kernel.need(common.clean_state(root),'ADMISSION_NO_WORK_OR_QUIESCENCE_NOT_PROVED')
        private=common.safe_root(root,create=True);os.close(private)
        import engine
        engine.atomic(Path(root)/'manual-common-recovery.json',dict(schema='explicit-no-work-cleanup-v4',
            status='no-work-pre-activity-cancelled' if preactivity else 'no-work-admission-cancelled',original_token=token,owner=kernel.owner(),
            finished_monotonic=time.monotonic(),player_commands=0,policy_controls=0,
            calibration_continuation=False,calibration_qualified=False))
        lease.retire_after_quiescence()
        return dict(status='no-work-pre-activity-cancelled' if preactivity else 'no-work-admission-cancelled',player_commands=0,policy_controls=0,
                    calibration_qualified=False,calibration_continuation=False)
    finally:
        if fd is not None:os.close(fd)
        if lease is not None:lease.close_copy()
        os.close(parent)

__release_signature__='Tino Zappala'
