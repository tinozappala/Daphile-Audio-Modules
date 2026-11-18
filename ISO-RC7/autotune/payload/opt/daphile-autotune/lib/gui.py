"""Opt-in local UNIX broker. CGI cannot choose a shell command or arbitrary path."""
import fcntl, json, math, os, pwd, secrets, signal, socket, struct, subprocess, sys, time, uuid
from pathlib import Path
from engine import STATE, atomic, load
SOCKET = '/run/daphile-autotune-gui/control.sock'
ACTIONS = {'scheduler', 'scan', 'status', 'apply', 'restore', 'buffer', 'network', 'stability', 'matrix-plan', 'matrix-run', 'cancel', 'activity', 'capture', 'compare', 'audio-setup', 'profile-plan'}
ACTIONS.update({'dashboard', 'rules', 'power-inventory', 'power-plan', 'power-trial', 'boot-plan', 'boot-stage', 'boot-status', 'boot-restore', 'adaptive-plan', 'adaptive-run', 'usb-start', 'usb-capture', 'usb-capture-auto', 'usb-report', 'usb-status', 'ab-run', 'ab-status', 'ab-reveal', 'ab-vote', 'ab-recover', 'automation-configure', 'automation-status', 'automation-start', 'automation-stop'})
ACTIONS.update({'export-create', 'export-read'})
ACTIONS.update({*()})
ACTIONS.update({'automatic-analyze', 'automatic-run', 'automatic-status', 'automatic-restore', 'search-plan', 'search-run', 'search-status', 'stress-run', 'resources', 'nas-tuning', 'human-report', 'portability', 'continuous-configure', 'continuous-status', 'continuous-start', 'continuous-stop'})
ACTIONS.add('automatic-prepare-player')

def completed_job_result(outpath,state,proc,metadata):
    """Retain structured CLI failure details instead of hiding every cause."""
    try:
        result=sanitize(json.loads(outpath.read_text(encoding='utf8')))
        if isinstance(result,dict):return result
    except (OSError,ValueError):pass
    if proc.returncode==-15:
        return cancelled_result(state,proc.pid,metadata['owner_start'])
    try:
        error_path=outpath.with_suffix('.err')
        with error_path.open('rb') as stream:raw=stream.read(16385)
        if len(raw)>16384:raise ValueError('Error exceeds UI limit')
        error=json.loads(raw)
        if not isinstance(error,dict) or error.get('status')!='failed' or not isinstance(error.get('reason'),str):
            raise ValueError('Unstructured error')
        import re
        reason=re.sub(r'(?i)(password|passwd|token|secret|credential)([\s:=]+)[^\s,;]+',r'\1\2[redacted]',error['reason'])
        reason=re.sub(r'(?i)([a-z]+://)[^/@\s]+:[^/@\s]+@',r'\1[redacted]@',reason)
        return dict(status='failed',reason=reason[:1200],message='AutoTune non completato: '+reason[:1200])
    except (OSError,ValueError):
        return {'status':'failed','reason':'Operazione fallita: consulta il registro tecnico.'}

def prune_completed_jobs(workers,metadata,keep=32):
    """Bound status work; private job files remain available on disk."""
    completed=sorted((ident for ident,(proc,_) in workers.items() if proc.poll() is not None),
                     key=lambda ident:metadata[ident]['started'],reverse=True)
    for ident in completed[keep:]:
        workers.pop(ident);metadata.pop(ident)

