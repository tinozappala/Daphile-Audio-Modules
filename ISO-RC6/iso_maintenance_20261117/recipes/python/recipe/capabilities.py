import errno
import json
import os
import sys

def descriptor(function, *args):
    fd = function(*args)
    os.close(fd)

def setns_self_mount():
    fd = os.open('/proc/self/ns/mnt', os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.setns(fd, os.CLONE_NEWNS)
    finally:
        os.close(fd)

ce = {}
checks = {'memfd_create': (descriptor, (os.memfd_create, 'python-probe')),
          'pidfd_open': (descriptor, (os.pidfd_open, os.getpid())),
          'setns': (setns_self_mount, ()),
          'unshare': (os.unshare, (os.CLONE_NEWNS,))}
for name, (call, args) in checks.items():
    try:
        call(*args)
        ce[name] = 'PASS'
    except OSError as e:
        if e.errno not in (errno.EPERM, errno.EACCES):
            raise
        ce[name] = {'status': 'SKIP_environment', 'errno': e.errno}
print(json.dumps(ce, indent=2))
