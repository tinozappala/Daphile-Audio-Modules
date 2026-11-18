"""Signal only the same Linux process, even if its numeric PID is reused."""
import os
import signal


def signal_record(record, sig, pid_key='pid', start_key='start'):
    return signal_owner({'owner_pid':record.get(pid_key),
                         'owner_start':record.get(start_key),
                         'boot_id':record.get('boot_id')}, sig)


def signal_owner(record, sig):
    import engine
    pid = record.get('owner_pid')
    start = record.get('owner_start')
    if type(pid) is not int or pid <= 1 or not start:
        return False
    if record.get('boot_id') != engine.boot_id():
        return False
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    try:
        if engine.start_ticks(pid) != start:
            return False
        # The descriptor pins this process. An exit/reuse after the identity
        # check cannot redirect the signal to its replacement.
        try:
            signal.pidfd_send_signal(fd, sig)
        except ProcessLookupError:
            return False
        return True
    finally:
        os.close(fd)

__release_signature__ = 'Tino Zappala'
