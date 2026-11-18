"""Absolute, independently supervised V2 recovery; no audio replay entry point.

The activity inherits the original deadline. A recovery subprocess is admitted
only after its PID/start/boot/namespace claim is durable; it has the same hard
deadline and an additional supervisor. Neither restart creates more time.
"""
import contextlib
import copy
import fcntl
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

if __name__=='__main__' and not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    __package__='autotune_v2'
try:import calibration_budget as budget_api
except ImportError:from .. import calibration_budget as budget_api

IDENTITY=('id','kind','owner','start','boot_id','parent_session_owner','playback','budget','owner_pid_namespace')


def recovered(value):
    return (isinstance(value,dict) and value.get('status')=='recovered'
        and isinstance(value.get('playback_recovery'),dict) and value['playback_recovery'].get('restored') is True)


def terminal(value):
    from .external_intent import activity_terminal
    return isinstance(value,dict) and (value.get('status')=='completed' or recovered(value) or activity_terminal(value))


def same(first,second):
    return all(first.get(key)==second.get(key) for key in IDENTITY)


def budget_fields(value):
    return {key:copy.deepcopy(value[key]) for key in budget_api.KEYS if key in value}


def bind_activity_deadline(root,activity,engine,seconds):
    """Called under the existing native lock, before publishing any activity."""
    automatic=engine.load(Path(root)/'automatic.json')
    if (not isinstance(automatic,dict) or automatic.get('kind')!='automatic-configuration-v2'
            or type(activity.get('owner')) is not int or activity['owner']<=1
            or not isinstance(activity.get('start'),str) or not activity['start']
            or activity.get('parent_session_owner')!={'kind':'automatic','id':automatic.get('id')}
            or automatic.get('owner_pid')!=activity.get('owner')
            or automatic.get('owner_start')!=activity.get('start')
            or automatic.get('boot_id')!=activity.get('boot_id')):
        raise RuntimeError('Activity budget owner differs from the automatic run')
    timing=budget_fields(automatic)
    if not budget_api.validate(timing):raise RuntimeError('A new V2 activity requires its original monotonic budget')
    if activity.get('boot_id')!=timing['budget_boot_id']:raise RuntimeError('Activity and time budget belong to different boots')
    if not budget_api.finite(seconds) or not 0<seconds<=budget_api.MAX_SECONDS:
        raise ValueError('Bounded activity time required')
    deadline=budget_api.Deadline(timing)
    if deadline.remaining()<300:raise budget_api.BudgetExpired('Setup consumed the time needed for safe calibration')
    activity.update(budget=timing,deadline=timing['calibration_deadline'],
                    owner_pid_namespace=budget_api.pid_namespace())


def current(root,ident,engine,expected=None):
    value=engine.load(Path(root)/'activity-session.json')
    if not isinstance(value,dict):raise RuntimeError('Explicit activity identity required')
    if value.get('id')!=ident:return None
    if expected is not None and not same(value,expected):raise RuntimeError('Activity ownership or absolute budget changed')
    return value


def qualified(root,ident,engine):
    value=current(root,ident,engine)
    if value is None:return None,None
    if value.get('kind')!='automatic-v2':raise RuntimeError('Only a native V2 activity may use this guard')
    automatic=engine.load(Path(root)/'automatic.json')
    if (not isinstance(automatic,dict) or type(value.get('owner')) is not int or value['owner']<=1
            or not isinstance(value.get('start'),str) or not value['start']
            or value.get('parent_session_owner')!={'kind':'automatic','id':automatic.get('id')}
            or automatic.get('kind')!='automatic-configuration-v2'
            or value.get('owner')!=automatic.get('owner_pid') or value.get('start')!=automatic.get('owner_start')
            or value.get('boot_id')!=automatic.get('boot_id') or automatic.get('activity_session_id') not in (None,ident)
            or value.get('budget')!=budget_fields(automatic)):
        raise RuntimeError('Independent activity and automatic deadline identities differ')
    if not budget_api.validate(value.get('budget')):raise RuntimeError('Unanchored historical activity needs explicit recovery')
    if value.get('boot_id')!=value['budget']['budget_boot_id']:raise RuntimeError('Activity and budget boot identities differ')
    if value.get('owner_pid_namespace')!=budget_api.pid_namespace():raise RuntimeError('Owner PID namespace differs')
    if value.get('deadline')!=value['budget']['calibration_deadline']:raise RuntimeError('Activity deadline differs from its original budget')
    return value,budget_api.Deadline(value['budget'])