def validate(request):
    if set(request) - {'action', 'profile', 'target', 'ms', 'periods', 'duration', 'token', 'confirm_audio', 'services', 'job', 'kind', 'label', 'margin', 'samples', 'generate_dsf', 'max_latency', 'clock', 'confirm_experimental', 'confirm_boot', 'tickless', 'cpus', 'profiles', 'repeats', 'blind', 'labels', 'condition', 'enabled', 'playback_priority', 'interval', 'apply_best', 'experimental', 'artifact', 'offset', 'prepare_player'}:
        raise ValueError('Unexpected request key')
    action = request.get('action')
    if 'prepare_player' in request and (action!='automatic-run' or type(request['prepare_player']) is not bool):
        raise ValueError('Player preparation belongs to an explicitly confirmed AutoTune run')
    if action in {'buffer','matrix-run','ab-run','adaptive-run','usb-capture-auto','stress-run'}:
        raise ValueError('Questa prova di riproduzione non è disponibile. Usa AutoTune per le prove controllate.')
    if action == 'search-run' or (action == 'apply' and request.get('profile') == 'experimental'):
        raise ValueError('Il confronto esteso non è disponibile. Usa AutoTune, con durata massima totale di 30 minuti.')
    if action not in ACTIONS:
        raise ValueError('Unknown action')
    if action == 'export-read':
        import re
        if not isinstance(request.get('artifact'), str) or not re.fullmatch('[0-9a-f]{32}', request['artifact']):
            raise ValueError('Invalid export identity')
        if type(request.get('offset', 0)) != int or not 0 <= request.get('offset', 0) <= 1024 * 1024 * 1024:
            raise ValueError('Invalid export offset')
    if action in ('automatic-run','automatic-prepare-player') and request.get('confirm_audio') is not True:
        raise ValueError('Automatic configuration playback/reboot acknowledgement required')
    if action in ['search-run', 'stress-run', 'usb-capture-auto'] and request.get('confirm_audio') is not True:
        raise ValueError('Audio test consent required')
    if 'experimental' in request and type(request['experimental']) != bool:
        raise ValueError('Boolean experimental choice required')
    if action == 'continuous-configure' and request.get('enabled') and (request.get('confirm_audio') is not True):
        raise ValueError('Saved audio test consent required')
    if action == 'power-trial' and request.get('confirm_experimental') is not True:
        raise ValueError('Explicit experimental trial acknowledgement required')
    if action == 'boot-stage' and request.get('confirm_boot') is not True:
        raise ValueError('Explicit boot configuration acknowledgement required')
    if action in ['power-plan', 'power-trial']:
        import re
        if request.get('max_latency') is not None and (type(request['max_latency']) != int or not 1 <= request['max_latency'] <= 10000):
            raise ValueError('Invalid C-state latency ceiling')
        if request.get('clock') is not None and (not isinstance(request['clock'], str) or not re.fullmatch('[A-Za-z0-9_-]{1,32}', request['clock'])):
            raise ValueError('Invalid clocksource name')
    if action in ['boot-plan', 'boot-stage']:
        cpus = request.get('cpus')
        if cpus is not None and (not isinstance(cpus, list) or not 1 <= len(cpus) <= 256 or len(set(cpus)) != len(cpus) or any((type(x) != int or not 1 <= x <= 4095 for x in cpus))):
            raise ValueError('Invalid isolated CPU set')
    if action in ['ab-run', 'adaptive-run', 'ab-recover']:
        if request.get('confirm_audio') is not True:
            raise ValueError('Explicit controlled playback acknowledgement required')
        if type(request.get('repeats', 2)) != int or not 2 <= request.get('repeats', 2) <= 6:
            raise ValueError('Repetitions2..6')
    if action == 'ab-run':
        from ab_session import choose
        choose(request.get('profiles'), request.get('repeats', 2))
    if action in ['ab-vote'] and request.get('condition') not in ['A', 'B', 'C', 'D']:
        raise ValueError('Invalid blind condition')
    if action == 'usb-start':
        labels = request.get('labels')
        if not isinstance(labels, list) or not 2 <= len(labels) <= 8 or any((not isinstance(x, str) or not x.isalnum() or len(x) > 24 for x in labels)) or (len(set(labels)) != len(labels)):
            raise ValueError('Invalid physical socket labels')
    if action in ['usb-capture', 'usb-capture-auto'] and (not isinstance(request.get('label'), str) or not request['label'].isalnum() or len(request['label']) > 24):
        raise ValueError('Invalid socket label')
    if action == 'automation-configure':
        if any((type(request.get(k, False)) != bool for k in ['enabled', 'playback_priority'])) or type(request.get('interval', 300)) != int or (not 60 <= request.get('interval', 300) <= 3600):
            raise ValueError('Invalid automation configuration')
    for key in ['blind', 'tickless', 'confirm_boot', 'confirm_experimental', 'apply_best']:
        if key in request and type(request[key]) != bool:
            raise ValueError('Boolean option required')
    if action in ['apply', 'profile-plan'] and request.get('profile') not in ['safe', 'audio-rt', 'low-activity', 't630-fixed', 'network-stable', 'isolated-audio', 'experimental']:
        raise ValueError('Invalid profile')
    if action == 'apply' and request.get('profile') == 'experimental' and (request.get('confirm_experimental') is not True):
        raise ValueError('Experimental search consent required')
    if action == 'restore' and request.get('target') not in ['previous', 'pending', 'default', 'last-known-good']:
        raise ValueError('Invalid restore')
    if action in ['buffer', 'matrix-run'] and request.get('confirm_audio') is not True:
        raise ValueError('Audio interruption must be explicitly acknowledged')
    if action == 'buffer' and (request.get('ms') not in [10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 150, 200] or request.get('periods') not in [3, 4]):
        raise ValueError('Invalid buffer')
    if action == 'restore' and request.get('target') == 'last-known-good' and (request.get('confirm_audio') is not True):
        raise ValueError('Last-known-good may restart audio; confirmation required')
    if action in ['capture', 'compare'] and request.get('kind') not in ['ab', 'ports']:
        raise ValueError('Invalid comparison kind')
    if action == 'capture':
        label = request.get('label')
        if not isinstance(label, str) or not label.isalnum() or len(label) > 32 or (request['kind'] == 'ab' and label not in ['A', 'B', 'C', 'D']):
            raise ValueError('Invalid capture label')
    if 'generate_dsf' in request and (action != 'matrix-plan' or not isinstance(request['generate_dsf'], bool)):
        raise ValueError('Generated samples require matrix plan and boolean choice')
    if action == 'matrix-run':
        margin = request.get('margin', 1.5)
        if isinstance(margin, bool) or not isinstance(margin, (int, float)) or (not math.isfinite(margin)) or (not 1.1 <= margin <= 5):
            raise ValueError('Invalid safety margin')
    samples = request.get('samples', [])
    if not isinstance(samples, list) or len(samples) > 7:
        raise ValueError('Invalid DSF samples')
    seen = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {'rate', 'path'}:
            raise ValueError('Invalid DSF sample')
        rate = sample['rate']
        path = sample['path']
        if isinstance(rate, bool) or not isinstance(rate, int) or rate not in [2822400, 5644800, 11289600, 22579200, 3072000, 6144000, 12288000] or (rate in seen):
            raise ValueError('Invalid DSF rate')
        if not isinstance(path, str) or not path.startswith('/media/music/') or (not path.lower().endswith('.dsf')) or (len(path) > 2048) or any((ord(c) < 32 for c in path)):
            raise ValueError('Invalid DSF library path')
        seen.add(rate)
    if samples and action != 'matrix-plan':
        raise ValueError('Samples require matrix plan')
    services = request.get('services', [])
    if not isinstance(services, list) or any((x not in ['bluetooth', 'cupsd', 'crond'] for x in services)) or (services and request.get('profile') != 'low-activity'):
        raise ValueError('Invalid optional services')
    if action == 'cancel':
        import re
        if not isinstance(request.get('job'), str) or not re.fullmatch('(?:automatic:)?[0-9a-f]{32}', request['job']):
            raise ValueError('Valid job identifier required')
    duration = request.get('duration', 30)
    if not isinstance(duration, int) or isinstance(duration, bool) or (not (60 <= duration <= (172800 if action == 'stress-run' else 86400) if action in ['stability', 'stress-run'] else 10 <= duration <= 3000)):
        raise ValueError('Invalid duration')
    if action == 'usb-capture-auto' and (not 30 <= duration <= 120):
        raise ValueError('Repeated USB window30..120 seconds')
    if action == 'search-run' and (not 30 <= duration <= 120 or type(request.get('repeats', 3)) != int or (not 3 <= request.get('repeats', 3) <= 6)):
        raise ValueError('Search30..120 seconds and3..6 repetitions')
    if action == 'scheduler' and duration > 60:
        raise ValueError('Scheduler trace limited to60 seconds')
    if action in ['activity', 'capture'] and duration > 600:
        raise ValueError('Observation limited to600 seconds')
    if action in ['ab-run', 'adaptive-run'] and (not 30 <= duration <= 120):
        raise ValueError('Controlled comparison30..120 seconds')
    if action in ['power-trial', 'usb-capture'] and (not 30 <= duration <= 600):
        raise ValueError('Trial30..600 seconds')
    return action

