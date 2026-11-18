"""Sampling deadline regressions with an independent jiffy/clock model."""
from pathlib import Path
import argparse,copy,math,sys,types
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--lib',type=Path,default=Path(__file__).parent/'working/lib');a=p.parse_args();sys.path.insert(0,str(a.lib.resolve()))
import runtime
class Clock:
 def __init__(self):self.now=100.
 def monotonic(self):return self.now
 def sleep(self,n):self.now+=max(0,n-.0000001) # A permitted slightly early return.
checks=[]
for seconds in (1,1.01,2.5,10,30):
 c=Clock();times=[]
 def counters():
  stamp=c.now;times.append(stamp);j=round(stamp*100)
  # 20% busy; the same jiffy count represents per-CPU and total counters.
  row=dict(counter_monotonic=stamp,telemetry={},stat=dict(cpu=[j*2,0,0,j*8,0,0,0,0],cpu0=[j*2,0,0,j*8,0,0,0,0],intr=[j],ctxt=[j]),irq=[],softirqs='',network='',disk='',mem='',procs=[],alsa={})
  c.now+=.002 # Cost of reading sysfs after the counter timestamp.
  return row
 with patch.object(runtime,'time',c),patch.object(runtime,'counters',counters),patch.object(runtime,'command',lambda argv:''),patch.object(runtime.telemetry,'summarize',lambda rows,pcm:{}):
  result=runtime.observe(seconds,pcm_path='/fixture/pcm')
 gaps=[b-a for a,b in zip(times,times[1:])]
 assert len(gaps)==math.ceil(seconds) and min(gaps)>.49,(seconds,gaps)
 assert result['max_cpu_percent']['cpu0']==20 and result['sample_count']==math.ceil(seconds)
 checks.append(dict(seconds=seconds,samples=len(gaps),minimum_interval=min(gaps)))
print(dict(status='PASS',cases=checks,hardware_claim=False))
