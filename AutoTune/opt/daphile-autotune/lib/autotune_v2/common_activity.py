"""Kernel exclusion/custody for admitted AutoTune operations, never new authority.

Existing run/session/transaction/host/budget checks still authorize every write.
DENY and descriptor records are negative gates/kernel hints, not JSON grants.
"""
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import sys
import time
import ctypes
from contextlib import contextmanager
import audio_activity_lease as kernel

_CURRENT = ContextVar('autotune_common_activity', default=None)


class CustodianGone(RuntimeError):
    pass


class ManualRecoveryExpired(BaseException):
    pass


@contextmanager
def manual_recovery_deadline():
    """A NEW explicit cleanup request, never an extension of calibration."""
    kernel.need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'NO_REPLACEMENT_OF_EXISTING_DEADLINE')
    old = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise ManualRecoveryExpired('Explicit operator cleanup reached its separate300second limit')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 300)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def current():
    value = _CURRENT.get()
    if value is not None:
        value['lease'].check()
    return value


def require_exclusive():
    value = current()
    if value is None:
        raise RuntimeError('Original common activity EX custody is required before mutation')
    kernel.need(kernel._LIVE[value['lease']]['mode'] == 'EX', 'AUTOTUNE_REQUIRES_EX')
    return value['lease']


def child_contract():
    lease = require_exclusive()
    return dict(pass_fds=(lease.fd,), env=lease.child_environment())


def register_guard(child):
    value = current()
    if value is None:
        raise RuntimeError('Cannot arm a recovery guard without original common EX')
    value['guards'].append(child)


def source_identity():
    path = Path(__file__).resolve()
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def safe_root(root, create=False):
    root = Path(root)
    parent = kernel.directory(str(root.parent))
    try:
        if create:
            try:
                os.mkdir(root.name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
        fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        value = os.fstat(fd)
        kernel.need(value.st_uid == value.st_gid == 0 and stat.S_IMODE(value.st_mode) == 0o700,
                    'PRIVATE_AUTOTUNE_STATE_REQUIRED')
        return fd
    finally:
        os.close(parent)


def write_custody(root, phase):
    value = current()
    lease = require_exclusive()
    import engine
    fd = safe_root(root, create=True)
    os.close(fd)
    internal = kernel._LIVE[lease]
    row = dict(schema='autotune-common-kernel-custody-v1', phase=phase, role=value['role'],
        root=str(Path(root)), token=lease.token, lease=internal['lease'],
        member_sha256=hashlib.sha256(internal['member']).hexdigest(), holder=kernel.owner(),
        slot=lease.fd, argv=list(sys.argv), source=source_identity(),
        process_argv_sha256=hashlib.sha256(kernel.read_bounded('/proc/self/cmdline',16384)).hexdigest(),
        process_script_sha256=hashlib.sha256(Path(sys.argv[0]).read_bytes()).hexdigest())
    leaf = ('activity-common-custody.json' if value['role'] == 'activity-guard' else
            'transaction-common-custody.json' if value['role'] == 'transaction-guard' else
            'operation-common-custody.json')
    engine.atomic(Path(root)/leaf, row)


def bind_inherited(root, role):
    if _CURRENT.get() is not None:
        require_exclusive()
        return
    kernel.need(role in ('activity-guard', 'transaction-guard', 'recovery-worker'), 'EXACT_GUARD_ROLE')
    lease = kernel.inherited('EX')
    _CURRENT.set(dict(lease=lease, root=Path(root), guards=[], role=role))
    if role != 'recovery-worker':
        write_custody(root, 'running')


def read_state(root, name):
    fd = safe_root(root)
    leaf = None
    try:
        try:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
        except FileNotFoundError:
            return None
        s = os.fstat(leaf)
        kernel.need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022
                    and s.st_nlink == 1 and s.st_size <= 4*1024**2, 'PRIVATE_RECOVERY_RECORD')
        raw = os.pread(leaf, 4*1024**2+1, 0)
        kernel.need(len(raw) == s.st_size, 'RECOVERY_RECORD_READ_BOUND')
        return json.loads(raw)
    finally:
        if leaf is not None:
            os.close(leaf)
        os.close(fd)


