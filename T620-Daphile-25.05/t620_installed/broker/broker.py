"""Privileged audio host: follows LMS state, accepts two preapproved modules only."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import struct
import stat
import subprocess
import sys
import time
import uuid
import zipfile

from common import IDS, atomic, identity, lock, pending, private, stop_owned, started_identity

MARKER = 'DAPHILE_NATIVE_AUDIO_HOST_V1'

NATIVE_RECORDS = ('native-audio-session.json', 'native-router.json', 'audio-processes.json')


def native_read(path, limit=65536):
    """One bounded observation of a private regular file, without symlink I/O."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or before.st_mode & 0o077 or before.st_nlink != 1 or before.st_size > limit):
            raise RuntimeError('Native recovery observation requires a bounded private file')
        data = os.read(fd, limit + 1)
        def key(meta):
            return (meta.st_dev, meta.st_ino, meta.st_mode, meta.st_uid, meta.st_nlink,
                    meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)
        if (len(data) != before.st_size or key(os.fstat(fd)) != key(before)
                or key(Path(path).lstat()) != key(before)):
            raise RuntimeError('Native recovery state changed during observation')
        return data
    finally:
        os.close(fd)


def native_json(data):
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result: raise ValueError('Duplicate native status member')
            result[name] = value
        return result
    def invalid(_): raise ValueError('Non-finite native status value')
    return json.loads(data.decode('utf8'), object_pairs_hook=unique, parse_constant=invalid)


def native_audio_http(port, token, deadline):
    """Fixed loopback GET; one absolute deadline, bounded headers/body, no redirects.

    Recompute the timeout for every socket operation: per-read HTTP timeouts
    alone would let a trickled response outlive the overall observation budget.
    """
    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0: raise TimeoutError('Native audio observation deadline exceeded')
        return seconds
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining())
        connection.connect(('127.0.0.1', port))
        request = ('GET /api/audio/status HTTP/1.1\r\nHost: 127.0.0.1:' + str(port)
                   + '\r\nCookie: peq_session=' + token + '\r\nConnection: close\r\n\r\n').encode('ascii')
        connection.settimeout(remaining()); connection.sendall(request)
        received = b''
        while b'\r\n\r\n' not in received:
            connection.settimeout(remaining()); block = connection.recv(4096)
            if not block: raise RuntimeError('Incomplete native audio response')
            received += block
            if len(received.split(b'\r\n\r\n', 1)[0]) > 4096:
                raise RuntimeError('Native audio response headers exceed the bound')
        header, body = received.split(b'\r\n\r\n', 1)
        lines = header.decode('ascii').split('\r\n')
        if not re.fullmatch(r'HTTP/1\.[01] 200(?: [\x20-\x7e]*)?', lines[0]):
            raise RuntimeError('Native audio observation was not accepted')
        fields = {}
        for line in lines[1:]:
            if ':' not in line or line[:1].isspace():raise ValueError('Invalid native audio header')
            name, value = line.split(':', 1); name = name.lower()
            if not re.fullmatch(r'[a-z0-9-]+', name) or name in fields:
                raise ValueError('Ambiguous native audio header')
            fields[name] = value.strip()
        size = fields.get('content-length', '')
        if (not re.fullmatch(r'0|[1-9][0-9]{0,4}', size) or int(size) > 8192
                or fields.get('content-type') != 'application/json' or 'transfer-encoding' in fields):
            raise ValueError('Bounded native audio JSON response required')
        size = int(size)
        while len(body) < size:
            connection.settimeout(remaining()); block = connection.recv(min(4096, size - len(body)))
            if not block: raise RuntimeError('Incomplete native audio response')
            body += block
        if len(body) != size:raise ValueError('Unexpected native audio response bytes')
        remaining()
        return native_json(body)


