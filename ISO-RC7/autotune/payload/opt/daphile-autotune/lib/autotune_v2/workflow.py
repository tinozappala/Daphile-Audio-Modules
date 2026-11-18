"""Native AutoTune V2 entry point with one uninterrupted recovery owner.

The activity lease owns all writes, including a successful commit. Its owner
is never rewritten to make it look automatic. A configured record is accepted
only together with the exact completed lease and committed transaction. Death
before that boundary leaves the independent lease responsible for rollback.
"""
import contextlib,copy,json,os,re,time,types,uuid
from pathlib import Path
from . import paired_search,search_reference,reference_migration,search_scope
from .controlled_workload import Workload,native_api as workload_api,append_capacity,replaceable,standalone
from .native_adapter import NativeAdapter,ProcessorContextChanged,native_api as adapter_api
from .acceptance import proposal
try:from playback_contract import PreparationRequired, CURSOR_TOLERANCE_SECONDS, preparation_requirements, same_state
except ImportError:from ..playback_contract import PreparationRequired, CURSOR_TOLERANCE_SECONDS, preparation_requirements, same_state
try:from measurement_contract import confirmation_issues
except ImportError:from ..measurement_contract import confirmation_issues
try:import calibration_budget
except ImportError:from .. import calibration_budget

KIND='automatic-configuration-v2'
FINAL={'retained-current','configured','failed-restored','inconclusive-restored','restored','cancelled-before-activity-no-effects'}


def completion_outcome(changed,search):
    if changed:
        return dict(status='configured',optimization_outcome='verified-runtime-configuration',
                    comparison_outcome='alternatives-compared',calibration_qualified=True,
                    message='Configurazione misurata applicata. Playlist ripristinata.')
    screened=search.get('screening') or []
    if not screened and (search.get('planning') or {}).get('available_families'):
        return dict(status='inconclusive-restored',optimization_outcome='reference-only-budget',
                    comparison_outcome='reference-only-budget',calibration_qualified=False,
                    message='Riferimento misurato e playlist ripristinata. Il tempo disponibile non ha consentito '
                            'di confrontare alternative: calibrazione non conclusa, impostazioni conservate.')
    if not screened:
        return dict(status='retained-current',optimization_outcome='no-applicable-alternative',
                    comparison_outcome='no-applicable-alternative',calibration_qualified=False,
                    message='Riferimento misurato e playlist ripristinata. Nessuna regolazione alternativa '
                            'applicabile alla configurazione osservata; impostazioni conservate.')
    complete=search.get('complete_comparisons',0)
    if type(complete) is not int or complete<=0:
        return dict(status='inconclusive-restored',optimization_outcome='no-complete-alternative-comparison',
                    comparison_outcome='alternative-measurements-rejected',calibration_qualified=False,
                    message='Le misure delle alternative non hanno superato tutti i controlli. '
                            'Playlist ripristinata e impostazioni conservate; calibrazione non conclusa.')
    return dict(status='retained-current',optimization_outcome='retained-current',
                comparison_outcome='alternatives-compared',calibration_qualified=True,
                message='Verifica completata. Le impostazioni attuali sono conservate: nessun miglioramento ripetibile dimostrato.')


def native_api():
    import engine,player,session,automatic,power_review,audio_setup
    from detection import live_scan as scan
    from .native_target import resolve,rate
    from . import persistence
    return types.SimpleNamespace(engine=engine,player=player,scan=scan,Lease=session.Lease,
        Workload=Workload,workload_api=workload_api,Adapter=NativeAdapter,adapter_api=adapter_api,
        external_trial=automatic.external_trial,power_report=power_review.report,audio_report=audio_setup.report,
        search=paired_search.run,publish=search_reference.publish_inactive_policy,uid=os.geteuid,
        reference_id=lambda root:search_reference.ReferenceStore(Path(root)/'reference-evidence').head_id(),
        owner_lock=owner_lock,now=time.time,pid=os.getpid,target=resolve,rate=rate,policy=persistence)


@contextlib.contextmanager
def owner_lock(root):
    import fcntl
    path=Path(root)/'automatic-owner.lock'
    if path.is_symlink():raise RuntimeError('Indirect automatic owner lock refused')
    with path.open('a') as stream:
        os.chmod(path,0o600);fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def load(root,name,api):
    path=Path(root)/name
    if path.is_symlink():raise RuntimeError('Indirect workflow state refused')
    return api.engine.load(path) if path.exists() else {}


def store(root,record,api):
    current=load(root,'automatic.json',api)
    if current.get('id')!=record['id']:raise RuntimeError('Newer automatic owner preserved')
    api.engine.atomic(Path(root)/'automatic.json',record)


