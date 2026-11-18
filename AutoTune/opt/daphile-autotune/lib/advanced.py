"""Runtime-only, explicitly requested advanced profiles; no boot isolation."""
import os,re,subprocess,time
from pathlib import Path
from detection import read,numbers,usb_devices

def ticks(pid):
 s=read('/proc/'+str(pid)+'/stat')
 return s[s.rfind(')')+2:].split()[19] if s else None

def plan(h,profile):
 changes=[];warnings=[];d=h['dac'];u=d.get('usb') or {}
 if d.get('status')!='verified-active' or not __import__('usb_target').identified(__import__('usb_target').identity(u)):return changes,['Verified serial-numbered USB DAC required']
 def add(parameter,before,after,**kw):
  if before!=after:changes.append(dict(host_uuid=read('/sys/class/dmi/id/product_uuid'),parameter=parameter,before=before,after=after,identity=d['identity'],classification='CONTROLLED TRIAL',reversible=True,**kw))
 if profile=='audio-rt':
  seen=set()
  for cpu in h['cpu']['cores']:
   path=str((Path('/sys/devices/system/cpu')/('cpu'+str(cpu['cpu']))/'cpufreq/scaling_governor').resolve())
   f=cpu['cpufreq']
   if path not in seen and 'performance' in (f.get('scaling_available_governors') or '').split() and f.get('scaling_governor'):
    seen.add(path);add('cpu_governor',f['scaling_governor'],'performance',path=path)
  irq=u.get('irq');entry=next((x for x in h.get('irq') or [] if str(x['irq'])==str(irq)),None)
  # Shared non-USB interrupts cannot be assigned safely using the controller IRQ alone.
  irqthreads=[t for p in h['squeezelite'] for t in p['threads'] if t['name'] and t['name'].startswith('irq/'+str(irq)+'-')]
  physical=[];seen=set()
  for c in sorted(h['cpu']['cores'],key=lambda x:x['cpu']):
   key=(c['topology'].get('physical_package_id'),c['topology'].get('core_id'))
   if None not in key and key not in seen:physical.append(c['cpu']);seen.add(key)
  if len(physical)>=3 and entry and irqthreads and all(re.search(r'ehci|xhci|ohci|uhci',t['name']) for t in irqthreads):
   suffix=re.split(r'(?:fasteoi|edge|level)\s+',entry['description'],maxsplit=1)
   owners=[x.strip() for x in suffix[-1].split(',')] if len(suffix)==2 else []
   if not owners or not all(re.fullmatch(r'(?:ehci|xhci|ohci|uhci)_hcd:usb[0-9]+',x) for x in owners):warnings.append('IRQ shared with non-USB or unknown device: affinity left unchanged')
   else:
    irqcpu=physical[1];outcpu=physical[-1]
    if entry['affinity'] is not None:add('usb_irq_affinity',entry['affinity'],str(irqcpu),path='/proc/irq/'+str(irq)+'/smp_affinity_list',irq=int(irq),controller=u['controller'],description=entry['description'])
    for t in irqthreads:
     add('thread_affinity',t['affinity'],[irqcpu],tid=t['tid'],start=ticks(t['tid']),role='usb-irq',name=t['name'])
    output=next((t for p in h['squeezelite'] if 'squeezelite' in p['name'] for t in p['threads'] if t['tid']==d['owner_pid']),None)
    if output:add('thread_affinity',output['affinity'],[outcpu],tid=output['tid'],start=ticks(output['tid']),role='audio-output',name=output['name'])
  else:warnings.append('Independent physical cores / verified USB IRQ unavailable: affinity left unchanged')
 if profile=='low-activity':
  for p in h['squeezelite']:
   if p.get('scanner'):
    try:add('scanner_nice',os.getpriority(os.PRIO_PROCESS,p['pid']),19,tid=p['pid'],start=ticks(p['pid']),role='scanner',name=p['name'])
    except OSError:pass
  for pci in h['pci']:
   if pci['driver'] not in ['xhci_hcd','ehci-pci','ohci-pci','uhci_hcd'] or pci['address']==u.get('controller'):continue
   attached=[x for x in h['usb'] if x.get('controller')==pci['address'] and not x['path'].startswith('usb')]
   if not attached and pci['power'].get('control')=='on':add('empty_usb_pm','on','auto',path='/sys/devices/'+str((Path('/sys/bus/pci/devices')/pci['address']).resolve()).split('/sys/devices/',1)[-1]+'/power/control',controller=pci['address'],driver=pci['driver'])
 warnings+=['Real-time priorities and throttling unchanged','Network, SSH, LMS, player, discovery and unknown services retained','No C-state, clocksource, boot or blacklist changes']
 return changes,warnings

