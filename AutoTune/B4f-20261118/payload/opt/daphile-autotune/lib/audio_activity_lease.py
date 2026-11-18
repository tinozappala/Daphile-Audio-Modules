"""Linux kernel exclusion only; NEVER a RAM/Host/root mutation grant.

Fresh admissions are root operations. LMS descendants may inherit the genuine
FD, subject to their existing profile/peer authority. DIRTY/registry are DENY-only.
No reset CLI, deadline reset, grant reconstruction, LOCK_UN or root repair exists.
Call retire_after_quiescence ONLY at an existing authenticated full-clean barrier.
"""
from contextlib import contextmanager
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import PurePosixPath
import platform
import re
import select
import stat
import sys
import uuid

ROOT = '/run/daphile-audio-activity'
PATH = ROOT+'/lease.lock'
ENV_FD = 'DAPHILE_AUDIO_ACTIVITY_FD'
ENV_TOKEN = 'DAPHILE_AUDIO_ACTIVITY_TOKEN'
_LIVE = {}
_MAX_MEMBERS = 64


class Refused(RuntimeError):
    pass


class Busy(Refused):
    pass


def need(ok, reason):
    if not ok:
        raise Refused(reason)


def root():
    need(sys.platform == 'linux' and os.geteuid() == 0, 'LINUX_ROOT_REQUIRED')


def read_bounded(name, limit):
    with open(name, 'rb') as stream:
        raw = stream.read(limit+1)
    need(len(raw) <= limit, 'KERNEL_READ_BOUND')
    return raw


def mount_id(fd):
    values = re.findall(rb'(?m)^mnt_id:[ \t]+([1-9][0-9]*)$', read_bounded('/proc/self/fdinfo/'+str(fd), 8192))
    need(len(values) == 1, 'ACTUAL_MOUNT_ID_REQUIRED')
    return int(values[0])


def namespaces(pid='self'):
    return tuple(os.readlink('/proc/'+str(pid)+'/ns/'+kind) for kind in ('pid', 'mnt'))


def image(fd):
    s = os.fstat(fd)
    return [s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode), stat.S_IMODE(s.st_mode),
            s.st_uid, s.st_gid, s.st_nlink, s.st_size, mount_id(fd)]


def attributes(fd):
    values = os.listxattr(fd)
    need(len(values) <= 16 and sum(len(x) for x in values) <= 4096, 'XATTR_BOUND')
    need(not values, 'EXACT_EMPTY_XATTRS_REQUIRED')


def directory(name):
    p = PurePosixPath(name)
    need(p.is_absolute() and str(p) == name and '..' not in p.parts, 'FIXED_CANONICAL_ROUTE')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for part in p.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
            s = os.fstat(fd)
            need(s.st_uid == s.st_gid == 0 and not s.st_mode & 0o022, 'PROTECTED_ROOT_ANCESTRY')
        return fd
    except BaseException:
        os.close(fd)
        raise