def public(record):
    keys=('kind','id','status','phase','message','percent','created','finished','sound_quality_claim',
          'optimization_outcome','comparison_outcome','interruption_reason','baseline_retries','restore_available','settings_changed','maximum_seconds','calibration_started',
          'calibration_deadline','work_deadline','recovery_reserve_seconds','summary','steps','runtime_summary',
          'power_inventory','recovery_errors','recovery_available','persistence','optimization_scope',
          'budget_protocol','calibration_monotonic_started','work_monotonic_deadline','calibration_monotonic_deadline',
          'calibration_finished_monotonic','recovery_supervision','worker_supervision','completion_timing_persisted','calibration_qualified','hardware_qualified','playback_restored','cancellation_guard_quiescent')
    result={k:copy.deepcopy(v) for k,v in record.items() if k in keys}
    if 'maximum_seconds' in record:result['timing']=calibration_budget.timing(record,record.get('finished'))
    return result


def completion_timing(root,record,api):
    """Timestamp the completed lease, including its final guard cleanup.

    Only metadata is written after that barrier. Failure of this optional
    timing write cannot roll back an already completed transaction or replay
    its playlist; the returned result explicitly reports missing persistence.
    """
    record.update(finished=api.now(),calibration_finished_monotonic=time.monotonic(),
                  completion_timing_persisted=True,deadline_guard_released=True)
    try:store(root,record,api)
    except (OSError,RuntimeError,calibration_budget.BudgetExpired):
        record.update(finished=api.now(),calibration_finished_monotonic=time.monotonic(),
                      completion_timing_persisted=False)
    return public(record)


def linked_activity(root,record,api):
    activity=load(root,'activity-session.json',api)
    if not activity:return {}
    exact=(activity.get('kind')=='automatic-v2' and activity.get('parent_session_owner')=={'kind':'automatic','id':record['id']}
           and activity.get('boot_id')==record.get('boot_id') and activity.get('owner')==record.get('owner_pid')
           and activity.get('start')==record.get('owner_start'))
    if record.get('activity_session_id') and activity.get('id')!=record['activity_session_id']:exact=False
    if record.get('budget_protocol') is not None:
        timing={key:record.get(key) for key in calibration_budget.KEYS}
        if (activity.get('budget')!=timing or activity.get('deadline')!=record.get('calibration_deadline')
                or activity.get('owner_pid_namespace')!=record.get('budget_pid_namespace')):exact=False
    if not exact:raise RuntimeError('Activity lease does not belong to this automatic run')
    return activity


def recovery_completed(root,record,api):
    """Observe a finished recovery for this exact abandoned automatic run.

    A queue-only result cannot discharge the full-player recovery contract.
    The predicate writes no journal, repairs no state and replays no command;
    callers may show completion or begin a separate explicitly requested run.
    """
    with api.engine.locked(root):
        if (load(root,'automatic.json',api)!=record or record.get('kind')!=KIND
                or record.get('status') not in ('running','recovering','recovery-required','timed-out-recovery-required')):
            return False
        if (record.get('boot_id')!=api.engine.boot_id()
                or type(record.get('owner_pid')) is not int or record['owner_pid']<=0
                or not isinstance(record.get('owner_start'),str) or not re.fullmatch(r'[1-9][0-9]*',record['owner_start'])
                or not isinstance(record.get('activity_session_id'),str) or not record['activity_session_id']
                or not isinstance(record.get('original_playback'),dict) or not record['original_playback']):
            return False
        activity=linked_activity(root,record,api)
        if (activity.get('status') not in ('recovered','completed')
                or activity.get('playback')!=record['original_playback']):
            return False
        recovery=activity.get('playback_recovery')
        if (not isinstance(recovery,dict) or recovery.get('restored') is not True
                or recovery.get('queue_restored') is False):
            return False
        if api.engine.start_ticks(record['owner_pid'])==record['owner_start']:
            return False
        api.engine.ensure_idle_transaction(root)
        # Cooperating writers share this lock. Also reject a changed receipt
        # observed during the native idle checks; never reconcile a newer run.
        return (load(root,'automatic.json',api)==record
                and load(root,'activity-session.json',api)==activity)


def guard_lease(root,expected,engine):
    """Called under the native state lock immediately before V2 lease finish."""
    latest=engine.load(Path(root)/'activity-session.json')
    keys=('id','kind','owner','start','boot_id','parent_session_owner','playback','budget','deadline','owner_pid_namespace')
    if any(latest.get(k)!=expected.get(k) for k in keys) or latest.get('status')!='running':
        raise RuntimeError('A different or recovering activity lease must be preserved')
    automatic=engine.load(Path(root)/'automatic.json')
    if automatic.get('kind')!=KIND or latest.get('parent_session_owner')!={'kind':'automatic','id':automatic.get('id')} or automatic.get('activity_session_id')!=latest['id']:
        raise RuntimeError('Automatic/activity completion link changed')
    if automatic.get('owner_pid')!=latest['owner'] or automatic.get('owner_start')!=latest['start'] or automatic.get('boot_id')!=latest['boot_id']:
        raise RuntimeError('Automatic/activity process identity differs')
    if automatic.get('budget_protocol') is not None:
        timing={key:automatic.get(key) for key in calibration_budget.KEYS}
        if (latest.get('budget')!=timing or latest.get('deadline')!=automatic.get('calibration_deadline')
                or latest.get('owner_pid_namespace')!=automatic.get('budget_pid_namespace')):
            raise RuntimeError('Original automatic/activity deadline differs at completion')