def args(request):
    action = validate(request)
    if action == 'search-plan':
        return ['search', 'plan']
    if action == 'search-status':
        return ['search', 'status']
    if action == 'search-run':
        return ['search', 'run', '--duration', str(request.get('duration', 30)), '--repeats', str(request.get('repeats', 3)), '--confirm-audio-test'] + ([] if request.get('experimental') else ['--stages', 'scheduling,network'])
    if action == 'stress-run':
        return ['stress', '--duration', str(request.get('duration', 86400)), '--confirm-audio-test']
    if action.startswith('continuous-'):
        cmd = ['continuous', action.split('-', 1)[1]]
        if action == 'continuous-configure':
            cmd += ['--enabled'] if request.get('enabled') else []
            cmd += ['--confirm-audio-test'] if request.get('confirm_audio') else []
            cmd += ['--experimental'] if request.get('experimental') else []
        return cmd
    if action == 'automatic-prepare-player':
        return ['automatic','prepare-player','--confirm-audio-test']
    if action == 'automatic-run':
        return ['automatic','run','--confirm-audio-test'] + (
            ['--prepare-player'] if request.get('prepare_player') is True else []) + (
            ['--experimental'] if request.get('experimental') else [])
    if action.startswith('automatic-'):
        return ['automatic', action.split('-', 1)[1]]
    if action.startswith('power-'):
        cmd = ['power', action.split('-', 1)[1]]
        if request.get('max_latency') is not None:
            cmd += ['--max-latency', str(request['max_latency'])]
        if request.get('clock') is not None:
            cmd += ['--clock', request['clock']]
        if action == 'power-trial':
            cmd += ['--duration', str(request.get('duration', 30)), '--confirm-experimental']
        return cmd
    if action.startswith('boot-'):
        cmd = ['boot-profile', action.split('-', 1)[1]]
        if request.get('cpus') is not None:
            cmd += ['--cpus', ','.join(map(str, request['cpus']))]
        if request.get('tickless'):
            cmd += ['--tickless']
        if action == 'boot-stage':
            cmd += ['--confirm-boot']
        return cmd
    if action.startswith('adaptive-'):
        return ['adaptive', action.split('-', 1)[1]] + (['--duration', str(request.get('duration', 30)), '--repeats', str(request.get('repeats', 3)), '--confirm-audio-test'] + (['--apply-best'] if request.get('apply_best') else []) if action == 'adaptive-run' else [])
    if action.startswith('usb-'):
        cmd = ['usb-wizard', action.split('-', 1)[1]]
        if action == 'usb-start':
            cmd += ['--labels-json', json.dumps(request['labels'])]
        if action in ['usb-capture', 'usb-capture-auto']:
            cmd += ['--label', request['label'], '--duration', str(request.get('duration', 30))]
        if action == 'usb-capture-auto':
            cmd += ['--confirm-audio-test']
        return cmd
    if action.startswith('ab-'):
        cmd = ['ab-session', action.split('-', 1)[1]]
        if action == 'ab-run':
            cmd += ['--profiles-json', json.dumps(request['profiles']), '--repeats', str(request.get('repeats', 2)), '--duration', str(request.get('duration', 30)), '--confirm-audio-test'] + ([] if request.get('blind', True) else ['--visible'])
        if action == 'ab-vote':
            cmd += ['--condition', request['condition']]
        if action == 'ab-recover':
            cmd += ['--confirm-audio-test']
        return cmd
    if action.startswith('automation-'):
        cmd = ['automation', action.split('-', 1)[1]]
        if action == 'automation-configure':
            cmd += ['--interval', str(request.get('interval', 300))] + (['--enabled'] if request.get('enabled') else []) + (['--playback-priority'] if request.get('playback_priority') else [])
        return cmd
    if action == 'profile-plan':
        return ['profile', 'show', request['profile']]
    if action == 'apply':
        return ['tune', request['profile'], '--duration', str(max(30, request.get('duration', 30)))] + [item for name in request.get('services', []) for item in ['--stop-service', name]] + (['--confirm-experimental'] if request.get('confirm_experimental') else [])
    if action == 'restore':
        return ['restore', request['target']] + (['--confirm-audio-test'] if request.get('confirm_audio') is True else [])
    if action == 'buffer':
        return ['player-buffer', str(request['ms']), str(request['periods']), '--duration', str(max(30, request.get('duration', 30))), '--confirm-audio-test']
    if action == 'scheduler':
        return ['scheduler', '--duration', str(request.get('duration', 10))]
    if action == 'activity':
        return ['activity', '--duration', str(request.get('duration', 30))]
    if action == 'capture':
        return ['ab' if request['kind'] == 'ab' else 'port', request['label'], '--duration', str(request.get('duration', 30))]
    if action == 'compare':
        return ['compare', request['kind']]
    if action == 'stability':
        return ['stability', '--duration', str(request.get('duration', 3600))]
    if action == 'matrix-plan':
        return ['matrix', 'plan'] + (['--generate-dsf'] if request.get('generate_dsf') is True else []) + [arg for item in request.get('samples', []) for arg in ['--dsf', str(item['rate']) + '=' + item['path']]]
    if action == 'matrix-run':
        return ['matrix', 'run', '--duration', str(min(600, request.get('duration', 30))), '--margin', str(request.get('margin', 1.5)), '--confirm-audio-test']
    return [action]

