#!/usr/bin/env python3
"""Offline regression test of the Squeezelite endpoint restart command.

No audio, USB or LMS processes are touched. Run in an environment where
native_player.py and its sibling modules can be imported.
"""
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from native_player import NativePlayer

player=NativePlayer()
player.guard=lambda target:None
player.require_standalone=lambda target:None
responses=iter([(42,'100'),(43,'110'),(43,'110')])
player.player_process=lambda target: next(responses)
player.rpc=lambda mac,cmd: {'player_connected':1}
with mock.patch('native_player.subprocess.run') as runner:
    player.restart({'card':3,'mac':'00:00:00:00:00:00'})
    runner.assert_called_once()
    assert runner.call_args.args[0]==[
        '/opt/spac/bin/spac-msg','restart','pcmC3D0p']
print('TEST_PASS: player-scoped restart, no card-wide usbreset command')