def commit_state(root,record,api,completed=False,*,for_restore=False):
    if type(for_restore) is not bool or (for_restore and not completed):
        raise ValueError('Owned restore requires an already completed policy')
    activity=linked_activity(root,record,api)
    if not activity or activity.get('status')!=('completed' if completed else 'running'):
        raise RuntimeError('Exact recovery lease completion required')
    owner={'kind':'activity','id':activity['id']}
    journals=[load(root,n,api) for n in ('transaction.json','current.json','last-known-good.json')]
    journal=journals[0]
    if not journal or any(j!=journal for j in journals) or journal.get('status')!='committed' or journal.get('profile')!='automatic':
        raise RuntimeError('Identical committed journals required')
    if not journal.get('changes') or journal.get('session_owner')!=owner or journal.get('boot_id')!=record['boot_id']:
        raise RuntimeError('Nonempty commit belongs to a different recovery owner')
    if journal.get('id')!=record.get('transaction_id') or journal.get('selected_policy')!=record.get('selected_policy'):
        raise RuntimeError('Commit differs from the accepted policy')
    if journal.get('workflow_v2')!=dict(run_id=record['id'],activity_id=activity['id'],protocol='activity-completion-v1'):
        raise RuntimeError('Explicit automatic/activity acceptance link required')
    if not for_restore:
        if proposal(record['search'])!=journal['selected_policy']:
            raise RuntimeError('Recorded policy no longer has a qualified repeated proposal')
        pair=journal.get('benchmark',{})
        if not isinstance(pair,dict):pair={}
        issues=confirmation_issues(pair.get('before'),pair.get('after'))
        if issues:raise RuntimeError('Recorded policy lacks a qualified fresh final pair: '+'; '.join(issues))
    return journal


def choose_rate(hardware):
    rates=hardware.get('dac',{}).get('pcm_rates')
    # scan exposes declared ALSA rates through playback_caps on supported
    # builds; never guess them from a model name or a current song suffix.
    if rates is None:
        import matrix
        rates=[row['rate'] for row in matrix.inventory(hardware)['entries'] if row['kind']=='PCM']
    if not isinstance(rates,(list,tuple,set)):raise RuntimeError('Declared DAC PCM rates unavailable')
    rates={int(v) for v in rates if type(v) in (int,str) and str(v).isdigit()}
    for rate in (192000,96000,48000,44100):
        if rate in rates:return rate
    raise RuntimeError('No qualified controlled PCM rate declared')


def prepare_state(root,h,api):
    """Retire only a fully proved historical empty result, never a real policy."""
    def idle():
        api.engine.ensure_session_owner(root);api.player.assert_library_idle(api.scan())
        if api.external_trial():raise RuntimeError('Another audio qualification owns the device')
    idle();old=load(root,'automatic.json',api)
    if (old.get('status') in ('running','recovering','recovery-required','timed-out-recovery-required')
            and not recovery_completed(root,old,api)):
        raise RuntimeError('Complete existing automatic recovery first')
    folder=root/'reference-retirements'
    if folder.is_symlink():raise RuntimeError('Indirect retirement root refused')
    if folder.exists():
        stages=list(folder.iterdir())
        if len(stages)>1000:raise RuntimeError('Retirement history exceeds bounded inspection')
        for stage in stages:
            manifest=json.loads(reference_migration.read(stage/'manifest.json'))
            if not manifest.get('transaction_pointer_detached') and manifest.get('boot')==api.engine.boot_id():
                # complete/detach CAS the full set, original owner and private
                # backups; neither may adopt a newer configured result.
                if not manifest.get('current_pointer_detached'):
                    reference_migration.detach_current(stage,api.engine.boot_id(),h['system']['product_uuid'],api.engine.locked,idle)
                reference_migration.detach_transaction(stage,api.engine.boot_id(),h['system']['product_uuid'],api.engine.locked,idle)
    current=load(root,'current.json',api)
    if current.get('status')=='committed' and current.get('changes')==[]:
        result=reference_migration.retire(root,api.engine.boot_id(),h['system']['product_uuid'],api.engine.locked,idle)
        reference_migration.detach_current(result['private_backup'],api.engine.boot_id(),h['system']['product_uuid'],api.engine.locked,idle)
        reference_migration.detach_transaction(result['private_backup'],api.engine.boot_id(),h['system']['product_uuid'],api.engine.locked,idle)
    with api.engine.locked(root):api.engine.ensure_idle_transaction(root)


