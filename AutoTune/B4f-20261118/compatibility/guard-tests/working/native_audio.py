"""Optional reversible PCM session; native DSD/DoP always bypasses Camilla."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import threading
import time

from native_route import NativeRoute,atomic_json
from native_player import NativePlayer,NativeIdentityNotReady,NativeTopologyUnavailable
from peq.model import new_profile,flat
from peq.routing import parse_hw_params
from peq.source_format import classify_source


class NativeSourceNotReady(RuntimeError):
    """Track or play state changed before any routing operation began."""


class NativeHandoffBlocked(RuntimeError):
    """The native decoder cannot be restarted without losing user state."""


class NativeAudio:
    def __init__(self,state,pipeline_factory,player=None,route=None):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
        self.player=player or NativePlayer();self.router=route or NativeRoute(self.state)
        self.pipeline_factory=pipeline_factory;self.pipeline=None;self.target=None
        self.lock=threading.RLock();self.stopping=threading.Event();self.app=None
        self.pcm_wake=threading.Event()
        self.control_wake=threading.Event()
        self.ledger=self.state/'native-audio-session.json'
        self.info=dict(mode='native-bypass',message='Percorso nativo; Camilla escluso dal segnale',rate=None)
        self.current=None
        self.identity_wait_since=None
        self.source_observation_deferred=False
        ram_config=Path('/mnt/hdd/system/daphile-ram/config.json')
        self.queue_lock=(Path(json.loads(ram_config.read_text())['state_root'])/'queue.lock') if ram_config.is_file() else None
        self.intent=self.state/'peq-intent.json'
        self.enabled=False;self.intent_pin=None;self.armed_target=None;self.epoch=0
        if self.intent.exists():
            saved=json.loads(self.intent.read_text())
            if set(saved)!={'version','enabled','target_pin'} or saved['version']!=1 or type(saved['enabled']) is not bool:
                raise RuntimeError('Preferenza equalizzatore non valida; nessuna attivazione automatica')
            self.enabled=saved['enabled'];self.intent_pin=saved['target_pin']
        if self.enabled:self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio',rate=None)

    def attach(self,app):self.app=app;app.apply_lock=self.lock

    def publish_route(self,route):
        if self.app:self.app.route=route

    def journal(self,phase):
        atomic_json(self.ledger,dict(version=1,phase=phase,target=self.target,
            boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))

    def event(self,name,**facts):
        record=dict(time=time.time(),event=name,**facts)
        fd=os.open(self.state/'native-audio-events.jsonl',os.O_APPEND|os.O_CREAT|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'a') as stream:stream.write(json.dumps(record)+'\n')

    def source(self,snapshot):return classify_source(snapshot.get('source'),snapshot.get('metadata'))

    def source_snapshot(self,target=None):
        target=self.target if target is None else target
        return self.player.current_source(target) if hasattr(self.player,'current_source') else self.player.snapshot(target)

    def profile(self):
        path=self.state/'active_profile.json'
        return json.loads(path.read_text()) if path.exists() else new_profile('Flat')

    def processing_profile(self,enabled=None):
        # OFF is a unity PCM path. Preserve the user's editable curve on disk.
        return self.profile() if (self.enabled if enabled is None else enabled) else flat()

    def switch_filters(self,enabled):
        """Switch a live PCM session without pause, seek, bind or restart."""
        if self.pipeline:
            self.player.guard(self.target)
            observed=self.player.fast_loopback_params(self.target)
            if observed!=self.current:
                self.stop_pipeline()
                self.info=dict(mode='waiting-pcm',message='In attesa del nuovo flusso PCM',rate=None)
            elif not self.pipeline.alive():
                raise RuntimeError('Pipeline PCM arrestata o DoP rilevato')
            else:
                from peq.model import compile_config
                from peq.buffering import apply_targets
                route=self.app.route
                cfg=compile_config(self.processing_profile(enabled),route['rate'],route['chunksize'],route['capture'],route['playback'])
                apply_targets(cfg,route)
                try:self.app.engine.apply(cfg)
                except RuntimeError:
                    self.stop_pipeline()
                    self.info=dict(mode='fault-paused',message='Commutazione filtri non confermata; recupero audio necessario.',rate=None)
                    raise
        try:
            atomic_json(self.intent,dict(version=1,enabled=enabled,target_pin=self.intent_pin))
        except OSError:
            self.stop_pipeline()
            self.info=dict(mode='fault-paused',message='Preferenza non salvata; percorso audio sospeso.',rate=None)
            raise
        self.epoch+=1;self.enabled=enabled
        if self.info['mode']=='pcm-active':
            self.info['message']='PCM elaborato da CamillaDSP' if enabled else 'Equalizzatore disattivato; PCM senza filtri né attenuazioni'
        self.control_wake.set()
        return self.status()

    @staticmethod
    def target_pin(target):
        return {key:target.get(key) for key in ('alsa_id','usb','mac','preferences_sha256')}

    def persist_intent(self):
        atomic_json(self.intent,dict(version=1,enabled=self.enabled,target_pin=self.intent_pin))

    def set_enabled(self,enabled):
        if type(enabled) is not bool:raise ValueError('Stato equalizzatore non valido')
        with self.lock:
            if self.info['mode'] in ('pcm-active','waiting-pcm') and self.ledger.exists():
                if enabled==self.enabled:return self.status()
                return self.switch_filters(enabled)
            if enabled and (self.ledger.exists() or self.router.record.exists()) and not self.enabled:
                raise RuntimeError('Recuperare prima il percorso audio interrotto')
            if enabled==self.enabled and (enabled or (not self.ledger.exists() and not self.router.record.exists())):return self.status()
            # OFF is committed only after the verified handoff. A later
            # topology refusal must not rewrite the preserved ON intent.
            if not enabled:return self.bypass(resume=True,disable=True)
            next_pin=None if enabled else self.intent_pin
            # The monitor must never see an enabled intent whose disk commit
            # failed. Commit the proposed record before changing live state.
            atomic_json(self.intent,dict(version=1,enabled=enabled,target_pin=next_pin))
            self.epoch+=1;self.enabled=enabled;self.intent_pin=next_pin
            self.control_wake.set()
            if enabled:self.armed_target=None
            if enabled:
                self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio',rate=None)
                # No playback command, route mutation or engine startup here.
                # The monitor observes a compatible *playing* source first.
                return self.status()

    def commit_disabled(self):
        atomic_json(self.intent,dict(version=1,enabled=False,target_pin=self.intent_pin))
        self.epoch+=1;self.enabled=False

    def step_armed(self):
        with self.lock:
            epoch=self.epoch;target=self.armed_target
            if not self.enabled or self.stopping.is_set():return
        try:
            if target is None:target=self.player.target()
            self.player.guard(target)
            observed=self.source_snapshot(target)
        except NativeIdentityNotReady:
            with self.lock:
                if self.epoch!=epoch or not self.enabled or self.stopping.is_set():return
                if self.info['mode'] in ('pcm-active','waiting-pcm') or self.ledger.exists() or self.router.record.exists():return
                if self.identity_wait_since is None:
                    self.identity_wait_since=time.monotonic();self.event('waiting-native-identity')
                self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa del player',rate=None)
            return
        except TimeoutError as error:
            if str(error) not in ('LMS source observation timed out','LMS source observation unavailable'):raise
            with self.lock:
                if self.epoch!=epoch or not self.enabled or self.stopping.is_set():return
                if not self.source_observation_deferred:self.event('source-observation-deferred')
                self.source_observation_deferred=True
                self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa del player',rate=None)
            return
        source=self.source(observed)
        with self.lock:
            if self.epoch!=epoch or not self.enabled or self.stopping.is_set():return
            if self.info['mode'] in ('pcm-active','waiting-pcm') or self.ledger.exists() or self.router.record.exists():return
            if self.source_observation_deferred:self.event('source-observation-restored')
            self.source_observation_deferred=False
            pin=self.target_pin(target)
            if self.intent_pin is not None and pin!=self.intent_pin:
                raise RuntimeError('Configurazione audio cambiata esternamente; nessuna sovrascrittura')
            if self.identity_wait_since is not None:
                self.event('native-identity-restored');self.identity_wait_since=None
            self.target=target;self.armed_target=target
            if self.intent_pin is None:
                atomic_json(self.intent,dict(version=1,enabled=self.enabled,target_pin=pin))
                self.intent_pin=pin
            if source['kind']=='DSD':
                self.info=dict(mode='dsd-bypass',message='Equalizzatore escluso per DSD; si riattiva automaticamente con PCM',rate=None)
            elif observed.get('mode')!='play':
                self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio',rate=None)
            elif source['kind']!='PCM':
                self.info=dict(mode='armed-unsupported',message='Equalizzatore escluso per questo formato',rate=None)
            else:
                try:self.start(expected_target=target)
                except NativeSourceNotReady:
                    self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio compatibile',rate=None)

    def expected_rate(self,snapshot):
        return self.player.expected_pcm_rate(self.target,snapshot) if hasattr(self.player,'expected_pcm_rate') else None

    def params(self):
        return self.player.loopback_params(self.target,self.router.base/'alsa-route.conf')

    def guard_loopback(self):
        if Path('/proc/asound/card0/id').read_text().strip()!='Loopback':raise RuntimeError('Loopback nativo non disponibile')
        for endpoint in ('pcm0p','pcm1c'):
            if Path('/proc/asound/card0',endpoint,'sub6/hw_params').read_text().strip()!='closed':
                raise RuntimeError('Canale Loopback riservato già occupato')

    def open_pipeline(self,params):
        self.player.guard(self.target)
        snapshot=self.source_snapshot()
        if self.source(snapshot)['kind']!='PCM':raise RuntimeError('Formato PCM non verificato; usare il bypass nativo')
        expected=self.expected_rate(snapshot)
        if expected is not None and params['rate']!=expected:raise RuntimeError('Frequenza nativa ancora in negoziazione')
        self.pipeline=self.pipeline_factory()
        playback=dict(type='Alsa',device='hw:'+self.target['alsa_id']+',0',channels=2,format='S32_LE')
        prepared=self.pipeline.start(params,playback,self.processing_profile())
        self.publish_route(prepared);self.current=params
        if self.app:self.app.engine.fault=None
        self.info=dict(mode='pcm-active',message='PCM elaborato da CamillaDSP' if self.enabled else 'Equalizzatore disattivato; PCM senza filtri né attenuazioni',rate=params['rate'])
        self.pcm_wake.set()
        self.event('pcm-active',rate=params['rate'])
        self.journal('pcm-active')

    @contextlib.contextmanager
    def queue_handoff(self):
        # RAM recovery briefly plays a muted track to reconstruct its paused
        # cursor. Do not interpret that internal PLAY as a user audio request.
        # Share its existing lock through our entire native handoff, so neither
        # controller can change transport beneath the other.
        if self.queue_lock is None:
            yield;return
        try:fd=os.open(self.queue_lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        except FileNotFoundError:
            raise NativeSourceNotReady('Ripristino della coda non ancora pronto; equalizzatore in attesa') from None
        try:
            try:fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:
                raise NativeSourceNotReady('Ripristino della coda in corso; equalizzatore in attesa') from None
            yield
        finally:os.close(fd)

    def start(self,expected_target=None):
        with self.lock,self.queue_handoff():
            if self.ledger.exists() or self.router.record.exists():raise RuntimeError('Sessione attiva o recupero necessario; usare Bypass nativo')
            # Switching this verified card restarts only its squeezelite, never
            # LMS or its scanner. A library scan must not disable normal PEQ
            # use. Full queue, volume, USB and preference guards still apply.
            self.target=self.player.target()
            if expected_target is not None and self.target_pin(self.target)!=self.target_pin(expected_target):
                raise RuntimeError('Configurazione audio cambiata esternamente; nessuna sovrascrittura')
            saved=self.player.snapshot(self.target)
            source=self.source(saved)
            if source['kind']=='DSD':raise NativeSourceNotReady('DSF/DSD: mantenere Bypass nativo. Nessuna conversione PCM autorizzata')
            if source['kind']!='PCM':raise NativeSourceNotReady('Formato sorgente non qualificato per il PEQ software')
            if saved['mode']!='play':raise NativeSourceNotReady('Avvia un brano PCM in Daphile, poi premi Avvia PEQ software')
            self.player.require_standalone(self.target)
            self.guard_loopback();self.journal('preparing')
            try:
                self.player.pause(self.target,saved)
                self.player.require_standalone(self.target)
                self.router.activate(self.target['alsa_id'],self.target['usb'])
                self.journal('router-bound')
                self.player.restart(self.target)
                self.player.resume(self.target,saved)
                deadline=time.monotonic()+8;stable=None;since=None
                while time.monotonic()<deadline:
                    params=self.params()
                    expected=self.expected_rate(self.source_snapshot())
                    if expected is not None and params is not None and params['rate']!=expected:params=None
                    if params!=stable:stable=params;since=time.monotonic()
                    if params is not None and since is not None and time.monotonic()-since>=.3:
                        self.open_pipeline(params);return self.status()
                    time.sleep(.03)
                raise RuntimeError('PCM nativo non pronto sul Loopback')
            except NativeTopologyUnavailable:
                self.topology_blocked()
                raise
            except BaseException:
                self.fail()
                raise

    def stop_pipeline(self):
        self.publish_route(None)
        if self.pipeline:self.pipeline.stop();self.pipeline=None
        self.current=None

    def topology_blocked(self):
        """Stop only our DSP; preserve intent, route journal and LMS state.

        fault-paused names this session's fault, not an observed LMS pause.
        """
        with self.lock:
            self.epoch+=1
            self.stop_pipeline()
            owned=self.ledger.exists() or self.router.record.exists()
            mode='fault-paused' if owned else ('armed-unsupported' if self.enabled else 'native-bypass')
            self.info=dict(mode=mode,message='Topologia LMS non indipendente o non verificata. '
                'Gruppo e preferenza ON/OFF conservati; nessun ulteriore comando di trasporto o ripristino.',rate=None)
            try:self.event('single-player-authority-unavailable',owned_route_retained=bool(owned))
            except OSError:pass

    def bypass(self,resume=True,disable=False):
        with self.lock:
            if not self.ledger.exists() and not self.router.record.exists():
                if disable:self.commit_disabled()
                self.stop_pipeline()
                self.info=dict(mode='native-bypass',message='Percorso nativo; Camilla escluso dal segnale',rate=None)
                return self.status()
            if self.target is None:
                if not self.ledger.exists():raise RuntimeError('Identità del recupero audio mancante')
                record=json.loads(self.ledger.read_text())
                if record.get('version')!=1:raise RuntimeError('Registro audio non riconosciuto')
                self.target=record['target']
            # A fault stops output even if LMS is unavailable. An ordinary
            # OFF first proves preservation, leaving all live state intact
            # when a native restart would destroy a pause/bookmark.
            if not resume:
                self.stop_pipeline()
                self.info=dict(mode='fault-paused',message='Percorso PCM arrestato; verifica il recupero da Daphile.',rate=None)
            if hasattr(self.player,'wait_ready'):self.player.wait_ready(self.target)
            current=self.player.snapshot(self.target)
            if not resume and current['mode']=='play':
                # Emergency silence may pause the owned player, but may not
                # turn that newly preserved pause into STOP/position zero.
                self.player.pause(self.target,current)
                current=self.player.snapshot(self.target)
            if current['mode']=='pause' or (current['mode']=='stop' and current['time']!=0):
                raise NativeHandoffBlocked('Disattivazione non eseguita: pausa e posizione del brano conservate. '
                    'Interrompi la riproduzione da Daphile oppure riprendila e riprova.')
            self.player.require_standalone(self.target)
            self.stop_pipeline()
            try:
                if current['mode']=='play':self.player.pause(self.target,current)
                self.journal('restoring-native')
                if self.router.record.exists():
                    self.player.require_standalone(self.target)
                    self.router.deactivate()
                self.player.restart(self.target)
                if resume:self.player.resume(self.target,current)
                self.player.require_standalone(self.target)
                if disable:self.commit_disabled()
                self.ledger.unlink(missing_ok=True)
            except NativeTopologyUnavailable:
                self.topology_blocked()
                raise
            except BaseException:
                self.info=dict(mode='fault-paused',message='Commutazione non completata; recupero del percorso audio necessario.',rate=None)
                raise
            self.info=dict(mode='native-bypass',message='Percorso nativo verificato; Camilla escluso dal segnale',rate=None)
            if self.app:self.app.engine.fault=None
            return self.status()

    def fail(self):
        with self.lock:
            self.stop_pipeline()
            if self.target is not None:
                try:self.player.require_standalone(self.target)
                except NativeTopologyUnavailable:
                    self.topology_blocked()
                    return
                except Exception:
                    # Other identity failures retain the existing fault path;
                    # its native guards still refuse an unowned player.
                    pass
            try:self.bypass(resume=False)
            except NativeTopologyUnavailable:
                self.topology_blocked()
                return
            except Exception:
                # A retained ledger prevents update/removal from hiding an
                # unresolved bind or child. No forced unmount or loud fallback.
                try:self.stop_pipeline()
                except Exception:pass
            # No OFF record is published before a later topology guard can
            # reject recovery. This is still a read/commit boundary, not CAS.
            if self.target is not None:
                try:self.player.require_standalone(self.target)
                except NativeTopologyUnavailable:
                    self.topology_blocked()
                    return
                except Exception:pass
            self.epoch+=1;self.enabled=False
            try:self.persist_intent()
            except OSError:
                # Removing the old ON record also means OFF on the next boot.
                # Do not leave a persisted ON after an unsuccessful disk write.
                try:self.intent.unlink(missing_ok=True)
                except OSError:
                    self.stop_pipeline();raise
                try:self.event('intent-off-persistence-failed')
                except OSError:pass
            self.info=dict(mode='fault-paused',message='Percorso PCM arrestato. Verifica il recupero con Bypass nativo prima di riprendere.',rate=None)

    def step(self):
        # LMS may stall independently of the audio engine. Do not hold the
        # status/apply lock during its bounded network read. A session changed
        # by bypass/start while that read was pending must discard the result.
        with self.lock:
            armed=self.enabled and self.info['mode'] not in ('pcm-active','waiting-pcm','fault-paused')
            if not armed and self.info['mode'] not in ('pcm-active','waiting-pcm'):return
            target=self.target
        if armed:return self.step_armed()
        snapshot_observed=False
        try:
            snapshot=self.source_snapshot(target)
            snapshot_observed=True
            with self.lock:
                if self.target is not target or self.info['mode'] not in ('pcm-active','waiting-pcm'):return
                result=self.step_checked(snapshot);self.identity_wait_since=None
                if self.source_observation_deferred:self.event('source-observation-restored')
                self.source_observation_deferred=False
                return result
        except TimeoutError as error:
            # Only an uncertain *read* can wait and recover automatically.
            # Pause/restart/resume and other writes remain fail-closed. Keep
            # the owned route and ON intent; stop DSP until a fresh source and
            # the local USB/preferences/PCM guards can be checked again.
            if snapshot_observed or str(error) not in ('LMS source observation timed out','LMS source observation unavailable'):raise
            with self.lock:
                if self.target is not target or self.info['mode'] not in ('pcm-active','waiting-pcm'):return
                self.stop_pipeline()
                self.info=dict(mode='waiting-pcm',message='Equalizzatore momentaneamente sospeso; riprende con il player',rate=None)
                if not self.source_observation_deferred:self.event('source-observation-deferred')
                self.source_observation_deferred=True
                self.journal('waiting-source-observation')
            return
        except NativeIdentityNotReady:
            with self.lock:
                if self.target is not target or self.info['mode'] not in ('pcm-active','waiting-pcm'):return
                if self.identity_wait_since is None:
                    self.identity_wait_since=time.monotonic();self.event('waiting-native-identity')
                self.stop_pipeline()
                self.info=dict(mode='waiting-pcm',message='Riavvio nativo del player in corso; DSP sospeso',rate=None)
                if time.monotonic()-self.identity_wait_since>15:
                    raise RuntimeError('Il player nativo non è tornato pronto; recupero conservato')

    def step_checked(self,snapshot=None):
        with self.lock:
            if self.info['mode'] not in ('pcm-active','waiting-pcm'):return
            self.player.guard(self.target)
            snapshot=self.source_snapshot() if snapshot is None else snapshot;source=self.source(snapshot)
            if source['kind']=='DSD':
                self.event('dsd-native-bypass')
                self.bypass(resume=True)
                self.info['message']='DSF/DSD in percorso nativo; Camilla escluso dal segnale'
                if self.enabled:
                    self.armed_target=self.target
                    self.info=dict(mode='dsd-bypass',message='Equalizzatore escluso per DSD; si riattiva automaticamente con PCM',rate=None)
                return
            if snapshot.get('source') is None and snapshot.get('mode')=='stop':
                self.stop_pipeline();self.info=dict(mode='waiting-pcm',message='In attesa di un brano PCM',rate=None)
                return
            if source['kind']!='PCM':raise RuntimeError('Nuova sorgente non qualificata PCM')
            params=self.params()
            expected=self.expected_rate(snapshot)
            if expected is not None and params is not None and params['rate']!=expected:params=None
            if params is None:
                if self.info['mode']!='waiting-pcm':
                    self.event('writer-closed')
                    self.stop_pipeline();self.info=dict(mode='waiting-pcm',message='In attesa del nuovo flusso PCM',rate=None)
                    self.journal('waiting-pcm')
                return
            if self.pipeline and not self.pipeline.alive():raise RuntimeError('Pipeline PCM arrestata o DoP rilevato')
            if params!=self.current:
                self.stop_pipeline();self.open_pipeline(params)

    def ready_for_apply(self,timeout=8):
        with self.lock:
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                self.step()
                if self.info['mode']=='pcm-active' and self.pipeline and self.pipeline.alive():return
                if self.info['mode']!='waiting-pcm':raise RuntimeError('Il PEQ richiede un percorso PCM attivo')
                time.sleep(.03)
            raise RuntimeError('Il nuovo flusso PCM non è pronto; nessun filtro applicato')

    def guard_pcm(self):
        with self.lock:
            if self.info['mode']!='pcm-active':return
            observed=self.player.fast_loopback_params(self.target) if hasattr(self.player,'fast_loopback_params') else self.params()
            if observed!=self.current:
                self.event('local-writer-closed-or-changed')
                self.stop_pipeline()
                self.info=dict(mode='waiting-pcm',message='Flusso PCM terminato o cambiato; DSP sospeso',rate=None)
                self.journal('waiting-pcm')

    def monitor_pcm(self):
        # Source/format RPC may take seconds; ALSA playback buffers can drain
        # in milliseconds. This local guard never waits for the LMS server.
        self.event('local-guard-started');observed_session=None
        while not self.stopping.is_set():
            with self.lock:active=self.info['mode']=='pcm-active'
            if not active:
                observed_session=None;self.pcm_wake.wait(.5);self.pcm_wake.clear();continue
            if self.stopping.wait(.002):break
            with self.lock:
                target=self.target
                if self.current is not observed_session:
                    observed_session=self.current;self.event('local-guard-observing',rate=self.info['rate'])
            try:self.guard_pcm()
            except Exception as error:self.monitor_error(target,error)

    def monitor(self):
        while not self.stopping.is_set():
            self.control_wake.wait(.5);self.control_wake.clear()
            if self.stopping.is_set():break
            with self.lock:epoch=self.epoch
            try:self.step()
            except Exception as error:
                with self.lock:
                    if epoch==self.epoch:self.monitor_error(self.target,error)

    def monitor_error(self,target,error):
        with self.lock:
            if self.target is not target or (not self.enabled and self.info['mode'] not in ('pcm-active','waiting-pcm')):return
            if isinstance(error,NativeTopologyUnavailable):
                self.topology_blocked()
                return
            known=(
                'LMS source observation timed out',
                'DAC rimosso, sostituito o rinumerato',
                'Configurazione audio cambiata esternamente; nessuna sovrascrittura',
                'Identità del player cambiata esternamente',
                'Coda audio non acquisibile integralmente',
                'Stato di riproduzione non verificato',
                'Volume del player non osservabile',
                'Nuova sorgente non qualificata PCM',
                'Pipeline PCM arrestata o DoP rilevato',
                'Proprietario del flusso PCM non verificato',
                'Il flusso PCM appartiene a un altro programma',
                'Il flusso PCM appartiene a un altro DAC',
                'Il player non usa il routing PCM privato verificato',
                'Coda modificata esternamente; intervento annullato',
                'Coda modificata durante la pausa',
                'Pausa del player non confermata',
                'Coda modificata; ripresa non eseguita',
                'Coda modificata durante la ripresa',
                'Volume cambiato durante la commutazione; nessuna sovrascrittura',
                'Ripresa del player non confermata',
                'Riavvio nativo della scheda non confermato',
                'Il player nativo non è tornato pronto; recupero conservato',
                'Daphile non è configurato per DSD nativo/DoP; ripresa DSF annullata senza conversione PCM',
                'Formato DSD nativo non esposto dal DAC USB; scegliere DoP in Daphile',
                'Frequenza nativa ancora in negoziazione')
            try:self.event('monitor-error',error_type=type(error).__name__,
                reason=str(error) if str(error) in known else 'unclassified-owned-route-error',
                child_exit_codes=[child.poll() for child in getattr(self.pipeline,'children',[])])
            except OSError:pass
            self.fail()


    def status(self):
        with self.lock:
            return dict(self.info,active=self.info['mode']=='pcm-active',
                        enabled=self.enabled,processing=self.enabled and self.info['mode']=='pcm-active',
                        recovery_required=self.info['mode']=='fault-paused' and (self.ledger.exists() or self.router.record.exists()),
                        dsd_policy='Native/DoP bypass only; no DSD-to-PCM conversion')

    def close(self):
        self.stopping.set()
        self.pcm_wake.set()
        self.control_wake.set()
        with self.lock:
            if self.ledger.exists() or self.router.record.exists():self.bypass(resume=False)
            else:self.stop_pipeline()
