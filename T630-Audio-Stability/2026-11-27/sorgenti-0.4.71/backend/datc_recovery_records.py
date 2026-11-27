# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Bounded validation and independent process-liveness observations for recovery."""
from dataclasses import dataclass
import hashlib,json,os,re,stat
from pathlib import Path,PurePosixPath
from player_router import valid_usb_identity

class RecoveryEvidenceError(RuntimeError):pass
PHASES=frozenset(('preparing','router-bound','pcm-ready-muted','pcm-active',
                  'waiting-pcm','waiting-rme','waiting-source-observation','restoring-native'))
def uuid(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value) is not None
def digest_ok(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None
def unique(pairs):
    result={}
    for k,v in pairs:
        if k in result:raise RecoveryEvidenceError('Registro JSON con chiavi duplicate')
        result[k]=v
    return result
def invalid_constant(value):raise RecoveryEvidenceError('Registro JSON non finito')

@dataclass(frozen=True)
class RecordSnapshot:
    path:Path
    fingerprint:tuple
    value:dict

def read_owned(path,limit=65536):
    """No symlink, non-regular file, foreign owner, writable/shared or oversized file."""
    path=Path(path)
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    except FileNotFoundError:
        if os.path.lexists(path):raise RecoveryEvidenceError('Registro indiretto')
        return None
    except OSError as exc:raise RecoveryEvidenceError('Registro non accessibile in sicurezza') from exc
    try:
        a=os.fstat(fd)
        if (not stat.S_ISREG(a.st_mode) or a.st_uid!=os.geteuid()
                or a.st_mode&0o077 or a.st_nlink!=1 or a.st_size>limit):
            raise RecoveryEvidenceError('Proprietà del registro non verificata')
        raw=b''
        while len(raw)<=limit:
            block=os.read(fd,limit+1-len(raw))
            if not block:break
            raw+=block
        b=os.fstat(fd)
        def key(x):return (x.st_dev,x.st_ino,x.st_uid,x.st_mode,x.st_size,x.st_mtime_ns,x.st_ctime_ns,x.st_nlink)
        try:c=path.lstat()
        except OSError as exc:raise RecoveryEvidenceError('Registro cambiato durante lettura') from exc
        if len(raw)>limit or len(raw)!=a.st_size or key(a)!=key(b) or key(a)!=key(c):
            raise RecoveryEvidenceError('Registro cambiato durante lettura')
    finally:os.close(fd)
    try:value=json.loads(raw,object_pairs_hook=unique,parse_constant=invalid_constant)
    except (ValueError,UnicodeError,RecursionError) as exc:raise RecoveryEvidenceError('Registro JSON non valido') from exc
    if not isinstance(value,dict):raise RecoveryEvidenceError('Registro JSON non oggetto')
    return RecordSnapshot(path,key(a)+(hashlib.sha256(raw).hexdigest(),),value)

def require_unchanged(snapshot):
    current=read_owned(snapshot.path)
    if current is None or current.fingerprint!=snapshot.fingerprint:
        raise RecoveryEvidenceError('Registro modificato durante il recupero')

def retire(snapshot):
    require_unchanged(snapshot)
    snapshot.path.unlink()
    if os.path.lexists(snapshot.path):raise RecoveryEvidenceError('Registro non ritirato')
    fd=os.open(snapshot.path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)

def validate_ledger(record):
    if (not isinstance(record,dict) or set(record)!={'version','phase','target','boot_id'}
            or type(record['version']) is not int or record['version']!=1
            or not isinstance(record['phase'],str) or record['phase'] not in PHASES or not uuid(record['boot_id'])):
        raise RecoveryEvidenceError('Registro sessione non riconosciuto')
    t=record['target']
    if (not isinstance(t,dict) or set(t)!={'card','alsa_id','usb','mac','path','preferences_sha256'}
            or type(t['card']) is not int or not 0<=t['card']<=1024
            or not isinstance(t['alsa_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',t['alsa_id'])
            or not valid_usb_identity(t['usb']) or not isinstance(t['mac'],str)
            or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',t['mac'])
            or not digest_ok(t['preferences_sha256']) or not isinstance(t['path'],str)
            or len(t['path'])>4096 or any(ord(c)<32 for c in t['path'])):
        raise RecoveryEvidenceError('Identità sessione non valida')
    path=PurePosixPath(t['path'])
    if not path.is_absolute() or '..' in path.parts or path.name!='volume.conf':
        raise RecoveryEvidenceError('Percorso preferenze non valido')
    return record

def validate_children(value,ledger=None):
    if not isinstance(value,dict) or set(value)!={'children'} or not isinstance(value['children'],list) or len(value['children'])>3:
        raise RecoveryEvidenceError('Registro processi non riconosciuto')
    seen=set();boots=set()
    for r in value['children']:
        if (not isinstance(r,dict) or set(r)!={'pid','start','boot','cmd'}
                or type(r['pid']) is not int or not 2<=r['pid']<=4194304 or not uuid(r['boot'])
                or not digest_ok(r['cmd']) or not isinstance(r['start'],str)
                or not re.fullmatch(r'[0-9]{1,20}',r['start']) or r['pid'] in seen):
            raise RecoveryEvidenceError('Identità processo non valida')
        seen.add(r['pid']);boots.add(r['boot'])
    if len(boots)>1 or (ledger is not None and boots and boots!={ledger['boot_id']}):
        raise RecoveryEvidenceError('Generazione dei registri incoerente')
    return value['children']

def observe_absent(record,boot,proc=Path('/proc')):
    """Independent stat identity, not an acknowledgement from the stop call."""
    if record['boot']!=boot:return True
    try:raw=(Path(proc)/str(record['pid'])/'stat').read_text()
    except FileNotFoundError:return True
    try:fields=raw.rsplit(')',1)[1].split();state=fields[0];start=fields[19]
    except (IndexError,ValueError) as exc:raise RecoveryEvidenceError('Stat processo non verificabile') from exc
    if not re.fullmatch(r'[0-9]+',start) or state not in ('R','S','D','Z','T','t','X','x','K','W','P','I'):
        raise RecoveryEvidenceError('Stat processo non valido')
    return start!=record['start'] or state in ('Z','X','x')

def recover_children(snapshot,records,boot,identity,stop,absent=observe_absent):
    """Validate every still-live record before effects; confirm all gone before retirement."""
    live=[]
    for record in records:
        if absent(record,boot):continue
        if identity(record['pid'])!=record:raise RecoveryEvidenceError('Identità processo cambiata; nessun arresto')
        live.append(record)
    require_unchanged(snapshot)
    for record in reversed(live):
        if not absent(record,boot):
            if identity(record['pid'])!=record:raise RecoveryEvidenceError('Identità processo cambiata durante recupero')
            stop(record,timeout=5)
    if any(not absent(r,boot) for r in records):
        raise RecoveryEvidenceError('Processo audio ancora presente; recupero conservato')
    retire(snapshot)
