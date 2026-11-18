"""Hardware-bound automatic workflow using the actual measured tuning adapters.

There is no audio-quality score. Keep a stable existing buffer when verified,
otherwise use the measured matrix margin. Reboots require the one run consent.
"""
import contextlib, fcntl, hashlib, json, os, re, signal, subprocess, sys, time, uuid
from pathlib import Path
import engine, player, power_policy, boot_recovery, matrix, audio_setup, calibration_budget
from detection import scan, read, numbers
FINAL = {'configured', 'restored', 'failed-restored', 'recovery-required', 'blocked', 'timed-out-recovery-required'}

def public(record):
    return {k: v for k, v in record.items() if k not in ['target', 'original_playback', 'original_preferences', 'owner_start', 'initial_current']}

def status(state=engine.STATE):
    p = Path(state) / 'automatic.json'
    if not p.exists():
        return {'status': 'idle', 'message': 'Pronto per analizzare il sistema.', 'steps': []}
    r = engine.load(p)
    result = public(r)
    result['timing'] = calibration_budget.timing(r, r.get('finished'))
    if r.get('status') == 'configured':
        result['active_configuration'] = current_configuration(state, r)
        if not result['active_configuration']['verified']:
            result.update(status='historical-configured', historical_status='configured', message='Risultato storico: configurazione attuale non verificata. ' + '; '.join(result['active_configuration']['reasons']))
    if r.get('status') == 'running' and r.get('phase') == 'search':
        p = Path(state) / 'search-progress.json'
        if p.exists() and p.stat().st_mtime >= r.get('phase_started', float('inf')):
            s = engine.load(p)
            result['search_progress'] = s
            result['message'] = 'Ricerca misurata: ' + str(s.get('completed', 0)) + ' / ' + str(s.get('total', 0)) + ' finestre; candidato ' + s.get('candidate', 'riferimento') + '; fase ' + s.get('phase', 'confronto')
    if r.get('status') == 'running' and r.get('phase') == 'matrix':
        p = Path(state) / 'matrix.json'
        if p.exists() and p.stat().st_mtime >= r['phase_started']:
            m = engine.load(p)
            rows = m.get('results', [])
            result['matrix_progress'] = {'completed': len(rows), 'total': r.get('matrix_total'), 'failed': sum((x.get('status') == 'failed' for x in rows)), 'buffer_ms': rows[-1].get('buffer_ms') if rows else None}
    return result

def current_configuration(state, r):
    reasons = []
    p = Path(state) / 'current.json'
    if not p.exists():
        return {'verified': False, 'reasons': ['Nessun profilo corrente registrato.']}
    try:
        j = engine.load(p)
        if j.get('status') != 'committed' or j.get('profile') != 'automatic':
            reasons.append('Il profilo corrente non corrisponde alla configurazione automatica.')
        if j.get('session_owner') != {'kind': 'automatic', 'id': r.get('id')}:
            reasons.append('Identificativo della configurazione non corrispondente.')
        if j.get('boot_id') != engine.boot_id():
            reasons.append('Configurazione di un avvio precedente.')
        if reasons:
            return {'verified': False, 'reasons': reasons}
        h = scan()
        binding = r.get('binding', {})
        if h['hardware_id'] != j.get('hardware_id') or h['system'].get('product_uuid') != binding.get('host_uuid') or h['kernel']['release'] != binding.get('kernel'):
            reasons.append('Hardware o kernel differente.')
        for c in j.get('changes', []):
            engine.guard(c, h)
            if engine.setting_value(c) != c['after']:
                reasons.append('Valore corrente differente: ' + c['parameter'])
    except (OSError, RuntimeError, ValueError, KeyError) as e:
        reasons.append('Verifica corrente incompleta: ' + str(e))
    return {'verified': not reasons, 'reasons': reasons, 'checked': time.time(), 'transaction_id': j.get('id')}

def change_key(c):
    parameter = c['parameter']
    if parameter in ['cpu_governor', 'governor_search']:
        return ('governor', c.get('policy') or str(Path(c['path']).parent))
    if parameter in ['thread_scheduler', 'audio_schedule_search']:
        return ('scheduler', c['tid'])
    return (parameter, c.get('path'), c.get('tid'), c.get('service'))
from core_policy import decide

