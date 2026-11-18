"""Crash-resumable retirement of an exact historical zero-control result.

Only three runtime metadata files change. Original private bytes remain in a
separate backup; no playlist, settings, boot or hardware API is present here.
The caller supplies the native state lock and an actual idle/recovery guard.
"""
import hashlib,json,os,re,stat,uuid
from pathlib import Path
from .legacy_reference import NAMES,historical_completion_plan
from .reference_state import atomic

MARKER='DAPHILE_V2_EMPTY_REFERENCE_MIGRATION_V1'
digest=lambda b:hashlib.sha256(b).hexdigest()
encoded=lambda v:(json.dumps(v,allow_nan=False,sort_keys=True)+'\n').encode()


def read(path):
    meta=path.lstat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_size>8*1024*1024:raise RuntimeError('Bounded regular metadata required')
    return path.read_bytes()


def private_write(path,data):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())


def syncdir(path):
    if os.name=='posix':
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def complete(stage,boot,host,locked,check_idle,write=atomic):
    """Resume this exact owned retirement; a newer state is never overwritten."""
    stage=Path(stage);root=stage.parent.parent
    if stage.is_symlink() or stage.parent.name!='reference-retirements' or stage.parent.is_symlink() or root.is_symlink() or not re.fullmatch('[a-f0-9]{32}',stage.name):
        raise RuntimeError('Unknown retirement stage')
    if read(stage/'.owned')!=MARKER.encode():raise RuntimeError('Unowned retirement stage')
    if os.name=='posix' and (stage.stat().st_uid!=os.geteuid() or stat.S_IMODE(stage.stat().st_mode)!=0o700):
        raise RuntimeError('Private retirement stage permissions required')
    with locked(root):
        check_idle()
        record=json.loads(read(stage/'manifest.json'))
        if record.get('boot')!=boot or record.get('host')!=host:raise RuntimeError('Retirement context changed')
        before={n:read(stage/n) for n in (*NAMES,'automatic.json')}
        if {n:digest(b) for n,b in before.items()}!=record['original_sha256']:raise RuntimeError('Private retirement backup changed')
        if read(root/'automatic.json')!=before['automatic.json']:raise RuntimeError('Newer automatic owner preserved')
        replacements=historical_completion_plan({n:json.loads(before[n]) for n in NAMES},json.loads(before['automatic.json']),boot,host)
        after={n:encoded(replacements[n]) for n in NAMES}
        # Validate the entire set BEFORE the first write, including a file
        # already replaced just before a prior process died.
        current={n:read(root/n) for n in NAMES}
        if any(current[n] not in (before[n],after[n]) for n in NAMES):raise RuntimeError('Concurrent metadata change preserved')
        for name in NAMES:
            if read(root/'automatic.json')!=before['automatic.json']:raise RuntimeError('Automatic owner changed during retirement')
            now=read(root/name)
            if now==before[name]:write(root/name,replacements[name])
            elif now!=after[name]:raise RuntimeError('Concurrent metadata change preserved')
            if read(root/name)!=after[name]:raise RuntimeError('Retirement write readback differs')
            record['replaced']=[n for n in NAMES if read(root/n)==after[n]]
            atomic(stage/'manifest.json',record)
        record.update(status='PASS_EMPTY_REFERENCE_METADATA_RETIRED',playback_commands=0,control_writes=0)
        atomic(stage/'manifest.json',record)
        return dict(status=record['status'],retired_files=list(NAMES),private_backup=str(stage),
            backup_sha256=record['original_sha256'],playback_commands=0,control_writes=0,
            automatic_record_byte_unchanged=True,boot_configuration_changed=False)


def retire(root,boot,host,locked,check_idle,write=atomic):
    root=Path(root)
    if root.is_symlink():raise RuntimeError('Indirect state root refused')
    with locked(root):
        check_idle()
        before={n:read(root/n) for n in (*NAMES,'automatic.json')}
        historical_completion_plan({n:json.loads(before[n]) for n in NAMES},json.loads(before['automatic.json']),boot,host)
        folder=root/'reference-retirements'
        if folder.is_symlink():raise RuntimeError('Indirect private backup root refused')
        folder.mkdir(mode=0o700,exist_ok=True)
        if os.name=='posix' and (folder.stat().st_uid!=os.geteuid() or stat.S_IMODE(folder.stat().st_mode)!=0o700):
            raise RuntimeError('Private retirement backup root required')
        stage=folder/uuid.uuid4().hex;stage.mkdir(mode=0o700)
        private_write(stage/'.owned',MARKER.encode())
        for name,data in before.items():private_write(stage/name,data)
        atomic(stage/'manifest.json',dict(status='prepared',boot=boot,host=host,replaced=[],original_sha256={n:digest(b) for n,b in before.items()}))
        syncdir(folder);syncdir(root)
    # Reacquire and CAS the whole state. A new owner between locks is kept;
    # complete() will refuse instead of retiring a different configuration.
    return complete(stage,boot,host,locked,check_idle,write)