def run(state,confirmed,resuming,experimental,budget,api=None):
    api=api or native_api();root=Path(state)
    if api.uid()!=0 or not confirmed:raise RuntimeError('Root and explicit audio-test consent required')
    if resuming:raise RuntimeError('V2 never replays an interrupted test or old playlist after reboot')
    with api.engine.locked(root):pass
    with api.owner_lock(root):
        h=api.scan();api.player.assert_library_idle(h)
        if api.external_trial():raise RuntimeError('Another audio qualification owns the device')
        target=api.target(h) if hasattr(api,'target') else api.player.target(h)
        original=api.player.playback_snapshot(target['mac'])
        replaceable(original)
        work_api=api.workload_api()
        topology=standalone(original['mac'],api.player.rpc,getattr(work_api,'verify_restore',None))
        capacity=append_capacity(api.player.rpc,len(original['urls']))
        prepare_state(root,h,api)
        reference_head=api.reference_id(root) if hasattr(api,'reference_id') else None
        record=dict(kind=KIND,id=uuid.uuid4().hex,status='running',phase='preflight',percent=5,created=api.now(),
            message='Preparo una sorgente controllata e verifico il sistema.',sound_quality_claim=False,
            boot_id=api.engine.boot_id(),owner_pid=api.pid(),owner_start=api.engine.start_ticks(api.pid()),
            target=target,original_playback=original,original_preferences=Path(target['path']).read_text(),
            append_capacity_before_lease=capacity,sync_before_lease=topology,
            binding=dict(host_uuid=h['system']['product_uuid'],hardware_id=h['hardware_id'],kernel=h['kernel']['release']),
            settings_changed=False,restore_available=False,steps=[],persistence=dict(status='not-qualified',enabled=False))
        record.update(budget.record);api.engine.atomic(root/'automatic.json',record)
        lease=None;workload=None;adapter=None;search={};policy_prepared=False
        try:
            budget.require(300)
            lease=api.Lease(root,original,max(1,budget.remaining(True)),'automatic-v2')
            record['activity_session_id']=lease.record['id'];store(root,record,api)
            workload=api.Workload(root,dict(id=lease.record['id'],original_playback=original),work_api)
            fixture=workload.create(target,api.rate(h,target) if hasattr(api,'rate') else choose_rate(h))
            budget.require(240)
            workload.select()
            budget.require(180)
            adapter=api.Adapter(root,fixture,api.adapter_api())
            if hasattr(adapter,'bind_observation_budget'):adapter.bind_observation_budget(budget)
            binding=adapter.binding()
            actual=api.scan()
            if actual['system']['product_uuid']!=record['binding']['host_uuid'] or actual['kernel']['release']!=record['binding']['kernel']:
                raise RuntimeError('Host or kernel changed while selecting controlled PCM')
            record['binding']['hardware_id']=actual['hardware_id']
            record.update(phase='search',percent=15,message='Confronto CPU, interrupt, priorità e risparmio energetico con misure ripetute.')
            record['power_inventory']=api.power_report(api.scan());store(root,record,api)
            def progress(value):
                record['search']=value
                record['baseline_retries']=getattr(adapter,'baseline_retry_count',0)
                active=value.get('active_candidate')
                if active and value.get('status')=='running':
                    record.update(percent=None,message='Confronto '+str(value.get('comparison_started',1))+': '+
                        ('riferimento attuale' if active.get('stage')=='reference' else str(active.get('name')))+
                        '. Misuro riferimento, alternativa e riferimento ripristinato.')
                record['optimization_scope']=search_scope.describe(value,fixture)
                record['summary']=search_scope.summary(record['optimization_scope'])
                store(root,record,api)
            search=api.search(adapter.inventory,adapter,budget,seconds=10,progress=progress)
            # The final search result is authoritative even when no progress
            # callback was emitted. Persistence must never depend on a UI hook.
            progress(copy.deepcopy(search))
            if adapter.binding()!=binding:raise RuntimeError('Context changed after temporary restoration')
            if search.get('status')=='retained-current':
                publication={'expected_reference_id':reference_head} if hasattr(api,'reference_id') else {}
                record['reference_publication']=api.publish(root,search,binding,adapter.binding,True,**publication)
                record['optimization_outcome']='retained-current'
            elif search.get('status')=='measured-proposal':
                selected=proposal(search);budget.require(100)
                record.update(phase='apply',percent=82,message='Verifico ancora la combinazione misurata prima di conservarla.')
                store(root,record,api)
                result=adapter.commit(selected,30,binding)
                if result.get('status')!='committed':raise RuntimeError('Final application did not commit measured controls')
                with api.engine.locked(root):
                    journal=load(root,'current.json',api)
                    owner={'kind':'activity','id':lease.record['id']}
                    if journal.get('session_owner')!=owner or journal.get('status')!='committed' or not journal.get('changes'):
                        raise RuntimeError('Final commit ownership is not verified')
                    if load(root,'transaction.json',api)!=journal or load(root,'last-known-good.json',api)!=journal:
                        raise RuntimeError('Concurrent committed journal change')
                    journal['selected_policy']=selected
                    journal['workflow_v2']=dict(run_id=record['id'],activity_id=lease.record['id'],protocol='activity-completion-v1')
                    for name in ('transaction.json','current.json','last-known-good.json'):api.engine.atomic(root/name,journal)
                    record.update(transaction_id=journal['id'],selected_policy=selected,
                                  settings_changed=True,optimization_outcome='verified-runtime-configuration')
                    store(root,record,api);commit_state(root,record,api)
            else:raise RuntimeError('Search did not complete its restoration and verification')
            budget.recovery();record.update(phase='recovery',percent=95,message='Ripristino la playlist e controllo il risultato.');store(root,record,api)
            record['playback_recovery']=workload.restore();workload.cleanup()
            if record['playback_recovery'].get('restored') is not True:
                raise RuntimeError('Full player state restoration required; a restored queue alone is insufficient')
            if adapter and hasattr(adapter,'close'):adapter.close()
            # This publication does not disarm the independent activity guard.
            # Until activity status is completed it rolls back even this commit.
            record.update(phase='complete',
                percent=100,finished=api.now(),calibration_finished_monotonic=time.monotonic(),restore_available=record['settings_changed'],
                **completion_outcome(record['settings_changed'],search))
            store(root,record,api)
            if record['settings_changed'] and hasattr(api,'policy'):
                api.policy.prepare(root,record)
                policy_prepared=True
                record['persistence']=dict(enabled=True,**api.policy.start(root))
                store(root,record,api)
            budget.require(1)
            lease.finish()
            if record['settings_changed']:commit_state(root,record,api,completed=True)
            lease=None
        except BaseException as error:
            budget.recovery();record.update(status='recovering',phase='recovery',message='Prova interrotta: recupero delle impostazioni.',
                                            private_error=type(error).__name__+': '+str(error))
            if isinstance(error,ProcessorContextChanged):record['interruption_reason']=error.public_reason
            store(root,record,api);errors=[]
            try:
                if policy_prepared:api.policy.disable(root,record['id'])
            except BaseException as recovery:errors.append('Policy continuation release: '+type(recovery).__name__+': '+str(recovery))
            try:
                if lease:
                    with api.engine.locked(root):
                        recovered=api.engine.recover_owned_transaction(root,{'kind':'activity','id':lease.record['id']})
                        if recovered.get('status') not in ('rolled-back','no-change'):raise RuntimeError('Settings recovery incomplete')
                    if workload:
                        record['playback_recovery']=workload.restore();workload.cleanup()
                        if record['playback_recovery'].get('restored') is not True:
                            raise RuntimeError('Full player state restoration incomplete')
                    if load(root,'activity-session.json',api).get('status')!='completed':lease.finish()
                    lease=None
                if Path(target['path']).read_text()!=record['original_preferences']:raise RuntimeError('Original audio preferences differ')
            except BaseException as recovery:errors.append(type(recovery).__name__+': '+str(recovery))
            if lease is None:
                # A constructor can publish its recovery journal and then
                # fail before returning the Lease object to this caller.
                activity=load(root,'activity-session.json',api)
                ours=(activity.get('parent_session_owner')=={'kind':'automatic','id':record['id']}
                    and activity.get('owner')==record['owner_pid']
                    and activity.get('start')==record['owner_start']
                    and activity.get('boot_id')==record['boot_id'])
                if ours and (activity.get('status') not in ('completed','recovered')
                        or activity.get('playback_recovery',{}).get('restored') is not True):
                    errors.append(activity.get('recovery_error') or 'Independent activity recovery is incomplete')
            try:
                if adapter and hasattr(adapter,'close'):adapter.close()
            except BaseException as recovery:errors.append('RT claim release: '+type(recovery).__name__+': '+str(recovery))
            record.update(status='recovery-required' if errors else 'failed-restored',settings_changed=False,
                restore_available=False,recovery_available=bool(errors),recovery_errors=errors,finished=api.now(),
                calibration_finished_monotonic=time.monotonic(),percent=100,
                deadline_guard_released=not errors,completion_timing_persisted=True,
                message='Prova interrotta. È necessario verificare il recupero.' if errors else
                        'Configurazione non applicata. Parametri e playlist recuperati; la prova non ha fornito un risultato completo.')
            if not errors and record.get('interruption_reason'):
                record['message']=record['interruption_reason']+' Playlist recuperata; ripeti la prova con impostazioni stabili.'
            store(root,record,api)
            if (errors and adapter is None and not policy_prepared and not search
                    and isinstance(error,RuntimeError) and str(error)==
                    'Queue or track changed during audio startup; calibration refused'):
                from . import external_intent
                try:
                    record=external_intent.cancel(root,api)
                    lease=None
                except (OSError,RuntimeError,ValueError,KeyError,TypeError):
                    # Original independent recovery keeps EX/DENY on any
                    # unproved effect or partially published cancellation.
                    pass
            return public(record)
        # The rollback region ends at the durable, verified activity barrier.
        # This timestamp includes lease.finish rather than preceding it.
        return completion_timing(root,record,api)


