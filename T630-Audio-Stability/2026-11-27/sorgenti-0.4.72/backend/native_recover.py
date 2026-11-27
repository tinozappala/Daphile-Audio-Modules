"""Recover only this Camilla session without requesting playback or changing intent."""
import argparse
import json
import os
import stat
from pathlib import Path

from common import stop_owned,identity
from datc_recovery_records import read_owned,validate_ledger,validate_children,recover_children,require_unchanged,retire,uuid,RecoveryEvidenceError
from native_audio import NativeAudio
from native_route import NativeRoute,digest
from datc_dac_lease import DacTransitionLease


def host_rme_state(state):
    """Read the same root-owned host configuration used by the broker."""
    state=Path(state)
    if not state.is_absolute() or state.name!='data' or state.parent.name!='DaphileCamillaDSP' or state.parents[1].name!='modules':
        raise RuntimeError('Percorso del modulo audio non verificato')
    root=state.parents[2]
    for path in (state,state.parent,state.parents[1],root):
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022:
            raise RuntimeError('Directory del modulo audio non verificata')
    fd=os.open(root/'config.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_mode&0o022 or info.st_size>65536:
            raise RuntimeError('Configurazione host non verificata')
        config=json.load(stream)
    if not isinstance(config,dict) or not isinstance(config.get('rme_state'),str):
        raise RuntimeError('Stato transazionale RME mancante')
    result=Path(config['rme_state'])
    if not result.is_absolute() or '..' in result.parts or result.is_symlink() or not result.is_dir():
        raise RuntimeError('Stato transazionale RME non verificato')
    return result


def recover(state,dac_lease=None):
    state=Path(state)
    route=NativeRoute(state)  # directory guard BEFORE reading journals
    ledger=read_owned(state/'native-audio-session.json')
    record=validate_ledger(ledger.value) if ledger is not None else None
    children=read_owned(state/'audio-processes.json')
    records=validate_children(children.value,record) if children is not None else []
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if not uuid(boot):raise RecoveryEvidenceError('Boot identity not verified')
    if ledger is not None:require_unchanged(ledger)
    if children is not None:recover_children(children,records,boot,identity,stop_owned)
    from pcm_pipeline import remove_abandoned_inputs
    remove_abandoned_inputs(state)
    if ledger is None:
        # Missing ledger cannot authorize a native restart or removal of a bind.
        route.verify_restored()
        return {'status':'no-native-route-recovery'}
    require_unchanged(ledger)
    lease=dac_lease if dac_lease is not None else DacTransitionLease(host_rme_state(state))
    if record['boot_id']!=boot:
        # Do not touch playback after reboot; inspect native binary and bind.
        with lease.hold():
            lease.assert_held()
            if route.bound():raise RecoveryEvidenceError('Bind inatteso dopo nuovo avvio')
            if route.record.exists():route.deactivate()
            route.verify_restored()
            lease.assert_held()
            retire(ledger)
            return {'status':'previous-boot-route-recovered','playback_changed':False}
    manager=NativeAudio(state,lambda:None,route=route,dac_lease=lease)
    manager.bypass(resume=False)
    if os.path.lexists(ledger.path) or os.path.lexists(state/'audio-processes.json'):
        raise RecoveryEvidenceError('Recupero nativo non concluso')
    route.verify_restored()
    return {'status':'native-route-recovered','resume_requested':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state',required=True);parser.add_argument('--rme-state');args=parser.parse_args()
    print(json.dumps(recover(args.state,dac_lease=DacTransitionLease(args.rme_state) if args.rme_state else None)))
