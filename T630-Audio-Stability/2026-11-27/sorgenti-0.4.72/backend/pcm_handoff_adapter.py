"""Experimental adapter from a PCM-open event to .64's existing release path.

Not wired into production. Caller owns socket lifecycle, authorizes the peer,
arms/disarms this adapter only while the private PCM route is owned, and must
never claim success without the two kernel state readbacks.
"""
import threading


class PreopenReleaseAdapter:
    def __init__(self, audio, playback_closed, capture_closed, authorize):
        self.audio = audio
        self.playback_closed = playback_closed
        self.capture_closed = capture_closed
        self.authorize = authorize
        self.armed = threading.Event()
        self.startup = threading.Event()

    def permitted(self):
        return self.startup.is_set() or self.audio.info.get('mode') in ('pcm-active', 'waiting-pcm')

    def request(self, pid, uid):
        """Return True only when the old playback and capture are closed.

        An already-closed capture does not need the session lock: initial
        player startup can be waiting inside that lock. Existing .64 logic only
        launches capture after it has observed the producer RUNNING. If that
        invariant cannot be guaranteed, this fast path is not safe to deploy.
        """
        audio = self.audio
        if (not self.armed.is_set() or not self.permitted() or audio.stopping.is_set() or uid != 0 or
                not self.authorize(pid, uid) or not self.playback_closed()):
            return False
        if self.capture_closed():
            return self.armed.is_set() and self.permitted() and not audio.stopping.is_set() and self.playback_closed()
        with audio.lock:
            if (not self.armed.is_set() or not self.permitted() or audio.stopping.is_set() or
                    not self.authorize(pid, uid) or not self.playback_closed()):
                return False
            if not self.capture_closed():
                if (audio.info.get('mode') not in ('pcm-active', 'waiting-pcm') or
                        audio.pipeline is None):
                    return False
                audio._enter_wait('waiting-pcm', 'In attesa del nuovo flusso PCM', True)
            return (self.armed.is_set() and self.permitted() and not audio.stopping.is_set() and
                    self.playback_closed() and self.capture_closed())