def _owned_restore_state(root,record,api):
    """Observe exact completed undo ownership, without qualifying performance.

    Status may offer an explicit undo after this read-only check. Restoration
    repeats it under the existing owner/state locks before any mutation.
    Neither caller can use this result to promote or reapply a policy.
    """
    saved=load(root,'v2-policy.json',api)
    continuation=hasattr(api,'policy') and saved.get('enabled') is True and saved.get('run_id')==record.get('id')
    if record.get('status')!='configured' or (record.get('boot_id')!=api.engine.boot_id() and not continuation):
        raise RuntimeError('Only a completed current-boot V2 policy can be restored')
    # Native ensure_session_owner only reads the activity/A-B journals. An
    # archived completed policy cannot make a different live lease undoable.
    api.engine.ensure_session_owner(root)
    journal=(api.policy.current_journal(root,record,api.policy.native_api(),for_restore=True) if continuation
             else commit_state(root,record,api,completed=True,for_restore=True))
    if journal.get('boot_id')!=api.engine.boot_id():raise RuntimeError('Current-boot policy verification required before undo')
    hardware=api.scan()
    if hardware['system']['product_uuid']!=record['binding']['host_uuid']:raise RuntimeError('Different host: no replay')
    return journal,saved,continuation,hardware


def status(state,api=None):
    api=api or native_api();root=Path(state);record=load(root,'automatic.json',api)
    if record.get('kind')!=KIND:raise RuntimeError('V2 automatic record required')
    result=public(record)
    policy=load(root,'v2-policy.json',api)
    policy_enabled=hasattr(api,'policy') and policy.get('enabled') is True and policy.get('run_id')==record.get('id')
    if record.get('boot_id')!=api.engine.boot_id() and not policy_enabled:
        result.update(status='historical-result',restore_available=False,
            message='Risultato di un avvio precedente. Nessuna playlist o prova viene riavviata automaticamente.')
        return result
    if record.get('status') in ('recovery-required','timed-out-recovery-required'):
        try:
            if recovery_completed(root,record,api):
                result.update(status='failed-restored',settings_changed=False,restore_available=False,
                    recovery_available=False,recovery_errors=[],
                    message='Sessione interrotta; il recupero completo è verificato. Puoi eseguire una nuova prova.')
        except (RuntimeError,OSError,ValueError,KeyError):pass
        return result
    if record.get('status') not in ('configured','running','recovering'):return result
    if record.get('status')=='configured':
        try:
            journal=api.policy.current_journal(root,record,api.policy.native_api()) if policy_enabled else commit_state(root,record,api,completed=True)
            h=api.scan()
            if journal.get('boot_id')!=api.engine.boot_id():
                continuation_status=api.policy.status(root)
                latest=continuation_status.get('latest',{})
                if latest.get('status')=='already-current-observed' and latest.get('boot_id')==api.engine.boot_id():
                    result.update(status='retained-current',restore_available=False,settings_changed=False,
                        message='Le impostazioni misurate sono già presenti. Nessuna nuova modifica applicata.',persistence=continuation_status)
                    return result
                result.update(status='awaiting-policy-verification',restore_available=False,
                    message='Configurazione salvata. La verifica riprende quando sarà disponibile audio compatibile.')
                result['persistence']=continuation_status
                return result
            for change in journal['changes']:
                api.engine.guard(change,h)
                if api.engine.setting_value(change)!=change['after']:raise RuntimeError('Runtime readback differs')
            result['active_configuration']=dict(verified=True,transaction_id=journal['id'])
        except (RuntimeError,OSError,ValueError,KeyError):
            activity=load(root,'activity-session.json',api)
            result.update(status='recovery-required' if activity.get('status') in ('running','recovering','recovery-required') else 'historical-configured',
                restore_available=False,message='Configurazione corrente non verificata. Il risultato precedente resta disponibile come riferimento.')
            try:
                owned,_,_,hardware=_owned_restore_state(root,record,api)
                for change in owned['changes']:
                    api.engine.guard(change,hardware,restoring=True)
                    if api.engine.setting_value(change) not in (change['before'],change['after']):
                        raise RuntimeError('External control value must be preserved')
            except (RuntimeError,OSError,ValueError,KeyError):
                pass
            else:
                result.update(restore_available=True,
                    message='Configurazione corrente non qualificata. È disponibile il ripristino delle impostazioni precedenti, previa nuova verifica delle identità.')
    elif record.get('status') in ('running','recovering'):
        activity=linked_activity(root,record,api)
        alive=api.engine.start_ticks(record.get('owner_pid'))==record.get('owner_start')
        if not alive:
            if activity.get('status') in ('recovered','completed'):
                try:complete=recovery_completed(root,record,api)
                except (RuntimeError,OSError,ValueError,KeyError):complete=False
                if complete:
                    result.update(status='failed-restored',settings_changed=False,restore_available=False,
                        recovery_available=False,recovery_errors=[],
                        message='Sessione interrotta; recupero completo verificato senza riprendere la riproduzione.')
                else:
                    result.update(status='recovery-required',restore_available=False,recovery_available=True,
                        message='Recupero incompleto: lo stato corrente è conservato e il riferimento originale resta disponibile.')
            else:
                result.update(status='recovery-required',restore_available=False,recovery_available=True,
                    message='Sessione interrotta: attendere e verificare il recupero indipendente.')
    return result