def cancel_saved_automatic(ident, state):
    """No arbitrary PIDs: verify the durable worker ID, boot, host and start ticks."""
    from engine import boot_id, start_ticks
    from detection import read
    p = Path(state) / 'automatic.json'
    r = load(p)
    if ident != 'automatic:' + r.get('id', '') or r.get('status') != 'running':
        raise RuntimeError('Automatic worker is not active')
    pid = r.get('owner_pid')
    if type(pid) != int or pid <= 1 or pid == os.getpid() or (r.get('boot_id') != boot_id()) or (not r.get('owner_start')) or (start_ticks(pid) != r['owner_start']) or (r.get('binding', {}).get('host_uuid') != __import__('host_identity').current(read)):
        raise RuntimeError('Worker identity changed; no signal sent')
    __import__('process_guard').signal_owner(r,signal.SIGTERM)
    return {'status': 'cancelling', 'id': ident, 'rollback': 'Automatic guard preserves the original session deadline'}

def current_progress(state, jobs):
    """A saved running flag cannot outlive its active broker job or precede it."""
    mapping = {'search-progress': {'search-run', 'apply'}, 'stress-progress': {'stress-run'}, 'usb-auto-progress': {'usb-capture-auto'}, 'stability': {'stability'}}
    progress = {}
    for name, actions in mapping.items():
        p = Path(state) / (name + '.json')
        if not p.is_file() or p.is_symlink():
            continue
        try:
            r = load(p)
        except (ValueError, OSError):
            continue
        if r.get('status') == 'running':
            owner = next((j for j in jobs if j.get('running') and j.get('action') in actions and (p.stat().st_mtime >= j.get('started', float('inf')))), None)
            if owner:
                r = dict(r, job_id=owner['id'], current=True)
            else:
                r = dict(r, status='historical-interrupted', historical_status='running', current=False, message='Prova precedente senza processo attivo; esito e ripristino da verificare nel registro.')
        progress[name] = r
    return progress