def cancellation_deferred(root,activity):
    from .external_intent import defer_original_recovery
    return defer_original_recovery(root,activity)


def mark_required(root,expected,engine,reason,expired=False):
    """Durable metadata only; never invent a completed restore after a timeout."""
    with engine.locked(root):
        latest=current(root,expected['id'],engine)
        if latest is None or not same(latest,expected) or terminal(latest) or cancellation_deferred(root,latest):return
        latest.update(status='recovery-required',recovery_error=reason,deadline_recovery_complete=False,
                      last_attempt=time.time(),deadline_expired=bool(expired))
        engine.atomic(Path(root)/'activity-session.json',latest)
        automatic=engine.load(Path(root)/'automatic.json')
        if latest.get('parent_session_owner')=={'kind':'automatic','id':automatic.get('id')}:
            automatic.update(status='timed-out-recovery-required' if expired else 'recovery-required',phase='recovery',
                recovery_available=True,restore_available=False,recovery_errors=[reason],
                message='Prova interrotta. Il recupero completo non è verificato; nessuna nuova prova viene avviata.')
            engine.atomic(Path(root)/'automatic.json',automatic)


def process_alive(owner,engine):
    return (isinstance(owner,dict) and type(owner.get('pid')) is int and owner['pid']>1
            and owner.get('boot_id')==engine.boot_id() and owner.get('namespace')==budget_api.pid_namespace()
            and bool(owner.get('start')) and engine.start_ticks(owner['pid'])==owner['start'])


def signal_process(owner,sig,engine):
    if not process_alive(owner,engine):return False
    import process_guard
    return process_guard.signal_record(owner,sig)


def worker_identity(activity):
    return dict(pid=activity.get('owner'),start=activity.get('start'),boot_id=activity.get('boot_id'),
                namespace=activity.get('owner_pid_namespace'))


def pause(seconds):
    # The process can be preempted between a loop condition and this call.
    # A negative sleep must never skip the following owned SIGKILL path.
    time.sleep(max(0,seconds))


def stop_worker(activity,engine,deadline):
    owner=worker_identity(activity)
    if not process_alive(owner,engine):return True
    if deadline.remaining(True)>0:
        signal_process(owner,signal.SIGTERM,engine)
        end=min(time.monotonic()+3,deadline.mono_end)
        while process_alive(owner,engine) and time.monotonic()<end and deadline.remaining(True)>0:
            pause(min(.05,end-time.monotonic(),deadline.remaining(True)))
    if process_alive(owner,engine):signal_process(owner,signal.SIGKILL,engine)
    # Never restore concurrently with a still alive, uninterruptible worker.
    end=min(time.monotonic()+1,deadline.mono_end)
    while process_alive(owner,engine) and time.monotonic()<end and deadline.remaining(True)>0:
        pause(min(.02,end-time.monotonic(),deadline.remaining(True)))
    return not process_alive(owner,engine)


def contain_worker(root,activity,engine,deadline,reason):
    """Contain the original proven PID even after losing its state pointer."""
    owner=worker_identity(activity);forced=False
    if process_alive(owner,engine):forced=signal_process(owner,signal.SIGKILL,engine)
    value=dict(protocol='absolute-worker-supervision-v1',activity_id=activity['id'],
        parent_session_owner=copy.deepcopy(activity['parent_session_owner']),budget=copy.deepcopy(activity['budget']),
        process=owner,termination_requested=bool(forced),process_terminated=not process_alive(owner,engine),
        activity_completion_observed=activity.get('status')=='completed',restoration_attempted=False,
        deadline_expired=deadline.remaining(True)<=0,reason=reason,
        observed_at=time.time(),observed_monotonic=time.monotonic())
    value['status']='forced-termination' if forced and value['process_terminated'] else 'termination-unconfirmed' if forced else 'already-exited'
    engine.atomic(Path(root)/('activity-worker-supervision-'+activity['id']+'.json'),value)
    try:
        with engine.locked(root):
            latest=current(root,activity['id'],engine)
            if latest is None or not same(latest,activity) or cancellation_deferred(root,latest):return value
            latest['worker_supervision']=copy.deepcopy(value)
            engine.atomic(Path(root)/'activity-session.json',latest)
            automatic=engine.load(Path(root)/'automatic.json')
            if (isinstance(automatic,dict) and latest.get('parent_session_owner')=={'kind':'automatic','id':automatic.get('id')}
                    and budget_fields(automatic)==activity['budget']
                    and automatic.get('owner_pid')==owner['pid'] and automatic.get('owner_start')==owner['start']
                    and automatic.get('boot_id')==owner['boot_id']):
                automatic['worker_supervision']=copy.deepcopy(value)
                engine.atomic(Path(root)/'automatic.json',automatic)
        if activity.get('status')!='completed':
            mark_required(root,activity,engine,reason,expired=deadline.remaining(True)<=0)
    except (ValueError,RuntimeError,OSError):pass
    return value