def restore(state,api=None):
    """Restore only this committed policy. Never replay its old music bookmark."""
    api=api or native_api();root=Path(state)
    if hasattr(api,'policy'):api.policy.stop(root)
    with api.owner_lock(root),api.engine.locked(root):
        record=load(root,'automatic.json',api)
        if record.get('kind')!=KIND:raise RuntimeError('V2 automatic record required')
        if record.get('status')=='retained-current':
            return dict(status='no-change',restore_available=False,message='Nessuna impostazione è stata cambiata: non occorre ripristinare.')
        journal,saved,continuation,_=_owned_restore_state(root,record,api)
        if continuation:
            # Already holding the native state lock; preserve its CAS owner.
            saved['enabled']=False;api.engine.atomic(root/'v2-policy.json',saved)
        if load(root,'transaction.json',api)!=journal:
            raise RuntimeError('Recupero della transazione corrente richiesto prima del ripristino')
        result=api.engine.recover_owned_transaction(root,journal['session_owner'])
        if result.get('status') not in ('rolled-back','no-change'):raise RuntimeError('Runtime restoration incomplete')
        previous=journal.get('previous_profile')
        if previous and load(root,'current.json',api)==previous:
            # The merged revalidated policy undoes to original defaults. Its
            # historical predecessor is not a verified current configuration.
            (root/'current.json').unlink()
        record.update(status='restored',settings_changed=False,restore_available=False,finished=api.now(),
            message='Impostazioni precedenti ripristinate. La playlist attuale non è stata modificata.')
        store(root,record,api);return public(record)


