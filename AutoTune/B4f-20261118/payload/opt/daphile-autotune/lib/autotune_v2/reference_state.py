"""A no-change calibration is evidence, never an applied runtime transaction.

First redesign component. No playback, rollback or hardware-control API exists
here. Integration with the complete calibration workflow remains to qualify.
"""
import contextlib,json,os,re,stat,tempfile,uuid
from pathlib import Path
from .accounting import issues as accounting_issues
try:from measurement_contract import optimization_issues
except ImportError:from ..measurement_contract import optimization_issues

IDENTITY={'boot_id','hardware_sha256','preferences_sha256','workload_sha256'}

def identity(value):
    if not isinstance(value,dict) or set(value)!=IDENTITY:raise ValueError('Complete observation identity required')
    try:valid_boot=isinstance(value['boot_id'],str) and str(uuid.UUID(value['boot_id']))==value['boot_id']
    except ValueError:valid_boot=False
    if not valid_boot:raise ValueError('Boot identity required')
    if any(not isinstance(value[k],str) or not re.fullmatch('[a-f0-9]{64}',value[k]) for k in IDENTITY-{'boot_id'}):raise ValueError('Verified fingerprints required')
    return dict(value)

def reference_proof(binding,observations,require_current_accounting=True):
    binding=identity(binding)
    if not isinstance(observations,list) or len(observations)<3:raise ValueError('Three complete reference observations required')
    for row in observations:
        if not isinstance(row,dict):raise ValueError('Structured reference observation required')
        if not require_current_accounting:
            # Historical bytes provide a CAS identity only. A newer metric
            # contract must not prevent reading/replacing an old observation.
            # current() below still requires the entire current contract.
            json.dumps(row,allow_nan=False)
            continue
        accounting=accounting_issues(row)
        # A read of an archived accounting version grants no new authority.
        # Current-version records always need their current boundary evidence.
        issues=optimization_issues(row,require_current_boundary=require_current_accounting or not accounting)
        if require_current_accounting:issues+=accounting
        if issues:raise ValueError('Incomplete reference: '+'; '.join(issues))
    return dict(schema=2,id=uuid.uuid4().hex,kind='verified-reference',binding=binding,
                observations=observations,settings_changed=False,restore_available=False)

def atomic(path,value):
    if path.is_symlink():raise RuntimeError('Indirect state path refused')
    fd,temp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        if os.name=='posix':os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf8',newline='\n') as f:
            json.dump(value,f,allow_nan=False,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
        if os.name=='posix':
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def validate_proof(proof,require_current_accounting=False):
    if not isinstance(proof,dict) or proof.get('kind')!='verified-reference' or proof.get('schema')!=2 or proof.get('settings_changed') is not False or proof.get('restore_available') is not False:raise ValueError('A reference must not contain applied controls')
    rebuilt=reference_proof(proof['binding'],proof['observations'],require_current_accounting)
    if set(proof)!=set(rebuilt) or not re.fullmatch('[a-f0-9]{32}',str(proof['id'])):raise ValueError('Unexpected proof fields')

class ReferenceStore:
    def __init__(self,root):
        self.root=Path(root)
        if self.root.is_symlink():raise RuntimeError('Indirect evidence store refused')
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)

    @contextlib.contextmanager
    def locked(self):
        path=self.root/'.reference.lock'
        if path.is_symlink():raise RuntimeError('Indirect evidence lock refused')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        stream=os.fdopen(fd,'r+b')
        try:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise RuntimeError('Regular evidence lock required')
            if os.name=='posix':
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            else:
                import msvcrt
                if os.fstat(stream.fileno()).st_size==0:stream.write(b'0');stream.flush()
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_LOCK,1)
            try:yield
            finally:
                if os.name=='posix':fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
                else:stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        finally:stream.close()

    def archive(self,folder,value):
        directory=self.root/folder
        if directory.is_symlink():raise RuntimeError('Indirect history refused')
        directory.mkdir(mode=0o700,exist_ok=True)
        path=directory/(value['id']+'.json')
        # O_EXCL also refuses an existing symlink or a colliding identifier.
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf8',newline='\n') as stream:
            json.dump(value,stream,allow_nan=False,sort_keys=True);stream.write('\n')
            stream.flush();os.fsync(stream.fileno())
        if os.name=='posix':
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)

    def attempt(self,outcome,reason):
        if outcome not in ('blocked','inconclusive','interrupted'):raise ValueError('This operation records refused attempts only')
        # Independent file: a refused attempt cannot destroy current evidence.
        value=dict(schema=2,id=uuid.uuid4().hex,status=outcome,reason=str(reason),settings_changed=False)
        with self.locked():
            self.archive('attempts',value)
            atomic(self.root/'last-attempt.json',value)

    def save(self,proof,expected_id=None):
        proof=json.loads(json.dumps(proof,allow_nan=False))
        validate_proof(proof,require_current_accounting=True)
        with self.locked():
            path=self.root/'verified-reference.json'
            if path.is_symlink():raise RuntimeError('Indirect reference refused')
            current=json.loads(path.read_text(encoding='utf8')) if path.exists() else None
            if current is not None:validate_proof(current)
            if (current['id'] if current else None)!=expected_id:raise RuntimeError('Reference changed; stale writer refused')
            self.archive('history',proof)
            atomic(path,proof)

    def head_id(self):
        """Snapshot the validated CAS head, including a historical binding.

        This grants no runtime authority. A subsequent save still compares
        this exact identifier under the store lock and refuses a newer writer.
        """
        with self.locked():
            path=self.root/'verified-reference.json'
            if path.is_symlink():raise RuntimeError('Indirect reference refused')
            if not path.exists():return None
            proof=json.loads(path.read_text(encoding='utf8'));validate_proof(proof)
            archived=self.root/'history'/(proof['id']+'.json')
            if archived.parent.is_symlink() or archived.is_symlink() or not archived.is_file():raise RuntimeError('Reference history missing')
            if json.loads(archived.read_text(encoding='utf8'))!=proof:raise RuntimeError('Reference history differs')
            return proof['id']

    def current(self,fresh_binding):
        fresh_binding=identity(fresh_binding);path=self.root/'verified-reference.json'
        with self.locked():
            if path.is_symlink():raise RuntimeError('Indirect reference refused')
            if not path.exists():return {'status':'unmeasured','restore_available':False}
            proof=json.loads(path.read_text(encoding='utf8'))
            validate_proof(proof)
            archived=self.root/'history'/(proof['id']+'.json')
            if archived.parent.is_symlink() or archived.is_symlink() or not archived.is_file():raise RuntimeError('Reference history missing')
            if json.loads(archived.read_text(encoding='utf8'))!=proof:raise RuntimeError('Reference history differs')
        if proof.get('binding')!=fresh_binding:return {'status':'historical','restore_available':False}
        if any(accounting_issues(row) or optimization_issues(row) for row in proof['observations']):
            return {'status':'historical','restore_available':False,'reason':'measurement-contract-version'}
        # Validate stored observations again; corrupted/incomplete data cannot
        # become a current result merely because a previous status says PASS.
        return {'status':'verified-reference','id':proof['id'],'settings_changed':False,'restore_available':False,'observed_windows':len(proof['observations'])}

__release_signature__ = 'Tino Zappala'