def clean_state(root):
    """Read-only combination of existing original completion predicates."""
    import engine
    import session
    root = Path(root)
    if not root.exists():
        return True
    from . import policy_custody
    if policy_custody.undo_pending(root):
        return False
    transaction = read_state(root, 'transaction.json')
    if transaction and transaction.get('status') not in ('committed', 'rolled-back'):
        return False
    activity = read_state(root, 'activity-session.json')
    if activity and activity.get('status') == 'cancelled-external-intent-preserved':
        from .external_intent import clean_cancelled
        return clean_cancelled(root)
    if activity:
        if activity.get('status') not in ('completed', 'recovered'):
            return False
        if activity.get('restore_playback') is True:
            recovery = activity.get('playback_recovery')
            if not isinstance(recovery, dict) or recovery.get('restored') is not True or recovery.get('queue_restored') is False:
                return False
    workload = read_state(root, 'controlled-workload.json')
    if workload:
        if workload.get('phase') != 'restored' or Path(workload.get('workload', {}).get('path', '/')).exists():
            return False
    automatic = read_state(root, 'automatic.json')
    if automatic and automatic.get('kind') == 'automatic-configuration-v2':
        from . import workflow
        if automatic.get('status') in ('running', 'recovering', 'recovery-required', 'timed-out-recovery-required'):
            if not workflow.recovery_completed(root, automatic, workflow.native_api()):
                return False
        elif automatic.get('status') not in workflow.FINAL:
            return False
        if automatic.get('status') in ('configured', 'retained-current'):
            if automatic.get('completion_timing_persisted') is not True or automatic.get('deadline_guard_released') is not True:
                return False
    return True


def wait_guards(value):
    """Only genuine direct Popen children registered before their readiness ACK."""
    for child in value['guards']:
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            return False
    return True


def custodian_fd(root, leaf, *, passive=False, expected_token=None):
    """Root-only kernel/OFD observation. The descriptor record is only a hint."""
    kernel.root()
    row = read_state(root, leaf)
    if row is None:
        raise CustodianGone('No published common custodian')
    kernel.need(row.get('schema') == 'autotune-common-kernel-custody-v1'
                and row.get('root') == str(Path(root)) and row.get('source') == source_identity(),
                'SOURCE_BOUND_COMMON_CUSTODIAN_RECORD')
    if expected_token is not None:
        if row['token'] != expected_token:
            raise CustodianGone('A previous member cannot lend the current operation FD')
    holder = row['holder']
    kernel.need(holder['boot'] == kernel.owner()['boot'] and holder['namespaces'] == list(kernel.namespaces()),
                'CUSTODIAN_CURRENT_BOOT_NAMESPACES')
    try:
        watch = os.pidfd_open(holder['pid'])
    except ProcessLookupError as error:
        raise CustodianGone('Original custodian exited') from error
    duplicate = None
    try:
        try:
            fields = kernel.read_bounded('/proc/'+str(holder['pid'])+'/stat', 4096).rsplit(b')', 1)[1].split()
        except FileNotFoundError as error:
            if select.select([watch], [], [], 0)[0]:
                raise CustodianGone('Original custodian exited') from error
            raise
        if int(fields[19]) != holder['start'] or select.select([watch], [], [], 0)[0]:
            raise CustodianGone('Original custodian exited or PID reused')
        kernel.need(not passive or row['phase'] == 'passive', 'ORIGINAL_CUSTODIAN_STILL_RECOVERING')
        expected_script = (str(Path(__file__).resolve().parents[1]/'session.py') if row['role'] == 'activity-guard'
                           else str(Path(__file__).resolve().parents[2]/'bin/dat'))
        kernel.need(row['role'] in ('activity-guard', 'transaction-guard') and row['argv'][0] == expected_script,
                    'ADMITTED_GUARD_COMMAND_ONLY')
        actual = kernel.read_bounded('/proc/'+str(holder['pid'])+'/cmdline', 16384).split(b'\0')
        expected = [os.fsencode(sys.executable), *map(os.fsencode, row['argv']), b'']
        kernel.need(actual == expected and os.path.realpath('/proc/'+str(holder['pid'])+'/exe') == os.path.realpath(sys.executable),
                    'GENUINE_KERNEL_GUARD_IMAGE_AND_ARGV')
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        duplicate = int(libc.syscall(438, watch, row['slot'], 0))
        kernel.need(duplicate >= 0, 'REAL_CUSTODIAN_PIDFD_GETFD_REQUIRED')
        os.set_inheritable(duplicate, False)
        parent = kernel.layout()
        try:
            kernel.need(kernel.image(duplicate) == row['lease'] == kernel.image_named(parent, 'lease.lock'),
                        'CUSTODIAN_FIXED_EX_INODE')
        finally:
            os.close(parent)
        kernel.kernel_locked(duplicate, 'EX')
        result, duplicate = duplicate, None
        return result, row
    finally:
        if duplicate is not None and duplicate >= 0:
            os.close(duplicate)
        os.close(watch)


