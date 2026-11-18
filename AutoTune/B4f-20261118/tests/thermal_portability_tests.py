"""CPU sensor fixtures for desktop, ARM/SoC and unknown sensor drivers."""
from pathlib import Path
import argparse,sys,tempfile
p=argparse.ArgumentParser();p.add_argument('--lib',type=Path,default=Path(__file__).parent/'working/lib');a=p.parse_args();sys.path.insert(0,str(a.lib.resolve()))
import telemetry
with tempfile.TemporaryDirectory() as d:
 r=Path(d)
 def put(path,value):
  p=r/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(value)
 for i,kind in enumerate(('cpu-thermal','soc_thermal','x86_pkg_temp','gpu','battery','cluster0')):
  path='class/thermal/thermal_zone'+str(i)+'/'
  for name,value in dict(type=kind,temp='52000',trip_point_0_type='critical',trip_point_0_temp='90000').items():put(path+name,value)
 for i,label in enumerate(('CPU package','Core 0','GPU')):
  path='class/hwmon/hwmon'+str(i)+'/'
  for name,value in dict(name='new-observed-driver',temp1_input='50000',temp1_label=label).items():put(path+name,value)
 data=telemetry.capture(r,r)['temperatures'];labels={v['label'] for v in data.values()}
 assert labels=={'cpu-thermal','soc_thermal','x86_pkg_temp','cluster0','CPU package','Core 0'},labels
 assert all(v['limit_c']==81.625 for v in data.values() if v['driver']=='thermal-zone')
 print(dict(status='PASS',sensor_fixtures=9,accepted=len(data),unrelated_sensors_excluded=True))
