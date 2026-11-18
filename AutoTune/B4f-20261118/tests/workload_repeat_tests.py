"""Fixture regression checks for repeat-current AutoTune playback.

Run: python -B workload_repeat_tests.py [--lib PATH_TO_WORKING_LIB]
The actual workload/quiet-recovery/adapter code runs against a fake LMS. No
native service, audio device or network endpoint is contacted. Wave writing
is reduced to a tiny deterministic payload; audio decoding is not tested.
"""
import argparse
import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

sys.dont_write_bytecode = True


class Interrupted(BaseException):
    """Model a process disappearing after a durable write or applied command."""


class TinyWave:
    def __init__(self, stream, mode):
        self.stream = stream

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def setparams(self, params):
        pass

    def writeframesraw(self, data):
        self.stream.write(data[:8])


class FixtureOS:
    """Allow semantic fixture checks on Windows; native DAC checks stay Linux."""
    def __getattr__(self, name):
        if os.name != 'posix':
            if name in ('O_NOFOLLOW', 'O_CLOEXEC'):
                return 0
            if name == 'fchmod':
                return lambda fd, mode: None
            if name == 'geteuid':
                return lambda: 0
        return getattr(os, name)


class FakeLMS:
    def __init__(self, original, dstm=True):
        self.state = copy.deepcopy(original)
        self.dstm = dstm
        self.dstm_appends = 0
        self.fixture_cycles = 0
        self.commands = []
        self.after_command = None

    @staticmethod
    def playlist_key(url):
        return url

    @staticmethod
    def lms_item(url):
        return url

    @staticmethod
    def catalog_ids(urls):
        return [None] * len(urls)

    @staticmethod
    def may_reference_fixture(url, path, identity=None):
        return url == Path(path).resolve().as_uri()

    def playback_snapshot(self, mac):
        assert mac == self.state['mac']
        return copy.deepcopy(self.state)

    def wait_audio(self, mac, timeout, expected_rate):
        assert self.state['mode'] == 'play'

    def _dstm_playlist_change(self):
        # Regression oracle: a provider can extend the last selected source
        # when repeat is disabled. Repeat-current suppresses this provider.
        if (self.dstm and self.state['repeat'] == 0 and self.state['urls']
                and self.state['index'] == len(self.state['urls']) - 1):
            self.dstm_appends += 1
            self.state['urls'].append('randomplay://album?fixture-test=' + str(self.dstm_appends))

    def source_finished(self):
        if self.state['repeat'] == 1:
            self.fixture_cycles += 1
            self.state['time'] = 0
            return
        self._dstm_playlist_change()
        self.state['mode'] = 'stop'
        self.state['time'] = 0

    def rpc(self, mac, command):
        command = list(command)
        if command and command[0]=='can' and command[-1]=='?':
            return {'_can':int(tuple(command[1:-1]) in {
                ('playlist','index'),('playlist','delete'),('playlist','add'),('playerpref',),('sync',),('stop',)})}
        if command == ['serverstatus', '0', '1']:
            return {'version': '9.1.1'}
        if command == ['connected', '?']:
            return {'_connected': 1}
        if command == ['sync', '?']:
            return {'_sync': '-'}
        if command == ['playerpref', 'syncgroupid', '?']:
            return {'_p2': None}
        if command == ['pref', 'maxPlaylistLength', '?']:
            return {'_p2': 0}
        if command == ['mixer', 'volume', '?']:
            return {'_volume': self.state['volume']}
        if command == ['mixer', 'muting', '?']:
            return {'_muting': self.state['mute']}
        self.commands.append(command)
        if command[:2] == ['playlist', 'repeat']:
            self.state['repeat'] = int(command[2])
        elif command[:2] == ['playlist', 'shuffle']:
            self.state['shuffle'] = int(command[2])
        elif command[:2] == ['playerpref', 'shuffle']:
            self.state['shuffle'] = int(command[2])
        elif command[:2] == ['playlist', 'add']:
            item = str(command[2])
            self.state['urls'].append(item if '://' in item else Path(item).resolve().as_uri())
            self._dstm_playlist_change()
        elif command[:2] == ['playlist', 'index']:
            self.state['index'] = int(command[2])
            self.state['time'] = 0
            self._dstm_playlist_change()
        elif command[:2] == ['playlist', 'delete']:
            del self.state['urls'][int(command[2])]
            self.state['index'] = min(self.state['index'], max(0, len(self.state['urls']) - 1))
        elif command == ['stop']:
            self.state['mode'] = 'stop'
            self.state['time'] = 0
        elif command == ['play']:
            self.state['mode'] = 'play'
            self._dstm_playlist_change()
        elif command == ['pause', 1]:
            self.state['mode'] = 'pause'
        elif command[:2] == ['playlist', 'clear']:
            raise AssertionError('Restoration must remove only the owned suffix; queue clear is forbidden')
        else:
            raise AssertionError('Unexpected fake LMS mutation: ' + repr(command))
        if self.after_command is not None:
            self.after_command(command)
        return {}


