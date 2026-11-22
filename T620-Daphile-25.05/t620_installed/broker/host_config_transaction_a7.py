"""Recoverable file-only installation of an already stopped native audio host.

This module uses only the standard library. Its exact bytes are copied to a
private stage before publication and run there as the independent guardian.
No service, player, mixer, preference or kernel control is ever changed.
Directory descriptors prevent symlink redirection; the maintenance locks are
cooperative, not a filesystem-wide CAS against another administrator.
"""
import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import time
import uuid

SCHEMA = 'daphile-existing-host-transaction-a7'
MAX_FILE = 256 * 1024 * 1024
MAX_PLAN = 4 * 1024 * 1024
INSTALL_DEADLINE_SECONDS = 180
TERMINAL = {'installed', 'rolled-back'}
MODULES = ('DaphileRME', 'DaphileCamillaDSP')
FILE_KEYS = {'dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'bytes', 'mtime_ns', 'ctime_ns', 'sha256'}
DIR_KEYS = {'dev', 'ino', 'mode', 'uid', 'gid'}


"""Embedded into new TxA4; references its original checked file primitives.

This is a kernel maintenance lock, never an execution grant or a backup seal.
Only dup/pass_fds preserve its open description across installer and guardians.
"""

_LIVE_MAINTENANCE={}
_CURRENT_MAINTENANCE=None
_LEASE_KEYS={'image','root_directory','file_mount_id','root_mount_id'}

def _fd_mount_and_flock(fd,require_lock=False):
    with Path('/proc/self/fdinfo',str(fd)).open('rb')as stream:info=stream.read(16385)
    if len(info)>16384:raise RuntimeError('Bounded native lock fdinfo required')
    text=info.decode('ascii');mounts=re.findall(r'^mnt_id:\s+(\d+)$',text,re.M)
    if len(mounts)!=1:raise RuntimeError('Native maintenance mount identity missing')
    if require_lock:
        value=os.fstat(fd)
        pattern=r'^lock:\s+\d+:\s+FLOCK\s+ADVISORY\s+WRITE\s+-?\d+\s+([0-9a-fA-F]+):([0-9a-fA-F]+):(\d+)\s+0\s+EOF$'
        locks=re.findall(pattern,text,re.M)
        if len(locks)!=1 or tuple(int(v,16)for v in locks[0][:2])!=(os.major(value.st_dev),os.minor(value.st_dev)) or int(locks[0][2])!=value.st_ino:
            raise RuntimeError('Descriptor does not own the exact native Host flock')
    return int(mounts[0])

def _validate_lease_description(value):
    if type(value)is not dict or set(value)!=_LEASE_KEYS:raise RuntimeError('Exact native maintenance description required')
    validate_state(value['image']);validate_state(value['root_directory'],directory_only=True)
    image=value['image']
    if image is None or image['mode']!=0o600 or image['bytes']!=0 or image['sha256']!=sha(b'') or image['uid']!=0 or image['nlink']!=1:
        raise RuntimeError('Empty private original Host lock required')
    if any(type(value[k])is not int or value[k]<=0 for k in ('file_mount_id','root_mount_id')):
        raise RuntimeError('Kernel maintenance mount identities required')

class HostMaintenanceLease:
    """Registry-backed owned descriptor. Holding it does not authorize writes."""
    __slots__=()
    @property
    def fd(self):
        if self not in _LIVE_MAINTENANCE:raise RuntimeError('Closed or forged Host maintenance lease')
        return _LIVE_MAINTENANCE[self][1]
    @property
    def root(self):
        if self not in _LIVE_MAINTENANCE:raise RuntimeError('Closed or forged Host maintenance lease')
        return Path(_LIVE_MAINTENANCE[self][0])
    @property
    def description(self):
        if self not in _LIVE_MAINTENANCE:raise RuntimeError('Closed or forged Host maintenance lease')
        return decode(_LIVE_MAINTENANCE[self][2])
    def check(self):
        if self not in _LIVE_MAINTENANCE:raise RuntimeError('Genuine live native maintenance lease required')
        _validate_lease_description(self.description)
        regular(os.fstat(self.fd),private=True);no_extended_attributes(self.fd)
        if stat_image(os.fstat(self.fd))!={k:v for k,v in self.description['image'].items()if k!='sha256'} or os.pread(self.fd,1,0)!=b'':
            raise RuntimeError('Native Host lock FD changed')
        if observe(self.root/'host.lock')!=self.description['image'] or dir_state(self.root)!=self.description['root_directory']:
            raise RuntimeError('Native Host maintenance route or root changed')
        with directory(self.root)as current:
            if _fd_mount_and_flock(current)!=self.description['root_mount_id'] or dir_state(self.root)!=self.description['root_directory']:
                raise RuntimeError('Native Host root kernel mount changed')
        if _fd_mount_and_flock(self.fd,True)!=self.description['file_mount_id']:
            raise RuntimeError('Native Host lock kernel mount changed')
        return self
    def close(self):
        record=_LIVE_MAINTENANCE.pop(self,None)
        if record is not None:
            try:current=os.fstat(record[1])
            except OSError as error:
                if error.errno==errno.EBADF:return
                raise
            expected=decode(record[2])['image']
            if (current.st_dev,current.st_ino)!=(expected['dev'],expected['ino']):raise RuntimeError('Lost Host descriptor was reused; foreign FD retained')
            os.close(record[1])
    def __enter__(self):return self.check()
    def __exit__(self,*unused):self.close()

def _enroll_maintenance(root,fd,description):
    value=object.__new__(HostMaintenanceLease);_LIVE_MAINTENANCE[value]=(str(absolute(root)),fd,encode(description))
    try:return value.check()
    except BaseException:value.close();raise

def acquire_host_maintenance(root,expected_image):
    """Lock only an already existing exact inode; no files are created."""
    if os.geteuid()!=0:raise RuntimeError('Native root Host maintenance required')
    root=absolute(root);validate_state(expected_image)
    if expected_image is None or observe(root/'host.lock')!=expected_image:raise RuntimeError('Explicit existing Host lock preimage required')
    with directory(root)as parent:
        root_image=dir_state(root);root_mid=_fd_mount_and_flock(parent)
        fd=os.open('host.lock',os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
    try:
        regular(os.fstat(fd),private=True);no_extended_attributes(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        description=dict(image=expected_image,root_directory=root_image,file_mount_id=_fd_mount_and_flock(fd,True),root_mount_id=root_mid)
        _validate_lease_description(description)
    except BaseException:os.close(fd);raise
    return _enroll_maintenance(root,fd,description)

def _maintenance_from_inherited(root,fd,description):
    if type(fd)is not int or fd<0:raise RuntimeError('Inherited native Host descriptor required')
    _validate_lease_description(description)
    return _enroll_maintenance(root,os.dup(fd),description)

def _check_plan_maintenance(plan):
    description=plan.get('borrowed_host_lock')
    if description is None:return None
    _validate_lease_description(description);value=_CURRENT_MAINTENANCE
    if type(value)is not HostMaintenanceLease or value.root!=Path(plan['root']) or value.description!=description:
        raise RuntimeError('Original inherited Host maintenance lease required for this plan')
    return value.check()

@contextlib.contextmanager
def _borrowed_fd_context(lease):
    lease.check();fd=os.dup(lease.fd)
    try:
        if _fd_mount_and_flock(fd,True)!=lease.description['file_mount_id']:raise RuntimeError('Duplicate Host maintenance descriptor differs')
        yield fd
    finally:os.close(fd)

@contextlib.contextmanager
def _maintenance_context(lease=None,*,stage=None,pin=None,inherited_fd=None):
    global _CURRENT_MAINTENANCE
    prior=_CURRENT_MAINTENANCE;owned=None
    try:
        if inherited_fd is not None:
            if prior is not None or lease is not None:raise RuntimeError('Conflicting inherited Host maintenance lease')
            plan=validate_stage(stage,pin)
            if plan.get('borrowed_host_lock')is None:raise RuntimeError('Unrequested inherited Host descriptor refused')
            owned=_maintenance_from_inherited(plan['root'],inherited_fd,plan['borrowed_host_lock']);lease=owned
        if lease is not None:
            if prior is not None or type(lease)is not HostMaintenanceLease:raise RuntimeError('Conflicting or forged native maintenance lease')
            lease.check();_CURRENT_MAINTENANCE=lease
        if stage is not None:_check_plan_maintenance(validate_stage(stage,pin))
        yield
    finally:
        _CURRENT_MAINTENANCE=prior
        if owned is not None:owned.close()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise RuntimeError('Duplicate transaction JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def absolute(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Canonical absolute transaction path required')
    return path


def directory_safe(info, ancestor=False):
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise RuntimeError('Direct root-owned transaction directory required')
    if info.st_mode & 0o022 and not (ancestor and info.st_mode & stat.S_ISVTX):
        raise RuntimeError('Writable transaction directory refused')


def no_extended_attributes(fd):
    if not hasattr(os, 'listxattr'):
        raise RuntimeError('Extended metadata observation is unavailable')
    try:
        names = os.listxattr(fd)
    except OSError as error:
        if error.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return
        raise
    if names:
        raise RuntimeError('Extended attributes or ACLs require a qualified preservation adapter')


@contextlib.contextmanager
def directory(path, allow_sticky=False):
    path = absolute(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open('/', flags)
    try:
        directory_safe(os.fstat(fd), ancestor=path != Path('/'))
        no_extended_attributes(fd)
        for i, part in enumerate(path.parts[1:]):
            child = os.open(part, flags, dir_fd=fd)
            try:
                directory_safe(os.fstat(child), ancestor=allow_sticky or i < len(path.parts) - 2)
                no_extended_attributes(child)
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def dir_state(path, ancestor=False):
    try:
        with directory(path, allow_sticky=ancestor) as fd:
            info = os.fstat(fd)
            return dict(dev=info.st_dev, ino=info.st_ino, mode=stat.S_IMODE(info.st_mode),
                        uid=info.st_uid, gid=info.st_gid)
    except FileNotFoundError:
        return None


def stat_image(info):
    return dict(dev=info.st_dev, ino=info.st_ino, mode=stat.S_IMODE(info.st_mode),
                uid=info.st_uid, gid=info.st_gid, nlink=info.st_nlink,
                bytes=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def regular(info, private=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & (0o077 if private else 0o022)):
        raise RuntimeError('Unsafe or indirect transaction file')


def read_at(parent, name, limit=MAX_FILE, private=False):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, 'rb') as stream:
        first = os.fstat(stream.fileno())
        regular(first, private)
        no_extended_attributes(stream.fileno())
        if first.st_size > limit:
            raise RuntimeError('Transaction file exceeds its size bound')
        # Keep the original full-read and limit+1 semantics, without a
        # speculative MAX_FILE-sized allocation for a tiny regular file.
        chunks = []
        remaining = limit + 1
        while remaining:
            block = stream.read(min(64 * 1024, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        data = b''.join(chunks)
        last = os.fstat(stream.fileno())
        if len(data) > limit or stat_image(first) != stat_image(last):
            raise RuntimeError('Transaction source changed during read')
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat_image(named) != stat_image(last):
            raise RuntimeError('Transaction source name changed during read')
        no_extended_attributes(stream.fileno())
        result = stat_image(last)
        result['sha256'] = sha(data)
        return data, result


def read(path, limit=MAX_FILE, private=False):
    path = absolute(path)
    try:
        with directory(path.parent, allow_sticky=True) as parent:
            return read_at(parent, path.name, limit, private)
    except FileNotFoundError:
        return None, None


def observe(path):
    return read(path)[1]


def same_parent(path, fd):
    original = os.fstat(fd)
    with directory(path) as current:
        actual = os.fstat(current)
        if (original.st_dev, original.st_ino) != (actual.st_dev, actual.st_ino):
            raise RuntimeError('Transaction parent directory changed')


def owned_image(current, expected):
    # rename can change ctime. An intent already records the inode allocated by
    # this transaction, payload, metadata and mtime before the rename. Once the
    # completed postimage is journaled, all fields including ctime are compared.
    return current is not None and expected is not None and all(
        current.get(key) == value for key, value in expected.items() if key != 'ctime_ns')


def validate_state(value, directory_only=False):
    if value is None:
        return
    keys = DIR_KEYS if directory_only else FILE_KEYS
    if (type(value) is not dict or set(value) != keys
            or any(type(value[k]) is not int for k in keys - {'sha256'})
            or value['dev'] < 0 or value['ino'] <= 0 or value['uid'] != 0 or value['gid'] < 0
            or not 0 <= value['mode'] <= 0o7777):
        raise RuntimeError('Malformed owned filesystem image')
    if not directory_only and (value['nlink'] != 1 or not 0 <= value['bytes'] <= MAX_FILE
            or value['mode'] & 0o022 or type(value['sha256']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value['sha256']) is None):
        raise RuntimeError('Malformed owned regular-file image')


def validate_process(value):
    if (type(value) is not dict or set(value) != {'pid', 'start', 'boot', 'cmd_sha256'}
            or type(value['pid']) is not int or value['pid'] <= 0
            or type(value['start']) is not str or not value['start'].isdigit()
            or type(value['boot']) is not str or not 1 <= len(value['boot']) <= 128
            or type(value['cmd_sha256']) is not str
            or re.fullmatch(r'[0-9a-f]{64}', value['cmd_sha256']) is None):
        raise RuntimeError('Malformed transaction process identity')


def private_write(path, data, mode=0o600, expected=None):
    """Atomic stage metadata; its complete preimage must still be owned."""
    path = absolute(path)
    with directory(path.parent) as parent:
        if read_at(parent, path.name, MAX_PLAN, True)[1] != expected:
            raise RuntimeError('Transaction metadata ownership lost')
        name = '.' + path.name + '.' + uuid.uuid4().hex
        token = None
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
                token = stat_image(os.fstat(stream.fileno()))
                token['sha256'] = sha(data)
            same_parent(path.parent, parent)
            if read_at(parent, path.name, MAX_PLAN, True)[1] != expected:
                raise RuntimeError('Transaction metadata changed before publication')
            if read_at(parent, name, MAX_PLAN, True)[1] != token:
                raise RuntimeError('Prepared transaction metadata changed before publication')
            os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            result = read_at(parent, path.name, MAX_PLAN, True)[1]
            if not owned_image(result, token):
                raise RuntimeError('Published transaction metadata differs from prepared inode')
            return result
        finally:
            if token is not None:
                try:
                    if read_at(parent, name, MAX_PLAN, True)[1] == token:
                        os.unlink(name, dir_fd=parent)
                        os.fsync(parent)
                except FileNotFoundError:
                    pass


def exclusive_file(path, data, mode=0o400):
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.fsync(parent)


def make_directory(path, mode=0o700):
    with directory(path.parent, allow_sticky=True) as parent:
        os.mkdir(path.name, mode, dir_fd=parent)
        os.fsync(parent)
    return dir_state(path)


@contextlib.contextmanager
def held_lock(path, create=True, exclusive=False):
    with directory(path.parent) as parent:
        if exclusive and not create:
            raise RuntimeError('Exclusive lock allocation requires creation')
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | (os.O_CREAT if create else 0) | (os.O_EXCL if exclusive else 0)
        fd = os.open(path.name, flags, 0o600, dir_fd=parent)
        try:
            regular(os.fstat(fd), private=True)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
                raise RuntimeError('Transaction lock was replaced')
            yield fd
        finally:
            os.close(fd)


def process_identity(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in ('Z', 'X'):
            return None
        command = Path('/proc', str(pid), 'cmdline').read_bytes()
        final = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
        if not command or len(command) > 65536 or final[19] != fields[19] or final[0] in ('Z', 'X'):
            return None
        return dict(pid=pid, start=fields[19], boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    cmd_sha256=sha(command))
    except (FileNotFoundError, ProcessLookupError):
        return None


def assert_pid_namespace():
    own = os.getpid()
    if (os.readlink('/proc/self') != str(own)
            or Path('/proc/self/stat').read_text().split(' ', 1)[0] != str(own)
            or process_identity(own) is None):
        raise RuntimeError('Coherent PID/proc namespace required before host installation')
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('Owned pidfd guardian control is unavailable')
    fd = os.pidfd_open(own)
    try:
        if select.select([fd], [], [], 0)[0]:
            raise RuntimeError('Self process observation is inconsistent')
    finally:
        os.close(fd)


def assert_quiescent(root, rme_states):
    """A stopped observation is required; no stop/restart is manufactured."""
    root = absolute(root)
    forbidden = [root / 'process.json', root / 'control.sock']
    for name in MODULES:
        module = root / 'modules' / name
        forbidden.extend((module / 'process.json', module / 'recovery-required.json'))
        forbidden.extend(module / 'data' / name for name in (
            'engine-process.json', 'native-audio-session.json', 'native-router.json',
            'audio-processes.json', 'routing.json', 'suspended-routing.json', 'recovery-required.json'))
    for path in forbidden:
        # lstat deliberately treats stale records and dangling symlinks as
        # unresolved. Their absence cannot be invented from an invalid PID.
        try:
            with directory(path.parent) as parent:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise RuntimeError('Stopped host required; process or recovery state is present: ' + str(path))
    for state in rme_states:
        state = absolute(state)
        if dir_state(state) is None:
            continue
        with directory(state) as parent:
            names = os.listdir(parent)
        for name in names:
            if not name.startswith('rme-'):
                continue
            data, _ = read(state / name / 'transaction.json', MAX_PLAN)
            if data is not None and decode(data).get('status') not in ('committed', 'restored', 'rolled-back', 'no-change'):
                raise RuntimeError('Pending RME recovery prevents host installation')
    prefix = os.fsencode(str(root))
    for item in Path('/proc').iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            with (item / 'cmdline').open('rb') as stream:
                raw = stream.read(65537)
        except (FileNotFoundError, ProcessLookupError):
            continue
        if len(raw) > 65536:
            raise RuntimeError('Process command exceeds observation bound')
        if any(arg == prefix or arg.startswith(prefix + b'/') for arg in raw.split(b'\0') if arg):
            raise RuntimeError('Active process still references the host prefix')


def allowed(path, root, external):
    path, root = absolute(path), absolute(root)
    if str(path) in external:
        return True
    if not path.is_relative_to(root):
        return False
    relative = path.relative_to(root)
    if len(relative.parts) == 1:
        return relative.name in ('.owned', 'approvals.json', 'config.json', 'repository.xml', 'repository-cgi.json')
    return (len(relative.parts) == 2 and relative.parts[0] in ('code', 'bundles')
            and re.fullmatch(r'[A-Za-z0-9_.-]+', relative.name) is not None)


def check_parents(plan, journal):
    for path, expected in plan['parents'].items():
        if dir_state(Path(path), ancestor=True) != expected:
            raise RuntimeError('Transaction ancestor changed: ' + path)
    for record in journal['directories']:
        if record['created'] is not None:
            current = dir_state(Path(record['path']))
            if current != record['created'] and not (current is None and record.get('removed')):
                raise RuntimeError('Created transaction directory changed')


class Journal:
    def __init__(self, stage, plan, value=None):
        self.stage, self.plan = stage, plan
        raw, self.image = read(stage / 'journal.json', MAX_PLAN, True)
        self.value = decode(raw) if value is None else value
        self.validate()

    def validate(self):
        value = self.value
        if (not isinstance(value, dict) or value.get('schema') != SCHEMA
                or value.get('plan_sha256') != sha(encode(self.plan))
                or value.get('phase') not in ('prepared', 'installing', 'installed', 'recovering', 'rolled-back', 'recovery-required')
                or len(value.get('files', [])) != len(self.plan['files'])
                or [r.get('path') for r in value.get('directories', [])] != self.plan['directories']
                or value.get('service_before') != 'stopped'):
            raise RuntimeError('Invalid host recovery journal')
        validate_process(value['owner'])
        if value.get('recovery_actor') is not None:
            validate_process(value['recovery_actor'])
        if value.get('guardian') is not None:
            validate_process(value['guardian'])
        for index, record in enumerate(value['files']):
            item = self.plan['files'][index]
            if (type(record) is not dict or set(record) != {'new', 'undo', 'restored', 'removed'}
                    or type(record['removed']) is not bool):
                raise RuntimeError('Malformed host file recovery record')
            validate_state(record['restored'])
            for key in ('new', 'undo'):
                token = record[key]
                if token is None:
                    continue
                expected = item['after'] if key == 'new' else item['before']
                name = '.' + Path(item['path']).name + '.' + self.plan['id'] + '.' + key
                if (expected is None or type(token) is not dict
                        or set(token) != {'name', 'allocated', 'staged', 'complete'} or token['name'] != name):
                    raise RuntimeError('Malformed host temporary ownership')
                allocated = token['allocated']
                if allocated is not None and (type(allocated) is not dict or set(allocated) != {'dev', 'ino'}
                        or any(type(v) is not int for v in allocated.values())
                        or allocated['dev'] < 0 or allocated['ino'] <= 0):
                    raise RuntimeError('Malformed allocated host inode')
                for state_name in ('staged', 'complete'):
                    image = token[state_name]
                    validate_state(image)
                    if image is not None and (allocated is None
                            or any(image[k] != allocated[k] for k in ('dev', 'ino'))
                            or any(image[k] != expected[k] for k in ('sha256', 'bytes', 'mode', 'uid', 'gid'))):
                        raise RuntimeError('Host inode or payload differs from immutable plan')
                if token['complete'] is not None and not owned_image(token['complete'], token['staged']):
                    raise RuntimeError('Host completion has no owned staged inode')
            if value['phase'] == 'installed' and (record['new'] is None or record['new']['complete'] is None):
                raise RuntimeError('Incomplete files cannot be marked installed')
        for record in value['directories']:
            if set(record) != {'path', 'created', 'removed'} or type(record['removed']) is not bool:
                raise RuntimeError('Malformed host directory record')
            validate_state(record['created'], directory_only=True)
        host = value['host_lock']
        if type(host) is not dict or set(host) != {'before', 'created', 'removal_intent', 'retained_after_rollback'} or type(host['removal_intent']) is not bool or type(host['retained_after_rollback']) is not bool:
            raise RuntimeError('Malformed host maintenance lock record')
        validate_state(host['before'])
        validate_state(host['created'])

    def save(self):
        self.validate()
        self.image = private_write(self.stage / 'journal.json', encode(self.value), expected=self.image)


def control_directory(root):
    return root.parent / (root.name + '-install-transactions')


def validate_stage(stage, pin, payloads=True):
    stage = absolute(stage)
    if not re.fullmatch(r'[0-9a-f]{32}', stage.name) or not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise RuntimeError('Explicit pinned transaction stage required')
    raw, info = read(stage / 'plan.json', MAX_PLAN, True)
    if raw is None or info['mode'] != 0o400 or sha(raw) != pin:
        raise RuntimeError('Immutable host plan pin differs')
    plan = decode(raw)
    root = absolute(plan['root'])
    if (plan.get('schema') != SCHEMA or stage.parent != control_directory(root)
            or plan.get('id') != stage.name or root in (Path('/'), Path('/tmp'), Path('/opt'), Path('/mnt'), Path('/mnt/hdd'))):
        raise RuntimeError('Unrecognized host transaction stage')
    if (type(plan.get('timeout_seconds')) not in (int, float)
            or not .05 <= plan['timeout_seconds'] <= 180
            or type(plan.get('deadline_monotonic')) not in (int, float)
            or not 0 < plan['deadline_monotonic'] < 1e15):
        raise RuntimeError('Bounded host guardian deadline required')
    if 'borrowed_host_lock' not in plan:raise RuntimeError('Explicit Host maintenance mode required')
    if plan['borrowed_host_lock'] is not None:_validate_lease_description(plan['borrowed_host_lock'])
    preserved = plan.get('preserved_files')
    if (type(preserved) is not dict
            or set(preserved) != {str(root / n) for n in ('config.json', '.owned')}
            or any(v is None for v in preserved.values())):
        raise RuntimeError('Exact existing native config and marker preservation required')
    for image in preserved.values():
        validate_state(image)
    if any(item['path'] in preserved for item in plan['files']):
        raise RuntimeError('Preserved native config or marker cannot be an update payload')
    if not 1 <= len(plan['files']) <= 256 or len({item['path'] for item in plan['files']}) != len(plan['files']):
        raise RuntimeError('Invalid bounded host file plan')
    externals = plan['external']
    if any(Path(p).name not in ('AudioPlugins', '97_daphile_audio_host.start', '97_daphile_audio_host.stop') for p in externals):
        raise RuntimeError('Unrecognized external host destination')
    for i, item in enumerate(plan['files']):
        if not allowed(item['path'], root, externals) or item['member'] != str(i):
            raise RuntimeError('Unrecognized host file destination')
        validate_state(item['before'])
        after = item['after']
        if (type(after) is not dict or set(after) != {'sha256', 'bytes', 'mode', 'uid', 'gid'}
                or type(after['bytes']) is not int or not 0 <= after['bytes'] <= MAX_FILE
                or type(after['mode']) is not int or after['mode'] not in (0o600, 0o644, 0o755)
                or type(after['uid']) is not int or after['uid'] != 0
                or type(after['gid']) is not int or after['gid'] != 0
                or type(after['sha256']) is not str or re.fullmatch(r'[0-9a-f]{64}', after['sha256']) is None):
            raise RuntimeError('Malformed planned host payload')
        if payloads:
            data, record = read(stage / ('new-' + str(i)), private=True)
            if data is None or sha(data) != item['after']['sha256'] or len(data) != item['after']['bytes'] or record['mode'] != 0o400:
                raise RuntimeError('Host candidate payload changed')
            if item['before'] is not None:
                data, record = read(stage / ('before-' + str(i)), private=True)
                if data is None or sha(data) != item['before']['sha256'] or len(data) != item['before']['bytes'] or record['mode'] != 0o400:
                    raise RuntimeError('Host recovery bytes changed')
    code, info = read(stage / 'recover.py', MAX_PLAN, True)
    if code is None or sha(code) != plan['recovery_sha256'] or info['mode'] != 0o500:
        raise RuntimeError('Independent host recovery code changed')
    active, _ = read(stage.parent / 'active.json', MAX_PLAN, True)
    if decode(active) != dict(stage=stage.name, plan_sha256=pin):
        raise RuntimeError('Host transaction is no longer the active generation')
    return plan


def guard(stage, pin, journal):
    _check_plan_maintenance(journal.plan)
    # Neither preserved file can be adopted as a postflight image.
    check_expected(journal.plan['preserved_files'])
    if validate_stage(stage, pin, payloads=False) != journal.plan:
        raise RuntimeError('Host plan changed')
    if read(stage / 'journal.json', MAX_PLAN, True)[1] != journal.image:
        raise RuntimeError('Host journal ownership lost')
    check_parents(journal.plan, journal.value)
    assert_quiescent(Path(journal.plan['root']), journal.plan['rme_states'])
    lock = journal.value['host_lock']
    expected_lock = lock['created'] if lock['created'] is not None else lock['before']
    if expected_lock is not None and observe(Path(journal.plan['root']) / 'host.lock') != expected_lock:
        raise RuntimeError('Host maintenance lock ownership lost')
    if journal.value['phase'] == 'installing':
        guardian = journal.value.get('guardian')
        if guardian is None or process_identity(guardian['pid']) != guardian:
            raise RuntimeError('Independent host guardian is no longer alive')
        if time.monotonic() >= journal.plan['deadline_monotonic'] or read(stage / 'guardian-event.json', MAX_PLAN, True)[0] is not None:
            raise RuntimeError('Host installation deadline or guardian interruption reached')


def staged_replace(stage, pin, journal, index, data, metadata, expected, undo=False):
    item = journal.plan['files'][index]
    record = journal.value['files'][index]
    path = Path(item['path'])
    key = 'undo' if undo else 'new'
    if sha(data) != metadata['sha256'] or len(data) != metadata['bytes']:
        raise RuntimeError('Host staged bytes differ from the immutable file plan')
    guard(stage, pin, journal)
    with directory(path.parent) as parent:
        if read_at(parent, path.name)[1] != expected:
            raise RuntimeError('Host file changed before replacement')
        name = '.' + path.name + '.' + journal.plan['id'] + '.' + key
        record[key] = dict(name=name, allocated=None, staged=None, complete=None)
        journal.save()  # The operation intent is durable before creation.
        fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'w+b') as stream:
            allocated = os.fstat(stream.fileno())
            record[key]['allocated'] = dict(dev=allocated.st_dev, ino=allocated.st_ino)
            journal.save()
            stream.write(data)
            stream.flush()
            os.fchown(stream.fileno(), metadata['uid'], metadata['gid'])
            os.fchmod(stream.fileno(), metadata['mode'])
            if 'mtime_ns' in metadata:
                os.utime(stream.fileno(), ns=(metadata['mtime_ns'], metadata['mtime_ns']))
            os.fsync(stream.fileno())
            candidate = stat_image(os.fstat(stream.fileno()))
            candidate['sha256'] = sha(data)
            record[key]['staged'] = candidate
            journal.save()  # Inode and full prepared payload precede rename.
        guard(stage, pin, journal)
        same_parent(path.parent, parent)
        if read_at(parent, path.name)[1] != expected or read_at(parent, name)[1] != candidate:
            raise RuntimeError('Host file or staged payload changed before rename')
        os.replace(name, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        actual = read_at(parent, path.name)[1]
        if not owned_image(actual, candidate):
            raise RuntimeError('Host postimage differs from allocated inode')
        record[key]['complete'] = actual
        journal.save()
        return actual


def remove_owned(path, expected):
    with directory(path.parent) as parent:
        if read_at(parent, path.name)[1] != expected:
            raise RuntimeError('Host removal preimage changed')
        same_parent(path.parent, parent)
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)


def classify(item, record):
    current = observe(Path(item['path']))
    if current == item['before']:
        return 'before', current
    for key in ('undo', 'new'):
        value = record.get(key)
        if not value:
            continue
        if value.get('complete') is not None:
            if current == value['complete']:
                return key, current
        elif owned_image(current, value.get('staged')):
            return key, current
    if record.get('removed') and current is None and item['before'] is None:
        return 'before', current
    raise RuntimeError('Foreign or modified host file prevents recovery: ' + item['path'])


def clean_temporary(path, value):
    if not value:
        return
    name, token = value['name'], value.get('allocated')
    with directory(path.parent) as parent:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if token is None or (info.st_dev, info.st_ino) != (token['dev'], token['ino']):
            raise RuntimeError('Unrecorded or foreign host temporary retained')
        if value.get('staged') is None or read_at(parent, name)[1] != value['staged']:
            raise RuntimeError('Unconfirmed or externally modified host temporary retained')
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)


def host_lock(stage, pin, journal):
    lease=_check_plan_maintenance(journal.plan)
    if lease is not None:
        if journal.value['host_lock']['before']!=lease.description['image'] or journal.value['host_lock']['created']is not None:raise RuntimeError('Borrowed Host lock journal preimage differs')
        context=_borrowed_fd_context(lease);context.__enter__();return context
    root = Path(journal.plan['root'])
    path = root / 'host.lock'
    state = observe(path)
    record = journal.value['host_lock']
    if state is None and record.get('removal_intent'):
        record['created'] = None
        record['removal_intent'] = False
        journal.save()
    if record['before'] is not None and state != record['before']:
        raise RuntimeError('Existing host maintenance lock changed')
    if record['created'] is not None and state != record['created']:
        raise RuntimeError('Created host maintenance lock changed')
    if state is not None and record['before'] is None and record['created'] is None:
        raise RuntimeError('Unrecorded host maintenance lock retained')
    context = held_lock(path, create=state is None, exclusive=state is None)
    fd = context.__enter__()
    try:
        opened = observe(path)
        opened_fd = stat_image(os.fstat(fd))
        if (opened is None or any(opened[k] != value for k, value in opened_fd.items())
                or (state is not None and opened != state)):
            raise RuntimeError('Host maintenance lock changed during acquisition')
        if state is None:
            if opened['bytes'] != 0 or opened['sha256'] != sha(b'') or opened['mode'] != 0o600:
                raise RuntimeError('New host maintenance lock has foreign content or metadata')
            os.fsync(fd)
            with directory(root) as parent:
                os.fsync(parent)
            if observe(path) != opened:
                raise RuntimeError('New host maintenance lock changed before ownership record')
            record['created'] = opened
            journal.save()
        return context
    except BaseException:
        context.__exit__(*sys.exc_info())
        raise


def cleanup_directories(journal):
    retained = []
    for record in reversed(journal.value['directories']):
        path = Path(record['path'])
        current = dir_state(path)
        if current is None:
            continue
        if record['created'] is None or current != record['created']:
            retained.append(dict(path=str(path), reason='unrecorded-or-changed-directory'))
            continue
        with directory(path) as fd:
            if os.listdir(fd):
                retained.append(dict(path=str(path), reason='nonempty-directory-preserved'))
                continue
        with directory(path.parent) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != (current['dev'], current['ino']):
                raise RuntimeError('Directory changed before cleanup')
            record['removed'] = True
            journal.save()
            os.rmdir(path.name, dir_fd=parent)
            os.fsync(parent)
    journal.value['retained_directories'] = retained


def recover_locked(stage, pin, journal):
    plan = journal.plan
    check_expected(plan['preserved_files'])
    if validate_stage(stage, pin) != plan:
        raise RuntimeError('Host recovery plan changed')
    assert_quiescent(Path(plan['root']), plan['rme_states'])
    check_parents(plan, journal.value)
    # Classify the entire set before the first undo. One foreign destination
    # prevents overwriting any other destination based on an incomplete view.
    states = [classify(item, record) for item, record in zip(plan['files'], journal.value['files'])]
    journal.value['phase'] = 'recovering'
    journal.value['recovery_actor'] = process_identity(os.getpid())
    journal.save()
    lock = None
    try:
        needs_lock = any(kind != 'before' for kind, _ in states) or journal.value['host_lock']['created'] is not None
        if needs_lock and dir_state(Path(plan['root'])) is not None:
            lock = host_lock(stage, pin, journal)
        for index in reversed(range(len(plan['files']))):
            guard(stage, pin, journal)
            item, record = plan['files'][index], journal.value['files'][index]
            kind, current = classify(item, record)
            if kind == 'new':
                if item['before'] is None:
                    record['removed'] = True
                    journal.save()
                    remove_owned(Path(item['path']), current)
                else:
                    data = read(stage / ('before-' + str(index)), private=True)[0]
                    staged_replace(stage, pin, journal, index, data, item['before'], current, undo=True)
            for key in ('new', 'undo'):
                if Path(item['path']).parent.exists():
                    clean_temporary(Path(item['path']), record.get(key))
            record['restored'] = observe(Path(item['path']))
            journal.save()
        for item, record in zip(plan['files'], journal.value['files']):
            actual = observe(Path(item['path']))
            if actual != record['restored']:
                raise RuntimeError('Host restoration changed before certification')
            expected = item['before']
            if expected is None:
                if actual is not None:
                    raise RuntimeError('New host file still present after recovery')
            elif any(actual.get(key) != expected[key] for key in ('sha256', 'bytes', 'uid', 'gid', 'mode', 'mtime_ns')):
                raise RuntimeError('Host before bytes or metadata not restored')
        # Existing-host rollback retains any empty maintenance lock allocated
        # by this transaction. Never unlink a flocked inode or release before
        # the terminal journal is durable; retention is explicit bookkeeping.
        journal.value['host_lock']['retained_after_rollback'] = journal.value['host_lock']['created'] is not None
        cleanup_directories(journal)
        if journal.value['retained_directories']:
            raise RuntimeError('Host files restored but directory cleanup is incomplete; retained state requires review')
        guard(stage, pin, journal)
        journal.value['phase'] = 'rolled-back'
        journal.value['service_after'] = 'stopped'
        journal.save()
        return journal.value
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


def _recover_original(stage, pin):
    assert_pid_namespace()
    stage = absolute(stage)
    with held_lock(stage.parent / 'operation.lock', create=False):
        plan = validate_stage(stage, pin)
        check_expected(plan['preserved_files'])
        journal = Journal(stage, plan)
        with held_lock(stage / 'execute.lock', create=False):
            if journal.value['phase'] == 'rolled-back':
                assert_quiescent(Path(plan['root']), plan['rme_states'])
                if any(observe(Path(item['path'])) != record['restored'] for item, record in zip(plan['files'], journal.value['files'])):
                    raise RuntimeError('Previously restored host files changed; no replay')
                return journal.value
            try:
                return recover_locked(stage, pin, journal)
            except Exception as error:
                journal.value.update(phase='recovery-required', error=str(error))
                journal.save()
                raise


def _watch_original(stage, pin):
    assert_pid_namespace()
    plan = validate_stage(stage, pin)
    journal = Journal(stage, plan)
    owner = journal.value['owner']
    me = process_identity(os.getpid())
    owner_fd = os.pidfd_open(owner['pid'])
    try:
        if process_identity(owner['pid']) != owner:
            raise RuntimeError('Host installer identity changed before guardian readiness')
        private_write(stage / 'guardian-ready.json', encode(me))
        while True:
            try:
                journal = Journal(stage, plan)
            except RuntimeError:
                # Atomic journal replacement can unlink the opened old inode
                # during observation. Retry while the recorded owner is alive;
                # the immutable deadline still bounds this retry path.
                if time.monotonic() < plan['deadline_monotonic'] and not select.select([owner_fd], [], [], .02)[0]:
                    continue
                journal = None
            if journal is not None and journal.value['phase'] in TERMINAL | {'recovery-required'}:
                return
            if select.select([owner_fd], [], [], 0)[0]:
                break
            if process_identity(owner['pid']) != owner:
                private_write(stage / 'guardian-event.json', encode(dict(status='recovery-required', reason='owner-identity-lost', owner=owner)))
                raise RuntimeError('Host installer identity lost; no signal or automatic replay')
            if time.monotonic() >= plan['deadline_monotonic']:
                private_write(stage / 'guardian-event.json', encode(dict(status='deadline-recovery-required', reason='fixed-deadline', owner=owner)))
                # pidfd is pinned to this installer birth. Recheck the complete
                # boot/start/command identity immediately before any signal.
                if process_identity(owner['pid']) != owner:
                    raise RuntimeError('Host installer identity changed at deadline')
                signal.pidfd_send_signal(owner_fd, signal.SIGTERM)
                if not select.select([owner_fd], [], [], 5)[0]:
                    if process_identity(owner['pid']) != owner:
                        raise RuntimeError('Installer identity lost before forced termination')
                    signal.pidfd_send_signal(owner_fd, signal.SIGKILL)
                    if not select.select([owner_fd], [], [], 5)[0]:
                        raise RuntimeError('Owned installer exit unconfirmed; recovery remains blocked')
                break
            select.select([owner_fd], [], [], .1)
    finally:
        os.close(owner_fd)
    recover(stage, pin)


class GuardianRetainedError(RuntimeError):
    """A live/unreaped owned child remains explicitly attached to this error.

    The caller must retain this handle and the private transaction stage;
    no successful cleanup or admission is inferred from this exception.
    """
    def __init__(self, handle, cause):
        super().__init__('RECOVERY_REQUIRED: owned guardian exit/reap unconfirmed')
        self.handle = handle
        self.cause = cause


class GuardianProcess:
    def __init__(self, child):
        self.child = child
        self.pid = child.pid
        self.pidfd = None
        try:
            # This is our direct, not-yet-reaped Popen child. Its PID cannot
            # be recycled before this pidfd is acquired.
            self.pidfd = os.pidfd_open(self.pid)
        except BaseException as error:
            try:
                child.kill()
                child.wait(timeout=5)
            except BaseException as cleanup:
                raise GuardianRetainedError(self, cleanup) from error
            raise

    def _close_pidfd(self):
        if self.pidfd is not None:
            os.close(self.pidfd)
            self.pidfd = None

    def poll(self):
        return self.child.poll()

    def wait(self, timeout):
        result = self.child.wait(timeout=timeout)
        self._close_pidfd()
        return result

    def stop_and_reap(self):
        try:
            if self.child.poll() is None:
                if self.pidfd is None:
                    # Only possible after pidfd constructor failure. Popen
                    # still owns this direct unreaped child.
                    self.child.terminate()
                else:
                    signal.pidfd_send_signal(self.pidfd, signal.SIGTERM)
                try:
                    self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if self.pidfd is None:
                        self.child.kill()
                    else:
                        signal.pidfd_send_signal(self.pidfd, signal.SIGKILL)
                    self.child.wait(timeout=5)
            self._close_pidfd()
        except BaseException as error:
            raise GuardianRetainedError(self, error) from error


def spawn_guardian(stage, pin):
    exclusive_file(stage / 'guardian.log', b'', 0o600)
    with directory(stage) as parent:
        fd = os.open('guardian.log', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, 'ab') as log:
            command=[sys.executable, '-B', str(stage / 'recover.py'), 'watch', '--stage', str(stage), '--expected-sha256', pin]
            lease=(_check_plan_maintenance(validate_stage(stage,pin)) if _CURRENT_MAINTENANCE is not None else None);passed=()
            if lease is not None:command+=['--maintenance-fd',str(lease.fd)];passed=(lease.fd,)
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                start_new_session=True, close_fds=True, pass_fds=passed)
    handle = GuardianProcess(child)
    try:
        end = time.monotonic() + 5
        while time.monotonic() < end and handle.poll() is None:
            data, _ = read(stage / 'guardian-ready.json', MAX_PLAN, True)
            if data is not None and decode(data) == process_identity(handle.pid):
                return handle
            time.sleep(.02)
        raise RuntimeError('Independent host guardian did not establish readiness')
    except BaseException:
        handle.stop_and_reap()
        raise


def expected_images(root, payloads, expected_preimages):
    required = {str(absolute(item['path'])) for item in payloads}
    preserved = {str(root / name) for name in ('config.json', '.owned')}
    if required & preserved:
        raise RuntimeError('Preserved native config or marker cannot be an update payload')
    if (type(expected_preimages) is not dict
            or any(type(k) is not str or str(absolute(k)) != k for k in expected_preimages)
            or set(expected_preimages) != required | preserved):
        raise RuntimeError('Exact expected preimages required for every payload, config and marker')
    for path, image in expected_preimages.items():
        validate_state(image)
        if path in preserved and image is None:
            raise RuntimeError('Existing native config and marker preimages required')
    # The caller can retain and mutate its dictionary without changing authority.
    result = decode(encode(expected_preimages))
    check_expected(result)
    if read(root / '.owned')[0] != b'DAPHILE_NATIVE_AUDIO_HOST_V1':
        raise RuntimeError('Exact existing native host marker required')
    return result


def check_expected(images):
    for path, expected in images.items():
        if observe(Path(path)) != expected:
            raise RuntimeError('Expected native host preimage changed: ' + path)


def _apply_original(root, payloads, *, directories, expected_preimages, external=(), rme_states=()):
    if os.geteuid() != 0:
        raise RuntimeError('Administrator host transaction required')
    assert_pid_namespace()
    root = absolute(root)
    if root in (Path('/'), Path('/tmp'), Path('/opt'), Path('/mnt'), Path('/mnt/hdd')):
        raise RuntimeError('Unsafe host transaction root')
    # Full read-only preflight precedes creation of even the stage directory.
    if dir_state(root.parent, ancestor=True) is None:
        raise RuntimeError('Existing host parent required for independent recovery stage')
    assert_quiescent(root, rme_states)
    expected = expected_images(root, payloads, expected_preimages)
    preserved_files = {str(root / name): expected[str(root / name)]
                       for name in ('config.json', '.owned')}
    rows, before_data, after_data, parents, missing = [], [], [], {}, set()
    for target in [absolute(p) for p in directories] + [absolute(item['path']).parent for item in payloads]:
        dir_state(target)  # Final destinations must not be public sticky directories.
        for directory_path in reversed((target, *target.parents)):
            value = dir_state(directory_path, ancestor=directory_path != target)
            if value is None:
                missing.add(str(directory_path))
            else:
                parents[str(directory_path)] = value
    for i, item in enumerate(payloads):
        path = absolute(item['path'])
        if not allowed(path, root, [str(absolute(p)) for p in external]):
            raise RuntimeError('Host transaction destination is outside its scope')
        data = item['data']
        if type(data) is not bytes or len(data) > MAX_FILE or item['mode'] not in (0o600, 0o644, 0o755):
            raise RuntimeError('Invalid bounded host payload')
        old, observed = read(path)
        before = expected[str(path)]
        if observed != before:
            raise RuntimeError('Expected native host preimage changed before staging')
        rows.append(dict(path=str(path), member=str(i), before=before,
                         after=dict(sha256=sha(data), bytes=len(data), mode=item['mode'], uid=0, gid=0)))
        before_data.append(old)
        after_data.append(data)
    if not 1 <= len(rows) <= 256 or len({r['path'] for r in rows}) != len(rows):
        raise RuntimeError('Duplicate or empty host transaction')
    control = control_directory(root)
    if dir_state(control) is None:
        make_directory(control)
    if dir_state(control)['mode'] != 0o700:
        raise RuntimeError('Private host recovery directory required')
    with held_lock(control / 'operation.lock'):
        check_expected(expected)
        previous, previous_image = read(control / 'active.json', MAX_PLAN, True)
        if previous is not None:
            pointer = decode(previous)
            old_stage = control / pointer['stage']
            old_plan = validate_stage(old_stage, pointer['plan_sha256'])
            if Journal(old_stage, old_plan).value['phase'] not in TERMINAL:
                raise RuntimeError('Previous host transaction requires explicit recovery')
        token = uuid.uuid4().hex
        stage = control / token
        make_directory(stage)
        code = Path(__file__).read_bytes()
        exclusive_file(stage / 'recover.py', code, 0o500)
        for i, data in enumerate(after_data):
            exclusive_file(stage / ('new-' + str(i)), data)
            if before_data[i] is not None:
                exclusive_file(stage / ('before-' + str(i)), before_data[i])
        plan = dict(schema=SCHEMA, id=token, root=str(root),
                    borrowed_host_lock=(_CURRENT_MAINTENANCE.check().description if _CURRENT_MAINTENANCE is not None else None),
                    preserved_files=preserved_files,
                    external=[str(absolute(p)) for p in external],
                    rme_states=[str(absolute(p)) for p in rme_states], files=rows, parents=parents,
                    directories=sorted(missing, key=lambda p: (len(Path(p).parts), p)), recovery_sha256=sha(code),
                    timeout_seconds=INSTALL_DEADLINE_SECONDS,
                    deadline_monotonic=time.monotonic()+INSTALL_DEADLINE_SECONDS)
        pin = sha(encode(plan))
        exclusive_file(stage / 'plan.json', encode(plan))
        initial = dict(schema=SCHEMA, plan_sha256=pin, phase='prepared', owner=process_identity(os.getpid()),
            service_before='stopped', service_actions=[], player_commands=[], guardian=None,
            directories=[dict(path=p, created=None, removed=False) for p in plan['directories']],
            files=[dict(new=None, undo=None, restored=None, removed=False) for _ in rows],
            host_lock=dict(before=observe(root / 'host.lock'), created=None, removal_intent=False, retained_after_rollback=False))
        private_write(stage / 'journal.json', encode(initial))
        exclusive_file(stage / 'execute.lock', b'', 0o600)
        private_write(control / 'active.json', encode(dict(stage=token, plan_sha256=pin)), expected=previous_image)
        journal = Journal(stage, plan)
        guardian, host_context = None, None
        with held_lock(stage / 'execute.lock', create=False):
            try:
                guard(stage, pin, journal)
                if any(observe(Path(row['path'])) != row['before'] for row in rows):
                    raise RuntimeError('Host preimage changed before installation')
                guardian = spawn_guardian(stage, pin)
                journal.value['guardian'] = process_identity(guardian.pid)
                journal.value['phase'] = 'installing'
                journal.save()
                for record in journal.value['directories']:
                    guard(stage, pin, journal)
                    path = Path(record['path'])
                    if dir_state(path) is not None:
                        raise RuntimeError('Foreign directory appeared before host creation')
                    record['created'] = make_directory(path, 0o755)
                    journal.save()
                host_context = host_lock(stage, pin, journal)
                for index, item in enumerate(rows):
                    staged_replace(stage, pin, journal, index, after_data[index], item['after'], item['before'])
                guard(stage, pin, journal)
                if any(observe(Path(item['path'])) != record['new']['complete'] for item, record in zip(rows, journal.value['files'])):
                    raise RuntimeError('Installed host file changed before certification')
                # Keep the shared broker lock inode until after the commit is
                # durable. Unlinking a locked file would let a new broker lock
                # a different inode and bypass this maintenance interval.
                journal.value.update(phase='installed', service_after='stopped')
                journal.save()
            except GuardianRetainedError as error:
                journal.value.update(phase='recovery-required', guardian_retained_pid=error.handle.pid)
                journal.save()
                raise
            except Exception as original:
                if host_context is not None:
                    host_context.__exit__(None, None, None)
                    host_context = None
                try:
                    recover_locked(stage, pin, journal)
                except Exception as error:
                    journal.value.update(phase='recovery-required', error=str(error), install_error=str(original))
                    journal.save()
                    raise RuntimeError('Host recovery incomplete; retain stage ' + str(stage) + ' and pin ' + pin) from error
                raise
            finally:
                if host_context is not None:
                    host_context.__exit__(None, None, None)
                if guardian is not None and journal.value['phase'] in TERMINAL | {'recovery-required'}:
                    try:
                        guardian_returncode = guardian.wait(timeout=5)
                        if guardian_returncode != 0:
                            raise RuntimeError('Independent Host guardian exited abnormally; no successful transaction return')
                    except subprocess.TimeoutExpired:
                        try:
                            guardian.stop_and_reap()
                        except GuardianRetainedError:
                            journal.value.update(phase='recovery-required', guardian_retained_pid=guardian.pid)
                            journal.save()
                            raise
                        raise RuntimeError('Guardian required forced terminal cleanup; no successful return')
        return dict(status='installed-stopped', stage=str(stage), plan_sha256=pin,
                    service_before='stopped', service_after='stopped', player_commands=0)



def apply(root,payloads,*,directories,expected_preimages,external=(),rme_states=(),maintenance=None):
    if _CURRENT_MAINTENANCE is not None:raise RuntimeError('Nested Host update refused before staging')
    if maintenance is not None and (type(maintenance)is not HostMaintenanceLease or maintenance.check().root!=absolute(root)):
        raise RuntimeError('Genuine Host maintenance lease bound to this exact root required')
    with _maintenance_context(maintenance):
        return _apply_original(root,payloads,directories=directories,expected_preimages=expected_preimages,external=external,rme_states=rme_states)

def recover(stage,pin,*,maintenance_fd=None):
    with _maintenance_context(stage=stage,pin=pin,inherited_fd=maintenance_fd):return _recover_original(stage,pin)

def watch(stage,pin,*,maintenance_fd=None):
    with _maintenance_context(stage=stage,pin=pin,inherited_fd=maintenance_fd):return _watch_original(stage,pin)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('recover', 'watch', 'status'))
    parser.add_argument('--stage', required=True, type=Path)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--maintenance-fd',type=int)
    args = parser.parse_args()
    if args.action == 'watch':
        watch(args.stage, args.expected_sha256,maintenance_fd=args.maintenance_fd)
    elif args.action == 'recover':
        print(json.dumps(recover(args.stage, args.expected_sha256,maintenance_fd=args.maintenance_fd), indent=2))
    else:
        plan = validate_stage(args.stage, args.expected_sha256)
        print(json.dumps(Journal(args.stage, plan).value, indent=2))


if __name__ == '__main__':
    main()
