"""Publish bounded, expiring identities only for owned optional PCM children."""
import hashlib,json,os,stat,time
from pathlib import Path
from common import atomic,identity,private

ROOT=Path('/run/daphile-audio-context')


def registry_root(host):
    # Isolated lifecycle hosts must NEVER publish over the production lease.
    return ROOT if host.root==Path('/mnt/hdd/daphile-audio-host') else host.root/'runtime-audio-context'


def context_pin(data):
    facts={}
    for name in ('active_profile.json','last_valid_config.json','engine-config.json','peq-intent.json','routing.json'):
        path=data/name
        if not path.exists():continue
        meta=path.lstat()
        if path.is_symlink() or not stat.S_ISREG(meta.st_mode) or meta.st_uid!=0 or meta.st_mode&0o022 or meta.st_size>1024*1024:
            raise RuntimeError('Private processor configuration required')
        digest=hashlib.sha256(path.read_bytes()).hexdigest();after=path.stat()
        if (meta.st_ino,meta.st_mtime_ns,meta.st_ctime_ns)!=(after.st_ino,after.st_mtime_ns,after.st_ctime_ns):
            raise RuntimeError('Processor configuration changed during read')
        facts[name]=dict(sha256=digest,inode=meta.st_ino,mtime=meta.st_mtime_ns,ctime=meta.st_ctime_ns)
    if not {'active_profile.json','peq-intent.json'}<=set(facts):raise RuntimeError('Complete processor configuration required')
    return hashlib.sha256(json.dumps(facts,sort_keys=True).encode()).hexdigest()


def task_starts(pid):
    result=[]
    for task in sorted((Path('/proc')/str(pid)/'task').glob('[0-9]*')):
        fields=(task/'stat').read_text().rsplit(') ',1)[1].split()
        if fields[0] not in ('Z','X'):result.append(dict(tid=int(task.name),start=fields[19]))
    if not 1<=len(result)<=64:raise RuntimeError('Bounded live PCM thread group required')
    return result


def collect(host,modules):
    owners=[]
    for name,status in modules.items():
        if not status.get('running') or status.get('error') or status.get('recovery_required'):continue
        state=host.state(name);path=state/'data/audio-processes.json'
        if not path.exists():continue
        if path.is_symlink() or path.stat().st_uid!=0 or stat.S_IMODE(path.stat().st_mode)!=0o600 or path.stat().st_size>16384:
            raise RuntimeError('Private owned audio child ledger required')
        controller=json.loads((state/'process.json').read_text())
        if identity(controller['pid'])!=controller:raise RuntimeError('Backend identity changed')
        ledger=json.loads(path.read_text());children=ledger.get('children')
        if set(ledger)!={'children'} or not isinstance(children,list) or len(children)>3:raise RuntimeError('Bounded child ledger required')
        pin=context_pin(state/'data') if children else None
        for child in children:
            if identity(child['pid'])!=child:raise RuntimeError('Audio child changed')
            proc=Path('/proc')/str(child['pid']);fields=(proc/'stat').read_text().rsplit(') ',1)[1].split()
            if proc.stat().st_uid!=0 or int(fields[1])!=controller['pid']:raise RuntimeError('Audio child has a different parent')
            tasks=task_starts(child['pid'])
            if identity(child['pid'])!=child:raise RuntimeError('Audio child changed during thread read')
            owners.append(dict(process={k:child[k] for k in ('pid','start','cmd')},threads=tasks,role='pcm-processor',context_sha256=pin))
        if identity(controller['pid'])!=controller:raise RuntimeError('Backend exited during owner read')
        if children and context_pin(state/'data')!=pin:raise RuntimeError('Processor configuration changed during publication')
    if len(owners)>8:raise RuntimeError('Too many optional PCM owners')
    return owners


def publish(host,modules,root=None):
    if os.geteuid()!=0:raise RuntimeError('Only root can publish audio ownership')
    root=private(registry_root(host) if root is None else root);me=identity(os.getpid());now=time.monotonic()
    atomic(root/'owners.json',dict(schema=1,boot_id=me['boot'],issued=now,expires=now+30,
        provider={k:me[k] for k in ('pid','start','cmd')},owners=collect(host,modules)))


def withdraw(host,root=None):
    path=Path(registry_root(host) if root is None else root)/'owners.json'
    if not path.exists():return
    if path.is_symlink():raise RuntimeError('Indirect audio registry retained')
    record=json.loads(path.read_text());me=identity(os.getpid())
    if record.get('provider')=={k:me[k] for k in ('pid','start','cmd')}:path.unlink()