def analyze(state=engine.STATE):
    h = scan()
    setup = audio_setup.report(h)
    from power_review import report as power_report
    power_inventory = power_report(h)
    from thermal_policy import OPERATING_LIMIT_C, CRITICAL_RESERVE_C
    temperature_policy = {'operating_limit_c': OPERATING_LIMIT_C, 'critical_reserve_c': CRITICAL_RESERVE_C}
    result = {'kind': 'automatic-analysis', 'status': 'analyzed', 'message': 'Analisi completata. Nessuna modifica applicata.', 'hardware': {'model': h['system'].get('product_name'), 'physical_cores': h['cpu'].get('physical_cores'), 'kernel': h['kernel']['release'], 'firmware': h['daphile_version']}, 'audio_setup': setup, 'power_inventory': power_inventory, 'temperature_policy': temperature_policy, 'runtime_summary': __import__('runtime_summary').current_summary(h), 'steps': [], 'sound_quality_claim': False}
    if h['dac']['status'] != 'verified-active':
        result.update(eligible=False, message='Avvia un brano sul DAC da configurare, poi ripeti l’analisi.')
        return result
    result.update(eligible=True, plan=decide(h))
    result['decisions'] = [{'setting': 'CPU, core, IRQ e priorità', 'result': 'Alternative ammissibili confrontate con misure complete; conservazione dei valori correnti in caso di incertezza.'}, {'setting': 'Buffer ALSA e formati', 'result': 'Il valore corrente viene mantenuto solo se supera le prove; altrimenti si sceglie un buffer verificato con margine.'}, {'setting': 'Periferiche e risorse', 'result': 'Controlli POWER inventariati; nessun cambiamento aggiunto senza prove dedicate.'}, {'setting': 'Segnale audio', 'result': 'Volume, toni e conversioni non vengono cambiati da AutoTune.'}]
    try:
        result['boot_plan'] = power_policy.isolation(h)
    except (ValueError, RuntimeError) as e:
        result['boot_plan'] = {'status': 'unavailable', 'reason': str(e)}
    result['preserved'] = ['Segnale, volume, resampling e trasporto DSD', 'Clocksource e C-state non dimostrati migliori', 'Servizi sconosciuti o periferiche necessarie', 'Isolamento preesistente']
    return result

def candidate_choice(report, original):
    if report.get('restoration') != 'rolled-back' or report.get('status') != 'finished':
        raise RuntimeError('Matrix did not restore its original configuration')
    allowed = {(g['buffer_ms'], g['periods']) for g in report.get('groups', []) if g.get('complete')}
    if original in allowed:
        return (original, 'Configurazione esistente verificata sui formati rappresentativi; mantenuta. Matrice completa disponibile separatamente.' if report.get('exhaustive') is False else 'Configurazione esistente verificata su tutti i formati supportati; mantenuta.')
    candidate = report.get('recommended_candidate')
    if candidate and (candidate['buffer_ms'], candidate['periods']) in allowed:
        return ((candidate['buffer_ms'], candidate['periods']), 'Configurazione verificata con margine misurato sui buffer.')
    raise RuntimeError('No complete stable buffer candidate; originals retained')

def external_trial():
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            args = p.read_bytes().split(b'\x00')
        except OSError:
            continue
        if any((re.fullmatch(b'/mnt/hdd/dat-fw2240-final-[0-9TZ]+/audio\\.py', a) or re.fullmatch(b'/mnt/hdd/dat-completion-qa-v[0-9]+-[0-9]+/runner\\.py', a) for a in args)):
            return True
    return False

def update(root, r, phase, message, percent):
    r.update(phase=phase, message=message, percent=percent, phase_started=time.time(), updated=time.time())
    engine.atomic(root / 'automatic.json', r)

def summary_changes(changes):
    labels = {'cpu_governor': 'Governatore CPU', 'thread_affinity': 'Affinità thread', 'usb_irq_affinity': 'Affinità IRQ USB', 'thread_scheduler': 'Priorità e schedulatore audio', 'dac_usb_runtime_pm': 'Alimentazione DAC', 'empty_usb_pm': 'Controller USB senza periferiche', 'scanner_nice': 'Priorità scansione libreria', 'player_buffer': 'Buffer ALSA'}
    rows = []
    for c in changes:
        before, after = (c['before'], c['after'])
        if c['parameter'] == 'player_buffer':
            before = audio_setup.preferences(before)
            after = audio_setup.preferences(after)
        rows.append({'parameter':c['parameter'],'setting': labels.get(c['parameter'], c['parameter']), 'target': c.get('name') or c.get('path') or c.get('controller'), 'before': before, 'after': after, 'verified': True})
    return rows

