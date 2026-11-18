"""Bounded original-byte cache shared by native streaming and prefetch.

Queue entries stay in LMS. Only cache storage is deduplicated. A native Song
holds a pin from the readiness request until its terminal state/destruction.
"""
from collections import deque
import hashlib
import os
import re
from pathlib import Path
import stat
import subprocess
import threading
import time
import uuid


def choose_evictions(rows, pinned, required, limit):
    """LRU selection; never remove an active/pending Song's file."""
    used = sum(row['bytes'] for row in rows)
    victims = []
    for row in rows:
        if used + required <= limit:
            break
        if row['target'] not in pinned:
            victims.append(row)
            used -= row['bytes']
    if used + required > limit:
        raise RuntimeError('RAM_CAPACITY_PINNED')
    return victims


class RollingCache:
    def __init__(self, manager, load, atomic, local_audio_path):
        self.m = manager
        self.load, self.atomic, self.local_path = load, atomic, local_audio_path
        self.lock = threading.RLock()
        self.io_lock = threading.RLock()
        self.suspended = False
        self.wake = threading.Event()
        self.requests = {}
        self.pending = deque()
        saved = load(manager.run / 'continuous-pins.json', {})
        self.pins = saved.get('pins', {}) if saved.get('boot') == manager.boot else {}
        self.limit = int(manager.cfg.get('cache_bytes', 67108864))
        self.state = {'state': 'READY', 'error': None, 'copies': 0, 'evictions': 0}

    def active(self):
        run = self.load(self.m.runtime, {})
        return run.get('boot') == self.m.boot and run.get('cache_ready') and self.m.cache_is_tmpfs()

    def save_pins(self):
        self.atomic(self.m.run / 'continuous-pins.json', {'boot': self.m.boot, 'pins': self.pins})

    def rows(self):
        return self.load(self.m.runtime, {}).get('mounts', [])

    def publish(self, rows):
        value = self.load(self.m.runtime, {})
        value['mounts'] = rows
        self.atomic(self.m.runtime, value)

    def resolve(self, url):
        path = self.local_path(url)
        if path is None or not path.is_absolute() or path.is_symlink():
            raise RuntimeError('RAM_LOCAL_FILE_REQUIRED')
        path = path.resolve()
        roots = [Path(x).resolve() for x in self.m.cfg['source_roots']]
        if not any(path.is_relative_to(root) for root in roots):
            raise RuntimeError('SOURCE_OUTSIDE_LIBRARY')
        return str(path)

    def request_path(self, url):
        # Native HTTP waits for this socket. Network filesystem validation
        # belongs to the copy worker, never to the request/status critical path.
        path = self.local_path(url)
        if path is None or not path.is_absolute():
            raise RuntimeError('RAM_LOCAL_FILE_REQUIRED')
        return os.path.normpath(str(path))

    def valid(self, row):
        if not row.get('ready', True):
            return False
        try:
            cached, target = Path(row['cache']).stat(), Path(row['target']).stat()
            return ([target.st_dev, target.st_ino] == row['cache_identity'] ==
                    [cached.st_dev, cached.st_ino] and target.st_size == row['bytes'])
        except OSError:
            return False

    def status(self):
        with self.lock:
            rows = self.rows()
            return dict(self.state, enabled=bool(self.active()), files=len(rows),
                        bytes=sum(r['bytes'] for r in rows), limit_bytes=self.limit,
                        pinned=len({r['target'] for r in self.pins.values()}),
                        pending=len(self.pending))

    def request(self, value, native):
        token = value.get('token')
        if not isinstance(token, str) or not 16 <= len(token) <= 128 or not token.replace('-', '').isalnum():
            raise RuntimeError('RAM_TOKEN')
        with self.lock:
            if self.suspended:
                return {'state': 'WAITING_SWITCH'}
            if not self.active():
                # An ON activation error must never silently fall back to disk.
                run = self.load(self.m.runtime, {})
                if run.get('error') or self.m.state_value().get('requested_ram'):
                    raise RuntimeError('RAM_NOT_READY')
                return {'state': 'OFF'}
            target = self.request_path(value.get('url'))
            pin = dict(target=target, pid=native['pid'], birth=native['birth'])
            if token in self.pins and self.pins[token] != pin:
                raise RuntimeError('RAM_TOKEN_CONFLICT')
            if token not in self.pins:
                self.pins[token] = pin
                self.save_pins()
            rows = self.rows()
            for i, row in enumerate(rows):
                if row['target'] == target and self.valid(row):
                    self.state.update(state='READY', error=None)
                    if i != len(rows) - 1:
                        self.publish(rows[:i] + rows[i + 1:] + [row])
                    return {'state': 'READY', 'bytes': row['bytes'], 'identity': row['cache_identity']}
            if token not in self.requests:
                self.requests[token] = {'state': 'PREPARING', 'target': target}
                self.pending.append(token)
                self.wake.set()
            result = self.requests[token]
            return {k: v for k, v in result.items() if k != 'target'}

    def release(self, value, native):
        with self.lock:
            token = value.get('token')
            old = self.pins.get(token)
            if old is not None:
                if (old['pid'], old['birth']) != (native['pid'], native['birth']):
                    raise RuntimeError('RAM_PIN_NATIVE_MISMATCH')
                del self.pins[token]
                self.save_pins()
            self.requests.pop(token, None)
            error = value.get('error')
            if isinstance(error, str) and re.fullmatch(r'RAM_[A-Z_]{1,80}', error):
                self.state.update(state='ERROR', error=error)
                self.atomic(self.m.run / 'continuous-error.json', dict(boot=self.m.boot, error=error, phase='NATIVE_GATE'))
            return {'state': 'RELEASED'}

    def clean_dead_pins(self, native):
        with self.lock:
            dead = [key for key, pin in self.pins.items()
                    if (pin['pid'], pin['birth']) != (native['pid'], native['birth'])]
            for key in dead:
                del self.pins[key]
                self.requests.pop(key, None)
            if dead:
                self.save_pins()

    def command(self, args):
        result = subprocess.run(args, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('RAM_MOUNT_OPERATION_FAILED')

    def evict(self, row):
        if not self.valid(row):
            raise RuntimeError('RAM_CACHE_IDENTITY_CHANGED')
        self.command(['umount', row['target']])
        actual = Path(row['target']).stat()
        if [actual.st_dev, actual.st_ino] != row['original_identity']:
            raise RuntimeError('ORIGINAL_FILE_IDENTITY')
        self.publish([r for r in self.rows() if r['target'] != row['target']])
        Path(row['cache']).unlink()
        self.state['evictions'] += 1

    def ensure(self, target, prefetch=False, keep=()):
        with self.io_lock:
            if self.suspended:
                raise RuntimeError('RAM_SWITCHING')
            return self._ensure(target, prefetch, keep)

    def _ensure(self, target, prefetch=False, keep=()):
        # Copy I/O deliberately runs outside the lock used by status/requests.
        # One worker owns mount mutations; pins are reconsidered before eviction.
        self.resolve(Path(target).as_uri())
        with self.lock:
            rows = self.rows()
            if any(r['target'] == target and self.valid(r) for r in rows):
                return
        source = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        destination = self.m.cache / ('rolling-' + uuid.uuid4().hex)
        mounted = False
        try:
            original = os.fstat(source)
            if not stat.S_ISREG(original.st_mode):
                raise RuntimeError('SOURCE_NOT_REGULAR')
            if original.st_size > self.limit:
                raise RuntimeError('RAM_FILE_EXCEEDS_CACHE')
            with self.lock:
                rows = self.rows()
                used = sum(r['bytes'] for r in rows)
                victims = choose_evictions(rows, {r['target'] for r in self.pins.values()} | set(keep), original.st_size, self.limit)
                for row in victims:
                    self.evict(row)
                self.state.update(state='PREPARING', error=None)
            output = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o444)
            digest = hashlib.sha256()
            count = 0
            try:
                while chunk := os.read(source, 1048576):
                    if self.suspended:
                        raise RuntimeError('RAM_SWITCHING')
                    count += len(chunk)
                    if count > original.st_size:
                        raise RuntimeError('SOURCE_CHANGED_DURING_COPY')
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        wrote = os.write(output, view)
                        if wrote <= 0:
                            raise RuntimeError('CACHE_SHORT_WRITE')
                        view = view[wrote:]
                os.utime(output, ns=(original.st_atime_ns, original.st_mtime_ns))
                os.fsync(output)
            finally:
                os.close(output)
            after, named = os.fstat(source), Path(target).stat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if count != original.st_size or identity(after) != identity(original) or identity(named) != identity(original):
                raise RuntimeError('SOURCE_CHANGED_DURING_COPY')
            with self.lock:
                self.command(['mount', '--bind', str(destination), target])
                mounted = True
                cached = destination.stat()
                row = dict(target=target, cache=str(destination), cache_identity=[cached.st_dev, cached.st_ino],
                           original_identity=[original.st_dev, original.st_ino], bytes=count, sha256=digest.hexdigest(), ready=False)
                # Journal immediately after bind, so recovery knows every mount.
                self.publish(self.rows() + [row])
                self.command(['mount', '-o', 'remount,bind,ro', target])
                row['ready'] = True
                self.publish([row if r['target'] == target else r for r in self.rows()])
                if not self.valid(row):
                    raise RuntimeError('RAM_BIND_NOT_OBSERVED')
                self.state.update(state='READY', error=None, copies=self.state['copies'] + 1)
        except BaseException:
            if mounted:
                # A partially applied mount is never a READY cache entry.
                self.command(['umount', target])
                self.publish([r for r in self.rows() if r['target'] != target])
                mounted = False
            raise
        finally:
            os.close(source)
            if not mounted:
                destination.unlink(missing_ok=True)

    def run(self, native_process, rpc):
        next_prefetch = 0
        while True:
            if self.suspended:
                self.wake.wait(.1);self.wake.clear();continue
            token = None
            try:
                with native_process(self.m.cfg) as native:
                    self.clean_dead_pins(native)
                    with self.lock:
                        if self.pending:
                            token = self.pending.popleft()
                            request = self.requests.get(token)
                        else:
                            request = None
                    if request is not None:
                        self.ensure(request['target'])
                        # request() observes the mount independently on retry.
                    elif self.active() and time.monotonic() >= next_prefetch:
                        next_prefetch = time.monotonic() + 2
                        deadline = time.monotonic() + 2
                        players = rpc(self.m.cfg, '', ['players', '0', '100'], deadline).get('players_loop', [])
                        keep = set()
                        for player in players:
                            if player.get('connected') not in (1, '1', True):
                                continue
                            queue = rpc(self.m.cfg, player['playerid'], ['status', '-', '8', 'tags:u'], deadline)
                            for row in queue.get('playlist_loop', []):
                                if self.pending:
                                    break
                                try:
                                    target = self.request_path(row.get('url'))
                                    self.ensure(target, prefetch=True, keep=keep)
                                    keep.add(target)
                                except RuntimeError as error:
                                    if str(error) == 'RAM_CAPACITY_PINNED':
                                        break
                                    continue
                                except (OSError, RuntimeError):
                                    # Only a stream request may publish a user error.
                                    continue
            except Exception as error:
                code = str(error) if type(error) is RuntimeError else 'RAM_SOURCE_UNAVAILABLE'
                with self.lock:
                    if token in self.requests:
                        if code in ('RAM_CAPACITY_PINNED', 'RAM_SWITCHING'):
                            self.requests[token].update(state='WAITING_SPACE')
                            self.pending.append(token)
                            self.state.update(state='WAITING_SPACE', error=None)
                        else:
                            self.requests[token].update(state='ERROR', error=code)
                            self.state.update(state='ERROR', error=code)
                if code in ('RAM_CAPACITY_PINNED', 'RAM_SWITCHING'):
                    self.wake.wait(.1)
                    self.wake.clear()
            if token is None:
                self.wake.wait(.25)
                self.wake.clear()