def native_recovery_status(state, running, current, config):
    """Distinguish an owned operational ledger from unresolved or unknown state."""
    state = Path(state); data = state / 'data'
    def markers():
        result = []
        for name in NATIVE_RECORDS:
            try:(data / name).lstat()
            except FileNotFoundError:continue
            result.append(name)
        return result
    names = markers()
    if not names:return dict(state='absent', audio=None, recovery_required=False)
    if running is not True or not isinstance(current, dict) or current.get('status') != 'running':
        return dict(state='pending', audio=None, recovery_required=True)
    deadline = time.monotonic() + .5
    def snapshot():
        found = markers()
        files = {name: native_read(data / name) for name in found}
        for name in found:
            value = native_json(files[name])
            if not isinstance(value, dict) or not value:
                raise ValueError('Invalid native recovery record')
        files['process.json'] = native_read(state / 'process.json', 4096)
        files['current.json'] = native_read(state / 'current.json', 4096)
        files['ready.json'] = native_read(data / 'ready.json', 4096)
        files['access.token'] = native_read(data / 'access.token', 128)
        return found, files
    names, before = snapshot()
    process = native_json(before['process.json']); ready = native_json(before['ready.json'])
    if (not isinstance(process, dict) or set(process) != {'pid', 'start', 'boot', 'cmd'}
            or type(process['pid']) is not int or process['pid'] <= 0
            or not isinstance(process['start'], str) or not process['start'].isdigit()
            or not isinstance(process['boot'], str) or not process['boot']
            or not isinstance(process['cmd'], str) or not re.fullmatch('[0-9a-f]{64}', process['cmd'])):
        raise ValueError('Invalid native controller identity')
    port = config['ports']['DaphileCamillaDSP']
    if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(ready, dict)
            or set(ready) != {'module', 'process', 'port'} or ready['module'] != 'DaphileCamillaDSP'
            or type(ready['port']) is not int or ready['port'] != port or ready['process'] != process
            or native_json(before['current.json']) != current or identity(process['pid']) != process):
        raise RuntimeError('Native controller readiness or identity changed')
    token = before['access.token'].decode('ascii').strip()
    if not re.fullmatch('[A-Za-z0-9_-]{43}', token):raise ValueError('Invalid private native session token')
    value = native_audio_http(port, token, deadline)
    if (not isinstance(value, dict) or set(value) != {'pid', 'audio'}
            or type(value['pid']) is not int or value['pid'] != process['pid']):
        raise ValueError('Native audio response does not identify the owned controller')
    audio = value['audio']
    modes = {'native-bypass', 'armed-idle', 'armed-unsupported', 'dsd-bypass',
             'pcm-active', 'waiting-pcm', 'fault-paused'}
    required = {'mode', 'message', 'rate', 'active', 'enabled', 'recovery_required', 'dsd_policy'}
    if (not isinstance(audio, dict)
            or set(audio) not in (required, required | {'processing'})
            or not isinstance(audio['mode'], str) or audio['mode'] not in modes
            or any(type(audio[key]) is not bool for key in ('active', 'enabled', 'recovery_required'))
            or audio['active'] != (audio['mode'] == 'pcm-active')
            or ('processing' in audio and (type(audio['processing']) is not bool
                or audio['processing'] != (audio['enabled'] and audio['active'])))
            or any(not isinstance(audio[key], str) or len(audio[key]) > 1024 for key in ('message', 'dsd_policy'))
            or (audio['rate'] is not None and (type(audio['rate']) is not int or not 1 <= audio['rate'] <= 768000))):
        raise ValueError('Native audio status is not an unambiguous observation')
    after_names, after = snapshot()
    if (after_names != names or after != before or identity(process['pid']) != process
            or time.monotonic() >= deadline):
        raise RuntimeError('Native audio owner or state changed during observation')
    ledger = native_json(before['native-audio-session.json']) if 'native-audio-session.json' in before else None
    phases = {'pcm-active': ('pcm-active',),
              'waiting-pcm': ('pcm-active', 'waiting-pcm', 'waiting-source-observation')}
    ledger_valid = (isinstance(ledger, dict) and set(ledger) == {'version', 'phase', 'target', 'boot_id'}
                    and type(ledger['version']) is int and ledger['version'] == 1
                    and isinstance(ledger['target'], dict) and bool(ledger['target'])
                    and ledger['boot_id'] == process['boot']
                    and ledger['phase'] in phases.get(audio['mode'], ()))
    route = native_json(before['native-router.json']) if 'native-router.json' in before else None
    route_valid = (isinstance(route, dict) and route.get('tag') == 'DAPHILE_CAMILLA_NATIVE_ROUTER_V1'
                   and route.get('phase') == 'bound')
    # Operational describes this coherent controller observation, not a fresh
    # certification of the bind, DAC, hardware or every live LMS/driver hook.
    operational = (ledger_valid and route_valid and audio['mode'] in ('pcm-active', 'waiting-pcm')
                   and audio['recovery_required'] is False)
    return dict(state='operational' if operational else 'pending', audio=audio,
                recovery_required=not operational)