def detach_current(stage,boot,host,locked,check_idle):
    """Remove only the exact retired pointer, retaining all original backups.

    A reference has no applied current policy. The retirement manifest and the
    two retained journal files remain the proof, including after process death
    immediately after unlink. No generic 'empty current' deletion is allowed.
    """
    stage=Path(stage);root=stage.parent.parent
    if stage.is_symlink() or stage.parent.is_symlink() or root.is_symlink() or stage.parent.name!='reference-retirements' or not re.fullmatch('[a-f0-9]{32}',stage.name):
        raise RuntimeError('Unknown retirement stage')
    if read(stage/'.owned')!=MARKER.encode():raise RuntimeError('Unowned retirement stage')
    if os.name=='posix' and (stage.stat().st_uid!=os.geteuid() or stat.S_IMODE(stage.stat().st_mode)!=0o700):
        raise RuntimeError('Private retirement stage required')
    if (root/'current.json').exists() or (root/'current.json').is_symlink():
        complete(stage,boot,host,locked,check_idle)
    with locked(root):
        check_idle();record=json.loads(read(stage/'manifest.json'))
        if record.get('boot')!=boot or record.get('host')!=host or record.get('status')!='PASS_EMPTY_REFERENCE_METADATA_RETIRED':
            raise RuntimeError('Completed retirement context required')
        if {n:digest(read(stage/n)) for n in (*NAMES,'automatic.json')}!=record['original_sha256']:
            raise RuntimeError('Private retirement backup changed')
        originals={n:json.loads(read(stage/n)) for n in NAMES}
        automatic=json.loads(read(stage/'automatic.json'))
        expected=historical_completion_plan(originals,automatic,boot,host)
        if read(root/'automatic.json')!=read(stage/'automatic.json'):
            raise RuntimeError('Newer automatic owner preserved')
        for name in ('transaction.json','last-known-good.json'):
            if read(root/name)!=encoded(expected[name]):raise RuntimeError('Retired journal changed')
        pointer=root/'current.json'
        if pointer.exists() or pointer.is_symlink():
            if read(pointer)!=encoded(expected['current.json']):raise RuntimeError('Newer current policy preserved')
            pointer.unlink();syncdir(root)
        record['current_pointer_detached']=True;atomic(stage/'manifest.json',record)
        return dict(status='PASS_RETIRED_REFERENCE_POINTER_DETACHED',private_backup=str(stage),
                    playback_commands=0,control_writes=0)


def detach_transaction(stage,boot,host,locked,check_idle):
    """A proved zero-control reference also has no pending runtime transaction.

    This is done before the new automatic owner is written. All old bytes and
    the immutable last-known-good retirement remain private and inspectable.
    A real, changed or uncertain transaction is never deleted here.
    """
    stage=Path(stage);root=stage.parent.parent
    if stage.is_symlink() or stage.parent.is_symlink() or root.is_symlink() or stage.parent.name!='reference-retirements' or not re.fullmatch('[a-f0-9]{32}',stage.name):
        raise RuntimeError('Unknown retirement stage')
    if read(stage/'.owned')!=MARKER.encode():raise RuntimeError('Unowned retirement stage')
    if os.name=='posix' and (stage.stat().st_uid!=os.geteuid() or stat.S_IMODE(stage.stat().st_mode)!=0o700):
        raise RuntimeError('Private retirement stage required')
    with locked(root):
        check_idle();record=json.loads(read(stage/'manifest.json'))
        if record.get('boot')!=boot or record.get('host')!=host or record.get('status')!='PASS_EMPTY_REFERENCE_METADATA_RETIRED' or record.get('current_pointer_detached') is not True:
            raise RuntimeError('Completed detached retirement required')
        raw={n:read(stage/n) for n in (*NAMES,'automatic.json')}
        if {n:digest(b) for n,b in raw.items()}!=record['original_sha256']:raise RuntimeError('Private retirement backup changed')
        if read(root/'automatic.json')!=raw['automatic.json']:raise RuntimeError('Newer automatic owner preserved')
        expected=historical_completion_plan({n:json.loads(raw[n]) for n in NAMES},json.loads(raw['automatic.json']),boot,host)
        if (root/'current.json').exists() or (root/'current.json').is_symlink():raise RuntimeError('Newer current policy preserved')
        if read(root/'last-known-good.json')!=encoded(expected['last-known-good.json']):raise RuntimeError('Retired history changed')
        path=root/'transaction.json'
        if path.exists() or path.is_symlink():
            if read(path)!=encoded(expected['transaction.json']):raise RuntimeError('Newer runtime transaction preserved')
            path.unlink();syncdir(root)
        record['transaction_pointer_detached']=True;atomic(stage/'manifest.json',record)
        return dict(status='PASS_EMPTY_REFERENCE_TRANSACTION_DETACHED',playback_commands=0,control_writes=0)

__release_signature__ = 'Tino Zappala'
