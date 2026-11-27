"""Lab-only lifecycle boundary for the pre-open broker, no implicit side effects."""
from pcm_handoff_adapter import PreopenReleaseAdapter
from pcm_handoff_broker import PreopenBroker


class PreopenLifecycle:
    def __init__(self, audio, socket_path, playback_closed, capture_closed, identity_factory):
        self.audio = audio
        self.adapter = None
        self.broker = PreopenBroker(socket_path, lambda pid, uid: self.adapter.request(pid, uid))
        identity = identity_factory(lambda: self.broker.wake_r.fileno())
        self.adapter = PreopenReleaseAdapter(audio, playback_closed, capture_closed, identity)
        self.started = False
        self.retiring = False

    def begin_start(self):
        """Start before the routed player can open; can run under session lock."""
        if self.started or self.retiring:
            raise RuntimeError('Handoff lifecycle already used')
        self.adapter.startup.set()
        try:
            self.broker.start()
            self.adapter.armed.set()
            self.started = True
        except BaseException:
            self.adapter.startup.clear()
            raise

    def committed(self):
        """Call after normal NativeAudio has published pcm-active."""
        if not self.started or self.retiring or self.audio.info.get('mode') != 'pcm-active':
            raise RuntimeError('Cannot commit inactive handoff')
        self.adapter.startup.clear()

    def begin_stop(self):
        """Disarm before native/DSD handoff; safe while session lock is held."""
        if not self.started or self.retiring:
            return
        self.retiring = True
        self.adapter.armed.clear()
        self.adapter.startup.clear()
        self.broker.request_stop()

    def finish_stop(self):
        """Join and remove owned socket only outside the session lock."""
        if not self.retiring:
            raise RuntimeError('Handoff retirement was not requested')
        self.broker.finish_stop()
