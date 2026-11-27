"""Laboratory lifecycle wrapper for a private per-session PCM handoff socket."""
import os
from pathlib import Path
import stat
import tempfile
import threading

from pcm_handoff_lifecycle import PreopenLifecycle
from pcm_peer_identity import for_native_player


class HandoffRetirementPending(RuntimeError):
    pass


def loopback_closed(endpoint, subdevice=6):
    try:
        root = Path('/proc/asound/card0')
        return ((root/'id').read_text().strip() == 'Loopback' and
                (root/endpoint/('sub%d' % subdevice)/'hw_params').read_text().strip() == 'closed')
    except OSError:
        return False


class HandoffRuntime:
    def __init__(self, audio, target, route_config, *, base_dir='/run', identity_factory=None,
                 playback_closed=None, capture_closed=None):
        self.audio = audio
        self.target = target
        self.route_config = Path(route_config)
        self.base_dir = Path(base_dir)
        self.identity_factory = identity_factory or (
            lambda cancel: for_native_player(target, self.route_config, cancel))
        self.playback_closed = playback_closed or (lambda: loopback_closed('pcm0p'))
        self.capture_closed = capture_closed or (lambda: loopback_closed('pcm1c'))
        self.directory = None
        self.dir_identity = None
        self.lifecycle = None
        self.finished = threading.Event()
        self.retire_error = None
        self.reaper = None

    def start(self):
        """Start before binding/restarting the player; caller owns session lock."""
        if self.directory is not None:
            raise RuntimeError('Handoff runtime already used')
        parent = self.base_dir.lstat()
        if (self.base_dir.is_symlink() or not stat.S_ISDIR(parent.st_mode) or
                parent.st_uid != 0 or parent.st_mode & 0o022):
            raise RuntimeError('Untrusted handoff runtime parent')
        self.directory = Path(tempfile.mkdtemp(prefix='darc-pcm-', dir=self.base_dir))
        try:
            info = self.directory.lstat()
            self.dir_identity = (info.st_dev, info.st_ino)
            if info.st_uid != 0 or info.st_mode & 0o077:
                raise RuntimeError('Untrusted handoff runtime directory')
            self.lifecycle = PreopenLifecycle(self.audio, self.directory/'s', self.playback_closed,
                                               self.capture_closed, self.identity_factory)
            self.lifecycle.begin_start()
        except BaseException:
            if self.dir_identity is not None:
                self._remove_directory()
            raise
        return self.directory/'s'

    def committed(self):
        self.lifecycle.committed()

    def _remove_directory(self):
        try:
            info = self.directory.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self.dir_identity:
            raise RuntimeError('Handoff runtime directory changed')
        self.directory.rmdir()  # only the empty, owned directory

    def begin_stop(self):
        """Disarm under session lock, finish asynchronously outside that lock."""
        if self.reaper is not None:
            return
        self.lifecycle.begin_stop()
        def finish():
            try:
                self.lifecycle.finish_stop()
                self._remove_directory()
            except Exception as error:
                self.retire_error = error
            finally:
                self.finished.set()
        self.reaper = threading.Thread(target=finish, name='pcm-handoff-reaper', daemon=True)
        self.reaper.start()

    def verify_retired(self):
        if not self.finished.is_set():
            raise HandoffRetirementPending('Previous handoff is still retiring')
        if self.retire_error is not None:
            raise RuntimeError('Previous handoff cleanup not verified') from self.retire_error
