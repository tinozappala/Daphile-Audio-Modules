"""Read-only audio setup: configured values are distinct from effective ALSA values."""
import re
from pathlib import Path
from detection import scan, read, VERSION
PREFERENCE_KEYS = {'BUFFERTIME', 'PERIODCOUNT', 'STREAM_BUFFER', 'OUTPUT_BUFFER', 'RESAMPLE', 'RATELIMIT', 'USEDOP', 'VOLCTRL', 'LINVOL', 'HDCD', 'DSDSR', 'DEEMPH', 'DECODE', 'LMS_DS'}

def preferences(text):
    result = {}
    for key, value in re.findall('^([A-Z_0-9]+)=([^\\r\\n]*)$', text or '', re.M):
        if key in PREFERENCE_KEYS:
            result[key] = value if key not in result else None
    return result

def effective(pcm):
    hw = pcm.get('hw') or {}
    values = {key: hw.get(key) for key in ['format', 'channels', 'rate', 'buffer_size', 'period_size']}
    try:
        rate = int(re.fullmatch('(\\d+)(?:\\s+\\(\\d+/\\d+\\))?', str(hw['rate']))[1])
        buffer, period = (int(hw[key]) for key in ['buffer_size', 'period_size'])
        if min(rate, buffer, period) <= 0:
            raise ValueError('Invalid ALSA values')
        values.update(buffer_ms=1000 * buffer / rate, period_ms=1000 * period / rate, periods=buffer / period)
    except (KeyError, TypeError, ValueError):
        values.update(buffer_ms=None, period_ms=None, periods=None)
    values['state'] = next(iter(re.findall('^state:\\s*(\\S+)', pcm.get('status') or '', re.M)), None)
    values['transport'] = 'native-dsd-container' if str(hw.get('format', '')).startswith('DSD_') else 'PCM container; DoP cannot be identified from hw_params alone' if hw else 'unknown'
    return values

def report(hardware=None):
    h = scan() if hardware is None else hardware
    d = h['dac']
    active = d.get('status') == 'verified-active'
    configured = {}
    issues = []
    selected = None
    if active:
        try:
            from player import target
            t = target(h)
            configured = preferences(read('/opt/spac/share/defaults.conf'))
            configured.update(preferences(read(t['path'])))
            selected = {'alsa_id': d.get('alsa_id'), 'card': d.get('card'), 'device': d.get('device'), 'product': (d.get('usb') or {}).get('product')}
        except (OSError, RuntimeError, ValueError) as e:
            issues.append(str(e))
    else:
        issues.append('Start playback on the intended DAC to verify its ownership and setup.')
    from t630 import eligibility
    t630 = eligibility(h)
    audio = effective(d.get('pcm') or {}) if active else effective({})
    for key, measured in [('BUFFERTIME', audio['buffer_ms']), ('PERIODCOUNT', audio['periods'])]:
        value = configured.get(key)
        if value is not None and measured is not None:
            try:
                if abs(float(value) - measured) > 0.1:
                    issues.append(key + ': configured and effective values differ')
            except ValueError:
                issues.append(key + ': invalid configured value')
    output = []
    for proc in h.get('squeezelite', []):
        for thread in proc.get('threads', []):
            if thread.get('tid') == d.get('owner_pid'):
                output.append({k: thread.get(k) for k in ['name', 'affinity', 'policy', 'priority']})
    usb = d.get('usb') or {}
    irq = next((x for x in h.get('irq') or [] if str(x['irq']) == str(usb.get('irq'))), None)
    return dict(engine_version=VERSION, read_only=True, dac_status=d.get('status'), selected=selected, configured=configured, effective=audio, output_threads=output, usb={k: usb.get(k) for k in ['path', 'controller', 'root_hub', 'irq', 'power']}, irq={k: irq.get(k) for k in ['irq', 'description', 'affinity', 'effective']} if irq else None, isolated_cpus=h.get('cpu', {}).get('isolated'), power_devices=[dict(address=p['address'], driver=p.get('driver'), power=p.get('power')) for p in h.get('pci', [])], cards=[dict(card=c['card'], id=c['id'], active=any((p['active'] for p in c['pcms']))) for c in h.get('alsa', {}).get('cards', [])], profiles=dict(generic=['safe', 'audio-rt', 'low-activity', 'network-stable'], t630_fixed=t630), buffer_guidance=dict(general_default_ms=100, general_default_periods=3), issues=issues, native_settings_url='/cgi-bin/Settings', limitations=['No signal-fidelity or sound-quality certification', 'Runtime profiles do not change boot isolation or real-time priorities', 'Audio Setup is read-only and performs no signal processing'])

__release_signature__ = 'Tino Zappala'
