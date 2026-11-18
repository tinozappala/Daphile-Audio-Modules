"""Hardware-bound reversible adapters for measured search; no shell strings."""
import os,re,subprocess,time
from pathlib import Path
from detection import read,command,numbers,usb_devices
import physical_audio

PARAMETERS={'audio_schedule_search','governor_search','frequency_limits','network_features','network_coalesce','peripheral_pm'}
NETFLAGS={'tcp-segmentation-offload':'tso','generic-segmentation-offload':'gso','generic-receive-offload':'gro','rx-checksumming':'rx','tx-checksumming':'tx'}

def net_identity(name):
 from network_profiles import nic_identity
 return nic_identity(name)

def features(name):
 text=command(['ethtool','-k',name])
 if text is None:return None
 rows={}
 for line in text.splitlines():
  m=re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]{0,79}): (on|off)(.*)',line.strip())
  # Parent features can alter subordinate flags. Capture all mutable flags so
  # readback and rollback also cover those driver side effects.
  if m and '[fixed]' not in m[3]:rows[m[1]]=m[2]
 return rows

def coalesce(name):
 text=command(['ethtool','-c',name])
 if text is None:return None
 rows={}
 for line in text.splitlines():
  m=re.fullmatch(r'(rx-usecs|tx-usecs):\s*(\d+)',line.strip())
  if m:rows[m[1]]=int(m[2])
 return rows or None

def network_inventory():
 from network_profiles import route_interface
 name=route_interface()
 if not name:return {'status':'unsupported','reason':'Default route absent or ambiguous'}
 try:return {'status':'available','identity':net_identity(name),'features':features(name),'coalesce':coalesce(name)}
 except RuntimeError as e:return {'status':'unsupported','reason':str(e)}

def pci_unused(address):
 """Unknown devices stay protected. No unbind, remove or driver unload."""
 p=Path('/sys/bus/pci/devices')/address
 cls=read(p/'class');driver=(p/'driver').resolve().name if (p/'driver').exists() else None
 protected={'storage','network','display','bridge','USB','unknown'}
 major=(cls or '')[2:4]
 kind={'01':'storage','02':'network','03':'display','06':'bridge','0c':'USB','04':'audio'}.get(major,'unknown')
 if kind in protected:return {'eligible':False,'reason':'Required or uncertain '+kind,'driver':driver,'class':cls}
 if kind=='audio':
  cards=[c for c in Path('/sys/class/sound').glob('card[0-9]*') if p.resolve() in (c/'device').resolve().parents or (c/'device').resolve()==p.resolve()]
  if not cards:return {'eligible':False,'reason':'Audio device ownership unknown','driver':driver,'class':cls}
  for c in cards:
   number=c.name[4:]
   for status in Path('/proc/asound').glob('card'+number+'/pcm*/sub*/status'):
    if (read(status) or '').strip()!='closed':return {'eligible':False,'reason':'Audio device open or unobservable','driver':driver,'class':cls}
  return {'eligible':bool(driver),'reason':'Secondary audio devices closed','driver':driver,'class':cls}
 return {'eligible':False,'reason':'Not independently proven unused','driver':driver,'class':cls}

def peripheral_inventory():
 result=[]
 for p in sorted(Path('/sys/bus/pci/devices').glob('*')):
  row=pci_unused(p.name);row.update(address=p.name,path=str(p.resolve())+'/power/control',control=read(p/'power/control'),runtime_status=read(p/'power/runtime_status'))
  row['actual_suspended']=row['runtime_status']=='suspended'
  result.append(row)
 return result

def change(h,parameter,before,after,**kwargs):
 if before==after:return None
 return dict(parameter=parameter,before=before,after=after,host_uuid=h['system']['product_uuid'],identity=h['dac']['identity'],classification='MEASURED TRIAL',reversible=True,**kwargs)

