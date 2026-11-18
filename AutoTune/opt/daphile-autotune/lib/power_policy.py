"""Explicit measured cpuidle/clocksource trials; never a sound-quality policy."""
import os,re
from pathlib import Path
import engine
from detection import read,scan,numbers

CLOCK=Path('/sys/devices/system/clocksource/clocksource0')
CPU=Path('/sys/devices/system/cpu')
PARAMETERS={'cstate_disable','clocksource'}

def inventory():
 rows=[]
 for path in sorted(CPU.glob('cpu[0-9]*/cpuidle/state[0-9]*')):
  rows.append({'path':str(path),'cpu':int(path.parent.parent.name[3:]),'index':int(path.name[5:]),**{k:read(path/k) for k in ['name','desc','latency','disable','usage','time']}})
 return {'cstates':rows,'clocksource':read(CLOCK/'current_clocksource'),'available_clocksources':(read(CLOCK/'available_clocksource') or '').split(),'classification':'EXPERIMENTAL','requires_measured_trial':True}

def plan(h,max_latency=None,clock=None):
 if h['dac'].get('status')!='verified-active' or not __import__('usb_target').identified(h['dac'].get('identity',{})):raise RuntimeError('Active serial-numbered DAC required')
 host=h['system'].get('product_uuid')
 if not host:raise RuntimeError('Host UUID unavailable')
 if max_latency is not None and (type(max_latency)!=int or not 1<=max_latency<=10000):raise ValueError('C-state latency ceiling1..10000 microseconds')
 inv=inventory();changes=[]
 def add(parameter,path,before,after,**kwargs):
  if before!=after:changes.append(dict(parameter=parameter,path=str(path),before=before,after=after,identity=h['dac']['identity'],host_uuid=host,classification='EXPERIMENTAL',reversible=True,evidence='CONTROLLED MEASUREMENT REQUIRED',**kwargs))
 if max_latency is not None:
  for row in inv['cstates']:
   if row['index']==0:continue
   if row['disable'] not in ['0','1'] or not (row['latency'] or '').isdigit():raise RuntimeError('Incomplete writable cpuidle inventory')
   if int(row['latency'])>max_latency:add('cstate_disable',Path(row['path'])/'disable',row['disable'],'1',state_name=row['name'],state_latency=row['latency'])
 if clock is not None:
  if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}',clock) or clock not in inv['available_clocksources']:raise ValueError('Clocksource is not advertised by running kernel')
  if inv['clocksource'] is None:raise RuntimeError('Current clocksource unknown')
  add('clocksource',CLOCK/'current_clocksource',inv['clocksource'],clock)
 return {'changes':changes,'inventory':inv,'classification':'EXPERIMENTAL','reboot_required':False,'sound_quality_claim':False}

def guard(c,h,restoring=False):
 if c.get('host_uuid')!=read('/sys/class/dmi/id/product_uuid') or not c.get('host_uuid'):raise RuntimeError('Power trial belongs to different hardware')
 ident=c.get('identity',{});matches=[u for u in h.get('usb',[]) if all(u.get(k)==v for k,v in ident.items())]
 if not __import__('usb_target').identified(ident) or len(matches)!=1:raise RuntimeError('Original DAC missing or replaced')
 path=c.get('path','')
 if c['parameter']=='cstate_disable':
  m=re.fullmatch(r'/sys/devices/system/cpu/cpu([0-9]+)/cpuidle/state([1-9][0-9]*)/disable',path)
  if not m or int(m[1]) not in numbers(read(CPU/'online')) or c['before'] not in ['0','1'] or c['after']!='1':raise RuntimeError('Invalid cpuidle target')
  p=Path(path).parent
  if read(p/'name')!=c['state_name'] or read(p/'latency')!=c['state_latency']:raise RuntimeError('C-state identity changed')
 elif c['parameter']=='clocksource':
  available=(read(CLOCK/'available_clocksource') or '').split()
  if path!=str(CLOCK/'current_clocksource') or any(x not in available for x in [c['before'],c['after']]):raise RuntimeError('Clocksource no longer supported')
 else:raise RuntimeError('Unknown power parameter')