class Host:
    def __init__(self, root):
        self.root = Path(root).resolve()
        if (self.root/'.owned').read_text() != MARKER: raise RuntimeError('Unowned host prefix')
        self.config = json.loads((self.root/'config.json').read_text())
        for path in (self.root, self.root/'config.json', self.root/'approvals.json'):
            if path.is_symlink() or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o022:
                raise RuntimeError('Host approval/configuration ownership is unsafe')
        self.children = {}
        self.approval_cache = {}

    def state(self, name):
        if name not in IDS: raise ValueError('Unknown module')
        return private(private(self.root/'modules')/name)

    def native_states(self):
        import yaml
        path = Path(self.config['prefs'])
        if path.is_symlink(): raise RuntimeError('Indirect native preferences refused')
        raw=path.read_bytes() if path.exists() else None
        cached=getattr(self,'native_state_cache',None)
        # Read the native file every time so even an in-place edit with the
        # same timestamps is seen. Parse YAML only when its exact bytes change;
        # repeated parsing dominated the two-second loop on slower machines.
        if cached is not None and cached[0]==raw:return dict(cached[1])
        value = yaml.safe_load(raw) if raw is not None else {}
        if not isinstance(value,dict): raise RuntimeError('Invalid native plugin state')
        states={name:value.get(name,'disabled') for name in IDS}
        self.native_state_cache=(raw,states)
        return dict(states)

    def approved(self, name):
        source = Path(self.config['plugins'])/name
        if not source.exists(): return None
        if source.is_symlink(): raise RuntimeError('Indirect native plugin directory refused')
        catalog = json.loads((self.root/'approvals.json').read_text())
        entries=list(catalog.get(name,[]))
        cached=self.approval_cache.get(name)
        # Check the still-approved, previously verified release first. An
        # older approved release can share a large engine binary, and trying
        # it first would hash that binary again on every two-second sync.
        # Removing or editing the root approval always invalidates this path.
        if cached and cached[2][0] in entries:
            entry=cached[2][0];entries.remove(entry);entries.insert(0,entry)
        for entry in entries:
            try:
                fingerprint = tuple((p, (source/p).lstat().st_ino, (source/p).lstat().st_ctime_ns, (source/p).lstat().st_mtime_ns, (source/p).lstat().st_size)
                                    for p in entry['files'])
            except FileNotFoundError: continue
            cached = self.approval_cache.get(name)
            if cached and cached[2][0]==entry and cached[0]==entry['sha256'] and cached[1]==fingerprint: return cached[2]
            if all((source/path).is_file() and not (source/path).is_symlink()
                   and hashlib.sha256((source/path).read_bytes()).hexdigest()==digest
                   for path,digest in entry['files'].items()):
                archive = self.root/'bundles'/entry['archive']
                data = archive.read_bytes()
                if hashlib.sha256(data).hexdigest() != entry['sha256']: raise RuntimeError('Approved bundle changed')
                result = entry,data
                self.approval_cache[name]=(entry['sha256'],fingerprint,result)
                return result
        raise RuntimeError('Native plugin does not match a root-approved release')

    def running(self, state):
        path = state/'process.json'
        if not path.exists(): return False
        record = json.loads(path.read_text())
        try:
            actual=identity(record['pid'])
            if actual['boot']!=record['boot'] or actual['start']!=record['start']: return False
            if actual != record: raise RuntimeError('Backend process identity mismatch')
            return True
        except (FileNotFoundError,ProcessLookupError): return False

    def stop(self, name, suspend_route=True):
        state = self.state(name); data = private(state/'data')
        transaction_state = private(self.config['rme_state']) if name=='DaphileRME' else data
        if suspend_route and (data/'routing.json').exists():
            os.replace(data/'routing.json', data/'suspended-routing.json')
        if (state/'process.json').exists():
            stop_owned(json.loads((state/'process.json').read_text()), timeout=45)
            (state/'process.json').unlink()
        child = self.children.pop(name,None)
        if child: child.wait(timeout=2)
        if (data/'engine-process.json').exists():
            stop_owned(json.loads((data/'engine-process.json').read_text()), timeout=10)
            (data/'engine-process.json').unlink()
        current = state/'current.json'
        if name=='DaphileCamillaDSP' and current.exists() and any((data/filename).exists() for filename in ('native-audio-session.json','native-router.json','audio-processes.json')):
            release=state/'releases'/json.loads(current.read_text())['release']
            recovery=release/'native_recover.py'
            if not recovery.is_file():raise RuntimeError('Pending native audio route prevents update/removal')
            subprocess.run([sys.executable,str(recovery),'--state',str(data)],check=True,timeout=60,
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if any((data/filename).exists() for filename in ('native-audio-session.json','native-router.json','audio-processes.json')):
                raise RuntimeError('Native audio recovery incomplete; module retained')
        if name=='DaphileRME' and current.exists() and pending(transaction_state):
            release = state/'releases'/json.loads(current.read_text())['release']
            # Recovery executes in its own interpreter: no cross-release imports.
            subprocess.run([sys.executable,str(release/'recover.py'),'--state',str(transaction_state)],check=True,timeout=45,
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if pending(transaction_state): raise RuntimeError('Pending transaction prevents update/removal')
        (data/'ready.json').unlink(missing_ok=True)

    def cgi(self, name, release=None):
        state = self.state(name); ownership = state/'cgi.json'
        path = Path(self.config['cgi'])/IDS[name][2]
        if ownership.exists():
            expected = json.loads(ownership.read_text())
            if path.exists() and (path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected['sha256']):
                raise RuntimeError('Native CGI changed externally; refusing overwrite/removal')
        elif path.exists() or path.is_symlink(): raise RuntimeError('Native CGI path already belongs to another component')
        if release is None:
            path.unlink(missing_ok=True); ownership.unlink(missing_ok=True); return
        content = (Path(release)/'bridge.py').read_text()
        if content.count('port=8876')!=1 or content.count("'/api/apply'}")!=1:
            raise RuntimeError('Unknown bridge contract')
        content = content.replace('port=8876',f'port={self.config["ports"][name]}')
        content = content.replace('/cgi-bin/PEQ', '/cgi-bin/'+IDS[name][2])
        if name=='DaphileRME':
            content = content.replace("'/api/apply'}", "'/api/apply','/api/rme/read','/api/rme/peq/apply','/api/rme/peq/restore','/api/rme/peq/recover','/api/rme/audio/apply','/api/rme/audio/restore','/api/rme/audio/recover'}")
        content = ('#!/usr/bin/python3\n'+content).encode()
        temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
        with temporary.open('xb') as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
        temporary.chmod(0o755); os.replace(temporary,path)
        atomic(ownership,{'sha256':hashlib.sha256(content).hexdigest()})

    def start(self, name, approval, preserve_route=False):
        entry, archive = approval
        state = self.state(name); data = private(state/'data'); releases = private(state/'releases')
        if name=='DaphileCamillaDSP' and not (data/'migration.json').exists():
            origin=Path(self.config.get('legacy_peq_state','/mnt/hdd/daphile-peq/state'))
            copied=[]
            for filename in ('presets.json','presets.previous.json','active_profile.json','access.token'):
                original=origin/filename;destination=data/filename
                if original.is_file() and not original.is_symlink() and not destination.exists():
                    content=original.read_bytes()
                    if len(content)>4*1024*1024: raise RuntimeError('Oversized legacy state')
                    if filename.endswith('.json'): json.loads(content)
                    with destination.open('xb') as stream: stream.write(content);stream.flush();os.fsync(stream.fileno())
                    destination.chmod(0o600);copied.append(filename)
            atomic(data/'migration.json',dict(copied=copied,originals_modified=False))
        current_path = state/'current.json'
        old = json.loads(current_path.read_text()) if current_path.exists() else None
        if old and old['release']==entry['sha256'] and self.running(state): return
        self.stop(name,suspend_route=not preserve_route)
        release = releases/entry['sha256']
        if not release.exists():
            stage = releases/('.stage-'+uuid.uuid4().hex); stage.mkdir(mode=0o700)
            try:
                import io
                with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                    names = zipped.namelist()
                    if len(names)!=len(set(names)): raise RuntimeError('Duplicate approved archive member')
                    for path,digest in entry['files'].items():
                        if not path.startswith('backend/'): continue
                        relative = Path(path.removeprefix('backend/'))
                        if relative.is_absolute() or '..' in relative.parts: raise RuntimeError('Unsafe backend path')
                        content = zipped.read(name+'/'+path)
                        if hashlib.sha256(content).hexdigest()!=digest: raise RuntimeError('Backend content mismatch')
                        destination = stage/relative; destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                        with destination.open('xb') as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
                        destination.chmod(0o755 if relative.as_posix()=='bin/camilladsp' else 0o600)
                os.rename(stage,release)
            finally:
                if stage.exists(): shutil.rmtree(stage)
        for path,digest in entry['files'].items():
            if path.startswith('backend/') and hashlib.sha256((release/path.removeprefix('backend/')).read_bytes()).hexdigest()!=digest:
                raise RuntimeError('Installed backend was altered')
        previous = old['release'] if old and old['release']!=entry['sha256'] else old.get('previous') if old else None
        atomic(current_path,dict(release=entry['sha256'],version=entry['version'],previous=previous,status='starting'))
        self.cgi(name,release)
        with (data/'controller.log').open('ab') as log:
            from worker import parent_death
            child = subprocess.Popen([sys.executable,'-B',str(release/'worker.py'),'--module',name,'--state',str(data),
                                      '--port',str(self.config['ports'][name]),'--rme-state',self.config['rme_state']],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                     cwd=release,preexec_fn=parent_death(),env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        self.children[name]=child
        try: atomic(state/'process.json',started_identity(child))
        except Exception:
            child.terminate();child.wait(timeout=15);self.children.pop(name,None)
            raise
        deadline = time.monotonic()+8
        while time.monotonic()<deadline and child.poll() is None:
            ready = data/'ready.json'
            if ready.exists() and json.loads(ready.read_text()).get('process')==identity(child.pid):
                atomic(current_path,dict(release=entry['sha256'],version=entry['version'],previous=previous,status='running'))
                return
            time.sleep(.05)
        self.stop(name)
        atomic(current_path,dict(release=entry['sha256'],version=entry['version'],previous=previous,status='failed-start'))
        raise RuntimeError('Backend failed to become ready; inspect private controller log')

    def uninstall(self, name):
        self.stop(name); state = self.state(name); self.cgi(name)
        # Only content-addressed, host-owned releases; user data stays outside.
        releases = state/'releases'
        if releases.exists():
            for release in releases.iterdir():
                import re
                if release.is_symlink() or not re.fullmatch('[0-9a-f]{64}',release.name):
                    raise RuntimeError('Unknown release material retained')
                catalog=json.loads((self.root/'approvals.json').read_text())
                entry=next((r for r in catalog.get(name,[]) if r['sha256']==release.name),None)
                expected={p.removeprefix('backend/'):digest for p,digest in entry['files'].items() if p.startswith('backend/')} if entry else {}
                actual={p.relative_to(release).as_posix():p for p in release.rglob('*') if not p.is_dir()}
                if not expected or set(actual)!=set(expected) or any(p.is_symlink() for p in release.rglob('*')) or any(p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected[n] for n,p in actual.items()):
                    raise RuntimeError('Altered or unknown release files retained')
                shutil.rmtree(release)
            releases.rmdir()
        (state/'current.json').unlink(missing_ok=True)

    def sync(self, name):
        native = self.native_states().get(name,'disabled')
        source = Path(self.config['plugins'])/name
        if native=='needs-uninstall' or not source.exists(): self.uninstall(name)
        elif native=='enabled':
            try: approval=self.approved(name)
            except Exception:
                self.stop(name)
                raise
            self.start(name,approval)
        else: self.stop(name)

    def status(self, name):
        state=self.state(name); current=state/'current.json'
        errors=[];running=None;record=None;recovery_required=False;native=None
        try:running=self.running(state)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:errors.append(str(exc))
        try:
            record=json.loads(current.read_text()) if current.exists() else None
            if record is not None and not isinstance(record,dict):raise ValueError('Invalid current module record')
        except (OSError,ValueError,RuntimeError) as exc:record=None;errors.append(str(exc))
        try:
            if name=='DaphileRME':
                recovery_required=bool(pending(private(self.config['rme_state'])))
            else:
                private(state/'data')
                native=native_recovery_status(state,running,record,self.config)
                recovery_required=native['recovery_required']
        except (OSError,ValueError,RuntimeError,AttributeError,TypeError,KeyError) as exc:
            recovery_required=True;errors.append(str(exc))
        result=dict(module=name,running=running,current=record,recovery_required=recovery_required or bool(errors),
                    error='; '.join(errors) if errors else None)
        if name=='DaphileCamillaDSP':result['native_recovery']=native
        return result


def serve(root):
    from menu import ensure,refresh
    from repository import ensure as ensure_repository
    from audio_context import publish as publish_audio_context,withdraw as withdraw_audio_context
    host=Host(root); path=host.root/'control.sock'; running=True
    def shutdown(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,shutdown);signal.signal(signal.SIGINT,shutdown)
    with lock(host.root/'host.lock'),socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        try:
            ensure(host.root,host.config['cgi'])
            (host.root/'menu-error.json').unlink(missing_ok=True)
        except Exception as exc:atomic(host.root/'menu-error.json',{'error':str(exc)})
        if path.exists(): path.unlink()
        server.bind(str(path));os.chown(path,os.geteuid(),host.config['lms_uid']);path.chmod(0o660)
        server.listen(4);server.settimeout(.5)
        atomic(host.root/'process.json',identity(os.getpid()))
        errors={};last=0;retries={}
        try:
            while running:
                if time.monotonic()-last>=2:
                    try:
                        ensure_repository(host.root,host.config['cgi'],host.config['lms_uid'])
                        errors.pop('repository',None)
                    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
                        errors['repository']=str(exc)
                    for name in IDS:
                        source=Path(host.config['plugins'])/name/'install.xml'
                        try:signature=(host.native_states().get(name),source.stat().st_ctime_ns if source.exists() else None)
                        except Exception as exc:
                            # A partial native preference write or transient
                            # metadata failure is not an instruction to stop
                            # both controllers. Retain their independent
                            # ownership guards, report unknown and reread.
                            errors[name]=str(exc);retries.pop(name,None);continue
                        retry=retries.get(name)
                        if retry and retry['signature']==signature and time.monotonic()<retry['after']: continue
                        try: host.sync(name); errors.pop(name,None);retries.pop(name,None)
                        except Exception as exc:
                            errors[name]=str(exc);count=retry['count']+1 if retry and retry['signature']==signature else 1
                            retries[name]=dict(signature=signature,count=count,after=time.monotonic()+min(60,5*2**min(count,4)))
                    modules={name:host.status(name) for name in IDS}
                    try:
                        publish_audio_context(host,modules)
                        errors.pop('audio-context',None)
                    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
                        errors['audio-context']=str(exc)
                        with contextlib.suppress(OSError,ValueError,RuntimeError,KeyError,TypeError):withdraw_audio_context(host)
                    atomic(host.root/'status.json',dict(errors=errors,modules=modules))
                    refresh(host.root,modules)
                    last=time.monotonic()
                try: connection,_=server.accept()
                except socket.timeout: continue
                with connection:
                    connection.settimeout(3)
                    try:
                        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        if uid not in (0,host.config['lms_uid']): raise PermissionError('Peer not authorized')
                        raw=b''
                        while b'\n' not in raw and len(raw)<=4096:
                            block=connection.recv(4097-len(raw))
                            if not block: break
                            raw+=block
                        if len(raw)>4096 or not raw.endswith(b'\n'): raise ValueError('Bounded request required')
                        request=json.loads(raw)
                        if set(request)!={'module','action'} or request['module'] not in IDS: raise ValueError('Unknown request')
                        name=request['module'];action=request['action']
                        if action=='sync': host.sync(name)
                        elif action=='restart' and uid==0:
                            host.stop(name,suspend_route=False)
                            if host.native_states().get(name)!='enabled': raise RuntimeError('Native plugin is not enabled')
                            host.start(name,host.approved(name),preserve_route=True)
                        elif action!='status': raise PermissionError('Operation not authorized')
                        response=host.status(name)
                        refresh(host.root,{n:host.status(n) for n in IDS})
                    except Exception as exc: response={'error':str(exc)}
                    connection.sendall(json.dumps(response).encode()+b'\n')
        finally:
            with contextlib.suppress(OSError,ValueError,RuntimeError,KeyError,TypeError):withdraw_audio_context(host)
            for name in IDS:
                try: host.stop(name)
                except Exception as exc: atomic(host.state(name)/'recovery-required.json',{'reason':str(exc)})
            refresh(host.root,{})
            path.unlink(missing_ok=True);(host.root/'process.json').unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);args=parser.parse_args();serve(args.root)
