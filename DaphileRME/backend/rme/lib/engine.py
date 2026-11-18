import contextlib,fcntl,json,os,uuid
from pathlib import Path
from detection import read
STATE=Path("/mnt/hdd/daphile-autotune")

def atomic(path,value):
 path=Path(path);tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
 with tmp.open('x') as out:
  os.chmod(tmp,0o600);json.dump(value,out,indent=2);out.write(chr(10));out.flush();os.fsync(out.fileno())
 os.replace(tmp,path);fd=os.open(path.parent,os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)

def load(path):return json.loads(Path(path).read_text())

@contextlib.contextmanager
def locked(state=STATE):
    state = Path(state)
    if state.is_symlink() or not state.parent.is_dir():
        raise RuntimeError('Unsafe or absent persistent state directory')
    state.mkdir(mode=448, exist_ok=True)
    if state.stat().st_uid != os.geteuid() or state.stat().st_mode & 63:
        raise RuntimeError('State must be private to its owner')
    if any((p.is_symlink() for p in state.iterdir())):
        raise RuntimeError('State contains symlinks')
    with (state / 'lock').open('a') as f:
        os.chmod(state / 'lock', 384)
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield state

def start_ticks(pid):
 data=read('/proc/'+str(pid)+'/stat')
 if not data:return None
 fields=data[data.rfind(')')+2:].split()
 return fields[19] if len(fields)>19 and fields[0] not in ['Z','X'] else None
