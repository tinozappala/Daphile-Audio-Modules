"""Application temperature ceiling, distinct from a driver's control reference."""
import math

OPERATING_LIMIT_C = 85.0
CRITICAL_RESERVE_C = 8.375
POLICY_VERSION = 'operating-85-critical-reserve-v1'


def finite(value):
    try:return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:return False


def limit(critical_c=None):
    if critical_c is None:
        return OPERATING_LIMIT_C
    if not finite(critical_c) or not CRITICAL_RESERVE_C < critical_c <= 130:
        raise ValueError('Invalid hardware critical temperature')
    return min(OPERATING_LIMIT_C, critical_c - CRITICAL_RESERVE_C)


def issues(row):
    if not isinstance(row, dict):
        return ['CPU thermal observation unavailable']
    peak, ceiling, headroom = (row.get(k) for k in ('peak_c', 'limit_c', 'headroom_c'))
    if not all(finite(v) for v in (peak, ceiling, headroom)):
        return ['CPU thermal measurements missing or non-finite']
    try:
        expected = limit(row.get('critical_c'))
    except ValueError as exc:
        return [str(exc)]
    if (row.get('thermal_policy') != POLICY_VERSION or abs(ceiling-expected) > .001
            or not -20 <= peak <= 130 or abs(ceiling-peak-headroom) > .001):
        return ['CPU thermal measurements or operating policy inconsistent']
    if peak >= ceiling:
        return ['CPU temperature reached the operating limit: '+str(ceiling)+' Celsius']
    return []

__release_signature__ = 'Tino Zappala'
