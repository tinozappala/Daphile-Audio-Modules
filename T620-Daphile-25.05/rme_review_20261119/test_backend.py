"""Qualifica il delta RME contro catture locali, senza accesso ALSA o al t620."""

from contextlib import closing, nullcontext
from copy import deepcopy
from http.client import HTTPConnection
from importlib import import_module, util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from types import ModuleType
import unittest
from unittest.mock import patch


REVIEW = Path(__file__).resolve().parent
ROOT = REVIEW.parent
BASE = ROOT / 'iso_rc7_20261118/github_release/DaphileRME/backend'
CANDIDATE = REVIEW / 'DaphileRME/backend'
CAPTURE = ROOT / 'reports/rme_extension_readonly.json'


def load(name, path):
    spec = util.spec_from_file_location(name, path)
    module = util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PresetBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = ModuleType('engine')
        engine.STATE = Path('unused')
        engine.atomic = engine.load = engine.locked = engine.start_ticks = cls.unexpected_write
        imports = {'engine': engine}
        if sys.platform == 'win32':
            imports['fcntl'] = ModuleType('fcntl')
        cls.enterClassContext(patch.dict(sys.modules, imports))
        cls.enterClassContext(patch.object(sys, 'path', [str(CANDIDATE/'rme/lib'), str(BASE), str(BASE/'rme/lib'), *sys.path]))
        cls.rme = import_module('rme')
        cls.peq = import_module('rme_peq')
        cls.memory = load('rme_memory', CANDIDATE / 'rme/lib/rme_memory.py')
        cls.baseline = load('baseline_rme_control', BASE / 'rme/lib/rme_control.py')
        cls.control = load('rme_control', CANDIDATE / 'rme/lib/rme_control.py')
        cls.server_module = load('candidate_rme_server', CANDIDATE / 'rme_server.py')
        cls.capture = json.loads(CAPTURE.read_text(encoding='utf-8'))

    @staticmethod
    def unexpected_write(*args):
        raise AssertionError('La prova di sola lettura non deve aprire un journal o inviare SET')

    def decode(self, frames):
        reply = self.rme.decode(b''.join(bytes.fromhex(frame) for frame in frames))
        reply.update(status='read-response-received', usb_serial='fixture')
        reply['current_eq'] = self.rme.eq_summary(reply['parameters'])
        return reply

    def setUp(self):
        self.state_dir = Path(self.enterContext(TemporaryDirectory()))
        def write_names(path, value):
            self.assertEqual(path, self.state_dir/'rme-memory-names.json')
            path.write_text(json.dumps(value),encoding='utf-8')
        self.enterContext(patch.object(self.memory,'atomic',write_names))
        self.enterContext(patch.object(self.memory,'load',lambda path: json.loads(path.read_bytes())))
        self.current = self.decode(self.capture['frames'])
        self.bank_reply = self.decode(self.capture['preset_captures'][0]['frames'])
        self.gets = []
        self.enterContext(patch.object(self.rme, 'query', self.query))
        for control in (self.baseline, self.control):
            self.enterContext(patch.object(control, 'operation', lambda state, recovery=False: nullcontext(state)))
            self.enterContext(patch.object(control, 'send', self.unexpected_write))
            self.enterContext(patch.object(control, 'spawn', self.unexpected_write))

    def query(self, request=None, **kwargs):
        self.gets.append(request)
        if request in (None, self.rme.REQUEST_ALL):
            return deepcopy(self.current)
        self.assertEqual(request, bytes.fromhex('F0 00 20 0D 71 03 0A F7'))
        return deepcopy(self.bank_reply)

    def parameter(self, slot):
        return {'output': 'line', 'expected': self.peq.revision(self.current), 'slot': slot}

    def change_registers(self, overrides):
        for param in self.current['parameters']:
            key = f"{param['address']}:{param['index']}"
            if key in overrides:
                raw = overrides[key]
                param.update(raw=raw, signed=raw - 4096 if raw & 2048 else raw,
                             frequency_mantissa=raw & 2047, frequency_factor=10 if raw & 2048 else 1)
        self.current['current_eq'] = self.rme.eq_summary(self.current['parameters'])

    def test_eq_off_verifies_tone_off_only_when_preset_policy_couples_them(self):
        for policy in (0, 1):
            with self.subTest(load_tones_with_preset=policy):
                requested = {'4:2': 0, '5:2': 0}
                self.change_registers({'4:2': 1, '5:2': 1, '4:20': 1, '4:27': policy})
                before = deepcopy(self.current)
                self.change_registers({**requested, '4:20': 0})
                if policy:
                    self.assertEqual(self.control.dependencies(before, self.current, requested), {'4:20': 0})
                    self.control.check(before, self.current, requested)
                else:
                    with self.assertRaisesRegex(RuntimeError, '4:20'):
                        self.control.check(before, self.current, requested)
        self.change_registers({'7:20': 1 - self.control.values(self.current)['7:20']})
        with self.assertRaisesRegex(RuntimeError, '7:20'):
            self.control.check(before, self.current, requested)

    def test_eq_on_verifies_tone_on_when_an_unchanged_hardware_preset_couples_them(self):
        requested = {'4:2': 1, '5:2': 1}
        for selector in (0, 2, 22):
            with self.subTest(selector=selector):
                self.change_registers({'4:2': 0, '5:2': 0, '4:20': 0, '4:27': 1, '4:28': selector})
                before = deepcopy(self.current)
                self.change_registers({**requested, '4:20': 1})
                if selector in (0,2):
                    self.assertEqual(self.control.dependencies(before, self.current, requested), {'4:20': 1})
                    self.control.check(before, self.current, requested)
                else:
                    with self.assertRaisesRegex(RuntimeError, '4:20'):
                        self.control.check(before, self.current, requested)

    def test_manual_recovery_can_continue_after_request_finishes_in_live_worker(self):
        path = self.state_dir / 'transaction.json'
        path.touch()
        journal = {'status': 'rollback-incomplete', 'request_finished': True}
        with patch.object(self.control, 'load', return_value=journal), \
             patch.object(self.peq, 'parent_alive', return_value=True), \
             patch.object(self.control, 'rollback', return_value=self.current) as rollback:
            self.assertEqual(self.control.recover(self.state_dir)['status'], 'recovered-verified')
            rollback.assert_called_once_with(self.state_dir, journal)
            journal.pop('request_finished')
            with self.assertRaisesRegex(RuntimeError, 'ancora in corso'):
                self.control.recover(self.state_dir)

    def test_same_selector_checks_entire_preset_before_accepting_noop(self):
        self.change_registers({'4:28': 2})
        _, expected, _ = self.baseline.preset_plan(self.current, self.parameter(1))
        self.assertTrue(any(self.baseline.values(self.current)[key] != value for key, value in expected.items()))
        old_reply = self.baseline.apply(self.state_dir, self.parameter(1), 'preset')
        self.assertEqual((old_reply['status'], old_reply['readback_verified']), ('no-change', True))
        with self.assertRaisesRegex(RuntimeError, 'non coincidono'):
            self.control.apply(self.state_dir, self.parameter(1), 'preset')

        self.change_registers(expected)
        result = self.control.apply(self.state_dir, self.parameter(1), 'preset')
        self.assertEqual((result['status'], result['readback_verified']), ('no-change', True))

        self.change_registers({'4:28': 0, '4:27': 1})
        _, with_tones, _ = self.control.preset_plan(self.current, self.parameter(1))
        self.assertNotIn('4:20', with_tones)
        self.change_registers({**with_tones, '4:2': 0, '5:2': 0, '4:20': 0})
        result = self.control.apply(self.state_dir, self.parameter(1), 'preset')
        self.assertEqual((result['status'], result['readback_verified']), ('no-change', True))
        self.change_registers({'4:2': 1, '5:2': 1})
        result = self.control.apply(self.state_dir, self.parameter(1), 'preset')
        self.assertEqual((result['status'], result['readback_verified']), ('no-change', True))

        self.change_registers({'4:28': 0})
        _, _, commands = self.control.preset_plan(self.current, self.parameter(1))
        self.assertEqual(set(commands), {'4:28'})
        result = self.control.apply(self.state_dir, self.parameter('manual'), 'preset')
        self.assertEqual(result['status'], 'no-change')

    def test_hardware_preset_allows_editing_current_sound(self):
        self.change_registers({'4:28': 2, '4:2': 1, '5:2': 1, '4:20': 1, '4:27': 1})
        context = {'chip':'unknown'}
        capabilities = self.control.registry.view(self.current, context)['capabilities']['outputs']['line']
        self.assertTrue(capabilities['tone_enabled']['writable'])
        for key in ('bass_gain', 'treble_gain', 'load_tones_with_preset', 'dual_eq'):
            self.assertTrue(capabilities[key]['writable'])
        patch = {'output':'line', 'expected':self.peq.revision(self.current), 'values':{'tone_enabled':False}}
        _, desired, commands = self.control.settings_plan(self.current, patch, context)
        self.assertEqual(desired, {'4:20':0})
        self.assertEqual(set(commands), {'4:20'})

    def test_edit_bank_accepts_only_local_temp_and_keeps_undo_order(self):
        self.change_registers({'4:28':2, '3:11':0})
        before=deepcopy(self.current)
        row=deepcopy(before['current_eq']['line']['left'][1])
        row['gain']=0.5 if row['gain']!=0.5 else 1
        patch={'output':'line','expected':self.peq.revision(before),'side':'stereo','bands':[row]}
        _,desired,commands=self.control.eq_plan(before,patch)
        self.assertNotIn('4:28',commands)
        self.change_registers({**desired,'4:28':1})
        self.control.check(before,self.current,desired)
        self.assertEqual(self.control.dependencies(before,self.current,desired),{'4:28':1})
        original=self.control.values(before)
        undo={loc:original[loc] for loc in [*commands,'4:28','4:20']}
        ordered=[loc for loc,_ in self.control.ordered_commands(self.current,undo,undo)]
        self.assertEqual(ordered[-2:],['4:28','4:20'])
        self.change_registers({'7:28':3})
        with self.assertRaisesRegex(RuntimeError,'7:28'):
            self.control.check(before,self.current,desired)

    def test_http_archives_slots_and_keeps_native_routes(self):
        profile = {'output': 'line', 'bands': self.current['current_eq']['line']['left'],
                   'enabled': False, 'audio': {}}
        saved = {'revision': 9, 'slots': [{'name': 'Profilo originale', 'profile': profile}, None, None, None]}
        archive = self.state_dir / 'preset-slots.json'
        original_bytes = json.dumps(saved, indent=2).encode('utf-8')
        archive.write_bytes(original_bytes)
        token = 'fixture-session'
        token_path = self.state_dir / 'access.token'
        token_path.write_text(token, encoding='ascii')
        token_path.chmod(0o600)
        if sys.platform == 'win32':
            # The controller uses Linux-only token-file permission primitives.
            self.enterContext(patch.object(self.server_module, 'private_token', return_value=token))
        server = self.server_module.make_server(CANDIDATE, self.state_dir, 0)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05})
        self.addCleanup(server.server_close)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

        def request(path, payload=None, cookie=None):
            headers = {} if cookie is None else {'Cookie': cookie}
            body = None if payload is None else json.dumps(payload)
            if body is not None:
                headers['Content-Type'] = 'application/json'
            with closing(HTTPConnection('127.0.0.1', server.server_port, timeout=3)) as connection:
                connection.request('GET' if body is None else 'POST', path, body=body, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read()), response.getheader('Set-Cookie')

        status, session, cookie = request('/api/session')
        self.assertEqual((status, session['authenticated']), (200, True))
        self.assertIsNotNone(cookie)
        cookie = cookie.split(';', 1)[0]
        self.assertEqual(request('/api/slots')[0], 401)
        self.assertEqual(request('/api/slots', cookie=cookie)[:2], (200, saved))
        removed = {
            '/api/slots/save': {'revision': 9, 'slot': 1, 'name': 'Sostituzione', 'profile': profile},
            '/api/slots/delete': {'revision': 9, 'slot': 1},
            '/api/rme/backup': None, '/api/rme/backup/preview': {'text': '{}'}, '/app.js': None,
        }
        for path, payload in removed.items():
            with self.subTest(path=path):
                self.assertEqual(request(path, payload, cookie)[0], 404)
        self.assertEqual(archive.read_bytes(), original_bytes)

        self.assertEqual(self.gets, [])

        status, current, _ = request('/api/rme/snapshot', {}, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(current['settings_write_commands_sent'], 0)
        self.assertEqual(request('/api/rme/memory', cookie=cookie)[0], 200)
        status, bank, _ = request('/api/rme/memory/read', {'slot': 1}, cookie)
        self.assertEqual((status, bank['slot'], bank['read_only']), (200, 1, True))
        status, snapshot, _ = request('/api/rme/snapshot', {}, cookie)
        self.assertIn({'identifier':1,'name':bank['name']},snapshot['hardware_names'])
        status, result, _ = request('/api/rme/preset/apply', {'confirm': True, 'patch': self.parameter('manual')}, cookie)
        self.assertEqual((status, result['status'], result['readback_verified']), (200, 'no-change', True))

        self.change_registers({'4:28': 2})
        status, error, _ = request('/api/rme/preset/apply', {'confirm': True, 'patch': self.parameter(1)}, cookie)
        self.assertEqual(status, 503)
        self.assertIn('non coincidono', error['error'])
        self.assertEqual(archive.read_bytes(), original_bytes)


    def test_store_qualifies_readback_and_refuses_a_changed_destination(self):
        self.change_registers({'4:25': 2844})
        empty = {'slot':20, 'fingerprint':'f'*64, 'empty':True, 'hardware_name':None}
        payload, dual = self.memory.projection(self.current,'line')
        self.assertEqual(payload[13,25], 2838)
        stored = {'slot':20,'empty':False,'dual_eq':dual,'hardware_name':'Ascolto sera',
                  'raw_parameters':{f'{a}:{i}':raw for (a,i),raw in payload.items()}}
        params = {**self.parameter(20), 'replace':False, 'fingerprint':'f'*64, 'name':'Ascolto sera'}
        with patch.object(self.memory,'read',side_effect=[empty,stored]), patch.object(self.memory,'write_bank') as send:
            result = self.memory.store(self.state_dir,params)
            self.assertEqual((result['status'],result['readback_verified']),('stored-verified',True))
            send.assert_called_once_with(20,payload,dual,'fixture','Ascolto sera')
        with patch.object(self.memory,'read',return_value=empty), patch.object(self.memory,'write_bank') as send:
            with self.assertRaisesRegex(RuntimeError,'memoria è cambiata'):
                self.memory.store(self.state_dir,{**params,'fingerprint':'e'*64})
            send.assert_not_called()

    def test_recall_waits_for_every_setting_without_waiting_for_periodic_status(self):
        receive = load('candidate_rme_receive', CANDIDATE/'rme/lib/rme_receive.py')

        def decode(data):
            return {
                'parse_issues': [],
                'parameters': [{'address':4,'index':i,'raw':i} for i, marker in enumerate(b'AB') if marker in data],
                'hardware_names': [{'identifier':33,'name':'Setup'}] if b'B' in data else [],
                'status_messages': [{'protocol_revision':2}] if b'S' in data else [],
            }

        def sample(configuration_only, expected, include_second_packet=True):
            now = 0
            chunks = [(0.24,b'A'),(0.51,b'B'),(1.12,b'S')]
            if not include_second_packet:
                chunks.pop(1)

            def sleep(seconds):
                nonlocal now
                now += seconds

            def read():
                return chunks.pop(0)[1] if chunks and chunks[0][0]<=now else b''

            return receive.collect(read,decode,expected,clock=lambda:now,sleep=sleep,
                                   configuration_only=configuration_only)

        expected, _ = receive.profile(decode(b'ABS'))
        data, inventory, metrics = sample(True,expected)
        self.assertEqual(data,b'AB')
        self.assertEqual(inventory[:2],expected[:2])
        self.assertEqual(metrics['completion'],'configuration-quiescence')
        self.assertGreater(metrics['receive_seconds'],0.51)
        standard = sample(False,expected)
        self.assertEqual(standard[0],b'ABS')
        self.assertGreater(standard[2]['receive_seconds'],1.12)
        self.assertEqual(sample(True,expected,False)[2]['completion'],'full-window')
        self.assertEqual(sample(True,None)[2]['completion'],'full-window')


if __name__ == '__main__':
    unittest.main(verbosity=2)
