"""Recover only this Camilla session without requesting playback or changing intent."""
import argparse
import json
from pathlib import Path

from common import stop_owned
from native_audio import NativeAudio
from native_route import NativeRoute


def recover(state):
    state=Path(state);children=state/'audio-processes.json'
    if children.exists():
        value=json.loads(children.read_text())
        if set(value)!={'children'} or not isinstance(value['children'],list) or len(value['children'])>3:
            raise RuntimeError('Registro dei processi audio non riconosciuto')
        for record in reversed(value['children']):stop_owned(record,timeout=5)
        children.unlink()
    ledger=state/'native-audio-session.json';route=NativeRoute(state)
    if not ledger.exists() and not route.record.exists():return {'status':'no-native-route-recovery'}
    if not ledger.is_file() or ledger.is_symlink():raise RuntimeError('Registro del percorso nativo mancante')
    record=json.loads(ledger.read_text())
    if record.get('version')!=1:raise RuntimeError('Registro della sessione audio non riconosciuto')
    # The saved preference is not permission to resume playback. Preserve its
    # exact bytes on both success and refusal. Worker startup calls this helper
    # before creating its controller/monitor and aborts on incomplete recovery;
    # the controller also refuses activation while any native ledger remains.
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if record.get('boot_id')!=boot:
        # A reboot clears our bind. Verify that fact and the original binary
        # before retiring this stale ledger; do not pause a new listening session.
        if route.bound():raise RuntimeError('Bind inatteso dopo un nuovo avvio; recupero conservato')
        if route.record.exists():route.deactivate()
        ledger.unlink();return {'status':'previous-boot-route-recovered','playback_changed':False}
    manager=NativeAudio(state,lambda:None,route=route)
    manager.bypass(resume=False)
    return {'status':'native-route-recovered','resume_requested':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);args=parser.parse_args()
    print(json.dumps(recover(args.state)))
