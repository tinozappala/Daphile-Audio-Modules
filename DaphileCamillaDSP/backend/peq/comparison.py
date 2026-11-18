"""Short, exclusive auditions on the current PCM route. Preset files are read-only here."""
import json
import secrets
import threading
import time
import uuid

from .engine import Engine, EngineError, WebSocket, engine_transaction
from .model import RATES, ValidationError
from .source_format import classify_source
from .store import ConflictError


class Comparison:
    lease_seconds = 30
    playback_required = 'Attiva l’equalizzatore e avvia un brano PCM per ascoltare il confronto.'
    session_finished = 'Il confronto è terminato. Puoi avviarne uno nuovo.'
    changed = 'La riproduzione o le impostazioni sono cambiate. Avvia un nuovo confronto.'

    def __init__(self, app):
        self.app = app
        self.owner = None
        self.route = None
        self.expected = None
        self.tag = None
        self.config = None
        self.confirmed_config = None
        self.pending_readback = False
        self.timer = None
        self.deadline = 0
        self.closed = False
        self.observer = None

    def active(self):
        # Called under app.apply_lock. The native adapter gives every newly
        # opened stream a new route object, including at the same sample rate.
        if self.owner and self.route is not self.app.route:
            self.clear()
        return self.owner is not None

    def clear(self):
        if self.timer:
            self.timer.cancel()
        self.timer = self.owner = self.route = self.tag = None
        self.config = self.confirmed_config = self.expected = None
        self.pending_readback = False
        self.observer = None

    def arm(self):
        self.deadline = time.monotonic() + self.lease_seconds
        if self.timer is None:
            self.schedule(self.lease_seconds)

    def schedule(self, delay):
        timer = threading.Timer(delay, self.expire, args=(self.tag,))
        timer.daemon = True
        self.timer = timer
        timer.start()

    def expire(self, tag):
        with self.app.apply_lock:
            if tag != self.tag or not self.active():
                return
            self.timer = None
            remaining = self.deadline - time.monotonic()
            if remaining > 0:
                self.schedule(remaining)
                return
            try:
                self.restore()
            except (EngineError, OSError, ValueError):
                # Keep ownership for an explicit recovery. Never repeat an
                # unconfirmed write on a timer. Native OFF still takes effect.
                self.app.audit('comparison_restore', 'unconfirmed')
                self.app.engine.fault = 'Ripristino del confronto da verificare. Disattiva l’equalizzatore per tornare all’ascolto normale.'

    def read_engine(self):
        ws = WebSocket(self.app.engine.port)
        try:
            return (json.loads(ws.command('GetConfigJson')), ws.command('GetState'), ws.command('GetMute'))
        finally:
            ws.close()

    def owns_config(self, live):
        # Exact equality includes normalized Camilla parameters which were not
        # explicit in our request (e.g. an externally bypassed pipeline step).
        # A write without a receipt also admits its uniquely tagged candidate,
        # so end can reconcile the command that lost its response.
        return ((self.confirmed_config is not None and self.confirmed_config == live)
                or (self.pending_readback and self.config is not None and Engine.matches(self.config, live)))

    def restore(self):
        if not self.active():
            return {'active': False, 'restored': False}
        app = self.app
        with engine_transaction(app.engine.port):
            live, _, _ = self.read_engine()
            if not self.owns_config(live):
                # Native bypass, a new stream, or another writer superseded the
                # audition. Do not restore an old snapshot over its work.
                self.clear()
                return {'active': False, 'restored': False}
            cfg = app.config_for_route(app.current_profile(), live)
            result = app.engine.apply(cfg)
            if not result.get('applied'):
                raise EngineError('Il ripristino dell’impostazione precedente non è confermato.')
        self.clear()
        return {'active': False, 'restored': True, 'muted': result.get('muted', False)}

    def playing_route(self):
        app = self.app
        state = app.audio.status() if app.audio else {}
        if not (state.get('processing') and state.get('mode') == 'pcm-active'
                and app.route and app.route.get('verified_pcm') is True):
            return None
        return app.route

    def request(self, data):
        action = data.get('comparison')
        fields = {'comparison', 'session', 'expected'}
        if action == 'listen':
            fields.add('profile')
            if 'rate' in data:
                fields.add('rate')
                if type(data['rate']) is not int or data['rate'] not in RATES:
                    raise ValidationError('La frequenza del brano di riferimento non è supportata.')
        if action not in ('listen', 'heartbeat', 'end') or set(data) != fields:
            raise ValidationError('Richiesta di confronto non valida.')
        try:
            owner = uuid.UUID(data['session']).hex
        except (AttributeError, TypeError, ValueError):
            raise ValidationError('Riapri il confronto per iniziare.') from None

        app = self.app
        with app.apply_lock:
            active = self.active()
            if self.closed:
                raise EngineError(self.session_finished)
            if action == 'end':
                if self.observer and self.observer[0] == owner:
                    self.observer = None
                if not active or self.owner != owner:
                    return {'active': False, 'restored': False}
                return self.restore()
            if active and self.owner != owner:
                raise ConflictError('È già aperto un confronto in un’altra finestra.')
            if action == 'heartbeat' and not active:
                raise ConflictError(self.session_finished)
            route = self.playing_route()
            if not route:
                raise ConflictError(self.playback_required)
            if self.observer is not None:
                raise ConflictError('Il lettore sta ancora verificando il confronto.')
            # End in another request must win even during the very first LMS
            # read, when the DSP is still untouched and no lease exists.
            observer = (owner, object())
            self.observer = observer

        try:
            return self.observe_and_audition(action, owner, data, route, observer)
        finally:
            with app.apply_lock:
                if self.observer is observer:
                    self.observer = None

    def observe_and_audition(self, action, owner, data, route, observer):
        app = self.app
        # LMS observation can wait on I/O. Never hold the native PCM guard
        # while it runs. Recheck the route and lease before touching filters.
        try:
            observed = app.audio.source_snapshot()
        except (OSError, RuntimeError, ValueError):
            raise EngineError('Il brano in riproduzione non è verificabile. Riprova tra poco.') from None
        is_playing = (observed.get('mode') == 'play'
                      and classify_source(observed.get('source'), observed.get('metadata'))['kind'] == 'PCM')

        with app.apply_lock, engine_transaction(app.engine.port):
            if self.closed or self.observer is not observer or self.playing_route() is not route:
                raise ConflictError(self.changed)
            if not is_playing:
                if self.owner == owner:
                    self.restore()
                raise ConflictError(self.playback_required)
            base = app.profile_state()
            if data['expected'] != base['profile_revision'] or (self.owner and self.expected != data['expected']):
                if self.owner == owner:
                    self.restore()
                raise ConflictError(self.changed)
            return self.audition(action, owner, base, data)

    def audition(self, action, owner, base, data):
        app = self.app
        live, state, muted = self.read_engine()
        if not live or state != 'Running' or muted is not False or app.engine.fault:
            if self.owner and (state == 'Paused' or muted is True) and not app.engine.fault:
                self.restore()
            raise ConflictError('È necessario un brano in riproduzione con il suono attivo.')
        rate = live['devices']['samplerate']
        if self.owner:
            if not self.owns_config(live):
                self.clear()
                raise ConflictError(self.changed)
            if not Engine.matches(self.config, live) or rate != self.config['devices']['samplerate']:
                self.restore()
                raise ConflictError(self.changed)
        if action == 'heartbeat':
            self.arm()
            return {'active': True, 'rate': rate}
        if 'rate' in data and data['rate'] != rate:
            if self.owner:
                self.restore()
            raise ConflictError('Il brano ha cambiato frequenza. Seleziona un estratto WAV alla nuova frequenza.')

        cfg = app.config_for_route(data['profile'], live)
        if cfg['devices']['samplerate'] != rate:
            raise ConflictError(self.changed)
        if not self.owner:
            baseline = app.config_for_route(base['profile'], live)
            # A new native stream can receive a generated ID for its initial
            # flat profile. Compare its actual processing, independent of name.
            if (not str(live.get('title', '')).startswith('Daphile PEQ ')
                    or not Engine.matches(baseline['filters'], live.get('filters'))
                    or not Engine.matches(baseline['pipeline'], live.get('pipeline'))
                    or not Engine.matches(baseline['devices'], live.get('devices'))):
                raise ConflictError('L’impostazione in uso è cambiata. Rileggi il profilo prima del confronto.')
            self.owner, self.route, self.expected = owner, app.route, base['profile_revision']
            self.tag = secrets.token_hex(12)
        cfg['title'] = 'Daphile PEQ confronto-' + self.tag + '-' + secrets.token_hex(4)
        # In case of an interrupted write, only these exact two states can
        # belong to us. A later end/expiry will first observe actual DSP state.
        self.config = cfg
        self.pending_readback = True
        self.arm()
        result = app.engine.apply(cfg)
        if not result.get('applied') or result.get('state') != 'Running' or result.get('muted') is not False:
            self.restore()
            raise ConflictError(self.playback_required)
        confirmed, state, muted = self.read_engine()
        if not Engine.matches(cfg, confirmed) or state != 'Running' or muted is not False:
            self.restore()
            raise ConflictError(self.changed)
        self.confirmed_config = confirmed
        self.pending_readback = False
        return {'active': True, 'applied': True, 'rate': rate, 'profile_revision': self.expected}

    def close(self):
        with self.app.apply_lock:
            self.closed = True
            try:
                self.restore()
            except (EngineError, OSError, ValueError):
                self.app.audit('comparison_restore', 'unconfirmed_shutdown')
            finally:
                self.clear()