def startup(state,api=None):
    # A completed result never grants authority to start audio, repeat search,
    # seek a song, or restore historical playlist data after a reboot.
    api=api or native_api();result=status(state,api)
    if hasattr(api,'policy'):
        try:result['persistence']=api.policy.start(state)
        except (RuntimeError,OSError,ValueError,KeyError):
            result.update(status='saved-policy-not-verified',restore_available=False,
                message='Configurazione salvata non verificata. Nessuna riproduzione avviata; esegui una nuova verifica AutoTune.')
    return result


def analyze(state,api=None):
    api=api or native_api();h=api.scan()
    from runtime_summary import current_summary
    result=dict(kind='automatic-analysis',status='analyzed',eligible=False,can_prepare=False,sound_quality_claim=False,
        message='Lettura completata. Nessuna modifica applicata.',steps=[],runtime_summary=current_summary(h),
        hardware=dict(model=h['system'].get('product_name'),kernel=h['kernel']['release']),
        power_inventory=api.power_report(h),audio_setup=api.audio_report(h),
        session_budget=dict(maximum_seconds=calibration_budget.MAX_SECONDS,
            operational_seconds=calibration_budget.WORK_SECONDS,recovery_reserve_seconds=calibration_budget.RECOVERY_SECONDS,
            includes_setup_commit_and_recovery=True,protocol=calibration_budget.PROTOCOL))
    try:
        _, saved, rate=player_readiness(api,h,allow_playing=True)
        result.update(can_prepare=True,controlled_pcm_rate=rate)
        replaceable(saved)
        result.update(eligible=True,controlled_pcm_rate=rate,message='Sistema riconosciuto. AutoTune userà una sorgente di prova controllata.')
    except PreparationRequired as error:
        result['eligibility_reason']=str(error)
        result['reason_code']=error.code
        result['message']='Prima di avviare AutoTune, completa i passaggi indicati nel pannello.'
    return result


def player_readiness(api, hardware, *, allow_playing=False):
    phase='audio-output'
    try:
        target=api.target(hardware)
        phase='audio-format'
        rate=api.rate(hardware,target)
        phase='library-check'
        api.player.assert_library_idle(hardware)
        phase='player-unreadable'
        saved=api.player.playback_snapshot(target['mac'])
        preparation_requirements(saved,allow_playing=allow_playing)
        phase='player-group-check'
        standalone(saved['mac'],api.player.rpc,getattr(api.workload_api(),'verify_restore',None))
        phase='queue-check'
        append_capacity(api.player.rpc,len(saved['urls']))
    except PreparationRequired:
        raise
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise PreparationRequired(phase, str(error)) from error
    return target,saved,rate


