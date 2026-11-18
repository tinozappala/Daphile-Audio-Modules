"""Passive measurements. Missing counters never imply zero errors."""
import math,re,time
from pathlib import Path
from detection import read,command,irq_map,processes
import telemetry
ERROR=re.compile(r"underrun|xrun|usb.*(?:reset|disconnect|error)|BUG:|Oops:|kernel panic|RT throttling|soft lockup|hard LOCKUP|blocked for more than",re.I)
def classify_kernel(text):
 errors=[];warnings=[]
 for line in text.splitlines():
  if ERROR.search(line):errors.append({'severity':'error','message':line[:4096]})
  elif re.search(r'WARNING:|Call Trace:|deprecated',line,re.I):warnings.append({'severity':'warning','message':line[:4096]})
 return {'errors':errors[:100],'warnings':warnings[:100],'truncated':len(errors)>100 or len(warnings)>100}

def counters():
 stat={}
 for line in (read('/proc/stat') or '').splitlines():
  a=line.split()
  if a and (a[0].startswith('cpu') or a[0] in ['ctxt','intr']):stat[a[0]]=list(map(int,a[1:11] if a[0].startswith('cpu') else a[1:2]))
 return dict(counter_monotonic=time.monotonic(),telemetry=telemetry.capture(),stat=stat,irq=irq_map(),softirqs=read('/proc/softirqs'),network=read('/proc/net/dev'),disk=read('/proc/diskstats'),mem=read('/proc/meminfo'),procs=[],alsa={str(p):read(p) for p in Path('/proc/asound').glob('card*/pcm*p/sub*/status')})
def observe(seconds=30,pcm_path=None):
 if not 1<=seconds<=3600:raise ValueError('Duration 1..3600 seconds')
 pcm_path=pcm_path or telemetry.selected_pcm();before=command(['dmesg']);first=counters();start=first['counter_monotonic'];samples=[]
 # Fixed, evenly spaced deadlines avoid a final microsecond-sized sample
 # when the preceding read falls just before the requested end. One jiffy
 # in such a tiny interval used to manufacture a 100% per-core peak.
 steps=math.ceil(seconds)
 for index in range(steps):
  due=start+seconds*(index+1)/steps
  time.sleep(max(0,due-time.monotonic()));samples.append(counters())
 elapsed=samples[-1]['counter_monotonic']-start;last=samples[-1];after=command(['dmesg']);known=before is not None and after is not None and after.startswith(before)
 new=after[len(before):] if known else '';loads={};peak={};previous=first
 for snap in samples:
  for k,v in snap['stat'].items():
   old=previous['stat'].get(k)
   if k.startswith('cpu') and old and len(v)>=5:
    d=[y-x for x,y in zip(old,v)];total=sum(d[:8])
    if total>0:peak[k]=max(peak.get(k,0),100*(total-d[3]-d[4])/total)
  previous=snap
 for k,v in last['stat'].items():
  old=first['stat'].get(k)
  if k.startswith('cpu') and old and len(v)>=5:
   d=[y-x for x,y in zip(old,v)];total=sum(d[:8]);loads[k]=100*(total-d[3]-d[4])/total if total else None
 irqa={x['irq']:sum(x['counts']) for x in first['irq'] or []}
 irq_rates={str(x['irq']):(sum(x['counts'])-irqa[x['irq']])/elapsed for x in last['irq'] or [] if x['irq'] in irqa}
 rate=lambda k:(last['stat'][k][0]-first['stat'][k][0])/elapsed if k in first['stat'] and k in last['stat'] else None
 return dict(telemetry=telemetry.summarize([first]+samples,pcm_path),observation_monotonic_start=start,observation_monotonic_end=start+elapsed,duration=elapsed,sample_count=len(samples),cpu_percent=loads,max_cpu_percent=peak,interrupts_per_second=rate('intr'),context_switches_per_second=rate('ctxt'),irq_rates=irq_rates,softirqs_before=first['softirqs'],softirqs_after=last['softirqs'],network_before=first['network'],network_after=last['network'],disk_before=first['disk'],disk_after=last['disk'],observed_alsa_xrun_states=sum('XRUN' in (v or '') for s in samples for v in s['alsa'].values()),new_kernel_errors=[x['message'] for x in classify_kernel(new)['errors']],new_kernel_warnings=classify_kernel(new)['warnings'],kernel_events_truncated=classify_kernel(new)['truncated'],kernel_log_observable=known,pcm_states=[s['alsa'] for s in samples],wakeups_per_second=None,max_scheduling_latency=None,hardware_xrun_counter=None,limitations=['ALSA samples can miss transient XRUNs','Context switches are not wakeups','Passive observation is not full-format audio certification','Observer activity is included in system CPU/interrupt metrics'])

__release_signature__ = 'Tino Zappala'
