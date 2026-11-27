"""Laboratory Unix-socket broker for the experimental pre-open event.

No automatic installation. Caller must create a root-owned private directory,
authorize the PID using the actual session identity, and stop outside the
NativeAudio lock after preventing new requests. No delay-based ACK is used.
"""
import os
from pathlib import Path
import select
import socket
import stat
import struct
import threading


class PreopenBroker:
    def __init__(self, path, on_request):
        self.path = Path(path)
        self.on_request = on_request
        self.stop_event = threading.Event()
        self.listener = None
        self.wake_r = None
        self.wake_w = None
        self.thread = None
        self.identity = None

    def start(self):
        parent = self.path.parent
        info = parent.lstat()
        if (not self.path.is_absolute() or parent.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != 0 or info.st_mode & 0o077 or os.path.lexists(self.path)):
            raise RuntimeError('Untrusted private handoff socket directory or path')
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        wake_r = wake_w = None
        try:
            listener.bind(str(self.path))
            initial = self.path.lstat()
            if not stat.S_ISSOCK(initial.st_mode) or initial.st_uid != 0:
                raise RuntimeError('Handoff socket ownership not verified')
            self.identity = (initial.st_dev, initial.st_ino)
            os.chmod(self.path, 0o600)
            socket_info = self.path.lstat()
            if (not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid != 0 or
                    (socket_info.st_dev, socket_info.st_ino) != self.identity):
                raise RuntimeError('Handoff socket ownership not verified')
            listener.listen(4)
            wake_r, wake_w = socket.socketpair()
            self.listener, self.wake_r, self.wake_w = listener, wake_r, wake_w
            self.thread = threading.Thread(target=self._serve, name='pcm-preopen-broker', daemon=True)
            self.thread.start()
        except BaseException:
            listener.close()
            if wake_r: wake_r.close()
            if wake_w: wake_w.close()
            self._unlink_owned()
            raise

    def _unlink_owned(self):
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if (self.identity is not None and stat.S_ISSOCK(info.st_mode) and
                (info.st_dev, info.st_ino) == self.identity):
            self.path.unlink()

    def _serve(self):
        while not self.stop_event.is_set():
            readable, _, _ = select.select([self.listener, self.wake_r], [], [])
            if self.wake_r in readable:
                break
            client, _ = self.listener.accept()
            with client:
                readable, _, _ = select.select([client, self.wake_r], [], [])
                if self.wake_r in readable:
                    break
                try:
                    data = client.recv(1)
                    pid, uid, _gid = struct.unpack('3i', client.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                    accepted = data == b'O' and not self.stop_event.is_set() and bool(self.on_request(pid, uid))
                except Exception:
                    accepted = False
                if self.stop_event.is_set(): accepted = False
                try: client.sendall(b'Y' if accepted else b'N')
                except OSError: pass

    def request_stop(self):
        """Wake event waits without joining a worker that may need the session lock."""
        self.stop_event.set()
        if self.wake_w:
            try: self.wake_w.send(b'X')
            except OSError: pass

    def finish_stop(self):
        """Call outside session lock, after disarming and request_stop."""
        if not self.stop_event.is_set():
            raise RuntimeError('Broker stop not requested')
        if self.thread: self.thread.join()
        for fd in (self.listener, self.wake_r, self.wake_w):
            if fd: fd.close()
        self._unlink_owned()

    def stop(self):
        self.request_stop()
        self.finish_stop()
