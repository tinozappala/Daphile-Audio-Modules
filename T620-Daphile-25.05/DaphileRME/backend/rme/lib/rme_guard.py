"""Pinned-process expiry guard for standalone RME transaction watchdogs."""
import json
import math
import os
from pathlib import Path
import signal
import time

FINAL={'committed','restored','rolled-back','no-change'}

def load(path):return json.loads(Path(path).read_text())
def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def process_start(pid):
    fields=Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()
    return None if fields[0] in ('Z','X') else fields[19]

def expired(record,ident):
    deadline=record.get('deadline')
    return (record.get('id')==ident and record.get('status') in {'armed','writing','verifying','rollback-incomplete'}
            and type(deadline) in (int,float) and math.isfinite(deadline)
            and deadline<=time.time())

def terminate_expired(path,ident):
    """Never redirect a stale PID or a completed journal into a termination."""
    first=load(path);pid=first.get('parent_pid');start=first.get('parent_start')
    if not expired(first,ident) or type(pid) is not int or pid<=1 or not start or first.get('boot_id')!=boot_id():return False
    try:fd=os.pidfd_open(pid)
    except ProcessLookupError:return False
    try:
        fresh=load(path)
        if not expired(fresh,ident) or any(fresh.get(k)!=first.get(k) for k in ('parent_pid','parent_start','boot_id')):return False
        if process_start(pid)!=start:return False
        try:signal.pidfd_send_signal(fd,signal.SIGTERM)
        except ProcessLookupError:return False
        return True
    except (FileNotFoundError,ProcessLookupError):return False
    finally:os.close(fd)
