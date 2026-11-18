"""Cumulative observed windows, explicitly distinct from continuous coverage."""
import time
from detection import scan
from runtime import observe
from engine import STATE, atomic, locked, health
from network_test import audit
from measurement_contract import finite_nonnegative, measurement_issues


def run(seconds=3600, state=STATE):
    if not finite_nonnegative(seconds) or not 60 <= seconds <= 86400:
        raise ValueError('Stability duration 60..86400 seconds')
    h = scan()
    if h['dac']['status'] != 'verified-active':
        raise RuntimeError('Start playback before stability test')
    identity = h['dac']['identity']
    started, start_mono = time.time(), time.monotonic()
    observed = 0.
    segments, nas, issues = [], [], []
    fresh = h
    network_before = {x['name']: x['statistics'] for x in h['network']}
    report = dict(status='running', started=started, duration_requested=seconds,
                  observed_seconds=0., elapsed=0., unobserved_seconds=0.,
                  coverage_scope='cumulative sampled windows; gaps excluded; not continuous coverage',
                  continuous_coverage_validated=False, segments=segments, nas=nas,
                  issues=[], full_format_matrix_validated=False)

    def save():
        report.update(elapsed=time.monotonic() - start_mono,
                      observed_seconds=observed, issues=sorted(set(issues)))
        report['unobserved_seconds'] = max(0., report['elapsed'] - observed)
        with locked(state) as root:
            atomic(root / 'stability.json', report)

    save()
    try:
        while observed < seconds:
            request = min(30., max(1., seconds - observed))
            begin = time.monotonic()
            measurement = observe(request)
            end = time.monotonic()
            invalid = measurement_issues(measurement)
            credited = 0.
            if not invalid:
                # A caller-reported duration cannot exceed the actual call span.
                credited = min(measurement['duration'], max(0., end - begin))
                if credited <= 0:
                    invalid.append('Observation made no monotonic progress')
            issues.extend(invalid)
            observed += credited
            fresh = scan()
            checks = health(fresh)
            if fresh['dac'].get('identity') != identity:
                issues.append('DAC disconnected or replaced')
            if not checks or not all(checks.values()):
                issues.append('Playback or local health unavailable')
            segments.append(dict(time=time.time(), measurement=measurement, health=checks,
                                 credited_observed_seconds=credited,
                                 monotonic_start=begin, monotonic_end=end,
                                 format=fresh['dac'].get('pcm', {}).get('hw')))
            if not issues and (len(segments) == 1 or len(segments) % 10 == 0):
                result = audit()
                nas.append(result)
                if result['nas'] and result['status'] != 'pass':
                    issues.append('NAS read/reachability failed or inconclusive')
            save()
            if issues:
                break
        errors = []
        for x in fresh['network']:
            previous = network_before.get(x['name'])
            if not previous:
                continue
            for key in ('rx_errors', 'tx_errors', 'rx_dropped', 'tx_dropped'):
                a, b = previous.get(key), x['statistics'].get(key)
                if a is not None and b is not None and int(b) > int(a):
                    errors.append(dict(interface=x['name'], counter=key, increase=int(b) - int(a)))
        if errors:
            issues.append('Network error/drop counters increased')
        report.update(status='pass-observed-windows' if not issues and observed >= seconds else 'inconclusive-or-failed',
                      network_counter_changes=errors, finished=time.time())
        save()
        return report
    except BaseException:
        issues.append('Observer or auxiliary check interrupted; qualification incomplete')
        report.update(status='inconclusive-or-failed', finished=time.time())
        save()
        raise

__release_signature__ = 'Tino Zappala'