def completion_released(root,activity,engine):
    value=engine.load(Path(root)/'automatic.json')
    if (not isinstance(value,dict) or activity.get('parent_session_owner')!={'kind':'automatic','id':value.get('id')}
            or value.get('kind')!='automatic-configuration-v2' or budget_fields(value)!=activity['budget']
            or value.get('owner_pid')!=activity['owner'] or value.get('owner_start')!=activity['start']
            or value.get('boot_id')!=activity['boot_id']):
        raise RuntimeError('Completed worker no longer owns its original automatic deadline')
    mono=value.get('calibration_finished_monotonic');wall=value.get('finished')
    return (value.get('deadline_guard_released') is True and value.get('completion_timing_persisted') is True
        and value.get('status') in ('configured','retained-current','failed-restored','inconclusive-restored')
        and budget_api.finite(mono) and activity['budget']['calibration_monotonic_started']<=mono<=activity['budget']['calibration_monotonic_deadline']
        and budget_api.finite(wall) and wall<=activity['budget']['calibration_deadline'])


def supervise_completed_worker(root,activity,engine,deadline):
    """Completed controls are never undone for a late metadata/worker exit."""
    try:
        while process_alive(worker_identity(activity),engine):
            if deadline.remaining(True)<=0:break
            latest=current(root,activity['id'],engine,activity)
            if latest is None or latest.get('status')!='completed':raise RuntimeError('Completed activity pointer changed')
            if completion_released(root,activity,engine):
                # The record can have been prepared before the deadline but
                # become visible late, or reading it can consume the remainder.
                if deadline.remaining(True)>0:return
                break
            if deadline.remaining(True)<=0:break
            pause(min(.05,deadline.remaining(True)))
        if not process_alive(worker_identity(activity),engine):return
        reason='Completed activity worker exceeded the original deadline before its final release'
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        reason='Completed activity supervision interrupted: '+str(error)
    contain_worker(root,activity,engine,deadline,reason)


def recovery_owner(activity):
    previous=activity.get('recovery_process')
    if previous is not None:
        if (not isinstance(previous,dict) or type(previous.get('pid')) is not int or previous['pid']<=1
                or not isinstance(previous.get('start'),str) or not previous['start']
                or previous.get('boot_id')!=activity['boot_id']
                or previous.get('namespace')!=activity['owner_pid_namespace']
                or not isinstance(previous.get('nonce'),str) or len(previous['nonce'])!=32
                or any(c not in '0123456789abcdef' for c in previous['nonce'])):
            raise RuntimeError('Existing recovery process ownership is not qualified')
    return previous


def start_recovery(root,activity,engine,deadline):
    from autotune_v2 import common_activity as common_kernel_activity
    common_kernel_activity.require_exclusive()
    latest=current(root,activity['id'],engine,activity)
    if latest is None:raise RuntimeError('A newer activity owns the state')
    previous=recovery_owner(latest)
    if previous and process_alive(previous,engine):return previous,None
    if deadline.remaining(True)<1:raise budget_api.HardDeadline('No time remains to admit a recovery worker')
    nonce=uuid.uuid4().hex
    child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),str(root),activity['id'],nonce],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        start_new_session=True,close_fds=True,**common_kernel_activity.child_contract())
    common_kernel_activity.register_guard(child)
    owner=dict(pid=child.pid,start=engine.start_ticks(child.pid),boot_id=engine.boot_id(),
               namespace=budget_api.pid_namespace(),nonce=nonce)
    try:
        if not process_alive(owner,engine):raise RuntimeError('Recovery process identity could not be observed')
        with engine.locked(root):
            latest=current(root,activity['id'],engine,activity)
            if latest is None or terminal(latest):raise RuntimeError('Recovery ownership already completed or changed')
            latest.update(status='recovering',recovery_process=owner)
            engine.atomic(Path(root)/'activity-session.json',latest)
        return owner,child
    except BaseException:
        signal_process(owner,signal.SIGKILL,engine)
        with contextlib.suppress(subprocess.TimeoutExpired):child.wait(timeout=0)
        raise