def prepare_player(state,confirmed,api=None,*,allow_playing=False):
    """Prepare the selected player and verify the original queue and settings."""
    from . import common_activity
    api=api or native_api()
    if api.uid()!=0 or confirmed is not True:
        raise RuntimeError('Root and explicit player preparation consent required')

    def perform():
        api.engine.ensure_idle_transaction(state)
        if api.external_trial():
            raise PreparationRequired('audio-busy','Another audio qualification owns the device')
        observed_at=time.monotonic()
        target,saved,_=player_readiness(api,api.scan(),allow_playing=allow_playing)
        mac=target['mac']
        current=api.player.playback_snapshot(mac)
        expected=saved
        if allow_playing and saved['mode']=='play' and current['mode']=='play':
            # The track can advance while LMS answers the read-only checks.
            elapsed=time.monotonic()-observed_at
            if -CURSOR_TOLERANCE_SECONDS<=current['time']-saved['time']<elapsed+CURSOR_TOLERANCE_SECONDS:
                expected=dict(saved,time=current['time'])
        if not same_state(expected,current,api.player.playlist_key):
            raise PreparationRequired('player-changed','Playback changed during preparation')
        if current['mode']=='stop' and current['time']<CURSOR_TOLERANCE_SECONDS:
            return dict(status='player-prepared',eligible=True,changed=False,player_commands=0)
        commands=0
        last_error=None

        def observe():
            observed=api.player.playback_snapshot(mac)
            expected=dict(saved,mode=observed['mode'],time=observed['time'])
            if not same_state(expected,observed,api.player.playlist_key):
                raise PreparationRequired('player-changed','Queue or player settings changed during preparation')
            return observed

        def send(command):
            nonlocal commands,last_error
            commands+=1
            try:
                api.player.rpc(mac,command)
            except (OSError,RuntimeError,ValueError,KeyError) as error:
                last_error=error
            # Observe the actual state even when the request response was lost.
            return observe()

        if current['mode']!='stop':
            current=send(['stop'])
        if current['mode']=='stop' and current['time']>=CURSOR_TOLERANCE_SECONDS and saved['urls']:
            # The final argument prevents LMS from starting playback on a stopped player.
            current=send(['playlist','index',str(saved['index']),0,1])
        if current['mode']!='stop' or current['time']>=CURSOR_TOLERANCE_SECONDS:
            raise PreparationRequired('stop-unverified',str(last_error or 'Native stop or rewind was not observed'))
        return dict(status='player-prepared',eligible=True,changed=True,player_commands=commands)

    try:
        return common_activity.run_owned(state,perform,role='player-prepare')
    except PreparationRequired as error:
        return dict(status='blocked',reason_code=error.code,reason=str(error),eligible=False)
    except common_activity.Busy as error:
        return dict(status='blocked',reason_code='audio-busy',reason=str(error),eligible=False)
    except common_activity.Refused as error:
        return dict(status='blocked',reason_code=error.code,reason=str(error),eligible=False)

__release_signature__ = 'Tino Zappala'


_workflow_run_without_common = run
_workflow_restore_without_common = restore

def run(state,confirmed,resuming,experimental,budget,api=None,*,prepare=False):
    from . import common_activity
    api = api or native_api()
    if api.uid() != 0 or confirmed is not True:
        raise RuntimeError('Root and explicit audio-test consent required')
    if resuming:
        raise RuntimeError('Una prova interrotta non viene ripresa automaticamente')
    # Readiness precedes exclusion and any session/configuration publication.
    # The actual workflow repeats its observations under the queue lock.
    if not prepare:
        readiness = analyze(state,api)
        if readiness.get('eligible') is not True:
            return dict(kind=KIND,status='blocked',phase='preflight',percent=0,
                message=readiness['message'],reason=readiness.get('eligibility_reason'),reason_code=readiness.get('reason_code'),
                settings_changed=False,restore_available=False,player_commands=0,
                calibration_qualified=False,hardware_qualified=False,sound_quality_claim=False)
    entered = False
    def perform():
        nonlocal entered
        entered = True
        if prepare:
            budget.require(300)
            prepared=prepare_player(state,confirmed,api,allow_playing=True)
            if prepared['status']!='player-prepared':
                return dict(prepared,kind=KIND,phase='preflight',percent=0,settings_changed=False,
                            restore_available=False,calibration_qualified=False,hardware_qualified=False,
                            sound_quality_claim=False)
        return _workflow_run_without_common(state,confirmed,resuming,experimental,budget,api)
    admission_deadline = time.monotonic() + 1.5
    while True:
        try:
            return common_activity.run_owned(state,perform,role='automatic-run')
        except (common_activity.Busy,common_activity.Refused) as error:
            if entered:
                raise
            if isinstance(error,common_activity.Busy) and time.monotonic()<admission_deadline:
                time.sleep(.05)
                continue
            return dict(kind=KIND,status='blocked',phase='preflight',percent=0,
                message='Audio occupato o ripristino ancora necessario. Attendi la conclusione delle operazioni e riprova AutoTune con RAM disattivata.',
                reason=str(error),reason_code=error.code if isinstance(error,common_activity.Refused) else 'audio-busy',
                settings_changed=False,restore_available=False,
                player_commands=0,calibration_qualified=False,hardware_qualified=False,
                sound_quality_claim=False)

def restore(state,api=None):
    from . import common_activity,persistence,current_policy
    persistence.drain(state)
    def restore_current():
        import engine
        with engine.locked(state):current_policy.reconcile(state,engine)
        return _workflow_restore_without_common(state,api)
    return common_activity.run_owned(state,
        restore_current,check_ram=False,
        role='policy-undo',allow_recovery=True)


def acknowledge_external_intent(state,confirmed=False):
    """Explicit root acceptance, exact pre-search abort and original EX only."""
    import os
    if os.geteuid()!=0 or confirmed is not True:
        raise RuntimeError('Root and explicit external-intent acknowledgment required')
    from . import common_activity,external_intent
    def cleanup():
        with owner_lock(state):
            return public(external_intent.cancel(state,require_dead=True))
    return common_activity.run_pending_recovery(state,cleanup)
