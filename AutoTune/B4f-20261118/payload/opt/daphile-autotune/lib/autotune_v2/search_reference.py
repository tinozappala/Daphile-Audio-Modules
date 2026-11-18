"""Publish an unchanged V2 result as evidence, never as a runtime policy.

The caller owns playback and independent recovery. Publication occurs only
after the search has restored every temporary control, while the same
controlled source is still available for its final identity check.
"""
import copy,hashlib,json
from pathlib import Path
from .reference_state import ReferenceStore,identity,reference_proof
from .acceptance import checked_pairs
try:from measurement_contract import BRACKET_PROTOCOL
except ImportError:from ..measurement_contract import BRACKET_PROTOCOL


def observations(search):
    if not isinstance(search,dict) or search.get('status')!='retained-current':
        raise ValueError('Only a completed unchanged search can be a reference')
    if search.get('settings_applied') is not False or search.get('complete_optimization') is not False or search.get('proposed_policy'):
        raise ValueError('An applied or proposed policy is not an unchanged result')
    if search.get('comparison_protocol')!=BRACKET_PROTOCOL:
        raise ValueError('Current A/B/A restored-baseline search required for publication')
    reference=search.get('reference',{})
    if reference.get('issues')!=[] or reference.get('application_observed') is not False:
        raise ValueError('Reference was incomplete or changed a control')
    checked_pairs(reference)
    return [copy.deepcopy(pair[key]) for pair in reference['measurements']
            for key in ('before','after','restored_baseline')]


def retain(root,search,expected_binding,fresh_binding,restoration_verified,expected_reference_id=None):
    # Run all validation before creating or touching even the evidence store.
    rows=observations(search);binding=identity(expected_binding)
    if restoration_verified is not True:raise RuntimeError('Prove full temporary-control restoration first')
    if identity(fresh_binding())!=binding:raise RuntimeError('Configuration changed; unchanged result is historical')
    proof=reference_proof(binding,rows)
    store=ReferenceStore(root)
    store.save(proof,expected_id=expected_reference_id)
    return dict(status='retained-current',reference_id=proof['id'],changes=[],
                settings_changed=False,restore_available=False,observed_windows=len(rows),
                optimization_applied=False,persistent_policy_created=False,
                message='Configurazione attuale conservata: i confronti non hanno dimostrato un miglioramento affidabile.')


def publish_inactive_policy(state,search,binding,fresh_binding,restoration_verified,expected_reference_id=None):
    """Publish measured evidence while preserving every runtime journal byte.

    A completed temporary trial may leave a rolled-back transaction; an active,
    uncertain or committed policy requires its separate ownership workflow.
    Nothing here restores or replays that policy's playback bookmark.
    """
    state=Path(state)
    if state.is_symlink() or (state/'current.json').exists() or (state/'current.json').is_symlink():
        raise RuntimeError('An applied policy cannot be replaced by a no-change reference')
    journal=state/'transaction.json'
    if journal.is_symlink():raise RuntimeError('Indirect runtime journal refused')
    before=journal.read_bytes() if journal.exists() else None
    if before is not None:
        record=json.loads(before)
        if record.get('status')!='rolled-back' or record.get('rollback_errors'):
            raise RuntimeError('Full temporary runtime restoration required before publication')
    root=state/'reference-evidence'
    result=retain(root,search,binding,fresh_binding,restoration_verified,expected_reference_id)
    store=ReferenceStore(root);current=store.current(fresh_binding())
    if current.get('status')!='verified-reference' or current.get('id')!=result['reference_id']:
        raise RuntimeError('Published reference readback is not current')
    after=journal.read_bytes() if journal.exists() else None
    if after!=before or (state/'current.json').exists() or (state/'current.json').is_symlink():
        raise RuntimeError('Concurrent runtime policy change during evidence publication')
    proof=json.loads((root/'verified-reference.json').read_text(encoding='utf8'))
    return dict(status='PASS_NO_CHANGE_REFERENCE_PUBLICATION',result=result,current=current,
        verified_reference=proof,proof_sha256=hashlib.sha256((root/'verified-reference.json').read_bytes()).hexdigest(),
        runtime_journal_byte_unchanged=True,applied_current_created=False,
        playlist_commands=0,persistent_policy_created=False)

__release_signature__ = 'Tino Zappala'
