"""Retire a provably empty old journal without any playback/rollback API."""
import re

NAMES=('last-known-good.json','current.json','transaction.json')


def empty_reference(records,current_boot):
    if set(records)!=set(NAMES):raise ValueError('Complete legacy journal set required')
    original=records['current.json']
    if any(record!=original for record in records.values()):raise ValueError('Legacy journals differ')
    if not re.fullmatch('[a-f0-9]{32}',str(original.get('id'))):raise ValueError('Exact legacy identity required')
    if original.get('status')!='committed' or original.get('profile')!='automatic' or original.get('changes')!=[]:
        raise ValueError('A real or incomplete application must retain its recovery journal')
    if original.get('previous_profile') is not None:raise ValueError('Previous owned settings must retain recovery')
    if original.get('selected_policy')!={'name':'combined','stage':'combined'}:raise ValueError('Policy contains selected controls')
    if not original.get('boot_id') or original['boot_id']==current_boot:raise ValueError('An earlier inactive boot is required')
    owner=original.get('session_owner',{})
    if owner.get('kind')!='automatic' or not re.fullmatch('[a-f0-9]{32}',str(owner.get('id'))):raise ValueError('Automatic owner identity required')
    return original


def replacements(original):
    # Deliberately omit all historical music, playback_resume, previous_profile,
    # native preferences and executable policy. Original bytes stay private.
    replacement=dict(schema=2,id=original['id'],status='retired-no-change',profile='historical-reference',
        boot_id=original['boot_id'],changes=[],settings_changed=False,restore_available=False,
        reason='Empty legacy reference; no configuration or playback restored')
    return {name:dict(replacement) for name in NAMES}


def retirement_plan(records,automatic,current_boot):
    if automatic.get('status')!='blocked' or automatic.get('phase')!='library-scan' or automatic.get('binding'):
        raise ValueError('Only the known scan-refused orphan reference may be retired')
    return replacements(empty_reference(records,current_boot))


def historical_completion_plan(records,automatic,current_boot,current_host):
    """Separate proof for a completed old zero-control application.

    A configured label alone is insufficient. Its exact transaction, boot,
    host, empty application and automatic owner must all agree. This retires
    runtime journals only; no boot/profile/settings/playback API is called.
    """
    original=empty_reference(records,current_boot)
    if automatic.get('kind')!='automatic-configuration' or automatic.get('status')!='configured' or automatic.get('phase')!='complete':
        raise ValueError('Completed legacy automatic owner required')
    if not current_host or automatic.get('binding',{}).get('host_uuid')!=current_host:
        raise ValueError('Legacy completion belongs to another host')
    if automatic.get('id')!=original['session_owner']['id'] or automatic.get('boot_id')!=original['boot_id']:
        raise ValueError('Legacy completion owner or boot mismatch')
    application=automatic.get('application',{})
    if application.get('status')!='committed' or application.get('transaction_id')!=original['id'] or application.get('changes')!=[]:
        raise ValueError('Legacy completion contains applied or unknown controls')
    return replacements(original)

__release_signature__ = 'Tino Zappala'