def publish_supervision(root,activity,engine,owner,deadline,verified,forced,terminated,reason):
    """A scoped receipt survives a replaced journal and separates two proofs."""
    value=dict(protocol='absolute-recovery-supervision-v1',activity_id=activity['id'],
        parent_session_owner=copy.deepcopy(activity['parent_session_owner']),budget=copy.deepcopy(activity['budget']),
        recovery_process=copy.deepcopy(owner),recovery_verified=bool(verified),termination_requested=bool(forced),
        process_terminated=bool(terminated),deadline_expired=deadline.remaining(True)<=0,
        observed_at=time.time(),observed_monotonic=time.monotonic(),reason=reason,
        status='forced-termination' if forced and terminated else
               'termination-unconfirmed' if forced else 'exited' if terminated else 'supervision-incomplete')
    # This filename belongs to the original activity, never to a new pointer.
    engine.atomic(Path(root)/('activity-supervision-'+activity['id']+'.json'),value)
    try:
        with engine.locked(root):
            latest=current(root,activity['id'],engine)
            if latest is None or not same(latest,activity) or cancellation_deferred(root,latest):return value
            latest['process_supervision']=copy.deepcopy(value)
            engine.atomic(Path(root)/'activity-session.json',latest)
            automatic=engine.load(Path(root)/'automatic.json')
            if (not isinstance(automatic,dict) or budget_fields(automatic)!=activity['budget']
                    or latest.get('parent_session_owner')!={'kind':'automatic','id':automatic.get('id')}
                    or automatic.get('kind')!='automatic-configuration-v2'
                    or automatic.get('owner_pid')!=activity.get('owner')
                    or automatic.get('owner_start')!=activity.get('start') or automatic.get('boot_id')!=activity.get('boot_id')):
                return value
            automatic['recovery_supervision']=copy.deepcopy(value)
            if verified and terminated:
                automatic.update(status='failed-restored',phase='complete',settings_changed=False,
                    restore_available=False,recovery_available=False,finished=time.time(),
                    calibration_finished_monotonic=time.monotonic(),
                    message='Sessione interrotta; recupero completo verificato. L’esito della supervisione del processo è registrato separatamente.')
            else:
                automatic.update(status='timed-out-recovery-required' if value['deadline_expired'] else 'recovery-required',
                    phase='recovery',restore_available=False,recovery_available=True,
                    message='Il recupero o la terminazione del processo richiede ancora verifica; nessuna nuova prova viene avviata.')
            engine.atomic(Path(root)/'automatic.json',automatic)
    except (ValueError,RuntimeError,OSError):
        # A newer, unreadable, or locked pointer must remain untouched. The
        # activity-specific receipt above is still the durable observation.
        pass
    return value


def supervise_recovery(root,activity,engine,deadline,owner,child=None):
    ident=activity['id'];verified=recovered(activity);forced=False;reason='Recovery process completed'
    try:
        while process_alive(owner,engine) and deadline.remaining(True)>0:
            latest=current(root,ident,engine,activity)
            if latest is None:
                reason='Activity pointer changed; original owned child contained';break
            verified=verified or recovered(latest)
            if verified and child is not None:
                with contextlib.suppress(subprocess.TimeoutExpired):child.wait(timeout=min(.05,deadline.remaining(True)))
            if process_alive(owner,engine):pause(min(.05,deadline.remaining(True)))
        latest=current(root,ident,engine,activity)
        if latest is not None:verified=verified or recovered(latest)
        if deadline.remaining(True)<=0:reason='Original absolute deadline reached'
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        reason='Independent recovery supervision interrupted: '+str(error)
    finally:
        # Once Popen/adoption granted exact process ownership, a malformed or
        # replaced journal cannot make the supervisor abandon that child.
        if process_alive(owner,engine):forced=signal_process(owner,signal.SIGKILL,engine)
        if child is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):child.wait(timeout=0)
        terminated=not process_alive(owner,engine)
        publish_supervision(root,activity,engine,owner,deadline,verified,forced,terminated,reason)
    if not verified or not terminated:
        try:mark_required(root,activity,engine,reason,expired=deadline.remaining(True)<=0)
        except (ValueError,RuntimeError,OSError):pass