def guard_exit(root):
    value = current()
    if value is None:
        return
    if value['role'] == 'recovery-worker':
        value['lease'].close_copy()
        return
    # A live original worker still retains the EX; it will discharge the full
    # barrier after guards exit. A clean policy-only guard must not discharge an
    # unrelated live activity. Existing originals remain the ownership authority.
    if value['lease'].original_owner_alive():
        value['lease'].close_copy()
        return
    if value['role'] == 'transaction-guard':
        transaction = read_state(root, 'transaction.json')
        if transaction and transaction.get('status') in ('committed', 'rolled-back'):
            try:
                fd, _ = custodian_fd(root, 'activity-common-custody.json', expected_token=value['lease'].token)
            except CustodianGone:
                pass
            else:
                os.close(fd)
                value['lease'].close_copy()
                return
    if clean_state(root) and wait_guards(value):
        value['lease'].retire_after_quiescence()
        return
    write_custody(root, 'passive')
    # No mutation/recovery window is extended. Passive EX + DENY are retained
    # after the original deadline until explicit genuine recovery proves clean.
    while True:
        # The existing explicit root `dat restore pending` may borrow the SAME
        # verified passive OFD and complete original native recovery. This loop
        # itself performs NO recovery after deadline and issues no work grant.
        internal = kernel._LIVE[value['lease']]
        if kernel.deny(internal['parent']) == b'C\n':
            group, rows = kernel.members(internal['parent'])
            try:
                if not rows:
                    # The authentic manual cleanup already retired the
                    # original member. Closing this same physical EX
                    # copy confers no authority and performs no recovery.
                    kernel.need(kernel.image(internal['fd']) == internal['lease'] ==
                                kernel.image_named(internal['parent'], 'lease.lock'), 'RETIRED_EX_ANCHOR')
                    kernel.kernel_locked(internal['fd'], 'EX')
                    value['lease'].close_copy()
                    return
            finally:
                os.close(group)
        select.select([], [], [], .2)


