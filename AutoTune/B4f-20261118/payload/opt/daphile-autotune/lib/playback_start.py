"""An LMS command timeout is uncertain; verify audio instead of resending play."""
import math
import time
import player
import playback_guard


def start_verified(mac, timeout=60):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 120:
        raise ValueError('Playback startup deadline 1..120 seconds required')
    saved = player.playback_snapshot(mac)
    deadline = time.monotonic() + timeout
    try:
        player.rpc(mac, ['play'])
    except TimeoutError:
        # The command may already have executed. Never resend it or treat a
        # successful RPC as proof that PCM is actually running.
        pass
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        try:
            hardware = player.wait_audio(mac, remaining)
            current = player.playback_snapshot(mac)
        except TimeoutError:
            continue
        if time.monotonic() >= deadline:
            break
        if not playback_guard.equivalent_queue(saved, current) or current['index'] != saved['index']:
            raise RuntimeError('Queue or track changed during audio startup; calibration refused')
        if current['mode'] != 'play':
            raise RuntimeError('Playback startup not confirmed')
        return hardware
    raise RuntimeError('Playback startup deadline exceeded; calibration refused')

__release_signature__ = 'Tino Zappala'
