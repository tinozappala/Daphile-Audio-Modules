"""Test the preparation action against an observed, mutable player fixture."""

from argparse import ArgumentParser
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--lib", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.lib))
    from autotune_v2 import common_activity, workflow
    from playback_contract import PreparationRequired
    from cli_result import exit_code
    from gui import args as broker_args, validate

    class FakePlayer:
        def __init__(self, *, mode="pause", time=93.7, power=1, shuffle=0,
                     stop_result="reset", scan=False, grouped=False, full=False):
            self.state = dict(mac="02:00:00:00:00:01", urls=["a", "b", "a"], index=2, mode=mode, time=time,
                              repeat=2, shuffle=shuffle, volume=82, mute=0, power=power)
            self.commands = []
            self.stop_result = stop_result
            self.scan = scan
            self.grouped = grouped
            self.full = full

        def assert_library_idle(self, _):
            if self.scan:
                raise PreparationRequired("library-scan", "Synthetic scan in progress")

        def playback_snapshot(self, _):
            return copy.deepcopy(self.state)

        def playlist_key(self, value):
            return value

        def rpc(self, mac, command):
            if command == ["sync", "?"]:
                return {"_sync": "peer" if self.grouped else "-"}
            if command == ["playerpref", "syncgroupid", "?"]:
                return {"_p2": None}
            if command == ["pref", "maxPlaylistLength", "?"]:
                return {"_p2": len(self.state["urls"]) if self.full else 0}
            assert mac == self.state["mac"]
            self.commands.append(command)
            if command == ["stop"]:
                if self.stop_result == "unchanged":
                    return {}
                self.state["mode"] = "stop"
                if self.stop_result != "retained":
                    self.state["time"] = 0
                if self.stop_result == "lost-response":
                    raise TimeoutError("Synthetic timeout after stop")
                if self.stop_result == "queue-changed":
                    self.state["urls"].append("user change")
            elif command == ["playlist", "index", "2", 0, 1] and self.state["mode"] == "stop":
                self.state["time"] = 0
            else:
                raise AssertionError(f"Unexpected command {command}")
            return {}

    def api(player, external=False):
        return SimpleNamespace(uid=lambda: 0, engine=SimpleNamespace(ensure_idle_transaction=lambda _: None),
                               external_trial=lambda: external, player=player, scan=lambda: {},
                               target=lambda _: {"mac": player.state["mac"]}, rate=lambda *_: 44100,
                               workload_api=lambda: SimpleNamespace(verify_restore=lambda: None))

    def run(player, *, allow_playing=False, **extra):
        with patch.object(common_activity, "run_owned", lambda root, action, **kw: action()):
            return workflow.prepare_player(Path("/fixture"), True, api(player, **extra),allow_playing=allow_playing)

    checks = []
    for mode, cursor, result, count in [("pause", 93.7, "reset", 1), ("pause", 93.7, "retained", 2),
                                        ("pause", 93.7, "lost-response", 1), ("stop", 0, "reset", 0),
                                        ("stop", 93.7, "reset", 1)]:
        player = FakePlayer(mode=mode, time=cursor, stop_result=result)
        original = player.playback_snapshot("")
        prepared = run(player)
        assert exit_code("automatic", "prepare-player", prepared) == 0
        assert prepared["player_commands"] == count == len(player.commands)
        assert player.state == dict(original, mode="stop", time=0)
        checks.append((mode, result, count))
    for condition, issue in [({"mode": "play"}, "pause-first"), ({"power": 0}, "player-off"),
                             ({"shuffle": 1}, "shuffle-on"), ({"grouped": True}, "player-group"),
                             ({"scan": True}, "library-scan"), ({"full": True}, "queue-full")]:
        player = FakePlayer(**condition)
        original = player.playback_snapshot("")
        assert run(player)["reason_code"] == issue and not player.commands and player.state == original
        checks.append(issue)
    player = FakePlayer(stop_result="unchanged")
    assert run(player)["reason_code"] == "stop-unverified" and player.commands == [["stop"]]
    player = FakePlayer(stop_result="queue-changed")
    assert run(player)["reason_code"] == "player-changed" and player.state["urls"][-1] == "user change"
    player = FakePlayer()
    assert run(player, external=True)["reason_code"] == "audio-busy" and not player.commands
    try:
        workflow.prepare_player(Path("/fixture"), False, api(player))
    except RuntimeError:
        assert not player.commands
    else:
        raise AssertionError("Consent required")
    request = {"action": "automatic-prepare-player"}
    try:
        validate(request)
    except ValueError:
        pass
    else:
        raise AssertionError("Broker allowed an unconfirmed request")
    request["confirm_audio"] = True
    assert validate(request) == request["action"]
    assert broker_args(request) == ["automatic", "prepare-player", "--confirm-audio-test"]
    request = {"action":"automatic-run","confirm_audio":True,"prepare_player":True}
    assert validate(request)==request["action"]
    assert broker_args(request)==["automatic","run","--confirm-audio-test","--prepare-player"]

    for mode in ("pause", "play"):
        player=FakePlayer(mode=mode)
        steps=[]
        def require(seconds):
            steps.append(("budget",seconds))
        def locked(root,function,**kwargs):
            steps.append(("lock",kwargs["role"]))
            return function()
        def trial(*_):
            steps.append(("trial",player.state["mode"],player.state["time"]))
            assert player.commands==[["stop"]]
            return {"status":"retained-current"}
        with patch.object(common_activity,"run_owned",locked),patch.object(workflow,"_workflow_run_without_common",trial):
            result=workflow.run(Path("/fixture"),True,False,False,SimpleNamespace(require=require),api(player),prepare=True)
        assert result["status"]=="retained-current"
        assert steps==[("lock","automatic-run"),("budget",300),("lock","player-prepare"),("trial","stop",0)]
        checks.append(mode+"+one-click")

    player=FakePlayer(grouped=True)
    with patch.object(common_activity,"run_owned",locked),patch.object(workflow,"_workflow_run_without_common",side_effect=AssertionError):
        blocked=workflow.run(Path("/fixture"),True,False,False,SimpleNamespace(require=require),api(player),prepare=True)
    assert blocked["reason_code"]=="player-group" and not player.commands
    print(f"PASS: {len(checks)+7} preparation, one-click and consent cases")


if __name__ == "__main__":
    main()