class Case:
    def __init__(self, root, workload_module, quiet_module, dstm=True):
        self.cw, self.quiet = workload_module, quiet_module
        self.root = root / 'state'
        self.folder = root / 'samples'
        self.root.mkdir(mode=0o700)
        self.folder.mkdir(mode=0o700)
        self.original = dict(mac='aa:bb:cc:dd:ee:ff',
            urls=['file:///fixture-library/track-%02d.flac' % (i % 11) for i in range(17)],
            index=2, mode='stop', time=0.0, repeat=2, shuffle=0, volume=100, mute=0, power=1)
        self.player = FakeLMS(self.original, dstm)
        self.quiet.player = self.player
        self.after_atomic = None
        self.target = {'mac': self.original['mac'], 'path': str(root / 'volume.conf')}
        Path(self.target['path']).write_text('fixture-preferences\n')
        self.owner = dict(id='a' * 32, boot_id='fixture-boot', playback=copy.deepcopy(self.original))
        def atomic(path, value):
            # Preserve real serialization/copy semantics rather than retaining
            # a reference to the mutable in-memory record.
            Path(path).write_text(json.dumps(value))
            if self.after_atomic is not None:
                self.after_atomic(copy.deepcopy(value))
        def start_verified(mac, timeout):
            self.player.rpc(mac, ['play'])
        self.api = types.SimpleNamespace(boot_id=lambda: self.owner['boot_id'], atomic=atomic,
            player=self.player, playback=lambda mac: dict(volume=self.player.state['volume'], mute=self.player.state['mute']),
            verify_restore=self.quiet.qualified_lms,
            verify_target=lambda target: None if target == self.target else (_ for _ in ()).throw(AssertionError('Wrong target')),
            start_verified=start_verified, restore_inactive=self.quiet.restore_inactive)
        self.work = self.cw.Workload(self.root,
            dict(id=self.owner['id'], original_playback=copy.deepcopy(self.original)), self.api, self.folder)
        with patch.object(self.cw.wave, 'open', TinyWave):
            self.work.create(self.target, rate=44100)

    def select(self):
        self.work.select()

    def recover(self):
        self.after_atomic = None
        self.player.after_command = None
        if os.name == 'posix':
            return self.cw.recover_session(self.root, self.owner, self.api, self.folder)
        # Windows lacks the Linux inode/DAC semantics of recover_session's
        # fixture cleanup. Exercise durable Workload.restore here and report
        # the omitted cleanup check explicitly; Linux runs the full entry.
        work = self.cw.Workload(self.root,
            dict(id=self.owner['id'], original_playback=copy.deepcopy(self.original)), self.api, self.folder)
        work.record = json.loads((self.root / 'controlled-workload.json').read_text())
        return work.restore()

    def assert_restored(self):
        assert self.player.state == self.original, (self.player.state, self.original)
        assert self.player.state['urls'].count(self.original['urls'][0]) == 2
        assert len(self.player.state['urls']) == 17
        if os.name == 'posix':
            assert not Path(self.work.record['workload']['path']).exists()
        assert not any(c[:2] == ['playlist', 'clear'] for c in self.player.commands)