def run_pending_recovery(root, function):
    """Existing root pending-restore authority; never reconstruct a grant.

    A live passive guardian lends its real EX OFD by pidfd_getfd. With all
    custodians dead, the explicit original pending restore may hold raw EX only
    after verifying its ONE exact original member and dead original writer.
    Neither path clears DENY before original full recovery/readback succeeds.
    """
    if current() is not None:
        return function()
    import engine
    import session
    from detection import read
    root = Path(root)
    from . import admission_recovery
    no_work = admission_recovery.recover_no_work(root)
    if no_work is not None:
        return no_work
    from . import policy_custody
    if policy_custody.recovery_candidate(root):
        return policy_custody.recover_monitor(root)
    operation = read_state(root, 'operation-common-custody.json')
    kernel.need(operation is not None and operation['schema'] == 'autotune-common-kernel-custody-v1'
                and operation['source'] == source_identity() and operation['root'] == str(root),
                'ORIGINAL_PENDING_OPERATION_REQUIRED')
    activity = read_state(root, 'activity-session.json')
    transaction = read_state(root, 'transaction.json')
    admitted = admission_recovery.member(root)
    admitted_owner = admitted[1]['owner'] if admitted is not None else None
    completed_automatic = bool(admitted is not None and admitted[2]['role']=='automatic-run' and
        activity and activity.get('kind')=='automatic-v2' and activity.get('status')=='completed' and
        activity.get('owner')==admitted_owner['pid'] and str(activity.get('start'))==str(admitted_owner['start']) and
        activity.get('boot_id')==admitted_owner['boot'])
    original = activity if activity and activity.get('status') in ('running', 'recovering', 'recovery-required', 'cancelled-external-intent-preserved') else transaction
    if completed_automatic:original = activity
    kernel.need(original is not None and original.get('boot_id') == engine.boot_id(), 'NO_PREVIOUS_BOOT_REPLAY')
    if activity and original is activity:
        kernel.need(activity.get('host_uuid') == read('/sys/class/dmi/id/product_uuid') and
                    policy_custody.dead(activity['owner'],activity['start'],activity['boot_id']),
                    'EXACT_DEAD_NATIVE_ACTIVITY_OWNER')
        primary_pid, primary_start = activity['owner'], activity['start']
        recovery = activity.get('recovery_process')
        if recovery:
            kernel.need(engine.start_ticks(recovery['pid']) != recovery['start'], 'ORIGINAL_RECOVERY_WORKER_STILL_ACTIVE')
    else:
        completed_native = bool(admitted is not None and admitted[2]['role'] in ('native-tune','native-trial') and
            transaction.get('status')=='committed' and transaction.get('parent_pid')==admitted_owner['pid'] and
            str(transaction.get('parent_start'))==str(admitted_owner['start']))
        kernel.need(transaction.get('status') in ('pending', 'rollback-incomplete') or completed_native,
                    'ONLY_ORIGINAL_PENDING_OR_EXACT_SOURCE_COMMIT_ROLLBACK')
        primary_pid, primary_start = transaction['parent_pid'], transaction['parent_start']
        kernel.need(policy_custody.dead(primary_pid,str(primary_start),engine.boot_id()), 'ORIGINAL_WRITER_STILL_ALIVE')
    borrowed = []
    descriptor = None
    try:
        for leaf in ('activity-common-custody.json', 'transaction-common-custody.json'):
            try:
                fd, row = custodian_fd(root, leaf, passive=True, expected_token=operation['token'])
            except CustodianGone:
                continue
            kernel.need(row['member_sha256'] == operation['member_sha256'], 'SAME_PENDING_MEMBER_SEAL')
            borrowed.append(fd)
        if borrowed:
            descriptor = borrowed.pop()
        else:
            parent = kernel.layout()
            group = None
            try:
                descriptor = kernel.take(parent, 'lease.lock', 'EX')
                with kernel.admission(parent):
                    group, rows = kernel.members(parent)
                    kernel.need(set(rows) == {operation['token']} and kernel.deny(parent) == b'D\n',
                                'ONE_ORIGINAL_ORPHAN_EX_MEMBER_REQUIRED')
                    raw = kernel.member_raw(group, operation['token'])
                    member = json.loads(raw)
                    kernel.need(hashlib.sha256(raw).hexdigest() == operation['member_sha256']
                                and member['mode'] == 'EX' and member['owner']['pid'] == primary_pid
                                and str(member['owner']['start']) == str(primary_start), 'EXACT_ORIGINAL_DEAD_MEMBER')
            finally:
                if group is not None:
                    os.close(group)
                os.close(parent)
        lease = kernel.Lease.inherit(descriptor, operation['token'], 'EX')
        descriptor = None
        value = dict(lease=lease, root=root, guards=[], role='manual-pending-recovery')
        token = _CURRENT.set(value)
        try:
            started = time.monotonic()
            engine.atomic(root/'manual-common-recovery.json', dict(
                schema='explicit-operator-cleanup-v1', status='running', maximum_seconds=300,
                original_token=operation['token'], owner=kernel.owner(), started_monotonic=started,
                calibration_continuation=False, calibration_budget_changed=False))
            with manual_recovery_deadline():
                if activity and activity.get('status') == 'cancelled-external-intent-preserved':
                    from .external_intent import clean_cancelled
                    kernel.need(clean_cancelled(root), 'CANCELLED_STATE_NOT_FULLY_CLEAN')
                    result = dict(status='cancelled-external-intent-preserved', playback_restored=False, hardware_qualified=False)
                elif completed_automatic:
                    automatic = read_state(root,'automatic.json')
                    kernel.need(automatic.get('owner_pid')==primary_pid and automatic.get('owner_start')==primary_start and
                                automatic.get('activity_session_id')==activity['id'], 'EXACT_COMPLETED_V2_OWNER_LINK')
                    from . import workflow
                    if automatic.get('status')=='configured':
                        result = workflow.restore(root)
                    elif automatic.get('status')=='retained-current':
                        kernel.need(workflow.recovery_completed(root,automatic,workflow.native_api()),
                                    'RETAINED_RESULT_FULL_RECOVERY_REQUIRED')
                        result = dict(status='retained-current-quiescent',calibration_qualified=False)
                    else:
                        raise RuntimeError('Completed source activity lacks an authentic V2 terminal result')
                else:
                    result = function()
                kernel.need(clean_state(root) and wait_guards(value), 'ORIGINAL_RECOVERY_NOT_FULLY_QUIESCENT')
            engine.atomic(root/'manual-common-recovery.json', dict(
                schema='explicit-operator-cleanup-v1', status=('cancelled-quiescent' if result.get('status') == 'cancelled-external-intent-preserved' else 'recovered'), maximum_seconds=300,
                original_token=operation['token'], owner=kernel.owner(), started_monotonic=started,
                finished_monotonic=time.monotonic(), calibration_continuation=False, calibration_budget_changed=False))
            lease.retire_after_quiescence()
            return result
        finally:
            lease.close_copy()
            _CURRENT.reset(token)
    finally:
        for fd in borrowed:
            os.close(fd)
        if descriptor is not None:
            os.close(descriptor)


