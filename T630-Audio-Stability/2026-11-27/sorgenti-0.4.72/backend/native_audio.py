"""Optional reversible PCM session; native DSD/DoP always bypasses Camilla."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import threading
import time

from native_route import NativeRoute,atomic_json
from datc_dac_lease import DacTransitionLease,DacTransitionBusy
from datc_recovery_records import read_owned,validate_ledger
from datc_pcm_effect import execute_pcm_transition,PCMCommitError,execute_pcm_release,PCMReleaseError,EffectAuthority,EffectDeadlineExceeded
from native_player import NativePlayer,NativeIdentityNotReady,NativeTopologyUnavailable
from peq.model import new_profile,flat
from peq.routing import parse_hw_params
from peq.source_format import classify_source
from datc_policy import Action,source_action,route_action,local_action,timeout_action,armed_action,intent_action,verify_native_snapshot,control_needs_work


class NativeSourceNotReady(RuntimeError):
    """Track or play state changed before any routing operation began."""


class NativeHandoffBlocked(RuntimeError):
    """The native decoder cannot be restarted without losing user state."""


class NativeAudio:
    def __init__(self,state,pipeline_factory,player=None,route=None,dac_lease=None):
        self.state=Path(state);self.state.mkdir(parents=True,exist_ok=True)
        self.player=player or NativePlayer();self.router=route or NativeRoute(self.state)
        self.pipeline_factory=pipeline_factory;self.pipeline=None;self.target=None
        self.lock=threading.RLock();self.stopping=threading.Event();self.app=None
        self.startup_snapshot=None
        self.dac_lease=dac_lease if dac_lease is not None else DacTransitionLease()
        self.pcm_wake=threading.Event()
        self.control_wake=threading.Event()
        self.ledger=self.state/'native-audio-session.json'
        self.info=dict(mode='native-bypass',message='Percorso nativo; Camilla escluso dal segnale',rate=None)
        self.current=None
        self.handoff=None
        self.waiting_key=None
        self.effects=EffectAuthority()
        self.observation_sequence=0
        self.accepted_observation=0
        self.telemetry_failures=0
        self.identity_wait_since=None
        self.source_observation_deferred=False
        self.lms_observation_grace_since=None
        ram_config=Path('/mnt/hdd/system/daphile-ram/config.json')
        self.queue_lock=(Path(json.loads(ram_config.read_text())['state_root'])/'queue.lock') if ram_config.is_file() else None
        self.intent=self.state/'peq-intent.json'
        self.enabled=False;self.intent_pin=None;self.armed_target=None;self.epoch=0;self.stream_generation=0
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

    def note(self,name,**facts):
        """Non-authoritative telemetry; a log failure must not change audio."""
        try:self.event(name,**facts)
        except OSError:
            self.telemetry_failures=min(self.telemetry_failures+1,2147483647)

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
            if self.stopping.is_set():raise RuntimeError('Servizio audio in arresto')
            ledger=self.ledger.exists();route=self.router.record.exists()
            action=intent_action(enabled,self.enabled,self.info['mode'],ledger,route)
            if action is Action.KEEP:return self.status()
            if action is Action.FILTERS:return self.switch_filters(enabled)
            if action is Action.REJECT:raise RuntimeError('Recuperare prima il percorso audio interrotto')
            # OFF is committed only after the verified handoff.
            if action is Action.BYPASS:return self.bypass(resume=True,disable=True)
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

    def issue_observation(self):
        # Called under the single session lock.
        self.observation_sequence+=1
        return self.observation_sequence

    def accept_observation(self,sequence):
        # An earlier RPC result (including a fault) cannot overtake a newer
        # accepted result in the same generation.
        if sequence<=self.accepted_observation:return False
        self.accepted_observation=sequence
        return True

    def armed_observation_current(self,token):
        return (token==(self.epoch,self.stream_generation) and self.enabled
                and not self.stopping.is_set()
                and self.info['mode'] not in ('pcm-active','waiting-pcm','fault-paused')
                and not any(os.path.lexists(p) for p in (self.ledger,self.router.record,self.state/'audio-processes.json')))

    def step_armed(self,token=None,dispatch_errors=False):
        with self.lock:
            if token is None:token=(self.epoch,self.stream_generation)
            if not self.armed_observation_current(token):return
            target=self.armed_target;sequence=self.issue_observation()
        observed=None;error=None
        try:
            if target is None:target=self.player.target()
            self.player.guard(target)
            observed=self.source_snapshot(target)
        except Exception as exc:error=exc
        with self.lock:
            if not self.armed_observation_current(token) or not self.accept_observation(sequence):return
            try:
                if isinstance(error,NativeIdentityNotReady):
                    if self.identity_wait_since is None:
                        self.identity_wait_since=time.monotonic();self.note('waiting-native-identity')
                    self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa del player',rate=None)
                    return
                if isinstance(error,TimeoutError) and str(error) in ('LMS source observation timed out','LMS source observation unavailable'):
                    if not self.source_observation_deferred:self.note('source-observation-deferred')
                    self.source_observation_deferred=True
                    self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio',rate=None)
                    return
                if error is not None:raise error
                source=self.source(observed)
                if self.source_observation_deferred:self.note('source-observation-restored')
                self.source_observation_deferred=False
                pin=self.target_pin(target)
                if self.intent_pin is not None and pin!=self.intent_pin:
                    raise RuntimeError('Configurazione audio cambiata esternamente; nessuna sovrascrittura')
                if self.identity_wait_since is not None:
                    self.note('native-identity-restored');self.identity_wait_since=None
                self.target=target;self.armed_target=target
                if self.intent_pin is None:
                    atomic_json(self.intent,dict(version=1,enabled=self.enabled,target_pin=pin))
                    self.intent_pin=pin
                action=armed_action(source['kind'],observed.get('mode'))
                if action is Action.BYPASS:
                    self.info=dict(mode='dsd-bypass',message='Equalizzatore escluso per DSD; si riattiva automaticamente con PCM',rate=None)
                elif action is Action.WAIT:
                    self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio',rate=None)
                elif action is Action.REJECT:
                    self.info=dict(mode='armed-unsupported',message='Equalizzatore escluso per questo formato',rate=None)
                else:
                    try:self.start(expected_target=target)
                    except (NativeSourceNotReady,DacTransitionBusy):
                        self.info=dict(mode='armed-idle',message='Equalizzatore attivato, in attesa di audio compatibile',rate=None)
            except Exception as exc:
                # start/fail or topology recovery may already have reconciled this intent.
                if dispatch_errors and self.epoch!=token[0]:return
                return self._dispatch_fault(exc,dispatch_errors)


    def expected_rate(self,snapshot):
        return self.player.expected_pcm_rate(self.target,snapshot) if hasattr(self.player,'expected_pcm_rate') else None

    def params(self):
        return self.player.loopback_params(self.target,self.router.base/'alsa-route.conf')

    def guard_loopback(self):
        if Path('/proc/asound/card0/id').read_text().strip()!='Loopback':raise RuntimeError('Loopback nativo non disponibile')
        for endpoint in ('pcm0p','pcm1c'):
            if Path('/proc/asound/card0',endpoint,'sub6/hw_params').read_text().strip()!='closed':
                raise RuntimeError('Canale Loopback riservato già occupato')

    def open_pipeline(self,params,outer_deadline=None):
        self.stream_generation+=1
        self.player.guard(self.target)
        snapshot=self.source_snapshot()
        if self.source(snapshot)['kind']!='PCM':raise RuntimeError('Formato PCM non verificato; usare il bypass nativo')
        expected=self.expected_rate(snapshot)
        if expected is not None and params['rate']!=expected:raise RuntimeError('Frequenza nativa ancora in negoziazione')
        fresh=self.pipeline is None
        if self.stopping.is_set():raise PCMCommitError('Servizio audio in arresto')
        token=self.effects.issue(self.epoch,self.stream_generation,"pcm-commit",outer_deadline)
        try:
            if fresh:self.pipeline=self.pipeline_factory()
            elif not self.pipeline.suspended:raise RuntimeError('Old PCM session still running')
        except BaseException:
            self.effects.finish(token)
            raise
        # Native target identity is checked again immediately before unmute.
        target=self.target;pipeline=self.pipeline
        def preflight():
            if self.stopping.is_set() or self.target is not target or self.pipeline is not pipeline:
                raise RuntimeError('Transizione PCM superata o servizio in arresto')
            self.effects.check(token,self.epoch,self.stream_generation,'pcm-commit')
        def fence():
            preflight()
            if not pipeline.alive() or self.player.fast_loopback_params(target)!=params:
                raise RuntimeError('Sorgente PCM cambiata prima del commit')
            try:self.dac_lease.assert_held()
            except DacTransitionBusy as exc:raise PCMCommitError('Lock RME perso durante la transizione PCM') from exc
        pipeline.output_guard=lambda:self.player.guard(target)
        pipeline.commit_note=self.note
        playback=dict(type='Alsa',device='hw:'+target['alsa_id']+',0',channels=2,format='S32_LE')
        try:
            def check_lease():
                try:self.dac_lease.assert_held()
                except DacTransitionBusy as exc:raise PCMCommitError('Lock RME non verificato durante preparazione PCM') from exc
            def prepare():
                return (pipeline.start if fresh else pipeline.reconfigure)(params,playback,self.processing_profile(),token)
            prepared=execute_pcm_transition(prepare,pipeline.commit_output,fence,self.journal,check_lease,params,playback,token,preflight)
            self.publish_route(prepared)
            if self.app:self.app.engine.fault=None
            fence()
            self.current=params.copy()
            self.waiting_key=None
            self.info=dict(mode='pcm-active',message='PCM elaborato da CamillaDSP' if self.enabled else 'Equalizzatore disattivato; PCM senza filtri né attenuazioni',rate=params['rate'])
            if self.handoff is not None:self.handoff.committed()
            self.pcm_wake.set()
            self.control_wake.set()
        except BaseException as exc:
            self.note('pcm-commit-failed',error_type=type(exc).__name__,reason=str(exc)[:240])
            self.info=dict(mode='fault-paused',message='Commit PCM non confermato; recupero conservato.',rate=None)
            self.stop_pipeline()
            raise
        finally:self.effects.finish(token)
        self.note('pcm-active',rate=params['rate'])

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

    def _collect_previous_handoff(self):
        if self.handoff is None:return
        from pcm_handoff_runtime import HandoffRetirementPending
        try:self.handoff.verify_retired()
        except HandoffRetirementPending:
            raise NativeSourceNotReady('Attesa chiusura percorso PCM precedente') from None
        self.handoff=None

    def _begin_handoff(self,target):
        from pcm_handoff_runtime import HandoffRuntime
        runtime=HandoffRuntime(self,target,self.router.base/'alsa-route.conf')
        socket=runtime.start()
        self.handoff=runtime
        return socket

    def _retire_handoff(self):
        if self.handoff is not None:self.handoff.begin_stop()

    def start(self,expected_target=None):
        with self.lock:
            if self.stopping.is_set():raise RuntimeError('Servizio audio in arresto')
            if self.info['mode']!='armed-idle' or not self.enabled:
                return self._start_transaction(expected_target)
            started=time.monotonic()
            initial=self.status()
            times=[]
            def phase(name):
                times.append((name,time.monotonic()-started))
                self.startup_snapshot=dict(initial,transition_phase=name,
                    message='Preparazione del percorso PCM in corso')
            try:
                phase('target')
                return self._start_transaction(expected_target,phase)
            finally:
                self.startup_snapshot=None
                if any(name=='pause' for name,_ in times):
                    self.note('initial-pcm-timing',phases=times,total_s=time.monotonic()-started)

    def _start_transaction(self,expected_target=None,phase=lambda name:None):
        with self.queue_handoff(),self.dac_lease.hold():
            self.stream_generation+=1
            if any(os.path.lexists(p) for p in (self.ledger,self.router.record,self.state/'audio-processes.json')):raise RuntimeError('Sessione attiva o recupero necessario; usare Bypass nativo')
            # Switching this verified card restarts only its squeezelite, never
            # LMS or its scanner. A library scan must not disable normal PEQ
            # use. Full queue, volume, USB and preference guards still apply.
            self.target=self.player.target()
            if expected_target is not None and self.target_pin(self.target)!=self.target_pin(expected_target):
                raise RuntimeError('Configurazione audio cambiata esternamente; nessuna sovrascrittura')
            phase('queue-snapshot')
            saved=self.player.snapshot(self.target)
            source=self.source(saved)
            if source['kind']=='DSD':raise NativeSourceNotReady('DSF/DSD: mantenere Bypass nativo. Nessuna conversione PCM autorizzata')
            if source['kind']!='PCM':raise NativeSourceNotReady('Formato sorgente non qualificato per il PEQ software')
            if saved['mode']!='play':raise NativeSourceNotReady('Avvia un brano PCM in Daphile, poi premi Avvia PEQ software')
            self.player.require_standalone(self.target)
            self.guard_loopback()
            self._collect_previous_handoff()
            target=self.target
            token=self.effects.issue(self.epoch,self.stream_generation,'native-prepare')
            def fence():
                self.effects.check(token,self.epoch,self.stream_generation,'native-prepare')
                if self.target is not target or self.stopping.is_set():
                    raise PCMCommitError('Preparazione nativa superata o servizio in arresto')
                try:self.dac_lease.assert_held()
                except DacTransitionBusy as exc:raise PCMCommitError('Lock RME perso durante preparazione nativa') from exc
            try:
                fence()
                self.journal('preparing')
                phase('pause')
                self.player.pause(target,saved)
                fence()
                self.player.require_standalone(target)
                phase('bind-router')
                socket=self._begin_handoff(target)
                self.router.activate(target['alsa_id'],target['usb'],6,socket)
                fence()
                self.journal('router-bound')
                fence()
                phase('restart')
                restarted=self.player.restart(target)
                fence()
                self.verify_restart(target,restarted)
                fence()
                phase('resume')
                observed=self.player.resume(target,saved)
                fence()
                verify_native_snapshot(saved,observed)
                self.verify_restart(target,restarted)
                phase('loopback-stability')
                deadline=time.monotonic()+8;stable=None;since=None
                while time.monotonic()<deadline:
                    fence()
                    params=self.params()
                    expected=self.expected_rate(self.source_snapshot())
                    if expected is not None and params is not None and params['rate']!=expected:params=None
                    if params!=stable:stable=params;since=time.monotonic()
                    if params is not None and since is not None and time.monotonic()-since>=.3:
                        fence()
                        self.verify_restart(target,restarted)
                        self.effects.finish(token)
                        phase('open-pipeline')
                        self.open_pipeline(params,outer_deadline=token.deadline);return self.status()
                    time.sleep(.03)
                raise RuntimeError('PCM nativo non pronto sul Loopback')
            except NativeTopologyUnavailable:
                self._retire_handoff()
                self.topology_blocked()
                raise
            except BaseException:
                self._retire_handoff()
                self.fail()
                raise
            finally:self.effects.finish(token)

    def verify_restart(self,target,restarted):
        if (not isinstance(restarted,tuple) or len(restarted)!=2 or
                type(restarted[0]) is not int or restarted[0]<=1 or
                not isinstance(restarted[1],str) or not restarted[1].isdigit() or
                self.player.player_process(target)!=restarted):
            raise PCMCommitError('Identita processo nativo non confermata dopo riavvio')

    def _release_pipeline(self,warm):
        self.stream_generation+=1
        self.waiting_key=None
        publish_error=None
        try:self.publish_route(None)
        except Exception as exc:publish_error=exc
        try:
            if self.pipeline:
                operation='pcm-suspend' if warm else 'pcm-stop'
                token=self.effects.issue(self.epoch,self.stream_generation,operation)
                pipeline=self.pipeline
                try:
                    execute_pcm_release(pipeline,warm,token)
                    # Never reject a successful terminal stop solely because it
                    # was slow; retain truthful cleanup and record its lateness.
                    self.effects.check(token,self.epoch,self.stream_generation,operation,allow_expired=not warm)
                    if self.pipeline is not pipeline:raise PCMReleaseError('Pipeline cambiata durante rilascio')
                    if not warm:
                        self.pipeline=None
                        if self.effects.clock()>=token.deadline:self.note('pcm-stop-budget-exceeded')
                finally:self.effects.finish(token)
            elif os.path.lexists(self.state/'audio-processes.json'):
                raise PCMReleaseError('Registro processi senza proprietario; recupero necessario')
            self.current=None
            if publish_error is not None:
                raise PCMReleaseError('Pubblicazione del rilascio non confermata') from publish_error
        except BaseException:
            self.info=dict(mode='fault-paused',message='Rilascio PCM non confermato; recupero conservato.',rate=None)
            raise

    def suspend_pipeline(self):
        self._release_pipeline(True)

    def stop_pipeline(self):
        self._release_pipeline(False)

    def _enter_wait(self,phase,message,warm):
        """Coalesce duplicate observations without dropping a new effect."""
        already_released=(self.current is None and
            ((self.pipeline is None and not os.path.lexists(self.state/'audio-processes.json'))
             or (warm and self.pipeline is not None and self.pipeline.suspended)))
        if not already_released:
            (self.suspend_pipeline if warm else self.stop_pipeline)()
        key=(self.epoch,self.stream_generation,phase,message)
        if self.waiting_key!=key:
            try:self.journal(phase)
            except Exception:
                self.info=dict(mode='fault-paused',message='Registro attesa PCM non confermato; recupero conservato.',rate=None)
                self.stop_pipeline()
                raise
            self.waiting_key=key
        self.info=dict(mode='waiting-pcm',message=message,rate=None)

    def topology_blocked(self):
        """Stop only our DSP; preserve intent, route journal and LMS state.

        fault-paused names this session's fault, not an observed LMS pause.
        """
        with self.lock:
            self.epoch+=1
            self._retire_handoff()
            self.stop_pipeline()
            owned=self.ledger.exists() or self.router.record.exists()
            mode='fault-paused' if owned else ('armed-unsupported' if self.enabled else 'native-bypass')
            self.info=dict(mode=mode,message='Topologia LMS non indipendente o non verificata. '
                'Gruppo e preferenza ON/OFF conservati; nessun ulteriore comando di trasporto o ripristino.',rate=None)
            try:self.note('single-player-authority-unavailable',owned_route_retained=bool(owned))
            except OSError:pass

    def bypass(self,resume=True,disable=False):
        with self.lock:
            if resume and self.stopping.is_set():raise RuntimeError('Servizio audio in arresto')
            if self.ledger.exists() or self.router.record.exists():
                # Emergency silence precedes any lease attempt. Transport
                # recovery, including emergency recovery, shares the RAM guard.
                if not resume:
                    self.stop_pipeline()
                    self.info=dict(mode='fault-paused',message='Percorso PCM arrestato; verifica il recupero da Daphile.',rate=None)
                with self.queue_handoff(),self.dac_lease.hold():
                    return self._bypass_transaction(resume,disable)
            return self._bypass_transaction(resume,disable)

    def _bypass_transaction(self,resume,disable):
        with self.lock:
            if not self.ledger.exists() and not self.router.record.exists():
                self._retire_handoff()
                self.stop_pipeline()
                if disable:self.commit_disabled()
                self.info=dict(mode='native-bypass',message='Percorso nativo; Camilla escluso dal segnale',rate=None)
                self.control_wake.set()
                return self.status()
            if self.target is None:
                if not self.ledger.exists():raise RuntimeError('Identità del recupero audio mancante')
                ledger=read_owned(self.ledger)
                if ledger is None:raise RuntimeError('Registro audio non riconosciuto')
                self.target=validate_ledger(ledger.value)['target']
            # A fault stops output even if LMS is unavailable. An ordinary
            # OFF first proves preservation, leaving all live state intact
            # when a native restart would destroy a pause/bookmark.
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
            # Read configured native DSD policy, never infer wire format.
            # Preserve existing Daphile settings even if the native server
            # converts DSD to PCM. Camilla itself remains excluded.
            dsd_policy=None
            if resume and current['mode']=='play' and self.source(current)['kind']=='DSD':
                dsd_policy=self.player.configured_dsd_source_transport(self.target)
            self._retire_handoff()
            self.stop_pipeline()
            target=self.target
            token=self.effects.issue(self.epoch,self.stream_generation,'native-handoff')
            def fence():
                self.effects.check(token,self.epoch,self.stream_generation,'native-handoff')
                if self.target is not target or self.pipeline is not None or (resume and self.stopping.is_set()):
                    raise PCMCommitError('Transizione nativa superata o servizio in arresto')
                try:self.dac_lease.assert_held()
                except DacTransitionBusy as exc:raise PCMCommitError('Lock RME perso durante ripristino nativo') from exc
            try:
                fence()
                if current['mode']=='play':self.player.pause(self.target,current)
                fence()
                self.journal('restoring-native')
                fence()
                if self.router.record.exists():
                    self.player.require_standalone(self.target)
                    fence()
                    self.router.deactivate()
                self.router.verify_restored()
                fence()
                restarted=self.player.restart(target)
                fence()
                self.verify_restart(target,restarted)
                fence()
                observed=self.player.resume(target,current) if resume else self.player.snapshot(target)
                fence()
                verify_native_snapshot(current,observed)
                if dsd_policy is not None and self.player.configured_dsd_source_transport(self.target)!=dsd_policy:
                    raise RuntimeError('Politica DSD nativa cambiata durante il ripristino')
                self.player.require_standalone(self.target)
                self.router.verify_restored()
                if self.player.player_process(target)!=restarted:
                    raise PCMCommitError('Processo nativo cambiato durante il ripristino')
                fence()
                if disable:self.commit_disabled()
                self.ledger.unlink(missing_ok=True)
            except NativeTopologyUnavailable:
                self.topology_blocked()
                raise
            except BaseException:
                self.info=dict(mode='fault-paused',message='Commutazione non completata; recupero del percorso audio necessario.',rate=None)
                raise
            finally:self.effects.finish(token)
            self.info=dict(mode='native-bypass',message='Percorso nativo verificato; Camilla escluso dal segnale',rate=None)
            if dsd_policy is not None:
                self.info['native_dsd_transport_policy']=dsd_policy
                self.info['native_dsd_wire_verified']=False
            if self.app:self.app.engine.fault=None
            self.control_wake.set()
            return self.status()

    def fail(self):
        with self.lock:
            self._retire_handoff()
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
                try:self.note('intent-off-persistence-failed')
                except OSError:pass
            self.info=dict(mode='fault-paused',message='Percorso PCM arrestato. Verifica il recupero con Bypass nativo prima di riprendere.',rate=None)

    def observation_current(self,target,token):
        return (self.target is target and token==(self.epoch,self.stream_generation)
                and not self.stopping.is_set() and self.info["mode"] in ("pcm-active","waiting-pcm"))

    def _dispatch_fault(self,error,dispatch_errors):
        # Caller holds the session lock. Do not reacquire after a generation change.
        if not dispatch_errors:raise error
        if not self.stopping.is_set():self.monitor_error(self.target,error)

    def _identity_wait_locked(self):
        if self.info['mode'] not in ('pcm-active','waiting-pcm'):return
        if self.identity_wait_since is None:
            self.identity_wait_since=time.monotonic();self.note('waiting-native-identity')
        self._enter_wait('waiting-pcm','Riavvio nativo del player in corso; DSP sospeso',False)
        if time.monotonic()-self.identity_wait_since>15:
            raise RuntimeError('Il player nativo non è tornato pronto; recupero conservato')

    def _lms_timeout_locked(self,target,error):
        if str(error) not in ('LMS source observation timed out','LMS source observation unavailable'):raise error
        now=time.monotonic()
        can_hold=(self.info['mode']=='pcm-active' and self.pipeline is not None
                  and self.pipeline.alive() and self.current is not None)
        if can_hold:
            self.player.guard(target)
            can_hold=self.player.fast_loopback_params(target)==self.current
        if timeout_action(can_hold,self.lms_observation_grace_since,now) is Action.KEEP:
            if self.lms_observation_grace_since is None:
                self.lms_observation_grace_since=now
                self.note('lms-observation-grace-started',local_pcm_verified=True)
            if not self.source_observation_deferred:self.note('source-observation-deferred')
            self.source_observation_deferred=True
            return
        if self.lms_observation_grace_since is not None:
            self.note('lms-observation-grace-ended',reason='timeout-or-local-route-change')
        self.lms_observation_grace_since=None
        self._enter_wait('waiting-source-observation','Equalizzatore sospeso: sorgente LMS non verificabile; riprende con il player',False)
        if not self.source_observation_deferred:self.note('source-observation-deferred')
        self.source_observation_deferred=True

    def step(self,dispatch_errors=False):
        with self.lock:
            if self.stopping.is_set():return
            armed=self.enabled and self.info['mode'] not in ('pcm-active','waiting-pcm','fault-paused')
            if not control_needs_work(self.enabled,self.info['mode']):return
            target=self.target;token=(self.epoch,self.stream_generation)
            sequence=self.issue_observation() if not armed else None
        if armed:return self.step_armed(token,dispatch_errors)
        snapshot=None;error=None
        try:snapshot=self.source_snapshot(target)
        except Exception as exc:error=exc
        with self.lock:
            if not self.observation_current(target,token) or not self.accept_observation(sequence):return
            try:
                if isinstance(error,NativeIdentityNotReady):return self._identity_wait_locked()
                if isinstance(error,TimeoutError):return self._lms_timeout_locked(target,error)
                if error is not None:raise error
                try:result=self.step_checked(snapshot)
                except NativeIdentityNotReady:return self._identity_wait_locked()
                self.identity_wait_since=None
                if self.source_observation_deferred:self.note('source-observation-restored')
                self.source_observation_deferred=False
                self.lms_observation_grace_since=None
                return result
            except Exception as exc:return self._dispatch_fault(exc,dispatch_errors)

    def step_checked(self,snapshot=None):
        with self.lock:
            if self.info['mode'] not in ('pcm-active','waiting-pcm'):return
            self.player.guard(self.target)
            snapshot=self.source_snapshot() if snapshot is None else snapshot;source=self.source(snapshot)
            action=source_action(source['kind'],snapshot.get('source'),snapshot.get('mode'))
            if action is Action.BYPASS:
                try:
                    native_status=self.bypass(resume=True)
                except (DacTransitionBusy,NativeSourceNotReady):
                    # Never send DSD to the PCM path while waiting for RME.
                    self._enter_wait('waiting-rme','Commutazione DSD in attesa della risorsa RME',False)
                    return
                self.note('dsd-native-bypass')
                self.info['message']='DSF/DSD in percorso nativo; Camilla escluso dal segnale'
                if self.enabled:
                    self.armed_target=self.target
                    self.info=dict(mode='dsd-bypass',message='Equalizzatore escluso per DSD; si riattiva automaticamente con PCM',rate=None)
                    if isinstance(native_status,dict) and 'native_dsd_transport_policy' in native_status:
                        self.info.update(native_dsd_transport_policy=native_status['native_dsd_transport_policy'],native_dsd_wire_verified=False)
                return
            if action is Action.WAIT:
                self._enter_wait('waiting-pcm','In attesa di un brano PCM',True)
                return
            if action is Action.REJECT:raise RuntimeError('Nuova sorgente non qualificata PCM')
            params=self.params()
            expected=self.expected_rate(snapshot)
            qualified=params is not None and (expected is None or params['rate']==expected)
            alive=not qualified or self.pipeline is None or self.pipeline.alive()
            action=route_action(self.info['mode'],self.current,params,expected,alive)
            if action is Action.SUSPEND:
                self.note('writer-closed')
                self._enter_wait('waiting-pcm','In attesa del nuovo flusso PCM',True)
            elif action is Action.FAULT:raise RuntimeError('Pipeline PCM arrestata o DoP rilevato')
            elif action is Action.REOPEN:
                # Release old-rate output even if the control lease is busy.
                if self.current is not None or (self.pipeline is not None and not self.pipeline.suspended):
                    self.suspend_pipeline()
                try:
                    with self.dac_lease.hold():self.open_pipeline(params)
                except DacTransitionBusy:
                    self._enter_wait('waiting-rme','Commutazione PCM in attesa della risorsa RME',True)

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
            if local_action(self.current,observed) is Action.SUSPEND:
                self.note('local-writer-closed-or-changed')
                self._enter_wait('waiting-pcm','Flusso PCM terminato o cambiato; DSP sospeso',True)

    @staticmethod
    def pcm_guard_interval(rate):
        """Bound monitoring delay below one common ALSA capture period.
        High-rate audio retains the previous 2 ms behavior.
        """
        if type(rate) is not int or rate<=0:return .002
        if rate>=352800:return .002
        if rate>=176400:return .003
        if rate>=88200:return .004
        return .006

    def monitor_pcm(self):
        # Source/format RPC may take seconds; ALSA playback buffers can drain
        # in milliseconds. This local guard never waits for the LMS server.
        self.note('local-guard-started');observed_session=None
        while not self.stopping.is_set():
            with self.lock:
                active=self.info['mode']=='pcm-active'
                rate=self.info.get('rate')
                if not active:
                    # Clear under the same lock as the PCM commit notification.
                    self.pcm_wake.clear()
                    if self.stopping.is_set():break
            if not active:
                observed_session=None;self.pcm_wake.wait();continue
            if self.stopping.wait(self.pcm_guard_interval(rate)):break
            with self.lock:
                if self.stopping.is_set():break
                if self.info['mode']!='pcm-active':continue
                target=self.target
                if self.current is not observed_session:
                    observed_session=self.current;self.note('local-guard-observing',rate=self.info['rate'])
                # Keep the observed failure and its recovery in the same
                # serialized session; a reused target is not a lease.
                try:self.guard_pcm()
                except Exception as error:self.monitor_error(target,error)

    def monitor(self):
        while not self.stopping.is_set():
            with self.lock:
                mode=self.info['mode']
                idle_waiting=mode=='waiting-pcm'
                work=control_needs_work(self.enabled,mode)
                if not work:
                    self.control_wake.clear()
                    if self.stopping.is_set():break
            if not work:
                self.control_wake.wait()
                continue
            self.control_wake.wait(.25 if idle_waiting else .5);self.control_wake.clear()
            if self.stopping.is_set():break
            self.step(dispatch_errors=True)

    def monitor_error(self,target,error):
        with self.lock:
            if self.target is not target or (not self.enabled and self.info['mode'] not in ('pcm-active','waiting-pcm')):return
            if isinstance(error,NativeTopologyUnavailable):
                try:self.topology_blocked()
                except Exception as exc:self.recovery_fault(exc)
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
            try:self.note('monitor-error',error_type=type(error).__name__,
                reason=str(error) if str(error) in known else 'unclassified-owned-route-error',
                child_exit_codes=[child.poll() for child in getattr(self.pipeline,'children',[])])
            except Exception:pass  # Diagnostic observation is not recovery authority.
            try:self.fail()
            except Exception as exc:
                self.recovery_fault(exc)

    def recovery_fault(self,error):
        # A failure in cleanup must not silently kill the monitor or re-arm
        # another route. Retain all owned objects and journals for explicit recovery.
        self.info=dict(mode='fault-paused',message='Recupero audio non confermato; intervento necessario.',rate=None,
                       recovery_fault=type(error).__name__)
        self.note('recovery-fault',error_type=type(error).__name__)


    def status(self):
        with self.lock:
            return dict(self.info,active=self.info['mode']=='pcm-active',
                        enabled=self.enabled,processing=self.enabled and self.info['mode']=='pcm-active',
                        recovery_required=self.info['mode']=='fault-paused' and (self.info.get('recovery_fault') is not None or self.pipeline is not None or any(os.path.lexists(p) for p in (self.state/'audio-processes.json',self.ledger,self.router.record))),
                        telemetry_failures=self.telemetry_failures,
                        dsd_policy='Native/DoP bypass only; no DSD-to-PCM conversion')

    def close(self):
        self.stopping.set()
        self.pcm_wake.set()
        self.control_wake.set()
        try:
            with self.lock:
                self._retire_handoff()
                if self.ledger.exists() or self.router.record.exists():self.bypass(resume=False)
                else:self.stop_pipeline()
        finally:
            if self.handoff is not None:
                if self.handoff.reaper is None:self.handoff.begin_stop()
                self.handoff.reaper.join()
                self.handoff.verify_retired()