def apply_combined(h, chosen, root, selected=None):
    raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
    """One journal and watchdog cover buffer, IRQ, governor and scheduler changes."""
    with engine.locked(root) as directory:
        engine.ensure_idle_transaction(directory)
        player.assert_library_idle(h)
        t = player.target(h)
        saved = player.playback_snapshot(t['mac'])
        text = Path(t['path']).read_text()
        before = review_observe(30)
        if engine.compare(before, before) or not all(engine.health(scan()).values()):
            raise RuntimeError('Unstable or incomplete pre-change observation')
        fresh = scan()
        if fresh['hardware_id'] != h['hardware_id']:
            raise RuntimeError('Hardware changed before applying')
        plan = decide(fresh, selected, before)
        changes = []
        after = player.edit(text, *chosen)
        if after != text:
            changes.append(dict(t, parameter='player_buffer', before=text, after=after))
        journal = engine.begin_journal(directory, fresh, changes, 300, 'automatic')
        journal['selected_policy'] = selected
        journal['playback_resume'] = saved
        engine.atomic(directory / 'transaction.json', journal)
        watcher = engine.spawn_watchdog(directory, journal)
        try:
            if changes:
                c = changes[0]
                player.guard(c)
                c['write_attempted'] = True
                engine.atomic(directory / 'transaction.json', journal)
                player.write(c, after)
                player.restore_playback(saved)
                fresh = player.wait_audio(t['mac'], buffers=chosen)
                plan = decide(fresh, selected, before)
            network_changes = any((c['parameter'].startswith('network_') for c in plan['changes']))
            if network_changes:
                from network_test import audit
                nas_before = audit()
                if nas_before.get('nas') and nas_before['status'] != 'pass':
                    raise RuntimeError('NAS baseline failed; automatic network change refused')
            journal['changes'] += plan['changes']
            engine.atomic(directory / 'transaction.json', journal)
            for c in plan['changes']:
                current = scan()
                engine.guard(c, current)
                if current['hardware_id'] != h['hardware_id']:
                    raise RuntimeError('Hardware changed during application')
                value = engine.setting_value(c)
                if value != c['before']:
                    from advanced import irq_thread_side_effect
                    if not irq_thread_side_effect(c, value, journal['changes']):
                        raise RuntimeError('Concurrent policy changed a setting')
                c['write_attempted'] = True
                engine.atomic(directory / 'transaction.json', journal)
                engine.write_control(c, c['after'])
            observed = review_observe(30)
            current = scan()
            from core_policy import application_issues
            reasons = application_issues(before, observed, journal['changes'])
            if current['hardware_id'] != h['hardware_id'] or not all(engine.health(current).values()):
                reasons.append('Post-change hardware or playback health incomplete')
            initial_hw = h['dac']['pcm']['hw']
            actual = current['dac'].get('pcm', {}).get('hw', {})
            if any((actual.get(k) != initial_hw.get(k) for k in ['format', 'rate', 'channels'])):
                reasons.append('Audio format changed')
            player.wait_audio(t['mac'], buffers=chosen)
            for c in journal['changes']:
                engine.guard(c, current)
                if engine.setting_value(c) != c['after']:
                    reasons.append('Setting overridden: ' + c['parameter'])
            if network_changes:
                nas_after = audit()
                journal['network_validation'] = {'before': nas_before, 'after': nas_after}
                if nas_after.get('nas') and nas_after['status'] != 'pass':
                    reasons.append('NAS after automatic network change failed')
            if plan['changes']:
                from core_policy import measured_gain
                if not measured_gain(before, observed): reasons.append('Combined policy has no independently observed improvement')
            if reasons:
                raise RuntimeError('Post-change regression: ' + '; '.join(reasons))
            journal.update(status='committed', profile='automatic', benchmark={'before': before, 'after': observed}, validation='matrix plus before/after observation; not sound certification')
            engine.atomic(directory / 'transaction.json', journal)
            engine.atomic(directory / 'current.json', journal)
            engine.atomic(directory / 'last-known-good.json', journal)
            return {'status': 'committed', 'transaction_id': journal['id'], 'changes': summary_changes(journal['changes']), 'warnings': plan['warnings'], 'benchmark': journal['benchmark']}
        except BaseException:
            engine.rollback(directory, journal)
            raise
        finally:
            engine.close_watchdog(watcher, journal)

