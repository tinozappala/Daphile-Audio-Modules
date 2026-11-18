"""Apply RAM ON/OFF to the current native stream without restarting LMS/OS."""
import os
from pathlib import Path
import threading
import time


class LiveSwitch:
    def __init__(self, manager, load, atomic, rpc, native_process):
        self.m, self.load, self.atomic = manager, load, atomic
        self.rpc, self.native_process = rpc, native_process
        self.path = manager.run / 'live-switch.json'
        self.flag = manager.run / 'live-switching'
        self.required = manager.run / 'continuous-required'

    def status(self):
        value = self.load(self.path, {})
        if value.get('boot') != self.m.boot:
            return dict(busy=False, phase='IDLE', error=None)
        return {k: value[k] for k in ['busy', 'phase', 'error', 'operation_id', 'requested_ram', 'user_state_changed'] if k in value}

    def record(self, phase, *, busy=True, error=None, **extra):
        old = self.load(self.path, {})
        value = dict(old, boot=self.m.boot, busy=busy, phase=phase, error=error, **extra)
        self.atomic(self.path, value)

    def start(self, value, operation_fd):
        state = self.status()
        if state.get('operation_id') == value['request_id'] and state.get('phase')!='FAILED':
            return False
        self.record('QUEUED', operation_id=value['request_id'], requested_ram=value['requested_ram'])
        threading.Thread(target=self.run, args=(value, operation_fd), daemon=True).start()
        return True

    def reconcile_startup(self):
        value = self.load(self.path, {})
        if value.get('boot') == self.m.boot and value.get('busy'):
            self.record('FAILED', busy=False, error='RAM_SWITCH_INTERRUPTED')
        self.flag.unlink(missing_ok=True)

    def require_unchanged(self, expected, actual, check_time=False):
        if not self.same_selection(expected, actual) or expected['mode'] != actual['mode']:
            raise RuntimeError('RAM_USER_STATE_CHANGED')
        if check_time and abs(float(expected['time'])-float(actual['time'])) > .15:
            raise RuntimeError('RAM_USER_STATE_CHANGED')

    def wait(self, predicate, deadline, error):
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(.1)
        raise RuntimeError(error)

    def current(self, player, deadline):
        return self.m.native_queue(player, deadline)

    @staticmethod
    def same_selection(before, after):
        return before['urls'] == after['urls'] and before.get('index') == after.get('index')

    def observed(self, player, deadline):
        return self.rpc(self.m.cfg, player, ['status', '-', '1', 'tags:u'], deadline)

    def pause_and_close(self, player, before, deadline):
        if before['mode'] == 'play':
            raise RuntimeError('RAM_PLAYER_PLAYING_MUST_PAUSE')
        paused_queue = before
        if paused_queue['mode'] == 'pause' and paused_queue['urls']:
            # Native _JumpPaused retains the cursor and closes the old source
            # stream. It does not play/select/rebuild the queue or change gain.
            self.record('CLOSING_PREVIOUS_STREAM')
            self.require_unchanged(paused_queue, self.current(player, deadline), check_time=True)
            self.m.client.call(player, ['time', paused_queue['time']], deadline)
            def closed():
                actual=self.current(player,deadline)
                self.require_unchanged(paused_queue,actual,check_time=True)
                return True
            self.wait(closed, deadline, 'RAM_PAUSED_CURSOR_NOT_OBSERVED')
        return paused_queue

    def preserved(self, player, paused, deadline):
        current = self.current(player, deadline)
        try:self.require_unchanged(paused,current,check_time=paused['mode']=='pause')
        except RuntimeError:return False
        return True

    def run(self, value, operation_fd):
        token = 'live-' + value['request_id']
        identity = None
        deadline = time.monotonic() + 120
        rolling = self.m.rolling
        selected = None
        try:
            with self.m.queue_locked(blocking=False):
                self.record('READING_PLAYER_STATE')
                players = self.rpc(self.m.cfg, '', ['players', '0', '100'], deadline).get('players_loop', [])
                connected = [p for p in players if p.get('connected') in (1, '1', True)]
                queues = [(p['playerid'], self.current(p['playerid'], deadline)) for p in connected]
                # PLAY includes native buffering/trackwait. Refuse before
                # persisting intent, seeking, suspending copying or mounting.
                if any(q['mode']=='play'for _,q in queues):
                    raise RuntimeError('RAM_PLAYER_PLAYING_MUST_PAUSE')
                active = [(p, q) for p, q in queues if q['urls'] and q['mode'] in ('play', 'pause')]
                if len(active) > 1:
                    raise RuntimeError('RAM_MULTIPLE_ACTIVE_PLAYERS')
                selected = active[0] if active else queues[0] if len(queues) == 1 else None
                if selected:
                    player, before = selected
                    if before['mode']=='pause' and before['urls'] and self.observed(player,deadline).get('can_seek')not in(1,'1',True):
                        raise RuntimeError('RAM_PAUSE_CANNOT_RELEASE')
                    prior=self.load(self.m.state/'live-switches'/(value['request_id']+'.json'),{})
                    if prior.get('boot')==self.m.boot and prior.get('paused'):
                        # A repeated request after controller restart may use
                        # the old play intent only if our paused state remains.
                        self.require_unchanged(prior['paused'],before,check_time=True)
                        # PAUSE/STOP are user choices. Never restore an older
                        # PLAY intent from a superseded implementation.
                    self.atomic(self.m.state / 'live-switches' / (value['request_id'] + '.json'),
                                dict(boot=self.m.boot, player_id=player, queue=before, requested_ram=value['requested_ram']))
                # Close the old source under its current mode. Enabling the
                # new gate before native pause can intercept its own teardown.
                paused = self.pause_and_close(*selected, deadline) if selected else None
                if selected:
                    path=self.m.state/'live-switches'/(value['request_id']+'.json')
                    self.atomic(path,dict(self.load(path),paused=paused))
                self.m.set(value)
                self.flag.write_text('Switch in progress\n');os.chmod(self.flag, 0o644)
                rolling.suspended = True
                # Native future opens must await this operation. On ON, put
                # the required marker up before invalidating the old stream.
                if value['requested_ram']:
                    self.required.write_text('Original-byte RAM streaming required\n');os.chmod(self.required, 0o644)
                self.record('WAITING_FOR_COPY_WORKER')
                if not rolling.io_lock.acquire(timeout=max(.1, deadline - time.monotonic())):
                    raise RuntimeError('RAM_COPY_WORKER_BUSY')
                try:
                    # All old native Song leases are released after the
                    # observed paused seek/stop. Do not discard live leases.
                    self.record('WAITING_FOR_STREAM_RELEASE')
                    def no_pins():
                        with rolling.lock:return not rolling.pins
                    self.wait(no_pins, deadline, 'RAM_STREAM_STILL_IN_USE')
                    self.record('PREPARING_RAM' if value['requested_ram'] else 'RESTORING_ORIGINAL_PATHS')
                    self.m.prepare()
                finally:
                    rolling.io_lock.release()
                if not value['requested_ram']:
                    self.required.unlink(missing_ok=True)
                    self.flag.unlink(missing_ok=True)
                rolling.suspended = False;rolling.wake.set()
                if value['requested_ram'] and selected and paused['urls']:
                    with self.native_process(self.m.cfg) as native:
                        identity = dict(pid=native['pid'], birth=native['birth'])
                    url = paused['urls'][int(paused['index'])]
                    self.record('PRELOADING_CURRENT_TRACK')
                    def cached():
                        result = rolling.request(dict(token=token, url=url), identity)
                        if result.get('state') == 'ERROR':
                            raise RuntimeError(result['error'])
                        return result if result.get('state') == 'READY' else None
                    self.wait(cached, deadline, 'RAM_PRELOAD_TIMEOUT')
                self.flag.unlink(missing_ok=True)
                preserved = self.preserved(selected[0], paused, deadline) if selected else True
                self.m.complete()
                self.record('COMPLETE', busy=False, user_state_changed=not preserved)
        except Exception as error:
            code = str(error) if type(error) is RuntimeError else 'RAM_LIVE_SWITCH_FAILED'
            self.record('FAILED', busy=False, error=code)
        finally:
            self.flag.unlink(missing_ok=True)
            rolling.suspended = False;rolling.wake.set()
            if identity is not None:
                try:rolling.release(dict(token=token), identity)
                finally:os.close(operation_fd)
            else:os.close(operation_fd)