def guard(c,h,restoring=False):
 if c.get('host_uuid')!=__import__('host_identity').current(read) or not c.get('host_uuid'):raise RuntimeError('Extended policy belongs to another host')
 ident=c.get('identity',{})
 if len(physical_audio.matches(ident,h))!=1:raise RuntimeError('Original physical ALSA output absent or replaced')
 k=c['parameter']
 if k=='audio_schedule_search':
  import engine
  if not c.get('start') or engine.start_ticks(c['tid'])!=c['start'] or read('/proc/'+str(c['tid'])+'/comm')!=c['name']:raise RuntimeError('Audio thread exited or changed')
  after=c['after']
  if set(after)!={'policy','priority'} or after['policy'] not in [os.SCHED_FIFO,os.SCHED_RR] or type(after['priority'])!=int or not 40<=after['priority']<=70:raise RuntimeError('Audio scheduler candidate outside measured bounds')
  if not restoring:
   from detection import live_scan as scan
   active=scan()
   if active['dac'].get('owner_pid')!=c['tid'] or active['dac'].get('identity')!=ident or active['kernel'].get('realtime')!='1':raise RuntimeError('Unverified RT audio thread')
 elif k in ['governor_search','frequency_limits']:
  p=Path(c['policy'])
  if not re.fullmatch(r'/sys/devices/system/cpu/cpufreq/policy\d+',str(p)) or read(p/'scaling_driver')!=c['driver']:raise RuntimeError('CPU policy changed')
  if k=='governor_search':
   if any(v not in (read(p/'scaling_available_governors') or '').split() for v in [c['before'],c['after']]):raise RuntimeError('Governor no longer advertised')
  else:
   lo,hi=int(read(p/'cpuinfo_min_freq')),int(read(p/'cpuinfo_max_freq'))
   for v in [c['before'],c['after']]:
    if set(v)!={'min','max'} or any(type(n)!=int for n in v.values()) or not lo<=v['min']<=v['max']<=hi:raise RuntimeError('Frequency bounds outside hardware limits')
   available={int(v) for v in (read(p/'scaling_available_frequencies') or '').split() if v.isdigit()}
   if not restoring and (not available or any(v not in available for v in c['after'].values())):raise RuntimeError('Fixed frequency is not explicitly advertised by this driver')
 elif k in ['network_features','network_coalesce']:
  if net_identity(c['nic']['name'])!=c['nic']:raise RuntimeError('NIC identity changed')
  if not restoring:
   from network_profiles import route_interface
   if route_interface()!=c['nic']['name'] or read('/sys/class/net/'+c['nic']['name']+'/carrier')!='1':raise RuntimeError('Route/carrier changed')
  if set(c['before'])!=set(c['after']) or not c['before']:raise RuntimeError('Network snapshot incomplete')
  if k=='network_features':
   advertised=features(c['nic']['name'])
   if advertised is None or set(advertised)!=set(c['before']):raise RuntimeError('Mutable feature inventory changed')
   if any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}',key) or val not in ['on','off'] for v in [c['before'],c['after']] for key,val in v.items()):raise RuntimeError('Invalid feature snapshot')
   if any(key not in NETFLAGS and c['before'][key]!=c['after'][key] for key in c['before']):raise RuntimeError('Candidate feature outside allowlist')
  elif any(key not in ['rx-usecs','tx-usecs'] or type(val)!=int or not 0<=val<=1000 for v in [c['before'],c['after']] for key,val in v.items()):raise RuntimeError('Coalesce outside bounded trial')
 elif k=='peripheral_pm':
  p=Path('/sys/bus/pci/devices')/c['address'];row=pci_unused(c['address'])
  if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]',c['address']) or c['path']!=str(p.resolve())+'/power/control' or row['driver']!=c['driver'] or row['class']!=c['pci_class']:raise RuntimeError('Peripheral identity changed')
  if (c['before'],c['after'])!=('on','auto'):raise RuntimeError('Invalid peripheral policy')
  if not restoring and not row['eligible']:raise RuntimeError('Peripheral became required')
 else:raise RuntimeError('Unknown extended adapter')

def value(c):
 k=c['parameter']
 if k=='audio_schedule_search':return {'policy':os.sched_getscheduler(c['tid']),'priority':os.sched_getparam(c['tid']).sched_priority}
 if k=='governor_search':return read(Path(c['policy'])/'scaling_governor')
 if k=='frequency_limits':return {'min':int(read(Path(c['policy'])/'scaling_min_freq')),'max':int(read(Path(c['policy'])/'scaling_max_freq'))}
 if k=='network_features':return features(c['nic']['name'])
 if k=='network_coalesce':return coalesce(c['nic']['name'])
 return read(c['path'])

def set_value(c,v):
 k=c['parameter']
 if k=='audio_schedule_search':os.sched_setscheduler(c['tid'],v['policy'],os.sched_param(v['priority']))
 elif k in ['network_features','network_coalesce']:
  flag='-K' if k=='network_features' else '-C';args=['ethtool',flag,c['nic']['name']]
  for key,val in v.items():args += [key,str(val)]
  subprocess.run(args,capture_output=True,check=True,timeout=8)
 elif k=='frequency_limits':
  set_frequency_limits(c,v)
 else:
  path=str(Path(c['policy'])/'scaling_governor') if k=='governor_search' else c['path']
  fd=os.open(path,os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
  try:os.write(fd,(str(v)+'\n').encode())
  finally:os.close(fd)

def settled_value(c,expected):
 if c['parameter']!='frequency_limits':return value(c)==expected
 # acpi-cpufreq policy updates can be asynchronous. Require two matching
 # readbacks within a bounded interval; never accept a different limit.
 deadline=time.monotonic()+3;stable=0
 while time.monotonic()<deadline:
  stable=stable+1 if value(c)==expected else 0
  if stable>=2:return True
  time.sleep(.02)
 return False

def set_frequency_limits(c,desired):
 current=value(c);p=Path(c['policy'])
 # Touch only changed bounds. Raise max first only if the requested minimum
 # lies above the current maximum; otherwise lower/update min first.
 order=['max','min'] if desired['min']>current['max'] else ['min','max']
 for key in order:
  if value(c)[key]!=desired[key]:
   (p/('scaling_'+key+'_freq')).write_text(str(desired[key])+'\n')
   deadline=time.monotonic()+3;stable=0
   while time.monotonic()<deadline:
    stable=stable+1 if value(c)[key]==desired[key] else 0
    if stable>=2:break
    time.sleep(.02)
   if stable<2:raise RuntimeError('CPU frequency bound did not settle: '+key)

def write(c,v):
 if v not in [c['before'],c['after']]:raise ValueError('Value outside transaction')
 guard(c,{'usb':usb_devices()},restoring=v==c['before'])
 initial=value(c)
 try:
  set_value(c,v)
  if not settled_value(c,v):raise RuntimeError('Extended policy readback mismatch')
 except BaseException:
  # Multi-field driver writes may partially succeed. Restore before propagating.
  set_value(c,initial)
  if not settled_value(c,initial):raise RuntimeError('Partial extended write recovery incomplete')
  raise

__release_signature__ = 'Tino Zappala'
