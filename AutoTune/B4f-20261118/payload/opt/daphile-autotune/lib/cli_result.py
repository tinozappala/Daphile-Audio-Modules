"""CLI completion is specific to the requested operation.

Automatic mutations fail closed: an unknown, malformed, interrupted or merely
historical result cannot become shell success. A successful status request
can report an unsuccessful workflow; it remains a completed read. Analysis
must explicitly report 'analyzed', independently of eligibility for a test.
The backend JSON is retained unchanged for diagnosis. This contract maps the
backend conclusion to an exit code; it does not independently qualify audio.
"""

AUTOMATIC_COMPLETED = {
    'run': frozenset(('configured', 'retained-current')),
    'resume': frozenset(('configured', 'retained-current')),
    'restore': frozenset(('restored', 'no-change')),
}


def exit_code(command, action, result):
    if not isinstance(result, dict):
        return 1
    status = result.get('status')
    if command == 'automatic':
        if type(action) is not str or type(status) is not str or not status or status.strip()!=status:
            return 1
        if action == 'acknowledge-external-intent':
            # This maps an already completed administrative cancellation only.
            # It grants no cleanup authority and never qualifies a run/resume.
            completed = (result.get('kind') == 'automatic-configuration-v2'
                and status == 'cancelled-external-intent-preserved'
                and result.get('phase') == 'cancelled'
                and result.get('cancellation_guard_quiescent') is True
                and all(result.get(key) is False for key in (
                    'playback_restored', 'calibration_qualified', 'hardware_qualified',
                    'settings_changed', 'restore_available', 'sound_quality_claim')))
            return 0 if completed else 1
        if action in AUTOMATIC_COMPLETED:
            return 0 if status in AUTOMATIC_COMPLETED[action] else 1
        if action == 'analyze':
            return 0 if status == 'analyzed' else 1
        return 0 if action == 'status' else 1
    # Preserve the existing result convention of unrelated native commands.
    # Missing status is legitimate for e.g. the read-only hardware inventory.
    return 1 if status in ('failed-restored', 'rollback-incomplete') else 0

__release_signature__ = 'Tino Zappala'
