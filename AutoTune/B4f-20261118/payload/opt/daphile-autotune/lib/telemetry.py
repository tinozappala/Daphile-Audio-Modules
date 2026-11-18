"""Read-only sampling of CPU topology, thermal margin and memory availability."""
import math,re
from datetime import datetime, timezone
from pathlib import Path
import time
try:
    from thermal_policy import limit, POLICY_VERSION
except ImportError:
    from .thermal_policy import limit, POLICY_VERSION


def read(path):
    try:return Path(path).read_text(encoding='utf8').strip()
    except OSError:return None


def numeric(path,scale=1):
    try:
        value=float(read(path))/scale
        return value if math.isfinite(value) else None
    except (TypeError,ValueError,OverflowError):return None


def capture(root=Path('/sys'),proc=Path('/proc')):
    sensors={}
    for p in root.glob('class/hwmon/hwmon*/temp*_input'):
        label=read(p.parent/(p.stem[:-6]+'_label')) or p.stem
        # Only CPU/package sensors establish CPU thermal headroom.
        driver=read(p.parent/'name')
        if (driver not in ('k10temp','coretemp','zenpower','cpu_thermal')
                and not re.search(r'(?i)(?:^|[^a-z])(?:cpu|core|package|tctl|tdie)(?:[^a-z]|$)',label)):continue
        stem=p.stem[:-6]
        maximum=numeric(p.parent/(stem+'_max'),1000)
        critical=numeric(p.parent/(stem+'_crit'),1000)
        try:ceiling=limit(critical)
        except ValueError:ceiling=None
        sensors[str(p.resolve())]=dict(label=label,driver=driver,celsius=numeric(p,1000),limit_c=ceiling,
                                      max_c=maximum,critical_c=critical,
                                      temperature_kind='Tctl' if driver=='k10temp' and stem=='temp1' else label,
                                      limit_kind='application-operating-limit',thermal_policy=POLICY_VERSION,
                                      limit_source='AutoTune 85 C; capped by hardware critical temperature minus reserve')
    # ARM/SoC and some integrated systems publish CPU temperatures through
    # thermal zones rather than the hwmon drivers used by desktop CPUs.
    for zone in root.glob('class/thermal/thermal_zone*'):
        kind=read(zone/'type') or ''
        if not re.search(r'(?i)(?:^|[^a-z])(?:cpu|soc|package|cluster|x86_pkg)(?:[^a-z]|$)',kind):continue
        critical=[]
        for trip in zone.glob('trip_point_*_type'):
            if (read(trip) or '').lower()=='critical':
                value=numeric(trip.with_name(trip.name[:-5]+'_temp'),1000)
                if value is not None:critical.append(value)
        critical_c=min(critical) if critical else None
        try:ceiling=limit(critical_c)
        except ValueError:ceiling=None
        sensors[str((zone/'temp').resolve())]=dict(label=kind,driver='thermal-zone',celsius=numeric(zone/'temp',1000),
            limit_c=ceiling,max_c=None,critical_c=critical_c,temperature_kind=kind,
            limit_kind='application-operating-limit',thermal_policy=POLICY_VERSION,
            limit_source='AutoTune 85 C; capped by hardware critical temperature minus reserve')
    memory={}
    for line in (read(proc/'meminfo') or '').splitlines():
        if ':' in line:
            k,v=line.split(':',1)
            try:memory[k]=int(v.split()[0])
            except (ValueError,IndexError):pass
    return dict(monotonic=time.monotonic(),collected_utc=datetime.now(timezone.utc).isoformat(),
                online=read(root/'devices/system/cpu/online'),temperatures=sensors,
                available_memory_kib=memory.get('MemAvailable'),swap_free_kib=memory.get('SwapFree'))


def selected_pcm(hardware=None):
    if hardware is None:
        from detection import scan
        hardware=scan()
    d=hardware.get('dac',{})
    if d.get('status')!='verified-active':return None
    coordinates=(d.get('card'),d.get('device'),d.get('pcm',{}).get('subdevice'))
    if any(type(v)!=int or v<0 for v in coordinates):return None
    card,device,subdevice=coordinates
    return f'/proc/asound/card{card}/pcm{device}p/sub{subdevice}/status'


def summarize(snapshots,pcm_path):
    items=[s.get('telemetry',{}) for s in snapshots]
    problems=[]
    if len(items)<2:return dict(status='incomplete',issues=['Sampling unavailable'])
    online=items[0].get('online')
    if not online or any(s.get('online')!=online for s in items):problems.append('CPU topology changed or unavailable')
    times=[s.get('monotonic') for s in items]
    gaps=[]
    if all(isinstance(t,(int,float)) and math.isfinite(t) for t in times):
        gaps=[b-a for a,b in zip(times,times[1:])]
        if any(not 0<gap<=3 for gap in gaps):problems.append('Sampling interval exceeded three seconds')
    else:problems.append('Sampling clock unavailable')
    sensor_ids=set(items[0].get('temperatures',{}))
    if not sensor_ids or any(set(s.get('temperatures',{}))!=sensor_ids for s in items):problems.append('CPU temperature observation incomplete')
    temperatures={}
    for key in sorted(sensor_ids):
        rows=[s.get('temperatures',{}).get(key,{}) for s in items]
        values=[s.get('celsius') for s in rows];limits=[s.get('limit_c') for s in rows]
        if any(v is None or not -20<=v<=130 for v in values):problems.append('Invalid CPU temperature');continue
        if any(type(v) not in (int,float) or not math.isfinite(v) or not 0<v<=130 for v in limits):problems.append('CPU thermal limit unavailable');continue
        if len(set(limits))!=1:problems.append('CPU thermal limit changed');continue
        peak=max(values);peak_index=values.index(peak)
        temperatures[key]=dict(peak_c=peak,limit_c=limits[0],headroom_c=limits[0]-peak,
                              peak_collected_utc=items[peak_index].get('collected_utc'),
                              last_sample_c=values[-1],last_sample_collected_utc=items[-1].get('collected_utc'),
                              limit_source=rows[0].get('limit_source'),max_c=rows[0].get('max_c'),critical_c=rows[0].get('critical_c'),
                              temperature_kind=rows[0].get('temperature_kind'),limit_kind=rows[0].get('limit_kind'),
                              thermal_policy=rows[0].get('thermal_policy'),
                              critical_headroom_c=rows[0]['critical_c']-peak if rows[0].get('critical_c') is not None else None,
                              headroom_basis='maximum sampled temperature in this observation; not current live temperature')
    memory=[s.get('available_memory_kib') for s in items]
    if any(type(v)!=int or v<0 for v in memory):problems.append('Memory observation unavailable');memory=[]
    active=bool(pcm_path) and all('state: RUNNING' in (s.get('alsa',{}).get(pcm_path) or '') for s in snapshots)
    if not active:problems.append('Selected PCM not observed RUNNING throughout sampled windows')
    return dict(status='observed' if not problems else 'incomplete',issues=problems,online=online,
                observation_start_utc=items[0].get('collected_utc'),observation_end_utc=items[-1].get('collected_utc'),
                sample_count=len(items),max_sample_gap_seconds=max(gaps) if gaps else None,
                temperatures=temperatures,minimum_available_memory_kib=min(memory) if memory else None,
                selected_pcm_running_every_sample=active,continuous_audio_validated=False)

__release_signature__ = 'Tino Zappala'