def service_value(name):
 p=subprocess.run(['rc-service',name,'status'],capture_output=True,text=True,timeout=5)
 return 'started' if p.returncode==0 else 'stopped'

def service_plan(h,names):
 result=[]
 for name in names:
  if name not in ['bluetooth','cupsd','crond']:raise ValueError('Service outside optional allowlist')
  if not (Path('/etc/init.d')/name).is_file():continue
  if name=='bluetooth' and list(Path('/sys/class/bluetooth').glob('hci*')):raise RuntimeError('Bluetooth adapter present; service retained')
  if service_value(name)=='started':result.append(dict(host_uuid=read('/sys/class/dmi/id/product_uuid'),parameter='optional_service',service=name,before='started',after='stopped',identity=h['dac']['identity'],classification='USER DECLARED UNUSED',reversible=True))
 return result

def guard(c,h,restoring=False):
 if not c.get('host_uuid') or c['host_uuid']!=read('/sys/class/dmi/id/product_uuid'):raise RuntimeError('Advanced profile belongs to different host')
 matches=[u for u in h.get('usb',[]) if all(u.get(k)==v for k,v in c['identity'].items())]
 if len(matches)!=1 or not __import__('usb_target').identified(c['identity']):raise RuntimeError('Original DAC absent/replaced')
 k=c['parameter'];path=c.get('path','')
 if k=='optional_service':
  if c['service'] not in ['bluetooth','cupsd','crond'] or (c['before'],c['after'])!=('started','stopped'):raise RuntimeError('Service outside allowlist')
  if not restoring and c.get('automatic_unused_check'):
   import resources
   resources.verify_services(c)
  if not restoring and c['service']=='bluetooth' and list(Path('/sys/class/bluetooth').glob('hci*')):raise RuntimeError('Bluetooth hardware appeared')
 elif k=='cpu_governor':
  if not re.fullmatch(r'/sys/devices/system/cpu/cpufreq/policy[0-9]+/scaling_governor',path) or c['after']!='performance':raise RuntimeError('Governor target outside allowlist')
  if c['before'] not in (read(str(Path(path).parent/'scaling_available_governors')) or '').split():raise RuntimeError('Original governor no longer supported')
 elif k=='empty_usb_pm':
  dev=Path('/sys/bus/pci/devices')/c['controller']
  if path!=str(dev.resolve())+'/power/control' or (c['before'],c['after'])!=('on','auto') or (dev/'driver').resolve().name!=c['driver']:raise RuntimeError('USB controller identity changed')
  if not restoring and any(x.get('controller')==c['controller'] and not x['path'].startswith('usb') for x in usb_devices()):raise RuntimeError('USB controller gained a device')
 elif k=='usb_irq_affinity':
  if path!='/proc/irq/'+str(c['irq'])+'/smp_affinity_list' or matches[0].get('controller')!=c['controller'] or str(matches[0].get('irq'))!=str(c['irq']):raise RuntimeError('IRQ changed')
  from detection import irq_map
  entry=next((x for x in irq_map() or [] if x['irq']==c['irq']),None)
  if not entry or entry['description']!=c['description']:raise RuntimeError('IRQ owner changed')
  if not numbers(c['after']) or any(x not in numbers(read('/sys/devices/system/cpu/online')) for x in numbers(c['after'])):raise RuntimeError('Invalid IRQ CPU set')
 elif k in ['thread_affinity','scanner_nice','thread_scheduler']:
  if ticks(c['tid'])!=c['start'] or not c['start']:raise RuntimeError('Thread exited or PID reused')
  name=read('/proc/'+str(c['tid'])+'/comm')
  if name!=c['name']:raise RuntimeError('Thread identity changed')
  if k=='thread_scheduler':
   if c.get('role')!='audio-output' or c['after']!={'policy':os.SCHED_FIFO,'priority':60}:raise RuntimeError('Scheduler action outside automatic output policy')
   for setting in [c['before'],c['after']]:
    if not isinstance(setting,dict) or set(setting)!={'policy','priority'} or any(type(v)!=int for v in setting.values()):raise RuntimeError('Invalid scheduler setting')
    if setting['policy'] not in [os.SCHED_OTHER,os.SCHED_BATCH,os.SCHED_IDLE,os.SCHED_FIFO,os.SCHED_RR]:raise RuntimeError('Unknown scheduling policy')
    if not os.sched_get_priority_min(setting['policy'])<=setting['priority']<=os.sched_get_priority_max(setting['policy']):raise RuntimeError('Scheduler priority outside kernel bounds')
   if not restoring:
    from detection import scan
    h=scan()
    if h['dac'].get('owner_pid')!=c['tid'] or h['dac'].get('identity')!=c['identity'] or h['kernel'].get('realtime')!='1':raise RuntimeError('Only the verified active RT audio output is eligible')
  elif k=='thread_affinity':
   if c['role'] not in ['usb-irq','audio-output','housekeeping'] or not c['after'] or any(x not in numbers(read('/sys/devices/system/cpu/online')) for x in c['after']):raise RuntimeError('Invalid thread target')
   if c['role']=='housekeeping':
    from detection import process_role
    pid=c.get('process_pid');proc=Path('/proc')/str(pid)
    if not (proc/'task'/str(c['tid'])).exists():raise RuntimeError('Housekeeping thread owner changed')
    exe=os.readlink(proc/'exe');argv=(proc/'cmdline').read_bytes().decode(errors='replace').split('\0')
    isolated=numbers(read('/sys/devices/system/cpu/isolated'))
    expected=sorted(set(numbers(read('/sys/devices/system/cpu/online')))-set(isolated)) if isolated else [0,1]
    if exe!=c.get('executable') or process_role(read(proc/'comm'),exe,argv)!=c.get('runtime_role') or c['after']!=expected:raise RuntimeError('Unverified housekeeping process or CPU allocation')
  elif c['role']!='scanner'  or c['after']!=19:raise RuntimeError('Invalid scanner priority')
 else:raise RuntimeError('Unsupported advanced action')