def value(c):return read(c['path'])
def write(c,value):
 from detection import usb_devices
 guard(c,{'usb':usb_devices()},restoring=value==c['before'])
 if value not in [c['before'],c['after']]:raise ValueError('Value outside power transaction')
 fd=os.open(c['path'],os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:os.write(fd,(value+'\n').encode())
 finally:os.close(fd)
 if read(c['path'])!=value:raise RuntimeError('Power policy readback mismatch')

def trial(max_latency=None,clock=None,seconds=30,state=engine.STATE,confirmed=False):
 if not confirmed:raise RuntimeError('Explicit experimental trial acknowledgement required')
 return engine.trial(lambda h:plan(h,max_latency,clock)['changes'],'power-experimental',seconds,state,commit=False,restore_position=False)

def isolation(h,selected=None,tickless=False):
 """Keep entire SMT groups together and two physical cores for housekeeping."""
 online=numbers(h['cpu'].get('online'));groups={};covered=set()
 if not online:raise RuntimeError('CPU topology coverage unavailable')
 for c in h['cpu']['cores']:
  if c['cpu'] not in online:continue
  if c['cpu'] in covered:raise RuntimeError('Duplicate CPU topology record')
  covered.add(c['cpu'])
  key=(c['topology'].get('physical_package_id'),c['topology'].get('core_id'))
  if any(v is None or str(v).strip()=='' or not str(v).isdigit() for v in key):raise RuntimeError('Physical CPU topology unavailable')
  groups.setdefault(key,[]).append(c['cpu'])
 if covered!=set(online):raise RuntimeError('Incomplete online CPU topology coverage')
 for c in h['cpu']['cores']:
  if c['cpu'] not in covered:continue
  siblings=c['topology'].get('thread_siblings_list')
  group=set(groups[(c['topology']['physical_package_id'],c['topology']['core_id'])])
  if siblings is not None and (not siblings or set(numbers(siblings))&set(online)!=group):raise RuntimeError('Inconsistent reciprocal SMT topology')
 ordered=sorted(groups.values(),key=min)
 if len(ordered)<3:raise RuntimeError('At least3physical cores required; two reserved for housekeeping')
 isolate=sorted(selected if selected is not None else [n for group in ordered[2:] for n in group])
 if not isolate or any(type(n)!=int or n not in online for n in isolate) or 0 in isolate or len(set(isolate))!=len(isolate):raise ValueError('Invalid isolated CPUs')
 if any(set(g)&set(isolate) and not set(g)<=set(isolate) for g in ordered):raise ValueError('SMT siblings must remain together')
 house=sorted(set(online)-set(isolate))
 if sum(set(g)<=set(house) for g in ordered)<2:raise ValueError('Two complete housekeeping cores required')
 iso=','.join(map(str,isolate));hk=','.join(map(str,house))
 options=['isolcpus=domain,managed_irq,'+iso,'irqaffinity='+hk]
 if tickless:
  import gzip
  with gzip.open('/proc/config.gz','rt') as f:config=f.read()
  if not all('CONFIG_'+flag+'=y' in config.splitlines() for flag in ['NO_HZ_FULL','RCU_NOCB_CPU']):raise RuntimeError('Tickless/RCU offload not built into running kernel')
  options+=['nohz_full='+iso,'rcu_nocbs='+iso]
 return {'isolated_cpus':isolate,'housekeeping_cpus':house,'options':options,'classification':'EXPERIMENTAL','reboot_required':True,'sound_quality_claim':False}

def isolated_affinity(h):
 from advanced import plan as runtime_plan
 isolated=numbers(read(CPU/'isolated'))
 if not isolated:return [],['No isolated CPUs in the running kernel; stage and verify the boot profile first']
 online=numbers(h['cpu'].get('online'))
 if not set(isolated)<=set(online):raise RuntimeError('Isolated CPUs are offline')
 changes,notes=runtime_plan(h,'audio-rt');selected=[]
 for c in changes:
  if c['parameter']=='usb_irq_affinity':c['after']=str(isolated[0])
  elif c['parameter']=='thread_affinity':c['after']=[isolated[-1] if c['role']=='audio-output' else isolated[0]]
  else:continue
  if c['before']!=c['after']:selected.append(c)
 return selected,notes

__release_signature__ = 'Tino Zappala'