def reboot():
    subprocess.Popen(['/bin/sh', '-c', 'sleep 3; /usr/local/bin/sudocmd reboot'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

def run(state=engine.STATE, confirmed=False, resuming=False, experimental=False, *, prepare=False):
    timing = calibration_budget.record()
    root = Path(state)
    if resuming:
        raise RuntimeError('V2 never resumes interrupted tests or extends historical deadlines')
    try:
        with calibration_budget.Deadline(timing) as budget:
            return bounded_run(state, confirmed, resuming, experimental, budget, prepare=prepare)
    except calibration_budget.BudgetExpired as error:
        from autotune_v2.budget_guard import expired_result
        from autotune_v2.workflow import public as public_v2
        return expired_result(root, timing, error, engine, public_v2)

def bounded_run(state, confirmed, resuming, experimental, budget):
    raise RuntimeError('Legacy automatic playback is unavailable: an explicitly selected native V2 workflow is required.')
    if os.geteuid() != 0 or not confirmed:
        raise RuntimeError('Root and automatic playback/reboot consent required')
    root = Path(state)
    with engine.locked(root):
        pass
    with (root / 'automatic-owner.lock').open('a') as lease:
        os.chmod(lease.name, 384)
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if external_trial():
            raise RuntimeError('Collaudo audio in corso. La configurazione automatica attenderà la sua conclusione; nessuna impostazione modificata.')
        if resuming:
            r = engine.load(root / 'automatic.json')
            r.update(status='running', owner_pid=os.getpid(), owner_start=engine.start_ticks(os.getpid()), boot_id=engine.boot_id())
        else:
            prior = status(root)
            if prior.get('status') == 'configured' and prior.get('active_configuration', {}).get('verified') and (not experimental):
                return {'status': 'current-verified', 'message': 'La configurazione automatica è già applicata. Ho verificato i valori correnti: nessuna nuova prova o modifica necessaria. Questa lettura non aggiunge un nuovo collaudo di stabilità.', 'active_configuration': prior['active_configuration'], 'audio_setup': audio_setup.report(), 'sound_quality_claim': False, 'new_trials_executed': False}
            if prior.get('status') in ['running', 'waiting-reboot', 'waiting-playback', 'recovery-required', 'timed-out-recovery-required']:
                raise RuntimeError('Automatic session still active or needs recovery')
            h = scan()
            try:
                player.assert_library_idle(h)
            except RuntimeError as error:
                if not str(error).startswith(('Library scanner active', 'LMS scan active')):
                    raise
                record = {'status': 'blocked', 'phase': 'library-scan', 'message': 'Scansione della libreria in corso. Attendere la fine e riprovare AutoTune; nessuna impostazione audio modificata.', 'created': time.time(), 'finished': time.time(), 'steps': [], 'configuration_writes': 0, 'player_commands': 0}
                engine.atomic(root / 'automatic-last-attempt.json', record)
                if not (root / 'automatic.json').exists():
                    engine.atomic(root / 'automatic.json', record)
                return record
            t = player.target(h)
            with engine.locked(root):
                try:
                    engine.ensure_idle_transaction(root)
                except RuntimeError as e:
                    raise RuntimeError('Esiste un profilo da recuperare prima di una nuova calibrazione. Usa Ripristina; nessuna nuova impostazione applicata.') from e
            r = {'kind': 'automatic-configuration', 'id': uuid.uuid4().hex, 'status': 'running', 'created': time.time(), 'steps': [], 'binding': {'hardware_id': h['hardware_id'], 'host_uuid': h['system']['product_uuid'], 'DAC': t['identity'], 'kernel': h['kernel']['release']}, 'target': t, 'original_preferences': Path(t['path']).read_text(), 'original_playback': player.playback_snapshot(t['mac']), 'initial_current': engine.load(root / 'current.json') if (root / 'current.json').exists() else None, 'experimental_search': experimental, 'boot_owned': False, 'boot_id': engine.boot_id(), 'sound_quality_claim': False, 'owner_pid': os.getpid(), 'owner_start': engine.start_ticks(os.getpid())}
        r.update(budget.record)
        manifest = None
        watcher = None
        try:
            update(root, r, 'hardware', 'Verifica identità del computer, del DAC e della topologia CPU.', 5)
            watcher = subprocess.Popen([sys.executable, str(Path(__file__).parent.parent / 'bin/dat'), '--state', str(root), 'automatic', 'watchdog'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
            ready = root / ('automatic-watch-ready-' + r['id'] + '.json')
            end = time.monotonic() + 5
            while not ready.exists() and watcher.poll() is None and (time.monotonic() < end):
                time.sleep(0.05)
            if not ready.exists() or engine.load(ready).get('pid') != watcher.pid:
                raise RuntimeError('Automatic deadline guard did not arm; no settings changed')
            t = r['target']
            text = Path(t['path']).read_text()
            player.guard(dict(t, parameter='player_buffer', before=text, after=text), restoring=True)
            h = scan()
            if h['system']['product_uuid'] != r['binding']['host_uuid'] or h['kernel']['release'] != r['binding']['kernel']:
                raise RuntimeError('Automatic session belongs to another host or kernel')
            __import__('playback_start').start_verified(t['mac'], 60)
            h = player.wait_audio(t['mac'], 60)
            if h['hardware_id'] != r['binding']['hardware_id'] or h['dac']['identity'] != r['binding']['DAC']:
                raise RuntimeError('DAC or hardware identity changed')
            player.assert_library_idle(h)
            if resuming:
                boot = boot_recovery.status(root)
                if boot.get('status') != 'healthy-boot' or boot.get('id') != r.get('boot_transaction'):
                    raise RuntimeError('Requested boot isolation did not pass automatic health verification')
                r['steps'].append({'setting': 'Isolamento dei core', 'result': 'Applicato e verificato dopo il riavvio', 'value': h['cpu'].get('isolated')})
            else:
                r['steps'].append({'setting': 'Isolamento dei core', 'result': 'Preesistente e preservato' if numbers(h['cpu'].get('isolated')) else 'Conservato: nessuna modifica di avvio senza confronto misurato', 'value': h['cpu'].get('isolated')})
            update(root, r, 'preflight', 'Verifica preventiva di latenza, temperatura, memoria e continuità audio.', 15)
            budget.require(120)
            r['preflight'] = review_observe(10)
            update(root, r, 'network', 'Verifica rete e lettura delle librerie NAS.', 20)
            import network_test
            budget.require(120)
            network = network_test.audit()
            r['network'] = {'status': network['status'], 'details': network}
            if network['status'] != 'pass' and network.get('nas'):
                raise RuntimeError('Network/NAS observation failed or incomplete: ' + network['status'])
            if not network.get('nas'):
                r['steps'].append({'setting': 'Rete/NAS', 'result': 'Nessuna libreria NAS montata: controllo NAS non applicabile.'})
            current = __import__('buffer_preferences').preferences(text)
            original = (int(current['BUFFERTIME']), int(current['PERIODCOUNT']))
            update(root, r, 'matrix', 'Verifica PCM e DSD con buffer compatibili; la playlist verrà ripristinata.', 25)
            budget.require(360)
            manifest = matrix.plan(generate=True, seconds=55, hardware=h, representative=True)
            from buffer_search import verify as verify_buffer
            r['matrix_total'] = len(manifest['entries'])
            r['matrix_observation_seconds'] = 10
            engine.atomic(root / 'automatic.json', r)
            measured, chosen, reason = verify_buffer(matrix, manifest, original, root, budget)
            r['matrix'] = {'groups': measured['groups'], 'current_first': measured.get('current_first'), 'alternatives_required': measured.get('alternatives_required'), 'buffer_attempts': measured.get('buffer_attempts'), 'cell_counts': {name: sum((x['status'] == name for x in measured['results'])) for name in ['pass-observed-window', 'unsupported', 'unvalidated', 'failed']}, 'observation_seconds_per_cell': 10, 'full_validation': False, 'scope': measured.get('scope'), 'deferred_entries': measured.get('deferred_entries', [])}
            r['steps'].append({'setting': 'Buffer ALSA', 'value': {'ms': chosen[0], 'periods': chosen[1]}, 'result': reason})
            update(root, r, 'search', 'Ricerca ripetuta delle configurazioni CPU, IRQ, priorità ed energia/rete; ogni candidato viene ripristinato.', 75)
            import search, nas_tuning, resources
            __import__('playback_start').start_verified(t['mac'], 60)
            player.wait_audio(t['mac'], 60)
            if budget.allows(320):
                decision = search.run(10, 3, root, True, False, calibration=budget, experimental=r.get('experimental_search', False))
            else:
                decision = {'status': 'finished-restored', 'combined_spec': {'name': 'current', 'stage': 'reference'}, 'completed': 0, 'total': 0, 'selected': [], 'coverage': {'exhaustive': False, 'scope': 'Confronti rinviati: tempo riservato alla verifica finale e al ripristino.'}}
            if decision['status'] != 'finished-restored':
                raise RuntimeError('Measured search did not restore its original configuration')
            selected = decision['combined_spec']
            r['search'] = {'completed': decision['completed'], 'total': decision['total'], 'selected': decision['selected'], 'combined_spec': selected, 'coverage': decision.get('coverage')}
            update(root, r, 'nas', 'Riepilogo rete e risorse; i confronti estesi NAS restano disponibili nella diagnostica.', 82)
            r['nas_tuning'] = {'status': 'deferred', 'reason': 'Ottimizzazione dei montaggi privati disponibile come diagnostica estesa; esclusa dall’autocalibrazione18minuti.'}
            r['resources'] = resources.status(root)
            budget.recovery()
            budget.require(160)
            update(root, r, 'apply', 'Applica la combinazione misurata e il buffer verificato; controllo finale e rollback.', 90)
            __import__('playback_start').start_verified(t['mac'], 60)
            h = player.wait_audio(t['mac'], 60)
            budget.require(140)
            r['application'] = apply_combined(h, chosen, root, selected)
            from continuous import key
            engine.atomic(root / 'automatic-policy.json', {'host_uuid': r['binding']['host_uuid'], 'hardware_id': h['hardware_id'], 'kernel': h['kernel']['release'], 'binding_key': key(h), 'selected': selected, 'buffer': list(chosen), 'status': 'verified', 'persistent': True, 'native_settings_sha256': __import__('policy_restore').native_digest()})
            from power_review import report as power_report
            r['power_inventory'] = power_report(h, application=r['application'])
            r['audio_setup'] = audio_setup.report()
            r['audio_setup']['measurement_context'] = 'Actual playback before original mode restoration'
            player.settle_mode(t['mac'], r['original_playback']['mode'])
            r.update(status='configured', finished=time.time(), optimization_outcome='verified-configuration' if r['application']['changes'] else 'retained-current')
            update(root, r, 'complete', 'Configurazione applicata e verificata. Playlist e modalità di riproduzione ripristinate.', 100)
            r['persistence'] = __import__('policy_restore').start(root)
            return public(r)
        except BaseException as e:
            if isinstance(e, calibration_budget.HardDeadline):
                r['status'] = 'timed-out-recovery-required'
                raise
            budget.recovery()
            update(root, r, 'recovery', 'Prove concluse; recupero dei parametri e della playlist nel tempo riservato.', 95)
            errors = []
            try:
                result = recover_runtime(root, r)
                if result.get('status') not in ['rolled-back', 'no-change']:
                    errors.append('Runtime recovery incomplete')
                if Path(r['target']['path']).read_text() != r['original_preferences']:
                    errors.append('Audio preferences differ from original')
                player.restore_playback(r['original_playback'])
                if r.get('boot_owned'):
                    boot_recovery.restore(root)
            except calibration_budget.HardDeadline:
                r['status'] = 'timed-out-recovery-required'
                raise
            except BaseException as recovery:
                errors.append(str(recovery))
            rejected = root / 'last-rejected-observation.json'
            if isinstance(getattr(e, 'rejected_measurement', None), dict):
                engine.atomic(rejected, e.rejected_measurement)
            if rejected.exists() and rejected.stat().st_mtime >= r['created']:
                r['rejected_observation'] = {'telemetry': engine.load(rejected).get('telemetry', {})}
            r.update(status='recovery-required' if errors else 'failed-restored', error=str(e), recovery_errors=errors, finished=time.time())
            update(root, r, 'error', 'Configurazione non applicata. ' + ('Occorre recuperare la configurazione.' if errors else 'Parametri e playlist originali ripristinati.') + ' ' + str(e), 100)
            return public(r)
        finally:
            if watcher and r.get('status') not in ['recovery-required', 'timed-out-recovery-required']:
                with contextlib.suppress(ProcessLookupError):
                    watcher.terminate()
                watcher.wait(timeout=5)
            if manifest:
                for fixture in manifest.get('generated_fixtures', []):
                    p = Path(fixture['path'])
                    if p.exists():
                        from dsf_fixture import verify_owned
                        verify_owned(p, fixture['sha256'])
                        p.unlink()

def verify_legacy_recovery_owners(root, r):
    host = read('/sys/class/dmi/id/product_uuid')
    current_boot = engine.boot_id()
    if not isinstance(host,str) or not host or r.get('binding',{}).get('host_uuid') != host:
        raise RuntimeError('Legacy recovery host identity differs or is unavailable')
    if not isinstance(current_boot,str) or not current_boot:
        raise RuntimeError('Current boot identity unavailable')
    if not isinstance(r.get('id'),str) or not r['id']:
        raise RuntimeError('Legacy automatic owner identity unavailable')
    if type(r.get('boot_owned',False)) is not bool:
        raise RuntimeError('Exact boot ownership flag required')
    if engine.load(Path(root)/'automatic.json') != r:
        raise RuntimeError('Automatic owner record changed before recovery')
    def stopped(record,pid_key,start_key,label):
        previous_boot = record.get('boot_id')
        if not isinstance(previous_boot,str) or not previous_boot:
            raise RuntimeError(label + ' boot identity unavailable')
        if previous_boot != current_boot:return
        pid, started = record.get(pid_key), record.get(start_key)
        if type(pid) is not int or pid <= 0 or not isinstance(started,str) or not started:
            raise RuntimeError(label + ' process identity unavailable')
        actual = engine.start_ticks(pid)
        if actual == started:
            raise RuntimeError(label + ' owner still running; no recovery started')
        if actual is None and (Path('/proc')/str(pid)).exists():
            raise RuntimeError(label + ' owner process cannot be verified')
    stopped(r,'owner_pid','owner_start','Legacy automatic')
    owner = {'kind':'automatic','id':r['id']}
    activity_path = Path(root)/'activity-session.json'
    if activity_path.exists():
        activity = engine.load(activity_path)
        if activity.get('parent_session_owner') == owner:
            if activity.get('host_uuid') != host or not isinstance(activity.get('id'),str) or not activity['id']:
                raise RuntimeError('Nested activity host or owner identity differs')
            stopped(activity,'owner','start','Nested activity')
        elif activity.get('status') in ['running','recovering','recovery-required']:
            raise RuntimeError('Unrelated activity owns recovery; no automatic recovery started')
    ab_path = Path(root)/'ab-session.json'
    if ab_path.exists() and engine.load(ab_path).get('status') in ['running','recovering','recovery-incomplete']:
        raise RuntimeError('Unrelated A/B activity owns recovery; no automatic recovery started')

def recover_runtime(root, r):
    verify_legacy_recovery_owners(root, r)
    owner = {'kind': 'automatic', 'id': r['id']} if r.get('id') else None
    nested_owner = None
    nested_errors = []
    p = Path(root) / 'activity-session.json'
    if p.exists() and owner:
        activity = engine.load(p)
        if activity.get('status') in ['running', 'recovering', 'recovery-required'] and activity.get('parent_session_owner') == owner:
            import session
            if activity.get('host_uuid') != read('/sys/class/dmi/id/product_uuid'):
                raise RuntimeError('Nested activity host identity differs; no recovery started')
            if session.alive(activity) and activity.get('owner') != os.getpid():
                raise RuntimeError('Nested session owner still running; await recovery')
            nested_owner = {'kind': 'activity', 'id': activity['id']}
            try:session.recover(root, activity)
            except Exception as error:
                nested_errors.append(type(error).__name__ + ': ' + str(error))
    with engine.locked(root) as directory:
        verify_legacy_recovery_owners(root, r)
        selected_owner = owner
        transaction = directory / 'transaction.json'
        if nested_errors and transaction.exists() and engine.load(transaction).get('session_owner') == nested_owner:
            selected_owner = nested_owner
        # Re-read actual controls under the native lock. A failed nested
        # playlist recovery must not skip the parent's owned settings, nor
        # turn a cached child flag into proof of current parameter recovery.
        result = engine.recover_owned_transaction(directory, selected_owner)
    if nested_errors:
        parameters = (result.get('status') in ['rolled-back', 'no-change']
            or (result.get('status') == 'rollback-incomplete'
                and result.get('parameter_restoration_complete') is True))
        return {'status': 'rollback-incomplete', 'parameter_restoration_complete': parameters,
                'nested_recovery_errors': nested_errors, 'bookmark_retained': True}
    return result

def restore(state=engine.STATE):
    root = Path(state)
    r = engine.load(root / 'automatic.json')
    # Owner identity is checked below for every status, before any recovery.
    if not r.get('binding'):
        if r.get('status')=='blocked' and r.get('phase')=='library-scan':return {'status':'no-change','message':'Il tentativo è stato fermato durante la scansione e non ha modificato impostazioni o playlist. La configurazione precedente resta invariata.'}
        raise RuntimeError('Dati della calibrazione incompleti. Nessun ripristino automatico eseguito.')
    if r['binding']['host_uuid'] != read('/sys/class/dmi/id/product_uuid'):
        raise RuntimeError('Restore belongs to another host')
    verify_legacy_recovery_owners(root, r)
    recovery = {'runtime_verified': False, 'preferences_verified': False,
                'playback_restored': False, 'bookmark_retained': True,
                'reboot_performed': False, 'errors': []}
    try:
        result = recover_runtime(root, r)
        recovery['runtime_status'] = result.get('status')
        recovery['runtime_verified'] = (result.get('status') in ['rolled-back', 'no-change']
            or (result.get('status') == 'rollback-incomplete'
                and result.get('parameter_restoration_complete') is True))
        if not recovery['runtime_verified']:recovery['errors'].append('Runtime restoration incomplete')
    except Exception as error:
        recovery['errors'].append('Runtime recovery: ' + type(error).__name__ + ': ' + str(error))
    try:
        recovery['preferences_verified'] = Path(r['target']['path']).read_text() == r['original_preferences']
        if not recovery['preferences_verified']:recovery['errors'].append('Original preferences were not restored')
    except Exception as error:
        recovery['errors'].append('Preferences recovery: ' + type(error).__name__ + ': ' + str(error))
    if r.get('boot_owned'):
        # Ownership refusal is not a recoverable playback failure. Repeat this
        # gate outside the aggregation handler before touching boot settings.
        verify_legacy_recovery_owners(root, r)
        try:
            boot = boot_recovery.restore(root)
            if boot.get('status') != 'disabled-reboot-required' or boot.get('runtime_isolation_unchanged_until_reboot') is not True:
                raise RuntimeError('Boot restoration evidence incomplete')
            recovery['boot_recovery'] = boot
            recovery['explicit_reboot_required'] = True
        except Exception as error:
            recovery['errors'].append('Boot recovery: ' + type(error).__name__ + ': ' + str(error))
    recovery['errors'].append('Legacy playback recovery unavailable; private bookmark retained without replay')
    r.update(status='recovery-required', recovery=recovery)
    update(root, r, 'recovery', 'Esito del recupero dei parametri registrato separatamente. Playlist precedente conservata senza riproduzione; verifica esplicita ancora necessaria.', 100)
    return public(r)

def watchdog(state=engine.STATE):
    root = Path(state)
    first = engine.load(root / 'automatic.json')
    if first.get('kind') == 'automatic-configuration-v2':
        return {'status': 'activity-budget-guard-owned', 'activity_id': first.get('activity_session_id')}
    r = first
    if r.get('status') != 'running' or r.get('boot_id') != engine.boot_id():
        return
    deadline = first.get('calibration_deadline', first['created'] + 1080)
    engine.atomic(root / ('automatic-watch-ready-' + first['id'] + '.json'), {'id': first['id'], 'pid': os.getpid(), 'start': engine.start_ticks(os.getpid())})
    while time.time() < deadline:
        r = engine.load(root / 'automatic.json')
        if r.get('id') != first['id']:
            return
        if r.get('status') == 'recovery-required':
            break
        if r.get('status') != 'running':
            return
        if engine.boot_id() != r.get('boot_id'):
            return
        if engine.start_ticks(r.get('owner_pid')) != r.get('owner_start'):
            break
        if time.time() >= first.get('work_deadline', deadline - 300) and r.get('phase') not in ['recovery', 'apply', 'complete']:
            with contextlib.suppress(ProcessLookupError):
                __import__('process_guard').signal_owner(r, signal.SIGTERM)
        time.sleep(min(0.2, max(0, deadline - time.time())))
    else:
        r = engine.load(root / 'automatic.json')
        if r.get('id') != first['id'] or r.get('status') != 'running' or r.get('boot_id') != engine.boot_id():
            return
        if engine.start_ticks(r.get('owner_pid')) == r.get('owner_start'):
            __import__('process_guard').signal_owner(r, signal.SIGKILL)
    if time.time() >= deadline:
        r.update(status='timed-out-recovery-required', message='Autocalibrazione terminata al limite18minuti. Recupero da verificare.', finished=time.time())
        engine.atomic(root / 'automatic.json', r)
        return
    time.sleep(0.2)
    r.update(status='recovery-required', error='Automatic worker interrupted')
    engine.atomic(root / 'automatic.json', r)
    for attempt in range(20):
        if time.time() >= deadline:
            r.update(status='timed-out-recovery-required', finished=time.time())
            engine.atomic(root / 'automatic.json', r)
            return
        try:
            recovery_budget = calibration_budget.Deadline(calibration_budget.record(first.get('calibration_started', first['created'])))
            recovery_budget.hard = True
            with recovery_budget:
                result = restore(root)
            if result.get('status') == 'restored':
                result.update(status='failed-restored', message='Sessione interrotta; configurazione e playlist recuperate automaticamente.')
                engine.atomic(root / 'automatic.json', dict(r, **{k: v for k, v in result.items() if k not in ['target', 'original_playback', 'original_preferences']}))
            return
        except BlockingIOError:
            time.sleep(1)
        except calibration_budget.HardDeadline:
            r.update(status='timed-out-recovery-required', finished=time.time())
            engine.atomic(root / 'automatic.json', r)
            return
        except BaseException as e:
            r.update(status='recovery-required', recovery_errors=[str(e)])
            engine.atomic(root / 'automatic.json', r)
            return

def startup(state=engine.STATE):
    root = Path(state)
    p = root / 'automatic.json'
    if not p.exists():
        return {'status': 'inactive'}
    r = engine.load(p)
    if not r.get('binding'):
        if r.get('status')=='blocked' and r.get('phase')=='library-scan':return public(r)
        return {'status':'recovery-required','message':'Identità della calibrazione non disponibile. Nessuna impostazione riapplicata.'}
    if r['binding']['host_uuid'] != read('/sys/class/dmi/id/product_uuid'):
        return {'status': 'different-host-no-replay'}
    if r.get('status') == 'restoring-reboot':
        r['status'] = 'restored'
        update(root, r, 'complete', 'Configurazione precedente ripristinata dopo il riavvio.', 100)
        return public(r)
    if r.get('status') == 'waiting-reboot' and time.time() >= r.get('calibration_deadline', r.get('created', time.time()) + 1080):
        r.update(status='timed-out-recovery-required', finished=time.time())
        update(root, r, 'error', 'Tempo18minuti esaurito durante il riavvio; nessuna nuova prova avviata. Recuperare la configurazione.', 100)
        return public(r)
    if r.get('status') == 'waiting-reboot' and r.get('boot_id') != engine.boot_id():
        if boot_recovery.status(root).get('status') != 'healthy-boot':
            r['status'] = 'blocked'
            update(root, r, 'error', 'Avvio non validato: configurazione automatica sospesa.', 100)
            return public(r)
        log = (root / 'automatic-resume.log').open('ab')
        p = subprocess.Popen([sys.executable, str(Path(__file__).parent.parent / 'bin/dat'), '--state', str(root), 'automatic', 'resume', '--confirm-audio-test'], stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
        log.close()
        return {'status': 'resuming', 'pid': p.pid}
    if r.get('status') == 'running' and engine.start_ticks(r.get('owner_pid')) != r.get('owner_start'):
        r['status'] = 'recovery-required'
        update(root, r, 'error', 'Sessione interrotta. Usa Ripristina per verificare e recuperare lo stato originale.', 100)
    return public(r)

def restore_good(saved, state=engine.STATE):
    raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
    if saved.get('status') != 'committed' or saved.get('profile') != 'automatic':
        raise RuntimeError('No verified automatic configuration')
    h = scan()
    if h['hardware_id'] != saved['hardware_id']:
        raise RuntimeError('Automatic last-known-good belongs to other hardware')
    changed = [c for c in saved.get('changes', []) if c['parameter'] == 'player_buffer']
    t = player.target(h)
    values = __import__('buffer_preferences').preferences(changed[0]['after'] if changed else Path(t['path']).read_text())
    selected = saved.get('selected_policy')
    if any(c['parameter'] != 'player_buffer' for c in saved.get('changes', [])) and selected is None:
        raise RuntimeError('Legacy configuration has no measured abstract policy; recalibrate instead of partial restoration')
    return apply_combined(h, (int(values['BUFFERTIME']), int(values['PERIODCOUNT'])), Path(state), selected)

def review_observe(seconds):
    import measure
    from measurement_contract import optimization_issues
    result = measure.observe(seconds)
    issues = optimization_issues(result)
    if issues:
        error = RuntimeError('Instrumented observation incomplete: ' + '; '.join(issues))
        error.rejected_measurement = result
        raise error
    return result

_startup_recovery = startup
def startup(state=engine.STATE):
    result = _startup_recovery(state)
    if result.get('status') == 'configured':
        from policy_restore import start
        result['policy_restore'] = start(state)
    return result

_legacy_bounded_run_v2 = bounded_run
_legacy_status_v2 = status
_legacy_startup_v2 = startup
_legacy_restore_v2 = restore
_legacy_restore_good_v2 = restore_good

def _is_v2(state):
    path=Path(state)/'automatic.json'
    return path.exists() and engine.load(path).get('kind')=='automatic-configuration-v2'

def bounded_run(state,confirmed,resuming,experimental,budget,*,prepare=False):
    from autotune_v2.workflow import run as run_v2
    return run_v2(state,confirmed,resuming,experimental,budget,prepare=prepare)

def status(state=engine.STATE):
    if _is_v2(state):
        from autotune_v2.workflow import status as status_v2
        return status_v2(state)
    return _legacy_status_v2(state)

def startup(state=engine.STATE):
    if _is_v2(state):
        from autotune_v2.workflow import startup as startup_v2
        return startup_v2(state)
    if not (Path(state)/'automatic.json').exists():return {'status':'inactive'}
    return {'status':'legacy-recovery-required','message':'La configurazione precedente richiede una verifica esplicita; nessuna riproduzione viene riavviata.'}

def restore(state=engine.STATE):
    if _is_v2(state):
        from autotune_v2.workflow import restore as restore_v2
        return restore_v2(state)
    return _legacy_restore_v2(state)

def restore_good(saved,state=engine.STATE):
    if saved.get('workflow_v2'):
        raise RuntimeError('Il risultato V2 richiede una nuova verifica sul sistema attuale; nessuna vecchia playlist o policy viene riapplicata.')
    return _legacy_restore_good_v2(saved,state)

def analyze(state=engine.STATE):
    from autotune_v2.workflow import analyze as analyze_v2
    return analyze_v2(state)

__release_signature__ = 'Tino Zappala'