def run_all(lib):
    sys.path.insert(0, str(lib))
    # quiet_playback binds this placeholder only; each Case installs its fake
    # player. This prevents importing the production RPC client's dependencies.
    sys.modules['player'] = types.ModuleType('player')
    cw = importlib.import_module('autotune_v2.controlled_workload')
    cw.os = FixtureOS()
    quiet = importlib.import_module('quiet_playback')
    adapter_module = importlib.import_module('autotune_v2.native_adapter')
    results = []

    def check(name, action):
        try:
            with tempfile.TemporaryDirectory(prefix='autotune-repeat-') as folder:
                action(Path(folder))
            results.append({'name': name, 'status': 'PASS'})
        except BaseException as error:
            results.append({'name': name, 'status': 'FAIL', 'error': type(error).__name__ + ': ' + str(error)})

    def dstm_oracle(root):
        case = Case(root, cw, quiet)
        player = case.player
        player.rpc(case.original['mac'], ['playlist', 'repeat', 0])
        player.rpc(case.original['mac'], ['playlist', 'add', case.work.record['workload']['path']])
        player.rpc(case.original['mac'], ['playlist', 'index', 17, 0, 1])
        assert player.dstm_appends == 1
        assert player.state['urls'][-1].startswith('randomplay://album')
    check('oracle reproduces repeat-off provider append', dstm_oracle)

    def selection_and_restore(root):
        case = Case(root, cw, quiet)
        assert case.work.record.get('fixture_repeat') == 1, 'New fixture must journal repeat-current'
        case.select()
        commands = case.player.commands
        repeat = commands.index(['playlist', 'repeat', 1])
        append = next(i for i, c in enumerate(commands) if c[:2] == ['playlist', 'add'])
        play = commands.index(['play'])
        assert repeat < append < play
        assert case.player.state['repeat'] == 1 and case.player.state['shuffle'] == 0
        fixture = Path(case.work.record['workload']['path']).resolve().as_uri()
        expected = case.original['urls'] + [fixture]
        assert case.player.state['urls'] == expected
        for _ in range(25):
            case.player.source_finished()
            assert case.player.state['urls'] == expected
            assert case.player.state['urls'].count(fixture) == 1
            assert case.player.state['index'] == len(expected) - 1
            assert case.player.state['mode'] == 'play'
        assert case.player.fixture_cycles == 25 and case.player.dstm_appends == 0
        result = case.recover()
        assert result['restored'] is True and result['queue_restored'] is True
        case.assert_restored()
    check('repeat set before append/play; 25 cycles stable; exact recovery', selection_and_restore)

    before_phases = ('selecting-flags', 'appending-fixture', 'selecting-fixture', 'starting-fixture', 'controlled-playback')
    for phase in before_phases:
        def interrupted_before(root, phase=phase):
            case = Case(root, cw, quiet)
            def interrupt(record):
                if record.get('phase') == phase:
                    raise Interrupted('after durable ' + phase)
            case.after_atomic = interrupt
            try:
                case.select()
            except Interrupted:
                pass
            else:
                raise AssertionError('Requested selection interruption was not reached')
            result = case.recover()
            assert result['restored'] is True
            assert case.player.dstm_appends == 0
            case.assert_restored()
        check('recover durable selection phase ' + phase, interrupted_before)

    for kind in ('repeat', 'append', 'index', 'play'):
        def interrupted_after(root, kind=kind):
            case = Case(root, cw, quiet)
            def interrupt(command):
                match = {'repeat': command[:2] == ['playlist', 'repeat'],
                         'append': command[:2] == ['playlist', 'add'],
                         'index': command[:2] == ['playlist', 'index'],
                         'play': command == ['play']}[kind]
                if match:
                    raise Interrupted('after applied ' + kind)
            case.player.after_command = interrupt
            try:
                case.select()
            except Interrupted:
                pass
            else:
                raise AssertionError('Requested command interruption was not reached')
            result = case.recover()
            assert result['restored'] is True
            assert case.player.dstm_appends == 0
            case.assert_restored()
        check('recover applied selection command ' + kind, interrupted_after)

    for kind in ('stop', 'delete', 'index', 'repeat'):
        def interrupted_restore(root, kind=kind):
            case = Case(root, cw, quiet)
            case.select()
            def interrupt(command):
                match = {'stop': command == ['stop'],
                         'delete': command[:2] == ['playlist', 'delete'],
                         'index': command[:2] == ['playlist', 'index'],
                         'repeat': command == ['playlist', 'repeat', 2]}[kind]
                if match:
                    raise Interrupted('after restoration ' + kind)
            case.player.after_command = interrupt
            try:
                case.work.restore()
            except Interrupted:
                pass
            else:
                raise AssertionError('Requested restoration interruption was not reached')
            result = case.recover()
            assert result['restored'] is True
            assert case.player.dstm_appends == 0
            case.assert_restored()
        check('recover interrupted restoration ' + kind, interrupted_restore)

    def manual_queue(root):
        case = Case(root, cw, quiet)
        case.select()
        case.player.state.update(urls=['file:///manual/new.flac', 'file:///manual/new.flac'],
                                 index=1, repeat=2, mode='pause', time=108.5)
        expected = copy.deepcopy(case.player.state)
        command_count = len(case.player.commands)
        result = case.work.restore()
        assert result['external_queue_preserved'] is True and result['restored'] is False
        assert result['player_commands'] == 0
        assert case.player.state == expected
        assert len(case.player.commands) == command_count
    check('new manual queue and duplicate order preserved without commands', manual_queue)

    for explicit in (False, True):
        def old_record(root, explicit=explicit):
            case = Case(root, cw, quiet, dstm=False)
            fixture = Path(case.work.record['workload']['path']).resolve().as_uri()
            case.work.record.pop('fixture_repeat', None)
            if explicit:
                case.work.record['fixture_repeat'] = 0
            case.work.record['phase'] = 'controlled-playback'
            case.api.atomic(case.root / 'controlled-workload.json', case.work.record)
            case.player.state.update(urls=case.original['urls'] + [fixture], index=17,
                                     repeat=0, mode='play', time=30.5)
            result = case.recover()
            assert result['restored'] is True
            case.assert_restored()
        check('legacy repeat-off recovery ' + ('explicit field' if explicit else 'missing field'), old_record)

    def adapter_flags(root):
        case = Case(root, cw, quiet)
        case.select()
        adapter = adapter_module.NativeAdapter.__new__(adapter_module.NativeAdapter)
        adapter.workload = copy.deepcopy(case.work.record['workload'])
        adapter.target = case.target
        adapter.fixture_playback = copy.deepcopy(case.player.state)
        adapter.fixture_pin = adapter.file_pin(Path(adapter.workload['path']))
        adapter.source = {'sample_hash': 'fixture-only', 'volume': 100}
        adapter.api = types.SimpleNamespace(player=case.player,
            comparison=types.SimpleNamespace(playback=lambda mac: dict(adapter.source)),
            verify_restore=quiet.qualified_lms)
        usb=dict(idVendor='1234',idProduct='5678',serial='fixture-output',product='Fixture',path='1-1')
        identity={key:usb[key] for key in ('idVendor','idProduct','serial','product','path')}
        adapter.target['identity']=identity
        hardware = {'dac': {'status':'verified-active','identity':identity,'usb':usb,
            'pcm': {'hw': {'rate': '44100', 'channels': 2, 'format': 'S32_LE'}}},
            'alsa':{'cards':[dict(card=1,id='Fixture',usb_path='1-1')]},'usb':[usb]}
        adapter.verify_workload(hardware)
        # Change both snapshots: the explicit repeat-current contract must
        # reject zero even when it equals the saved fixture snapshot.
        case.player.state['repeat'] = adapter.fixture_playback['repeat'] = 0
        try:
            adapter.verify_workload(hardware)
        except RuntimeError:
            pass
        else:
            raise AssertionError('NativeAdapter accepted repeat disabled')
    check('NativeAdapter accepts repeat-current and rejects repeat-off', adapter_flags)

    failed = sum(item['status'] == 'FAIL' for item in results)
    print(json.dumps({'environment': 'fixture-only; no native LMS, network or audio',
        'linux_recovery_entry_and_cleanup_checked': os.name == 'posix',
        'lib': str(lib), 'passed': len(results) - failed, 'failed': failed, 'checks': results}, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib', type=Path, default=Path(__file__).resolve().parent / 'working' / 'lib')
    args = parser.parse_args()
    raise SystemExit(run_all(args.lib.resolve()))
