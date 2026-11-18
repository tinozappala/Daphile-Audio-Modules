"""Recover the accepted policy metadata around one native reapply trial.

The native transaction remains the authority for controls and their readbacks.
This preimage records ordinary state, not source hashes or resource permits.
All callers hold the real queue flock and the native state lock.
"""
import copy,os,time
from pathlib import Path

SCHEMA='autotune-policy-reapply-recovery-v1'
FILE='policy-reapply-before.json'
JOURNALS=('transaction.json','current.json','last-known-good.json')


def need(value,reason):
    if not value:raise RuntimeError(reason)


def load(root,name,engine):
    path=Path(root)/name
    need(not path.is_symlink(),'Indirect policy recovery state refused')
    return engine.load(path) if path.exists() else None


def _snapshot(root,engine):
    row=load(root,FILE,engine)
    if row is None:return None
    need(row.get('schema')==SCHEMA,'Unknown policy reapply recovery record')
    need(row.get('phase') in ('prepared','trial','promoting','completed','recovered'),
         'Unknown policy reapply recovery phase')
    return row


def _owned(row,activity):
    owner=row['owner']
    need(activity.get('kind')=='policy-revalidation-v2' and activity.get('restore_playback') is False
         and activity.get('owner')==owner['pid'] and activity.get('start')==owner['start']
         and activity.get('boot_id')==owner['boot_id']
         and activity.get('initial_transaction_id')==row['journal']['id']
         and activity.get('id')!=row['previous_activity']['id'],
         'Policy reapply activity differs from its captured preimage')
    need(row.get('activity_id') in (None,activity.get('id')),'Newer policy activity preserved')


def _native(row,journal,activity):
    need(isinstance(journal,dict) and journal.get('session_owner')=={'kind':'activity','id':activity['id']}
         and journal.get('parent_pid')==activity['owner'] and journal.get('parent_start')==activity['start']
         and journal.get('boot_id')==activity['boot_id'] and journal.get('previous_profile')==row['journal']
         and journal.get('profile')=='automatic-policy-reapply' and journal.get('changes'),
         'Exact native policy reapply transaction required')


def _same_journal(current,before):
    if not isinstance(current,dict) or not isinstance(before,dict):return False
    mutable={'status','parameter_restoration_complete','rollback_errors','changes'}
    if {k:v for k,v in current.items() if k not in mutable}!={k:v for k,v in before.items() if k not in mutable}:return False
    a,b=current.get('changes'),before.get('changes')
    if not isinstance(a,list) or not isinstance(b,list) or len(a)!=len(b):return False
    for actual,original in zip(a,b):
        ignore={'rollback_irq_hint_attempted','rollback_irq_write_verified','accepted_intermediate'}
        if {k:v for k,v in actual.items() if k not in ignore}!={k:v for k,v in original.items() if k not in ignore}:return False
        allowed=list(original.get('accepted_intermediate',[]))
        if original.get('parameter')=='usb_irq_affinity' and original.get('effective_before'):
            from detection import numbers
            allowed.append(','.join(str(n) for n in sorted(set(numbers(original['effective_before'])))))
        if any(v not in allowed for v in actual.get('accepted_intermediate',[])):return False
        if any(v not in actual.get('accepted_intermediate',[]) for v in original.get('accepted_intermediate',[])):return False
        if any(k in actual and actual[k] is not True for k in ('rollback_irq_hint_attempted','rollback_irq_write_verified')):return False
    return True


def _cas(root,row,engine):
    need(load(root,'automatic.json',engine)==row['record'],'Newer automatic result preserved')
    saved=load(root,'v2-policy.json',engine)
    need(saved==row['saved'] or saved==row.get('updated'),'Newer persistent policy preserved')
    allowed=[row['journal'],row.get('native'),row.get('promoted')]
    for name in JOURNALS:
        actual=load(root,name,engine)
        need(any(actual==candidate for candidate in allowed if candidate is not None)
             or (name=='transaction.json' and _same_journal(actual,row.get('native'))
                 and actual.get('status') in ('rollback-incomplete','rolled-back'))
             or (name!='transaction.json' and _same_journal(actual,row.get('native'))
                 and actual.get('status')=='committed'),
             'Newer policy journal preserved: '+name)