def cancelled_result(state, pid, start):
    """Only the exact cancelled lease can attest recovery; never infer success."""
    r = {'status': 'interrupted-recovery-unverified', 'message': 'Operazione interrotta. Il ripristino non è ancora attestato; controlla il recupero prima di altre modifiche.'}
    p = Path(state) / 'activity-session.json'
    if not p.is_file() or p.is_symlink():
        return r
    try:
        lease = load(p)
    except (ValueError, OSError):
        return r
    from engine import boot_id
    if lease.get('owner') != pid or lease.get('start') != start or lease.get('boot_id') != boot_id():
        return r
    state = lease.get('status')
    if state in ['running', 'recovering']:
        r.update(status='recovering', message='Prova interrotta. Il guardiano sta recuperando impostazioni e playlist; attendi la conferma.')
    elif state == 'recovery-required':
        r.update(status='recovery-required', message='Prova interrotta: recupero incompleto. Usa i controlli di recupero nella sezione avanzata.')
    elif state == 'recovered':
        recovery = lease.get('playback_recovery', {})
        if recovery.get('restored') is True or recovery.get('status') == 'untouched-passive-observer':
            r.update(status='cancelled-restored', message='Prova interrotta su richiesta. Impostazioni recuperate e stato della riproduzione verificato; il collaudo non è completato.')
    return r

