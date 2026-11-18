#!/usr/bin/python3
"""Compatibility monitor; never imposes an unmeasured fixed CPU/IRQ policy."""
import fcntl,json,os,signal,time
from pathlib import Path

BASE=Path('/run/daphile-t630-affinity')
BASE.mkdir(mode=0o700,exist_ok=True)
if BASE.is_symlink() or BASE.stat().st_uid!=0:raise RuntimeError('Unsafe compatibility monitor directory')
lock=(BASE/'lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
stop=False


def finish(*_):
    global stop
    stop=True


signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
(BASE/'pid').write_text(str(os.getpid()))
try:
    while not stop and not (BASE/'stop').exists():
        target=BASE/'status.json';temporary=BASE/'.status.new'
        with temporary.open('w') as stream:
            json.dump(dict(status='monitor-only',settings_written=False,reason='Runtime changes require measured AutoTune validation',time=time.time()),stream)
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,target)
        for _ in range(20):
            if stop or (BASE/'stop').exists():break
            time.sleep(.5)
finally:
    if (BASE/'pid').read_text()==str(os.getpid()):(BASE/'pid').unlink()
    lock.close()

__release_signature__ = 'Tino Zappala'
