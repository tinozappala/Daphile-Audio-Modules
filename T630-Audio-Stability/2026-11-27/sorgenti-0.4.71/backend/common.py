"""Root-owned backend utilities. No plugin discovery or public registry."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import time
import uuid

IDS = {'DaphileRME': ('rme', 8891, 'RMEPlugin'), 'DaphileCamillaDSP': ('camilla', 8892, 'CamillaPlugin')}
TERMINAL = {'committed', 'restored', 'rolled-back', 'no-change'}


def atomic(path, value):
    path = Path(path)
    temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    with temporary.open('x', encoding='utf8', newline='\n') as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, allow_nan=False)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def private(path):
    path = Path(path)
    if path.is_symlink(): raise RuntimeError('Symlink directory refused')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077:
        raise RuntimeError('Private owner-only directory required')
    return path


@contextlib.contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def identity(pid):
    process = Path('/proc')/str(pid)
    stat = (process/'stat').read_text().rsplit(')', 1)[1].split()
    if stat[0] in ('Z', 'X'): raise ProcessLookupError(pid)
    command=(process/'cmdline').read_bytes()
    final=(process/'stat').read_text().rsplit(')',1)[1].split()
    # Exit can happen between reading stat and cmdline. Never journal the
    # empty command of a dying process as an owned, running controller.
    if not command or final[0] in ('Z','X') or final[19]!=stat[19]: raise ProcessLookupError(pid)
    return dict(pid=pid, start=stat[19], boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                cmd=hashlib.sha256(command).hexdigest())


def stop_owned(record, timeout=15):
    try:
        fd = os.pidfd_open(record['pid'])
        try:
            actual=identity(record['pid'])
            if actual['boot']!=record['boot'] or actual['start']!=record['start']: return
            if actual != record: raise RuntimeError('Process identity changed; no signal sent')
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not select.select([fd], [], [], timeout)[0]:
                raise RuntimeError('Graceful stop not confirmed; recovery retained')
        finally: os.close(fd)
    except (FileNotFoundError, ProcessLookupError): pass


def started_identity(child, timeout=3):
    deadline=time.monotonic()+timeout
    expected=hashlib.sha256(b''.join(os.fsencode(arg)+b'\0' for arg in child.args)).hexdigest()
    while time.monotonic()<deadline and child.poll() is None:
        try:
            record=identity(child.pid)
            if record['cmd']==expected: return record
        except (FileNotFoundError,ProcessLookupError): pass
        time.sleep(.01)
    raise RuntimeError('Owned child exited before its identity could be recorded')


def pin_engine_namespace(child, record):
    """Pin only the network namespace of the still-live recorded child."""
    process_fd = os.pidfd_open(child.pid)
    namespace_fd = None
    try:
        if child.poll() is not None or identity(child.pid) != record:
            raise RuntimeError('Engine identity changed before namespace pin')
        namespace_fd = os.open(f'/proc/{child.pid}/ns/net', os.O_RDONLY | os.O_CLOEXEC)
        if (child.poll() is not None or identity(child.pid) != record
                or select.select([process_fd], [], [], 0)[0]):
            raise RuntimeError('Engine exited or changed during namespace pin')
        pinned, namespace_fd = namespace_fd, None
        return pinned
    finally:
        if namespace_fd is not None: os.close(namespace_fd)
        os.close(process_fd)


def pending(state):
    return [p for p in Path(state).glob('rme-*/transaction.json')
            if json.loads(p.read_text()).get('status') not in TERMINAL]


def recover_rme(release, state):
    import sys
    sys.path.insert(0, str(Path(release)/'rme/lib'))
    import rme_peq, rme_audio
    # Both transports keep the native global locks, including with AutoTune.
    for module, directory in ((rme_peq, 'rme-peq'), (rme_audio, 'rme-audio')):
        path = Path(state)/directory/'transaction.json'
        if path.exists() and json.loads(path.read_text()).get('status') not in TERMINAL:
            module.recover(state)
    extended=Path(state)/'rme-control/transaction.json'
    if extended.exists() and json.loads(extended.read_text()).get('status') not in TERMINAL:
        import rme_control
        rme_control.recover(state)
    if pending(state): raise RuntimeError('RME recovery is incomplete')
