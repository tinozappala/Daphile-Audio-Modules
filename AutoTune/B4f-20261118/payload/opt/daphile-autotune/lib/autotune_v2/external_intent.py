"""Truthful cancellation of the exact pre-search external-queue abort.

No policy/player restoration is performed here. Original root run authority,
EX custody and original owner/state locks remain mandatory. An explicit root
acknowledgment can finish the same narrow cancellation after its owner died.
"""
import copy,hashlib,json,os,re,time
from pathlib import Path

TERMINAL='cancelled-external-intent-preserved'
PROTOCOL='autotune-presearch-external-intent-cancellation-v1'
FAILURE='RuntimeError: Queue or track changed during audio startup; calibration refused'
NAMES=('automatic.json','activity-session.json','controlled-workload.json')
FORBIDDEN=('transaction.json','current.json','last-known-good.json','v2-policy.json')
JOURNAL='external-intent-cancellation.json'


def need(ok,reason):
    if not ok:raise RuntimeError(reason)


def seal(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),
                                    allow_nan=False,ensure_ascii=True).encode()).hexdigest()


def intent(value,key):
    return seal({k:([key(u) for u in value[k]] if k=='urls' else value[k])
                 for k in ('mac','urls','mode','repeat','shuffle','volume','mute','power')})


def semantic(before,boot,host,budget_check):
    a,s,w=(before[n] for n in NAMES)
    need(a.get('kind')=='automatic-configuration-v2' and s.get('kind')=='automatic-v2','CANCEL_RECORD_KIND')
    need(a.get('status') in ('recovery-required','timed-out-recovery-required') and
         s.get('status') in ('running','recovering','recovery-required') and a.get('phase')=='recovery',
         'CANCEL_PRESEARCH_ABORT_ONLY')
    need(a.get('settings_changed')is False and a.get('sound_quality_claim')is False and
         a.get('private_error')==FAILURE and a.get('steps')==[] and
         a.get('persistence')=={'status':'not-qualified','enabled':False},'CANCEL_EFFECTS_UNPROVEN')
    need(not any(k in a for k in ('search','results','selected_policy','transaction_id','commit',
                                 'selected','applied','policy','reference_publication')),'CANCEL_SEARCH_OR_POLICY')
    need(a.get('boot_id')==s.get('boot_id')==w.get('boot_id')==boot and
         a.get('binding',{}).get('host_uuid')==s.get('host_uuid')==host,'CANCEL_HOST_BOOT')
    need(a.get('activity_session_id')==s.get('id')==w.get('owner_id') and
         s.get('parent_session_owner')=={'kind':'automatic','id':a.get('id')} and
         a.get('owner_pid')==s.get('owner') and a.get('owner_start')==s.get('start'), 'CANCEL_OWNER_LINK')
    need(type(s.get('owner'))is int and s['owner']>1 and isinstance(s.get('start'),str) and
         re.fullmatch('[1-9][0-9]*',s['start']) and
         all(isinstance(v.get('id'),str) and re.fullmatch('[a-f0-9]{32}',v['id']) for v in (a,s)),
         'CANCEL_OWNER_IDENTITY')
    need(s.get('restore_playback')is True and s.get('initial_transaction_id')is None and not s.get('companion'),
         'CANCEL_PREEXISTING_EFFECT')
    need(a.get('original_playback')==s.get('playback')==w.get('original_playback'),'CANCEL_BOOKMARK_LINK')
    need(w.get('marker')=='DAPHILE_AUTOTUNE_V2_WORKLOAD_V1' and w.get('phase')=='external-queue-preserved' and
         w.get('selection_protocol')=='append-owned-fixture-v1' and w.get('fixture_complete')is True,
         'CANCEL_WORKLOAD_PHASE')
    need(a.get('playback_recovery',{}).get('restored')is False and
         a.get('playback_recovery',{}).get('queue_restored')is False,'CANCEL_FALSE_RESTORATION')
    budget_check(a,s)


def plan(before,evidence,actor,now):
    after=copy.deepcopy(before)
    audit=dict(protocol=PROTOCOL,before_record_seals={n:seal(before[n]) for n in NAMES},
               observed_intent=evidence,actor=actor,acknowledged_at=now,player_commands=0,
               policy_controls_written=0,calibration_qualified=False,hardware_qualified=False,
               playback_restored=False,original_bookmark_retained=True)
    recovery=dict(status='external-intent-acknowledged',restored=False,queue_restored=False,
                  player_commands=0,original_bookmark_retained=True)
    for n in NAMES[:2]:
        after[n].update(status=TERMINAL,cancellation=copy.deepcopy(audit),
                         playback_recovery=copy.deepcopy(recovery),playback_restored=False,
                         calibration_qualified=False,hardware_qualified=False,cancellation_guard_quiescent=True)
    after['automatic.json'].update(phase='cancelled',settings_changed=False,restore_available=False,
        recovery_available=False,sound_quality_claim=False,
        message='Prova annullata: la nuova riproduzione è conservata. '
                'Calibrazione non qualificata; playlist originale non ripristinata.')
    return after