def _watch(root,ident,session_api):
    root=Path(root);engine=session_api.engine
    initial=current(root,ident,engine)
    if initial is None or cancellation_deferred(root,initial):return
    try:
        activity,deadline=qualified(root,ident,engine)
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        mark_required(root,initial,engine,'Absolute recovery guard refused: '+str(error));return
    if activity.get('status')=='completed':
        return supervise_completed_worker(root,activity,engine,deadline)
    # A child may have published its recovery proof immediately before a
    # supervisor crash. That proof does not grant the child extra lifetime.
    if terminal(activity):
        previous=recovery_owner(activity)
        if previous and process_alive(previous,engine):
            return supervise_recovery(root,activity,engine,deadline,previous)
        return
    engine.atomic(root/('activity-ready-'+ident+'.json'),{'id':ident,'watchdog':os.getpid()})
    try:
        while process_alive(worker_identity(activity),engine) and deadline.remaining()>0:
            latest=current(root,ident,engine,activity)
            if latest is None:raise RuntimeError('Activity pointer changed during the owned operation')
            if cancellation_deferred(root,latest):return
            if latest.get('status')=='completed':return supervise_completed_worker(root,latest,engine,deadline)
            from .external_intent import activity_terminal
            if activity_terminal(latest):return
            if recovered(latest):raise RuntimeError('Recovery proof appeared while the original worker was still active')
            if latest.get('status')=='recovery-required':break
            if latest.get('status') not in ('running','recovering'):raise RuntimeError('Unexpected active worker state')
            pause(min(.2,deadline.remaining()))
        latest=current(root,ident,engine,activity)
        if latest is None:raise RuntimeError('Activity pointer changed before worker termination')
        if cancellation_deferred(root,latest):return
        if latest.get('status')=='completed':return supervise_completed_worker(root,latest,engine,deadline)
        from .external_intent import activity_terminal
        if activity_terminal(latest):return
        if recovered(latest):
            previous=recovery_owner(latest)
            if previous and process_alive(previous,engine):return supervise_recovery(root,latest,engine,deadline,previous)
            return
        if not stop_worker(activity,engine,deadline):
            mark_required(root,activity,engine,'Owned worker did not stop; concurrent recovery refused',expired=deadline.remaining(True)<=0);return
        latest=current(root,ident,engine,activity)
        if latest is None:raise RuntimeError('Activity pointer changed after worker termination')
        if cancellation_deferred(root,latest):return
        if latest.get('status')=='completed':return supervise_completed_worker(root,latest,engine,deadline)
        from .external_intent import activity_terminal
        if activity_terminal(latest):return
        if recovered(latest):return
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        contain_worker(root,activity,engine,deadline,'Operational supervision interrupted: '+str(error));return
    if deadline.remaining(True)<=0:
        mark_required(root,activity,engine,'Absolute session deadline reached before recovery completion',expired=True);return
    try:
        owner,child=start_recovery(root,activity,engine,deadline)
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        mark_required(root,activity,engine,'Independent recovery refused: '+str(error),expired=deadline.remaining(True)<=0);return
    return supervise_recovery(root,activity,engine,deadline,owner,child)