def serve(state=STATE):
    if os.geteuid() != 0:
        raise RuntimeError('Root required to explicitly enable GUI broker')
    user = pwd.getpwnam('user')
    directory = Path(SOCKET).parent
    if directory.is_symlink():
        raise RuntimeError('Unsafe runtime directory')
    directory.mkdir(mode=488, exist_ok=True)
    os.chown(directory, 0, user.pw_gid)
    os.chmod(directory, 488)
    guard = (directory / 'broker.lock').open('a')
    fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if Path(SOCKET).exists():
        Path(SOCKET).unlink()
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(SOCKET)
    os.chown(SOCKET, 0, user.pw_gid)
    os.chmod(SOCKET, 432)
    sock.listen(4)
    sock.settimeout(5)
    from engine import start_ticks
    atomic(directory / 'pid.json', dict(pid=os.getpid(), start=start_ticks(os.getpid())))
    token = secrets.token_urlsafe(32)
    workers = {}
    job_meta = {}
    try:
        while True:
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(3)
                try:
                    pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid not in [0, user.pw_uid]:
                        raise PermissionError('Unauthorized local peer')
                    data = b''
                    while b'\n' not in data:
                        chunk = conn.recv(4096)
                        if not chunk or len(data) + len(chunk) > 8192:
                            raise ValueError('Invalid request length')
                        data += chunk
                    request = json.loads(data.split(b'\n')[0])
                    action = validate(request)
                    if action not in ['scan', 'status'] and (not secrets.compare_digest(str(request.get('token', '')), token)):
                        raise PermissionError('Invalid CSRF token')
                    if action == 'export-read':
                        import base64
                        path = Path(state) / ('gui-export-' + request['artifact'] + '.tar.gz')
                        if path.is_symlink() or not path.is_file():
                            raise RuntimeError('Export absent')
                        if any((p.poll() is None for p, _ in workers.values())):
                            raise RuntimeError('Await export completion')
                        offset = request.get('offset', 0)
                        size = path.stat().st_size
                        if offset > size:
                            raise ValueError('Offset outside export')
                        with path.open('rb') as f:
                            f.seek(offset)
                            data = f.read(65536)
                        result = {'status': 'export-chunk', 'artifact': request['artifact'], 'bytes': size, 'offset': offset, 'next': offset + len(data), 'data': base64.b64encode(data).decode(), 'done': offset + len(data) == size}
                    elif action == 'cancel':
                        ident = request['job']
                        if ident.startswith('automatic:'):
                            result = cancel_saved_automatic(ident, state)
                        else:
                            if ident not in workers:
                                raise ValueError('Unknown job')
                            proc, _ = workers[ident]
                            if proc.poll() is None:
                                proc.terminate()
                            result = dict(status='cancelling', id=ident, rollback='Watchdog recovers a pending tuning transaction')
                    elif action == 'status':
                        prune_completed_jobs(workers,job_meta)
                        jobs = []
                        for ident, (proc, outpath) in workers.items():
                            code = proc.poll()
                            result = None
                            if code is not None:
                                result = completed_job_result(outpath,state,proc,job_meta[ident])
                                if result and result.get('status') == 'exported':
                                    result['artifact'] = ident
                            jobs.append(dict(id=ident, running=code is None, recovering=bool(result and result.get('status') == 'recovering'), exit=code, result=result, action=job_meta[ident]['action'], started=job_meta[ident]['started']))
                        import automatic
                        progress = current_progress(state, jobs)
                        result = dict(enabled=True, token=token, jobs=jobs, automatic_supported=True, automatic=automatic.status(state), progress=progress, expires_seconds=None)
                    else:
                        import automatic
                        if action not in {'automatic-status', 'automatic-analyze', 'scan', 'audio-setup', 'profile-plan', 'dashboard', 'rules', 'power-inventory', 'boot-plan', 'boot-status', 'adaptive-plan', 'usb-status', 'ab-status', 'ab-vote', 'ab-reveal', 'resources', 'human-report', 'export-create', 'search-plan', 'search-status', 'portability', 'continuous-status'} and automatic.external_trial():
                            raise RuntimeError('Collaudo audio in corso: operazione rinviata prima di modificare il sistema.')
                        if action == 'automatic-status':
                            import automatic
                            conn.sendall(json.dumps(sanitize(automatic.status(state))).encode() + b'\n')
                            continue
                        if action in ['ab-status', 'ab-vote', 'ab-reveal']:
                            import ab_session
                            result = ab_session.vote(request['condition'], state) if action == 'ab-vote' else ab_session.status(state, action == 'ab-reveal')
                            conn.sendall(json.dumps(sanitize(result)).encode() + b'\n')
                            continue
                        if any((p.poll() is None for p, _ in workers.values())):
                            raise RuntimeError('Another job is running')
                        if len(workers) >= 64:
                            completed = [key for key, (process, _) in workers.items() if process.poll() is not None]
                            for key in completed[:-16]:
                                workers.pop(key)
                                job_meta.pop(key, None)
                            if len(workers) >= 64:
                                raise RuntimeError('Session job limit reached; restart GUI broker')
                        state.mkdir(mode=448, exist_ok=True)
                        if state.is_symlink() or state.stat().st_uid != 0 or state.stat().st_mode & 63:
                            raise RuntimeError('Unsafe state')
                        ident = uuid.uuid4().hex
                        output = state / ('gui-job-' + ident + '.json')
                        error = state / ('gui-job-' + ident + '.err')
                        arguments = ['export', str(Path(state) / ('gui-export-' + ident + '.tar.gz'))] if action == 'export-create' else args(request)
                        cmd = [sys.executable, str(Path(__file__).parent.parent / 'bin/dat'), '--state', str(state), *arguments]
                        started = time.time()
                        with output.open('x') as out, error.open('x') as err:
                            os.chmod(output, 384)
                            os.chmod(error, 384)
                            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True, close_fds=True)
                        from engine import start_ticks
                        workers[ident] = (proc, output)
                        job_meta[ident] = {'action': action, 'started': started, 'owner_start': start_ticks(proc.pid)}
                        result = dict(id=ident, status='started', action=action, started=started)
                    conn.sendall(json.dumps(result).encode() + b'\n')
                except Exception as e:
                    try:
                        conn.sendall(json.dumps(dict(status='failed', reason=str(e))).encode() + b'\n')
                    except OSError:
                        pass
    finally:
        sock.close()
        Path(SOCKET).unlink(missing_ok=True)
        p = directory / 'pid.json'
        if p.exists() and load(p).get('pid') == os.getpid():
            p.unlink()
        guard.close()