def reconcile(current,before,after):
    need(all(current[n] in (before[n],after[n]) for n in NAMES),'CANCEL_JOURNAL_CAS_DRIFT')
    return [n for n in NAMES[:2] if current[n]!=after[n]]


def activity_terminal(value):
    return (isinstance(value,dict) and value.get('status')==TERMINAL and
            value.get('cancellation',{}).get('protocol')==PROTOCOL and
            all(value.get(k)is False for k in ('playback_restored','calibration_qualified','hardware_qualified')) and
            value.get('playback_recovery',{}).get('restored')is False and
            value.get('playback_recovery',{}).get('queue_restored')is False)


def defer_original_recovery(root,activity):
    """DENY-only: never replay a superseded queue during a partial cancel.

    This does not attest clean or release EX. The original source-pinned writer
    durably creates INTENT only after its owned pre-search cleanup; uncertain
    mixed records retain EX/DENY until the full cancellation proof succeeds.
    """
    from . import common_activity as common
    journal=common.read_state(root,JOURNAL)
    if not journal or journal.get('protocol')!=PROTOCOL or journal.get('state')!=str(Path(root)):return False
    if journal.get('status') not in ('INTENT','ACKNOWLEDGED'):return False
    before=journal.get('before',{}).get('activity-session.json',{})
    identity=('id','kind','owner','start','boot_id','parent_session_owner','playback','budget','owner_pid_namespace')
    return (before.get('kind')=='automatic-v2' and
            all(before.get(k)==activity.get(k) for k in identity) and
            activity_terminal(journal.get('after',{}).get('activity-session.json')))


def native_facts(root,before,api,*,require_dead=False,cleanup=False,committed=False):
    from . import common_activity as common,budget_guard
    common.require_exclusive()
    a,s,w=(before[n] for n in NAMES)
    def budget(a,s):
        need(budget_guard.budget_api.validate(s.get('budget')) and
             s['budget']==budget_guard.budget_fields(a) and s.get('deadline')==a.get('calibration_deadline') and
             s.get('owner_pid_namespace')==budget_guard.budget_api.pid_namespace() and
             a.get('maximum_seconds')==1800 and a.get('recovery_reserve_seconds')==300,'CANCEL_ORIGINAL_BUDGET')
    from detection import read
    semantic(before,api.engine.boot_id(),__import__('host_identity').current(read),budget)
    if require_dead:
        need(api.engine.start_ticks(s['owner'])!=s['start'],'CANCEL_ORIGINAL_WRITER_ALIVE')
    else:
        need(s['owner']==os.getpid() and s['start']==api.engine.start_ticks(os.getpid()),'CANCEL_NOT_CURRENT_RUN')
    for key in ('recovery_process',):
        child=s.get(key)
        if child:need(api.engine.start_ticks(child['pid'])!=child['start'],'CANCEL_RECOVERY_WORKER_ALIVE')
    for name in FORBIDDEN:need(common.read_state(root,name)is None,'CANCEL_POLICY_JOURNAL_PRESENT')
    folder=Path('/mnt/hdd/daphile-autotune-samples');fixture=Path(w['workload']['path'])
    need(fixture.parent==folder and re.fullmatch('[a-f0-9]{32}\\.wav',fixture.name) and
         not folder.is_symlink() and folder.is_dir() and not fixture.is_symlink(),'CANCEL_FIXTURE_ROUTE')
    info=folder.stat();need(info.st_uid==0 and not info.st_mode&0o022,'CANCEL_FIXTURE_DAC')
    need(a['target']==w['target'] and a['target']['mac']==s['playback']['mac'],'CANCEL_TARGET_LINK')
    if cleanup and fixture.exists():
        from .controlled_workload import Workload
        work=Workload(root,dict(id=s['id'],original_playback=s['playback']),api.workload_api(),folder)
        work.record=w;work.cleanup() # Original safe unlink only, no restore/player writes.
    need(not fixture.exists() and not fixture.is_symlink(),'CANCEL_FIXTURE_NOT_ABSENT')
    preference=Path(a['target']['path'])
    need(not preference.is_symlink() and preference.read_text()==a['original_preferences'],'CANCEL_AUDIO_PREFS_CHANGED')
    current=api.player.playback_snapshot(a['target']['mac'])
    from playback_contract import validate
    validate(current);key=api.player.playlist_key
    need(not any(api.player.may_reference_fixture(u,fixture,w.get('fixture_identity')) for u in current['urls']),'CANCEL_FIXTURE_POSSIBLY_REFERENCED')
    if not committed:
        need([key(u) for u in current['urls']]!=[key(u) for u in s['playback']['urls']],'CANCEL_REPLACEMENT_INTENT_MISSING')
    # The adapter is never constructed for this failure; reject an active RT
    # claim rather than removing or pretending to release another owner.
    rt=Path('/run/daphile-audio-policy/autotune-rt.json')
    if rt.exists() or rt.is_symlink():
        need(not rt.is_symlink(),'CANCEL_INDIRECT_RT_CLAIM')
        claim=json.loads(rt.read_text())
        need(claim.get('boot_id')!=api.engine.boot_id() or
             api.engine.start_ticks(claim.get('pid'))!=claim.get('start'),'CANCEL_LIVE_RT_CLAIM')
    return dict(intent_seal=intent(current,key),snapshot=current,fixture_absent=True,
                policy_journals_absent=True,player_commands=0,policy_controls_written=0)