def _clear(root,row,engine,phase):
    row=dict(row,phase=phase,finished=time.time())
    engine.atomic(Path(root)/FILE,row)
    (Path(root)/FILE).unlink()
    directory=os.open(root,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def begin(root,saved,record,journal,previous_activity,engine):
    reconcile(root,engine)
    need(load(root,FILE,engine) is None,'Unfinished policy reapply recovery required')
    need(load(root,'v2-policy.json',engine)==saved and load(root,'automatic.json',engine)==record,
         'Policy changed before its recovery snapshot')
    need(all(load(root,name,engine)==journal for name in JOURNALS),'Policy journals changed before reapply')
    row=dict(schema=SCHEMA,phase='prepared',saved=copy.deepcopy(saved),record=copy.deepcopy(record),
             journal=copy.deepcopy(journal),previous_activity=copy.deepcopy(previous_activity),
             owner=dict(pid=os.getpid(),start=engine.start_ticks(os.getpid()),boot_id=engine.boot_id()),
             activity_id=None,created=time.time())
    engine.atomic(Path(root)/FILE,row)
    return row


def bind(root,activity,engine):
    row=_snapshot(root,engine);need(row is not None,'Policy reapply preimage missing')
    _owned(row,activity);row['activity_id']=activity['id'];engine.atomic(Path(root)/FILE,row)


def promotion(root,activity,native,promoted,updated,engine):
    row=_snapshot(root,engine);need(row is not None,'Policy reapply preimage missing')
    _owned(row,activity);_native(row,native,activity)
    need(native.get('status')=='committed','Only a committed native trial may be promoted')
    row.update(phase='promoting',activity_id=activity['id'],native=copy.deepcopy(native),
               promoted=copy.deepcopy(promoted),updated=copy.deepcopy(updated))
    _cas(root,row,engine)
    engine.atomic(Path(root)/FILE,row)


def prepare_recovery(root,activity,engine):
    """Before native rollback: recover its original, unmerged before-values."""
    row=_snapshot(root,engine)
    if row is None or activity.get('kind')!='policy-revalidation-v2':return
    _owned(row,activity)
    transaction=load(root,'transaction.json',engine)
    if transaction==row['journal']:
        _cas(root,row,engine);return
    native=row.get('native')
    if native is None:
        _native(row,transaction,activity)
        native=copy.deepcopy(transaction)
        row.update(phase='trial',activity_id=activity['id'],native=native)
        engine.atomic(Path(root)/FILE,row)
    _cas(root,row,engine)
    if transaction==row.get('promoted'):
        # Native rollback must restore the immediately preceding runtime, not
        # the initial calibration defaults merged into the final policy undo.
        engine.atomic(Path(root)/'transaction.json',native)
    else:
        need(_same_journal(transaction,native),'Native policy rollback journal changed')


def recover(root,activity,engine):
    """After native runtime + playback rollback: restore the accepted metadata."""
    row=_snapshot(root,engine)
    if row is None or activity.get('kind')!='policy-revalidation-v2':return
    _owned(row,activity);_cas(root,row,engine)
    native=row.get('native')
    if native is not None:
        actual=load(root,'transaction.json',engine)
        receipt=row.get('runtime_recovered') if actual==row['journal'] else actual
        need(_same_journal(receipt,native) and receipt.get('status')=='rolled-back'
             and receipt.get('parameter_restoration_complete') is True and not receipt.get('rollback_errors'),
             'Native policy rollback and readbacks are not complete')
        # A durable receipt permits idempotent continuation if metadata
        # replacement itself is interrupted. Never turn an intent into proof.
        row['runtime_recovered']=copy.deepcopy(receipt)
        engine.atomic(Path(root)/FILE,row)
        for change in receipt['changes']:
            if not change.get('write_attempted'):continue
            if change.get('tid') and (receipt.get('boot_id')!=engine.boot_id()
                                     or engine.start_ticks(change['tid'])!=change.get('start')):continue
            hardware={'dac':{'status':'unknown'},'usb':engine.usb_devices()}
            engine.guard(change,hardware,restoring=True)
            from rollback_order import same_value
            from detection import numbers
            need(same_value(change,engine.setting_value(change),change['before'],numbers),
                 'Recovered policy control differs from native pretrial value')
    else:
        need(all(load(root,name,engine)==row['journal'] for name in JOURNALS),
             'Policy changed without an owned native reapply transaction')
    for name in JOURNALS:engine.atomic(Path(root)/name,row['journal'])
    engine.atomic(Path(root)/'v2-policy.json',row['saved'])
    need(all(load(root,name,engine)==row['journal'] for name in JOURNALS)
         and load(root,'v2-policy.json',engine)==row['saved'],'Accepted policy recovery readback differs')
    _clear(root,row,engine,'recovered')


def complete(root,activity,engine):
    row=_snapshot(root,engine)
    if row is None:return
    _owned(row,activity);_cas(root,row,engine)
    need(activity.get('status')=='completed','Policy activity completion is not observed')
    expected=row.get('promoted',row['journal']);saved=row.get('updated',row['saved'])
    need(all(load(root,name,engine)==expected for name in JOURNALS)
         and load(root,'v2-policy.json',engine)==saved,'Completed policy metadata differs')
    _clear(root,row,engine,'completed')


def reconcile(root,engine):
    """Explicit EX recovery for death before Lease creation or after finish."""
    row=_snapshot(root,engine)
    if row is None:return
    if row['phase'] in ('completed','recovered'):
        _clear(root,row,engine,row['phase']);return
    activity=load(root,'activity-session.json',engine)
    if activity==row['previous_activity']:
        _cas(root,row,engine)
        need(all(load(root,name,engine)==row['journal'] for name in JOURNALS),
             'Pretrial policy metadata changed')
        _clear(root,row,engine,'recovered');return
    _owned(row,activity)
    if activity.get('status')=='completed':return complete(root,activity,engine)
    if activity.get('status')=='recovered':return recover(root,activity,engine)
    raise RuntimeError('Independent policy activity recovery is still required')


__release_signature__ = 'Tino Zappala'