def exchange(request):
    sock = socket.socket(socket.AF_UNIX)
    sock.settimeout(5)
    try:
        sock.connect(SOCKET)
        sock.sendall(json.dumps(request).encode() + b'\n')
        data = b''
        while b'\n' not in data:
            part = sock.recv(65536)
            if not part or len(data) > 4 * 1024 * 1024:
                raise RuntimeError('Incomplete or oversized broker response')
            data += part
        return json.loads(data.split(b'\n')[0])
    finally:
        sock.close()

def check_origin(env):
    host = env.get('HTTP_HOST', '')
    origin = env.get('HTTP_ORIGIN', '')
    if not host or origin != 'http://' + host or (not re_host(host)):
        raise PermissionError('Same-origin HTTP request required on the device IP address')

def re_host(host):
    import ipaddress
    from detection import command
    name = host.rsplit(':', 1)[0] if ':' in host else host
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    if name in ['127.0.0.1', '::1']:
        return True
    addresses = command(['ip', '-o', '-4', 'addr', 'show']) or ''
    return any((name == part.split('/')[0] for part in addresses.split() if '/' in part))

def sanitize(value):
    if isinstance(value, dict):
        result = {k: sanitize(v) for k, v in value.items() if k not in ['playback_resume', 'urls', 'accepted_intermediate', 'token_secret', 'pcm_states', 'network_before', 'network_after', 'disk_before', 'disk_after', 'softirqs_before', 'softirqs_after', 'original_playback', 'original_preferences', 'memory_samples', 'playback']}
        if result.get('parameter') == 'player_buffer':
            import re
            for key in ['before', 'after']:
                if isinstance(result.get(key), str):
                    result[key] = dict(re.findall('^(BUFFERTIME|PERIODCOUNT)=(.*)$', result[key], re.M))
        if isinstance(result.get('segments'), list):
            result['segment_count'] = len(result['segments'])
            result['segments'] = result['segments'][-3:]
        return result
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    return value

ACTIONS.add('power-review')

__release_signature__ = 'Tino Zappala'