def cancel(root,api=None,*,require_dead=False):
    """Caller holds original owner lock and genuine common EX before entry."""
    from . import common_activity as common,workflow
    common.require_exclusive();api=api or workflow.native_api();root=Path(root)
    with api.engine.locked(root):
        current={n:common.read_state(root,n) for n in NAMES}
        journal=common.read_state(root,JOURNAL)
        before=copy.deepcopy(journal['before'] if journal else current)
        if journal:
            need(journal.get('protocol')==PROTOCOL and journal.get('state')==str(root),'CANCEL_FOREIGN_JOURNAL')
            reconcile(current,before,journal['after'])
        facts=native_facts(root,before,api,require_dead=require_dead,cleanup=True)
        if journal:
            if facts['intent_seal']!=journal['evidence']['intent_seal']:
                need(require_dead,'CANCEL_RETRY_INTENT_CHANGED')
                # Explicit root acknowledgment after owner death accepts the
                # later intent without rewriting either original before-image
                # or the earlier truthful cancellation observation.
                journal['explicit_later_external_intent_ack']=dict(facts=facts,pid=os.getpid(),
                    start=api.engine.start_ticks(os.getpid()),boot_id=api.engine.boot_id(),at=time.time())
                api.engine.atomic(root/JOURNAL,journal)
        else:
            after=plan(before,facts,dict(pid=os.getpid(),start=api.engine.start_ticks(os.getpid()),
                                        boot_id=api.engine.boot_id(),explicit_dead_owner_ack=require_dead),time.time())
            journal=dict(protocol=PROTOCOL,state=str(root),before=before,after=after,evidence=facts,status='INTENT')
            api.engine.atomic(root/JOURNAL,journal)
        again=native_facts(root,before,api,require_dead=require_dead)
        need(again['intent_seal']==facts['intent_seal'],'CANCEL_INTENT_CHANGED_BEFORE_COMMIT')
        for name in reconcile(current,before,journal['after']):
            need({n:common.read_state(root,n) for n in NAMES}==current,'CANCEL_RECORD_CHANGED_BEFORE_WRITE')
            api.engine.atomic(root/name,journal['after'][name]);current[name]=journal['after'][name]
        need({n:common.read_state(root,n) for n in NAMES}==journal['after'],'CANCEL_FINAL_READBACK')
        final=native_facts(root,before,api,require_dead=require_dead)
        need(final['intent_seal']==facts['intent_seal'],'CANCEL_INTENT_CHANGED_AFTER_COMMIT')
        journal.update(status='ACKNOWLEDGED',observation_after=final,
                       after_record_seals={n:seal(journal['after'][n]) for n in NAMES})
        api.engine.atomic(root/JOURNAL,journal)
        return journal['after']['automatic.json']


def clean_cancelled(root):
    """Read-only full after-image barrier, including partial journal-finalization."""
    from . import common_activity as common,workflow
    common.require_exclusive();journal=common.read_state(root,JOURNAL)
    if not journal or journal.get('protocol')!=PROTOCOL or journal.get('state')!=str(Path(root)):return False
    current={n:common.read_state(root,n) for n in NAMES}
    if current!=journal.get('after') or not all(activity_terminal(current[n]) for n in NAMES[:2]):return False
    try:
        # Current original worker may still own EX while waiting for its guard;
        # a dead-owner guardian/manual cleanup uses the same original evidence.
        import engine
        before=journal['before'];s=before['activity-session.json']
        dead=engine.start_ticks(s['owner'])!=s['start']
        native_facts(root,before,workflow.native_api(),require_dead=dead,committed=True)
    except (RuntimeError,OSError,ValueError,KeyError,TypeError):return False
    return True