def exclusive_description():
    lease = require_exclusive()
    value = kernel._LIVE[lease]
    return dict(schema='daphile-audio-activity-ex-observation-v1', path=kernel.PATH,
                mode='EX', token=lease.token, fd=lease.fd, lease=list(value['lease']),
                member_sha256=hashlib.sha256(value['member']).hexdigest(),
                owner=json.loads(value['member'])['owner'])


def corroborate_ram_off(mac=None):
    require_exclusive()
    if mac is None:
        import player
        from detection import scan
        from .native_target import resolve
        mac = resolve(scan(),player=player)['mac']
    from . import ram_off_binding
    return ram_off_binding.prove(mac, exclusive_description())


def startup_v2(root):
    """Only the final V2 monitor; no legacy boot/AB/playback recovery path."""
    record = read_state(root, 'automatic.json') if Path(root).exists() else None
    if record is None:
        return dict(status='inactive', audio_started=False)
    if record.get('kind') != 'automatic-configuration-v2':
        return dict(status='legacy-recovery-required', audio_started=False)
    from .workflow import startup
    return startup(root)


def run_owned(root, function, *, check_ram=True, role='operation', denial_context=None):
    """One admitted root operation; nested calls borrow the SAME physical OFD."""
    root = Path(root)
    prior = current()
    if prior is not None:
        kernel.need(prior['root'] == root, 'NESTED_ACTIVITY_STATE_ROOT_CHANGED')
        return function()
    if denial_context is None:
        from . import admission_recovery
        if role in admission_recovery.ROLES:
            denial_context = lambda: admission_recovery.context(root,role)
    lease = kernel.Lease.acquire('EX', denial_context=denial_context)
    value = dict(lease=lease, root=root, guards=[], role=role)
    token = _CURRENT.set(value)
    effects_entered = False
    try:
        if check_ram:
            corroborate_ram_off()
        write_custody(root, 'running')
        effects_entered = True
        return function()
    finally:
        try:
            if (not effects_entered or clean_state(root)) and wait_guards(value):
                lease.retire_after_quiescence()
            # Otherwise the independent guard retains its copy and enters
            # passive custody, or DENY survives total custodian death.
        finally:
            lease.close_copy()
            _CURRENT.reset(token)


def cli_gate(args):
    """Uncovered distributed legacy mutators refuse BEFORE their first effect."""
    command = args.command
    action = getattr(args, 'action', None)
    readonly = {'scan', 'analyze', 'report', 'status', 'verify', 'audio-setup', 'dashboard',
                'rules', 'portability', 'compare', 'export'}
    if command in readonly or command == 'watchdog':
        return
    if command == 'tune' and getattr(args,'profile',None) not in ('default','experimental'):
        return
    if command == 'restore' and getattr(args,'target',None) == 'pending':
        return
    if command == 'automatic' and action in ('analyze', 'status', 'run', 'restore', 'acknowledge-external-intent'):
        return
    if command == 'policy-restore' and action in ('status','serve-v2'):
        return
    if command == 'profile' and action in ('list', 'show'):
        return
    if command == 'gui':
        return  # GUI lifecycle only; every spawned mutator re-enters this CLI.
    if command == 'startup':
        return  # CLI dispatch is startup_v2; legacy startup is unreachable.
    if command == 'power' and action in ('inventory', 'review', 'plan'):
        return
    if command == 'boot-profile' and action in ('plan', 'status'):
        return
    if command == 'adaptive' and action == 'plan':
        return
    if command == 'matrix' and action == 'plan':
        return
    raise RuntimeError('This legacy mutation surface is not admitted by the common activity protocol')


__release_signature__ = 'Tino Zappala'