def layout():
    need(sys.platform == 'linux', 'LINUX_REQUIRED')
    need(namespaces() == namespaces(1), 'ACTUAL_INIT_NAMESPACES_REQUIRED')
    run = directory('/run')
    try:
        mid = mount_id(run)
        rows = [line.split(b' - ', 1) for line in read_bounded('/proc/self/mountinfo', 1024**2).splitlines()
                if line.split(b' ', 1)[0] == str(mid).encode()]
        need(len(rows) == 1 and rows[0][0].split()[4] == b'/run'
             and rows[0][1].split()[0] == b'tmpfs', 'REAL_RUN_TMPFS_REQUIRED')
    finally:
        os.close(run)
    fd = directory(ROOT)
    try:
        need(stat.S_IMODE(os.fstat(fd).st_mode) == 0o755 and mount_id(fd) == mid, 'FIXED_LEASE_DIRECTORY')
        attributes(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular(parent, name, mode, size, *, write=False):
    fd = os.open(name, (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                 dir_fd=parent)
    try:
        value = image(fd)
        need(value[2:8] == [stat.S_IFREG, mode, 0, 0, 1, size]
             and value[-1] == mount_id(parent), 'EXACT_PROTECTED_FILE')
        attributes(fd)
        need(value == image_named(parent, name), 'NAMED_FILE_CHANGED')
        return fd
    except BaseException:
        os.close(fd)
        raise


def image_named(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    try:
        return image(fd)
    finally:
        os.close(fd)


def kernel_locked(fd, mode):
    need(mode in ('SH', 'EX'), 'EXACT_LOCK_MODE')
    raw = read_bounded('/proc/self/fdinfo/'+str(fd), 8192)
    word = b'READ' if mode == 'SH' else b'WRITE'
    need(re.search(rb'(?m)^lock:\s+[0-9]+:\s+FLOCK\s+ADVISORY\s+'+word+rb'\s', raw),
         'GENUINE_HELD_FLOCK_REQUIRED')


def take(parent, name, mode, permissions=0o644):
    fd = regular(parent, name, permissions, 0)
    try:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if mode == 'SH' else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise Busy('COMMON_ACTIVITY_BUSY') from error
        need(image(fd) == image_named(parent, name), 'LOCK_REPLACED_DURING_ACQUIRE')
        kernel_locked(fd, mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def admission(parent):
    fd = take(parent, 'admission.lock', 'EX', 0o600)
    try:
        yield
    finally:
        os.close(fd)


def deny(parent, value=None):
    fd = regular(parent, 'deny', 0o644, 2, write=value is not None)
    try:
        raw = os.pread(fd, 3, 0)
        need(raw in (b'C\n', b'D\n'), 'DENY_MARKER_INVALID')
        if value is not None:
            root()
            need(value in (b'C\n', b'D\n'), 'DENY_MARKER_VALUE')
            need(os.pwrite(fd, value, 0) == 2, 'DENY_SHORT_WRITE')
            os.fsync(fd)
        return raw
    finally:
        os.close(fd)


def owner():
    fields = read_bounded('/proc/self/stat', 4096).rsplit(b')', 1)[1].split()
    return dict(pid=os.getpid(), start=int(fields[19]),
                boot=read_bounded('/proc/sys/kernel/random/boot_id', 64).strip().decode(),
                namespaces=list(namespaces()))


def member_raw(parent, token):
    need(re.fullmatch('[0-9a-f]{32}', token), 'MEMBER_TOKEN')
    fd = os.open(token, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and stat.S_IMODE(s.st_mode) == 0o600
             and s.st_nlink == 1 and 0 < s.st_size <= 4096, 'DENY_MEMBER_DAC')
        attributes(fd)
        raw = os.pread(fd, 4097, 0)
        need(len(raw) == s.st_size and image(fd) == image_named(parent, token), 'DENY_MEMBER_CHANGED')
        return raw
    finally:
        os.close(fd)


def members(parent):
    fd = os.open('members', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        s = os.fstat(fd)
        need(s.st_uid == s.st_gid == 0 and stat.S_IMODE(s.st_mode) == 0o700
             and mount_id(fd) == mount_id(parent), 'ROOT_PRIVATE_MEMBER_DIRECTORY')
        attributes(fd)
        names = os.listdir(fd)
        need(len(names) <= _MAX_MEMBERS, 'MEMBER_BOUND')
        rows = {name: json.loads(member_raw(fd, name)) for name in names}
        return fd, rows
    except BaseException:
        os.close(fd)
        raise


def retained_shared_witness(rows, expected_image):
    """Obtain EVERY family's original OFD. One live family cannot heal an orphan."""
    root()
    need(platform.machine().lower() in ('x86_64', 'amd64'), 'QUALIFIED_SYSCALL_ABI_REQUIRED')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    need(rows, 'ORPHAN_DENY_WITHOUT_MEMBERS')
    need(all(type(row) is dict and row.get('mode') == 'SH' for row in rows.values()),
         'ORPHAN_OR_NONSHARED_MEMBER')
    retained = []
    for row in rows.values():
        watch = duplicate = None
        try:
            identity = row['owner']
            need(set(identity) == {'pid', 'start', 'boot', 'namespaces'}, 'MEMBER_OWNER_SHAPE')
            need(identity['boot'] == owner()['boot'] and identity['namespaces'] == list(namespaces()), 'MEMBER_NAMESPACE')
            watch = os.pidfd_open(identity['pid'])
            fields = read_bounded('/proc/'+str(identity['pid'])+'/stat', 4096).rsplit(b')', 1)[1].split()
            need(int(fields[19]) == identity['start'] and not select.select([watch], [], [], 0)[0], 'MEMBER_ACTUAL_BIRTH')
            duplicate = int(libc.syscall(438, watch, row['slot'], 0))
            need(duplicate >= 0, 'REAL_PIDFD_GETFD_REQUIRED')
            os.set_inheritable(duplicate, False)
            need(image(duplicate) == expected_image == row['lease'], 'SH_WITNESS_FIXED_INODE')
            kernel_locked(duplicate, 'SH')
            retained.append(duplicate)
            duplicate = None
        except (OSError, Refused, KeyError, ValueError, TypeError) as error:
            for held in retained:
                os.close(held)
            raise Refused('ORPHAN_DENY_WITHOUT_ALL_GENUINE_SHARED_CUSTODIANS') from error
        finally:
            if duplicate is not None and duplicate >= 0:
                os.close(duplicate)
            if watch is not None:
                os.close(watch)
    return retained


def publish_member(fd, token, row):
    raw = (json.dumps(row, sort_keys=True, separators=(',', ':'))+'\n').encode()
    child = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    try:
        os.fchmod(child, 0o600)
        need(os.write(child, raw) == len(raw), 'MEMBER_SHORT_WRITE')
        os.fsync(child)
    finally:
        os.close(child)
    os.fsync(fd)
    return raw


class Lease:
    def __new__(cls):
        raise Refused('USE_KERNEL_ADMISSION_OR_GENUINE_INHERITANCE')

    @classmethod
    def acquire(cls, mode, *, denial_context=None):
        root()
        need(mode in ('SH', 'EX'), 'EXACT_MODE')
        # Optional negative evidence travels atomically with the existing EX
        # member. It neither grants admission nor authorizes retirement. The
        # unchanged v1 SH provider accepts this extra top-level EX field.
        if denial_context is not None:
            need(mode == 'EX' and (type(denial_context) is dict or callable(denial_context)), 'EX_DENIAL_CONTEXT_ONLY')
        parent = layout()
        fd = group = None
        witnesses = []
        try:
            fd = take(parent, 'lease.lock', mode)
            with admission(parent):
                group, rows = members(parent)
                state = deny(parent)
                if state == b'C\n':
                    need(not rows, 'CLEAN_WITH_MEMBERS_REFUSED')
                else:
                    need(mode == 'SH', 'ORPHAN_OR_ACTIVE_DENY_EX_REFUSED')
                    witnesses = retained_shared_witness(rows, image(fd))
                if denial_context is not None:
                    # The product's bounded read-only snapshot runs only after
                    # real EX and clean admission, BEFORE any DENY publication.
                    # Death here drops a lock without publishing a new member;
                    # no product effects have started. A dict keeps the v1 API.
                    if callable(denial_context):denial_context = denial_context()
                    need(type(denial_context) is dict, 'EX_DENIAL_CONTEXT_OBJECT')
                    raw_context = json.dumps(denial_context, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                    need(len(raw_context) <= 2048, 'BOUNDED_DENIAL_CONTEXT')
                    denial_context = json.loads(raw_context)
                deny(parent, b'D\n')
                token = uuid.uuid4().hex
                row = dict(schema='audio-activity-deny-member-v1', token=token, mode=mode,
                           owner=owner(), slot=fd, lease=image(fd))
                if denial_context is not None:
                    row['denial_context'] = denial_context
                    need(len((json.dumps(row, sort_keys=True, separators=(',', ':'))+'\n').encode()) <= 4096,
                         'EX_DENIAL_MEMBER_BOUND')
                raw = publish_member(group, token, row)
            obj = object.__new__(cls)
            _LIVE[obj] = dict(fd=fd, parent=parent, token=token, mode=mode, member=raw,
                              lease=image(fd), closed=False, original=True)
            fd = parent = None
            return obj
        finally:
            for value in (*witnesses, group, fd, parent):
                if value is not None:
                    os.close(value)

    @classmethod
    def inherit(cls, fd, token, mode):
        """FD continuity only. Existing session/profile still supplies authority."""
        need(type(fd) is int and fd >= 0 and mode in ('SH', 'EX'), 'INHERITED_DESCRIPTOR')
        parent = layout()
        duplicate = group = None
        try:
            duplicate = os.dup(fd)
            os.set_inheritable(duplicate, False)
            need(image(duplicate) == image_named(parent, 'lease.lock'), 'INHERITED_FIXED_LEASE')
            kernel_locked(duplicate, mode)
            need(deny(parent) == b'D\n', 'INHERITED_DENY_REQUIRED')
            group, rows = members(parent)
            need(token in rows and rows[token]['mode'] == mode and rows[token]['lease'] == image(duplicate), 'INHERITED_MEMBER')
            raw = member_raw(group, token)
            obj = object.__new__(cls)
            _LIVE[obj] = dict(fd=duplicate, parent=parent, token=token, mode=mode, member=raw,
                              lease=image(duplicate), closed=False, original=False)
            duplicate = parent = None
            # Consume the one explicitly inherited slot. Keeping an untracked
            # second SH copy would prevent the genuine last-holder EX barrier.
            os.close(fd)
            return obj
        finally:
            for value in (group, duplicate, parent):
                if value is not None:
                    os.close(value)

    @property
    def fd(self):
        self.check()
        return _LIVE[self]['fd']

    @property
    def token(self):
        self.check()
        return _LIVE[self]['token']

    def check(self):
        row = _LIVE.get(self)
        need(row is not None and not row['closed'] and row['fd'] is not None, 'LIVE_PHYSICAL_CUSTODY_REQUIRED')
        need(image(row['fd']) == row['lease'] == image_named(row['parent'], 'lease.lock'), 'LEASE_INODE_DRIFT')
        attributes(row['fd'])
        kernel_locked(row['fd'], row['mode'])
        need(deny(row['parent']) == b'D\n', 'DIRTY_CUSTODY_MARKER_REQUIRED')
        return True

    def child_environment(self, env=None):
        self.check()
        result = dict(os.environ if env is None else env)
        result[ENV_FD] = str(self.fd)
        result[ENV_TOKEN] = self.token
        return result

    def shared_profile_description(self):
        """Facts for an ALREADY authenticated root profile; not a grant.

        The native SH join must precede its final original root-peer liveness
        check/ACK. A cached description alone never authorizes RAM effects.
        lease list ABI: dev,ino,type,mode,uid,gid,nlink,size,mount_id.
        """
        root()
        self.check()
        value = _LIVE[self]
        need(value['mode'] == 'SH', 'PROFILE_REQUIRES_REAL_SHARED_CUSTODY')
        row = json.loads(value['member'])
        return dict(schema='daphile-audio-activity-sh-profile-v1', path=PATH,
                    mode='SH', token=value['token'], lease=list(value['lease']),
                    member_sha256=hashlib.sha256(value['member']).hexdigest(), owner=row['owner'])

    def original_owner_alive(self):
        """Actual pidfd/birth observation, not cleanup authority.

        False means this original process exited (or its PID was reused).
        Permission/protocol/namespace failures raise; they never mean 'dead'.
        Product native/keeper reap and full clean proofs are still required.
        """
        root()
        self.check()
        expected = json.loads(_LIVE[self]['member'])['owner']
        need(expected['boot'] == owner()['boot'] and expected['namespaces'] == list(namespaces()),
             'ORIGINAL_OWNER_BOOT_NAMESPACE')
        try:
            watch = os.pidfd_open(expected['pid'])
        except ProcessLookupError:
            return False
        try:
            try:
                fields = read_bounded('/proc/'+str(expected['pid'])+'/stat', 4096).rsplit(b')', 1)[1].split()
            except FileNotFoundError:
                need(select.select([watch], [], [], 0)[0], 'OWNER_OBSERVATION_RACE')
                return False
            if int(fields[19]) != expected['start']:
                return False
            return not bool(select.select([watch], [], [], 0)[0])
        finally:
            os.close(watch)

    def retire_after_quiescence(self):
        """Call ONLY after original full clean + required child exit/reap proofs.

        No boolean/JSON argument can authorize cleanup. This low-level method
        does not create those proofs; the admitted product callsite must own them.
        An inherited guard may finish its SAME member after original-owner death.
        """
        root()
        row = _LIVE.get(self)
        need(row is not None and not row['closed'], 'LIVE_RETIRED_CONTEXT_REQUIRED')
        parent = row['parent']
        group = exclusive = None
        with admission(parent):
            try:
                group, current = members(parent)
                need(member_raw(group, row['token']) == row['member'], 'EXACT_OWN_DENY_MEMBER')
                others = set(current)-{row['token']}
                if row['mode'] == 'EX':
                    self.check()
                    need(not others, 'EX_WITH_OTHER_DENY_MEMBERS')
                elif others:
                    self.check()
                else:
                    # Marker remains DIRTY during conversion. A pending fresh
                    # SH must drop on the busy admission mutex, never wait here.
                    if row['fd'] is not None:
                        os.close(row['fd'])
                        row['fd'] = None
                    exclusive = take(parent, 'lease.lock', 'EX')
                os.unlink(row['token'], dir_fd=group)
                os.fsync(group)
                if not others:
                    deny(parent, b'C\n')
            finally:
                if group is not None:
                    os.close(group)
                if exclusive is not None:
                    os.close(exclusive)
        self.close_copy()
        return dict(own_member_retired=True, global_clean=not others)

    def close_copy(self):
        """Never clears DENY/member, never LOCK_UN; safe for child copy release."""
        row = _LIVE.get(self)
        if row is None or row['closed']:
            return
        for name in ('fd', 'parent'):
            if row[name] is not None:
                os.close(row[name])
                row[name] = None
        row['closed'] = True


def inherited(mode='EX'):
    raw = os.environ.get(ENV_FD)
    token = os.environ.get(ENV_TOKEN)
    need(raw is not None and re.fullmatch('[0-9]{1,6}', raw) and token is not None, 'EXPLICIT_INHERITED_LEASE_REQUIRED')
    return Lease.inherit(int(raw), token, mode)


def provision_at_real_boot():
    """Boot caller only. Existing directory is validated, NEVER reset/repaired."""
    root()
    run = directory('/run')
    try:
        try:
            os.mkdir('daphile-audio-activity', 0o755, dir_fd=run)
        except FileExistsError:
            parent = layout()
            try:
                for name, mode, size in (('lease.lock', 0o644, 0), ('deny', 0o644, 2), ('admission.lock', 0o600, 0)):
                    fd = regular(parent, name, mode, size)
                    os.close(fd)
                deny(parent)
                group, _ = members(parent)
                os.close(group)
            finally:
                os.close(parent)
            return 'EXISTING_LAYOUT_PRESERVED'
        parent = os.open('daphile-audio-activity', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=run)
        try:
            os.fchmod(parent, 0o755)
            for name, mode, raw in (('lease.lock', 0o644, b''), ('deny', 0o644, b'C\n'), ('admission.lock', 0o600, b'')):
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=parent)
                try:
                    os.fchmod(fd, mode)
                    need(os.write(fd, raw) == len(raw), 'BOOT_PROVISION_WRITE')
                    os.fsync(fd)
                finally:
                    os.close(fd)
            os.mkdir('members', 0o700, dir_fd=parent)
            os.fsync(parent)
            os.fsync(run)
        finally:
            os.close(parent)
        checked = layout()
        os.close(checked)
        return 'NEW_VOLATILE_BOOT_LAYOUT_CREATED'
    finally:
        os.close(run)
