"""Serialize AutoTune while reserving RAM's queue for shared native readers.

An exclusive mutex in the actual RAM state directory serializes all AutoTune
writers. A shared queue flock blocks RAM mutation while allowing Camilla's
cold-start reader. Recovery children inherit both same open file descriptions.
Their existing journals decide recovery; these locks supply exclusion only.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

RAM_CONFIG = Path('/mnt/hdd/system/daphile-ram/config.json')
ENV_FD = 'DAPHILE_AUTOTUNE_QUEUE_FD'
ENV_SERIAL_FD = 'DAPHILE_AUTOTUNE_SERIAL_FD'
ENV_ROOT = 'DAPHILE_AUTOTUNE_QUEUE_ROOT'
_CURRENT = ContextVar('autotune_queue_activity', default=None)


class Busy(BlockingIOError):
    pass


class Refused(RuntimeError):
    def __init__(self,message,code='preparation-unavailable'):
        super().__init__(message)
        self.code=code


class CustodianGone(RuntimeError):
    pass


class ManualRecoveryExpired(BaseException):
    pass


def need(value, message, code='preparation-unavailable'):
    if not value:
        raise Refused(message,code)


def _root():
    need(sys.platform == 'linux' and os.geteuid() == 0,
         'Root-owned Linux queue coordination is required')


def _directory(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'Absolute protected directory required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
            value = os.fstat(fd)
            need(value.st_uid == 0 and not value.st_mode & 0o022,
                 'Externally writable coordination directory refused')
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _json(path, default=None):
    path = Path(path)
    parent = _directory(path.parent)
    fd = None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=parent)
        except FileNotFoundError:
            return default
        value = os.fstat(fd)
        need(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022
             and value.st_nlink == 1 and value.st_size <= 4 * 1024**2,
             'Protected bounded JSON state required')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            raw = stream.read(4 * 1024**2 + 1)
        need(len(raw) <= 4 * 1024**2, 'JSON state exceeds read bound')
        result = json.loads(raw)
        need(isinstance(result, dict), 'JSON state object required')
        return result
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def _ram_paths():
    cfg = _json(RAM_CONFIG)
    need(cfg is not None and isinstance(cfg.get('state_root'), str), 'Current RAM configuration unavailable')
    state = Path(cfg['state_root'])
    run = Path(cfg.get('run_root', '/run/daphile-ram'))
    need(state.is_absolute() and run.is_absolute(), 'Absolute RAM state paths required')
    return state, run


def _lock_identity(fd, path, mode):
    held = os.fstat(fd)
    named = path.lstat()
    need(stat.S_ISREG(held.st_mode) and stat.S_ISREG(named.st_mode)
         and held.st_uid == 0 and not held.st_mode & 0o022 and held.st_nlink == 1
         and (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino),
         'Coordination lock inode changed or is unprotected: ' + path.name)
    need(mode in ('READ', 'WRITE'), 'Exact coordination flock mode required')
    if mode == 'WRITE':
        need(stat.S_IMODE(held.st_mode) == 0o600,
             'AutoTune mutex must be a private root-owned file')
    raw = Path('/proc/self/fdinfo/' + str(fd)).read_bytes()
    pattern = rb'(?m)^lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+' + mode.encode() + rb'\s'
    need(re.search(pattern, raw), 'Actual kernel ' + mode + ' flock required: ' + path.name)


class QueueLease:
    def __init__(self, fd, path, serial_fd, serial_path):
        self._fd, self.path = fd, path
        self._serial_fd, self.serial_path = serial_fd, serial_path
        self.check()

    @property
    def fd(self):
        self.check()
        return self._fd

    @property
    def serial_fd(self):
        self.check()
        return self._serial_fd

    def check(self):
        need(self._fd is not None and self._serial_fd is not None,
             'AutoTune coordination flock copy is closed')
        need(self._fd != self._serial_fd, 'Distinct queue and mutex descriptors required')
        _lock_identity(self._fd, self.path, 'READ')
        _lock_identity(self._serial_fd, self.serial_path, 'WRITE')
        queue, serial = os.fstat(self._fd), os.fstat(self._serial_fd)
        need((queue.st_dev, queue.st_ino) != (serial.st_dev, serial.st_ino),
             'Distinct queue and mutex inodes required')
        return True

    def child_environment(self, env=None):
        result = dict(os.environ if env is None else env)
        result[ENV_FD] = str(self.fd)
        result[ENV_SERIAL_FD] = str(self.serial_fd)
        result[ENV_ROOT] = str(_CURRENT.get()['root'])
        return result

    def close_copy(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._serial_fd is not None:
            os.close(self._serial_fd)
            self._serial_fd = None
        # Never LOCK_UN: recovery children share both of these OFDs.


def current():
    value = _CURRENT.get()
    if value is not None:
        value['lease'].check()
    return value


def require_exclusive():
    value = current()
    need(value is not None, 'AutoTune mutex and shared RAM queue flock required before mutation')
    return value['lease']


def child_contract():
    lease = require_exclusive()
    return dict(pass_fds=(lease.fd, lease.serial_fd), env=lease.child_environment())


def register_guard(child):
    require_exclusive()
    _CURRENT.get()['guards'].append(child)


def bind_inherited(root, role):
    _root()
    root = Path(root)
    prior = current()
    if prior is not None:
        need(prior['root'] == root, 'Inherited activity state root changed')
        return
    need(role in ('activity-guard', 'transaction-guard', 'recovery-worker'), 'Unknown recovery role')
    raw = os.environ.pop(ENV_FD, None)
    serial_raw = os.environ.pop(ENV_SERIAL_FD, None)
    expected_root = os.environ.pop(ENV_ROOT, None)
    need(raw is not None and re.fullmatch(r'[0-9]{1,8}', raw)
         and serial_raw is not None and re.fullmatch(r'[0-9]{1,8}', serial_raw)
         and expected_root == str(root), 'Both explicit inherited coordination descriptors required')
    fd, serial_fd = int(raw), int(serial_raw)
    need(fd > 2 and serial_fd > 2 and fd != serial_fd,
         'Two distinct dedicated inherited coordination descriptors required')
    os.set_inheritable(fd, False)
    os.set_inheritable(serial_fd, False)
    try:
        state, _ = _ram_paths()
        lease = QueueLease(fd, state / 'queue.lock', serial_fd, state / 'autotune.lock')
    except BaseException:
        os.close(fd)
        os.close(serial_fd)
        raise
    _CURRENT.set(dict(lease=lease, root=root, guards=[], role=role))


def read_state(root, name):
    root = Path(root)
    need(isinstance(name, str) and Path(name).name == name and name not in ('', '.', '..'),
         'A direct state filename is required')
    if not os.path.lexists(root):
        return None
    return _json(root / name)


def _completed_historical_state(root):
    """Accept a terminal older result for admission, without replay or upgrade."""
    if any(read_state(root,name) is not None for name in
           ('current.json','last-known-good.json','v2-policy.json','policy-reapply-before.json')):
        return False
    automatic=read_state(root,'automatic.json')
    activity=read_state(root,'activity-session.json')
    transaction=read_state(root,'transaction.json')
    workload=read_state(root,'controlled-workload.json')
    if not all(isinstance(row,dict) for row in (automatic,activity,transaction,workload)):
        return False
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    historical=automatic.get('boot_id')
    if not historical or historical==boot or any(row.get('boot_id')!=historical for row in (activity,transaction,workload)):
        return False
    return (automatic.get('kind')=='automatic-configuration-v2'
        and automatic.get('status')=='retained-current'
        and automatic.get('playback_recovery',{}).get('restored') is True
        and activity.get('kind')=='automatic-v2' and activity.get('status')=='completed'
        and activity.get('parent_session_owner')==dict(kind='automatic',id=automatic.get('id'))
        and activity.get('owner')==automatic.get('owner_pid')
        and activity.get('start')==automatic.get('owner_start')
        and activity.get('playback_recovery',{}).get('queue_restored') is True
        and transaction.get('status')=='rolled-back'
        and workload.get('owner_id')==activity.get('id')
        and workload.get('phase')=='restored'
        and isinstance(workload.get('workload',{}).get('path'),str)
        and not os.path.lexists(workload['workload']['path']))


def clean_state(root):
    """Read only the actual recovery records; incomplete recovery blocks a run."""
    continuation = read_state(root, 'policy-reapply-before.json')
    if (continuation and continuation.get('schema') == 'autotune-policy-reapply-recovery-v1'
            and continuation.get('phase') not in ('completed', 'recovered')):
        return False
    if _completed_historical_state(root):
        return True
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
        if workload.get('phase') != 'restored':
            return False
        path = workload.get('workload', {}).get('path')
        if not isinstance(path, str) or os.path.lexists(path):
            return False
    automatic = read_state(root, 'automatic.json')
    if automatic and automatic.get('kind') == 'automatic-configuration-v2':
        if automatic.get('status') in ('running', 'recovering', 'recovery-required',
                                       'timed-out-recovery-required'):
            from . import workflow
            return workflow.recovery_completed(root, automatic, workflow.native_api())
        if automatic.get('status') not in ('retained-current', 'configured', 'failed-restored',
                'inconclusive-restored', 'restored', 'cancelled-before-activity-no-effects'):
            return False
        if automatic.get('status') in ('configured', 'retained-current'):
            if automatic.get('completion_timing_persisted') is not True or automatic.get('deadline_guard_released') is not True:
                return False
    return True


def corroborate_ram_off(mac=None):
    """Observe current RAM state while shared queue ownership excludes mutation."""
    require_exclusive()
    state, run = _ram_paths()
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    # Manager.state_value's native default is OFF when no intent exists yet.
    intent = _json(state / 'intent.json', dict(requested_ram=False))
    need(type(intent.get('requested_ram')) is bool and not intent['requested_ram'],
         'AutoTune requires RAM requested OFF','ram-on')
    runtime = _json(run / 'runtime.json')
    need(runtime is not None and runtime.get('boot') == boot, 'Current RAM runtime state unavailable')
    need(not runtime.get('cache_ready') and not runtime.get('effective_ram') and not runtime.get('mounts'),
         'AutoTune requires RAM effectively OFF','ram-on')
    live = _json(run / 'live-switch.json', {})
    need(not (live.get('boot') == boot and live.get('busy')), 'RAM switch is in progress')
    need(not os.path.lexists(run / 'live-switching') and not os.path.lexists(run / 'continuous-required'),
         'RAM streaming or switching is still enabled')
    cache = str(run / 'cache')
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        mounted = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), before.split()[4])
        need(mounted != cache, 'RAM cache remains mounted')
    return dict(status='current-ram-off', requested_ram=False, effective_ram=False)


def wait_guards(value):
    end = time.monotonic() + 4
    for child in value['guards']:
        try:
            child.wait(timeout=max(0, end - time.monotonic()))
        except subprocess.TimeoutExpired:
            return False
    return True


def guard_exit(root):
    value = current()
    if value is None:
        return
    need(value['root'] == Path(root), 'Recovery activity state root changed')
    try:
        wait_guards(value)
    finally:
        # Any still-running recovery child retains its own inherited copy.
        # Failed recovery remains durable and blocks new AutoTune admission.
        value['lease'].close_copy()
        _CURRENT.set(None)


def _owned(root, function, check_ram, role, recovery=False):
    _root()
    root = Path(root)
    prior = current()
    if prior is not None:
        need(prior['root'] == root, 'Nested activity state root changed')
        return function()
    state, _ = _ram_paths()
    parent = _directory(state)
    fd = None
    serial_fd = None
    token = None
    lease = None
    try:
        serial_fd = os.open('autotune.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                            0o600, dir_fd=parent)
        try:
            fcntl.flock(serial_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy('Another AutoTune operation holds the global mutex') from error
        fd = os.open('queue.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy('A RAM mutation holds the queue lock') from error
        lease = QueueLease(fd, state / 'queue.lock', serial_fd, state / 'autotune.lock')
        fd = serial_fd = None
        value = dict(lease=lease, root=root, guards=[], role=role)
        token = _CURRENT.set(value)
        if not recovery:
            need(clean_state(root), 'Pending AutoTune recovery must be completed before a new operation','recovery-pending')
        if check_ram:
            corroborate_ram_off()
        return function()
    finally:
        try:
            if token is not None:
                wait_guards(_CURRENT.get())
        finally:
            if lease is not None:
                lease.close_copy()
            if fd is not None:
                os.close(fd)
            if serial_fd is not None:
                os.close(serial_fd)
            os.close(parent)
            if token is not None:
                _CURRENT.reset(token)


def run_owned(root, function, *, check_ram=True, role='operation', denial_context=None,
              allow_recovery=False):
    need(denial_context is None, 'Obsolete source-bound activity admission is unavailable')
    need(type(allow_recovery) is bool, 'Explicit boolean recovery contract required')
    return _owned(root, function, check_ram, role, recovery=allow_recovery)


@contextmanager
def manual_recovery_deadline():
    need(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'Existing deadline cannot be replaced')
    old = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise ManualRecoveryExpired('Explicit recovery reached its 300 second limit')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 300)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def run_pending_recovery(root, function):
    """Acquire both locks afresh after all original AutoTune holders exited."""
    def recover():
        import engine
        for name, pid_key, start_key in (('activity-session.json', 'owner', 'start'),
                                         ('transaction.json', 'parent_pid', 'parent_start')):
            record = read_state(root, name)
            if record and record.get('boot_id') == engine.boot_id():
                pid, start = record.get(pid_key), record.get(start_key)
                need(type(pid) is int and isinstance(start, str) and start,
                     'Original recovery process identity is unavailable')
                need(pid != os.getpid() and engine.start_ticks(pid) != start,
                     'Original AutoTune worker is still alive')
        with manual_recovery_deadline():
            return function()
    return _owned(root, recover, False, 'manual-recovery', recovery=True)


def startup_v2(root):
    record = read_state(root, 'automatic.json')
    if record is None:
        return dict(status='inactive', audio_started=False)
    if record.get('kind') != 'automatic-configuration-v2':
        return dict(status='legacy-recovery-required', audio_started=False)
    from .workflow import startup
    return startup(root)


def cli_gate(args):
    """Keep unsupported legacy mutations outside the current V2 path."""
    command, action = args.command, getattr(args, 'action', None)
    if command in {'scan', 'analyze', 'report', 'status', 'verify', 'audio-setup', 'dashboard',
                   'rules', 'portability', 'compare', 'export', 'watchdog', 'gui', 'startup'}:
        return
    if command == 'tune' and getattr(args, 'profile', None) not in ('default', 'experimental'):
        return
    if command == 'restore' and getattr(args, 'target', None) == 'pending':
        return
    if command == 'automatic' and action in ('analyze', 'status', 'prepare-player', 'run', 'restore', 'acknowledge-external-intent'):
        return
    if command == 'policy-restore' and action in ('status', 'serve-v2', 'stop'):
        return
    if command == 'profile' and action in ('list', 'show'):
        return
    if command == 'power' and action in ('inventory', 'review', 'plan'):
        return
    if command == 'boot-profile' and action in ('plan', 'status'):
        return
    if command in ('adaptive', 'matrix') and action == 'plan':
        return
    raise Refused('This legacy mutation surface is unavailable in the current AutoTune integration')


def _obsolete(*args, **kwargs):
    raise Refused('Obsolete source-bound activity custodians are unavailable; use the current queue lock')


custodian_fd = exclusive_description = write_custody = source_identity = _obsolete

__release_signature__ = 'Tino Zappala'