def watch(root,ident,session_api):
    """A restarted guardian may adopt its child; a second live guardian may not."""
    root=Path(root);engine=session_api.engine
    if not isinstance(ident,str) or not 1<=len(ident)<=64 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in ident):
        raise ValueError('Bounded native activity identifier required')
    with engine.locked(root):
        descriptor=os.open(root/'activity-budget-guard.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        details=os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_uid!=os.geteuid() or details.st_mode&0o077 or details.st_nlink!=1:
            os.close(descriptor);raise RuntimeError('Private single-owner budget guard lock required')
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return
        current_lock=(root/'activity-budget-guard.lock').lstat();held=os.fstat(descriptor)
        if (not stat.S_ISREG(current_lock.st_mode) or not stat.S_ISREG(held.st_mode)
                or (current_lock.st_dev,current_lock.st_ino)!=(held.st_dev,held.st_ino)
                or held.st_nlink!=1 or held.st_uid!=os.geteuid() or held.st_mode&0o077):
            raise RuntimeError('Budget guard lock pathname changed during acquisition')
        initial=current(root,ident,engine)
        if initial is None:return
        try:return _watch(root,ident,session_api)
        except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
            mark_required(root,initial,engine,'Independent deadline supervision incomplete: '+str(error))
    finally:os.close(descriptor)


def recovery_worker(root,ident,nonce,session_api):
    from autotune_v2 import common_activity as common_kernel_activity
    common_kernel_activity.require_exclusive()
    root=Path(root);engine=session_api.engine
    activity,deadline=qualified(root,ident,engine)
    if activity is None:return 1
    deadline.hard=True
    # Popen alone grants no restoration authority. Wait briefly for the exact
    # PID claim to become durable, then verify it again before each retry.
    end=min(time.monotonic()+2,deadline.mono_end)
    expected=dict(pid=os.getpid(),start=engine.start_ticks(os.getpid()),boot_id=engine.boot_id(),
                  namespace=budget_api.pid_namespace(),nonce=nonce)
    try:
        with deadline:
            while time.monotonic()<end and deadline.remaining(True)>0:
                latest=current(root,ident,engine,activity)
                if latest is None:return 1
                if latest.get('recovery_process')==expected:break
                pause(min(.02,deadline.remaining(True)))
            else:raise RuntimeError('Recovery process has no durable owner claim')
            if process_alive(worker_identity(activity),engine):raise RuntimeError('Original worker still owns the controls')
            while deadline.remaining(True)>0:
                latest=current(root,ident,engine,activity)
                if latest is None or latest.get('recovery_process')!=expected:
                    raise RuntimeError('Recovery process ownership changed')
                try:
                    session_api.recover(root,activity)
                    latest=current(root,ident,engine,activity)
                    if recovered(latest):return 0
                    raise RuntimeError('Full recovery did not produce its durable completion proof')
                except BlockingIOError:pause(min(.05,deadline.remaining(True)))
            raise budget_api.HardDeadline('Independent recovery deadline reached')
    except (ValueError,RuntimeError,OSError,budget_api.BudgetExpired) as error:
        mark_required(root,activity,engine,'Recovery worker incomplete: '+str(error),expired=deadline.remaining(True)<=0)
        return 1


def expired_result(root,timing,error,engine,public):
    """Do not overwrite a previous or newer run if setup expired before owning it."""
    result=dict(status='blocked',reason=str(error),maximum_seconds=timing['maximum_seconds'])
    try:
        with engine.locked(root):
            path=Path(root)/'automatic.json'
            if not path.exists():return result
            value=engine.load(path)
            if (value.get('kind')!='automatic-configuration-v2' or budget_fields(value)!=timing
                    or value.get('owner_pid')!=os.getpid() or value.get('owner_start')!=engine.start_ticks(os.getpid())
                    or value.get('boot_id')!=engine.boot_id()):return result
            expired=budget_api.Deadline(timing).remaining(True)<=0
            value.update(status='timed-out-recovery-required' if expired else 'recovery-required',error=str(error),recovery_available=True,
                restore_available=False,finished=time.time(),calibration_finished_monotonic=time.monotonic(),
                message='Scadenza della sessione raggiunta. Il recupero completo resta da verificare.' if expired else
                        'Sessione interrotta. Il recupero completo resta da verificare.')
            engine.atomic(path,value);return public(value)
    except (BlockingIOError,OSError,RuntimeError):
        expired=budget_api.Deadline(timing).remaining(True)<=0
        result.update(status='timed-out-recovery-required' if expired else 'recovery-required',reason=str(error)+'; durable activity guard retains recovery ownership')
        return result


if __name__=='__main__':
    if len(sys.argv)!=4:raise SystemExit(2)
    import session
    from autotune_v2 import common_activity
    common_activity.bind_inherited(sys.argv[1],'recovery-worker')
    try:code=recovery_worker(Path(sys.argv[1]),sys.argv[2],sys.argv[3],session)
    finally:common_activity.guard_exit(sys.argv[1])
    raise SystemExit(code)

__release_signature__ = 'Tino Zappala'
