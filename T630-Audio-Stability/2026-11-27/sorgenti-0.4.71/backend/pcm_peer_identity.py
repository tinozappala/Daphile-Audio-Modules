"""Authorize a pre-open peer against its live native player and spacd pidfile.

Uses pidfd/inotify to wait for spacd's event-driven PID publication; no polling
or delay is used. A caller-provided cancellation fd wakes shutdown. Laboratory
candidate: integration must supply the existing USB/argv identity validator.
"""
import ctypes
import hashlib
import os
from pathlib import Path
import select
import stat

_EVENTS = (0x00000002 | 0x00000004 | 0x00000008 | 0x00000080 | 0x00000100 |
           0x00000400 | 0x00000800)
_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.inotify_init1.argtypes = [ctypes.c_int]
_LIBC.inotify_init1.restype = ctypes.c_int
_LIBC.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
_LIBC.inotify_add_watch.restype = ctypes.c_int


class PeerIdentity:
    def __init__(self, pidfile, expected_exe_sha256, expected_config, verify_argv,
                 cancel_fd, proc_root=Path('/proc'), on_wait=None, on_reject=None):
        self.pidfile = Path(pidfile)
        self.expected_exe_sha256 = expected_exe_sha256
        self.expected_config = os.fsencode(expected_config)
        self.verify_argv = verify_argv
        self.cancel_fd = cancel_fd
        self.proc_root = Path(proc_root)
        self.on_wait = on_wait
        self.on_reject = on_reject

    def _fingerprint(self, pid):
        proc = self.proc_root / str(pid)
        before = (proc/'stat').read_text().rsplit(')', 1)[1].split()
        if before[0] in ('Z', 'X'):
            raise ProcessLookupError('peer exited')
        start = before[19]
        with (proc/'exe').open('rb') as executable:
            digest = hashlib.file_digest(executable, 'sha256').hexdigest()
        argv = [os.fsdecode(value) for value in (proc/'cmdline').read_bytes().split(b'\0') if value]
        environ = (proc/'environ').read_bytes().split(b'\0')
        after = (proc/'stat').read_text().rsplit(')', 1)[1].split()
        if (after[0] in ('Z', 'X') or after[19] != start or digest != self.expected_exe_sha256
                or not argv or not self.verify_argv(argv)
                or environ.count(b'ALSA_CONFIG_PATH=' + self.expected_config) != 1):
            raise PermissionError('peer process identity not verified')
        return start

    def _read_pidfile(self):
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, 'O_NOFOLLOW', 0)
        try:
            fd = os.open(self.pidfile, flags)
        except FileNotFoundError:
            return None
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise PermissionError('untrusted spacd pidfile')
            data = os.read(fd, 128)
            if len(data) >= 128:
                raise PermissionError('invalid spacd pidfile')
            value = data.strip()
            return int(value) if value.isdigit() else None
        finally:
            os.close(fd)

    def _wait_for_pidfile(self, pid, pidfd):
        fd = _LIBC.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if fd < 0:
            raise OSError(ctypes.get_errno(), 'inotify_init1')
        announced = False
        try:
            while True:
                directory = self.pidfile.parent
                while not directory.is_dir() and directory != directory.parent:
                    directory = directory.parent
                key = os.fsencode(directory)
                # Re-arm after every event: spacd can delete and recreate the
                # same directory path while replacing a player.
                if _LIBC.inotify_add_watch(fd, key, _EVENTS) < 0:
                    error = ctypes.get_errno()
                    if error == 2:  # directory disappeared before the watch
                        continue
                    raise OSError(error, 'inotify_add_watch')
                # Check after installing the watch to avoid missing a create.
                if self._read_pidfile() == pid:
                    return True
                if not announced and self.on_wait:
                    self.on_wait()
                    announced = True
                readable, _, _ = select.select([fd, pidfd, self.cancel_fd()], [], [])
                if pidfd in readable or self.cancel_fd() in readable:
                    return False
                if fd in readable:
                    try:
                        while os.read(fd, 65536):
                            pass
                    except BlockingIOError:
                        pass
        finally:
            os.close(fd)

    def __call__(self, pid, uid):
        if uid != 0 or type(pid) is not int or pid <= 1:
            return False
        try:
            pidfd = os.pidfd_open(pid, 0)
            try:
                start = self._fingerprint(pid)
                if not self._wait_for_pidfile(pid, pidfd):
                    return False
                # Check the pinned process again after the pidfile event.
                return self._fingerprint(pid) == start and self._read_pidfile() == pid
            finally:
                os.close(pidfd)
        except (OSError, ValueError, IndexError, PermissionError) as error:
            if self.on_reject:
                self.on_reject(type(error).__name__)
            return False


def for_native_player(target, expected_config, cancel_fd):
    """Build verifier using the pre-existing .64 player/USB identity check."""
    from native_route import NATIVE_SHA256
    from player_router import selected_card
    card = target['card']
    if type(card) is not int or not 0 <= card <= 1024:
        raise ValueError('invalid native card')
    route = dict(version=1, alsa_id=target['alsa_id'], usb=dict(target['usb']))
    return PeerIdentity(Path('/run/spacd/c')/str(card)/'d/0/pid', NATIVE_SHA256,
                        expected_config, lambda argv: selected_card(argv[1:], route) == card,
                        cancel_fd)