def value(c):
 if c['parameter']=='thread_scheduler':return {'policy':os.sched_getscheduler(c['tid']),'priority':os.sched_getparam(c['tid']).sched_priority}
 if c['parameter']=='optional_service':return service_value(c['service'])
 if c['parameter']=='thread_affinity':return sorted(os.sched_getaffinity(c['tid']))
 if c['parameter']=='scanner_nice':return os.getpriority(os.PRIO_PROCESS,c['tid'])
 return read(c['path'])

def irq_thread_side_effect(c,current,changes):
 """Accept only an IRQ thread following our preceding verified IRQ write."""
 if c.get('parameter')!='thread_affinity' or c.get('role')!='usb-irq' or current!=c.get('after'):return False
 owners=[x for x in changes if x.get('parameter')=='usb_irq_affinity' and x.get('write_attempted') and x.get('identity')==c.get('identity') and x.get('host_uuid')==c.get('host_uuid') and numbers(x.get('after'))==c.get('after') and (c.get('name') or '').startswith('irq/'+str(x.get('irq'))+'-')]
 if len(owners)!=1:return False
 owner=owners[0];effective=set(numbers(read('/proc/irq/'+str(owner['irq'])+'/effective_affinity_list')))
 return numbers(value(owner))==numbers(owner['after']) and bool(effective) and effective<=set(c['after'])

def verify_irq(c,v):
 """Pending IRQ migration may complete only after the next interrupt."""
 desired=set(numbers(v))
 if not desired:raise RuntimeError('Empty IRQ affinity cannot be verified')
 deadline=time.monotonic()+2;stable=0
 while True:
  actual=value(c);effective=read('/proc/irq/'+str(c['irq'])+'/effective_affinity_list');cpus=set(numbers(effective))
  stable=stable+1 if set(numbers(actual))==desired and cpus and cpus<=desired else 0
  if stable>=2:return
  if time.monotonic()>=deadline:raise RuntimeError('Effective IRQ affinity not verified within2seconds: requested='+str(actual)+' effective='+str(effective))
  time.sleep(.02)

def write(c,v):
 guard(c,{'usb':usb_devices()},v!=c['after'])
 if c['parameter']=='optional_service':
  if v not in ['started','stopped']:raise ValueError('Invalid service state')
  subprocess.run(['rc-service','--nodeps',c['service'],'start' if v=='started' else 'stop'],capture_output=True,check=True,timeout=15)
 elif c['parameter']=='thread_scheduler':os.sched_setscheduler(c['tid'],v['policy'],os.sched_param(v['priority']))
 elif c['parameter']=='thread_affinity':os.sched_setaffinity(c['tid'],v)
 elif c['parameter']=='scanner_nice':os.setpriority(os.PRIO_PROCESS,c['tid'],v)
 else:
  fd=os.open(c['path'],os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
  try:
   guard(c,{'usb':usb_devices()},v!=c['after']);os.write(fd,(str(v)+chr(10)).encode())
  finally:os.close(fd)
 actual=value(c)
 if c['parameter']=='usb_irq_affinity':
  verify_irq(c,v)
 elif actual!=v:raise RuntimeError('Setting not retained')

__release_signature__ = 'Tino Zappala'
