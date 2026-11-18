"""Strictly allowlisted hardware-bound transactions, inactive at boot."""
import contextlib,fcntl,json,os,re,signal,subprocess,sys,time,uuid
from pathlib import Path
from detection import read,live_scan as scan,usb_devices
def observe(seconds):
 from measure import observe as instrumented_observe
 return instrumented_observe(seconds)
PROFILES=['default','safe','audio-rt','low-activity','t630-fixed','network-stable','isolated-audio','experimental']
STATE=Path('/mnt/hdd/daphile-autotune')
def atomic(path,value):
 path=Path(path);tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
 with tmp.open('x') as out:
  os.chmod(tmp,0o600);json.dump(value,out,indent=2);out.write(chr(10));out.flush();os.fsync(out.fileno())
 os.replace(tmp,path);fd=os.open(path.parent,os.O_DIRECTORY)
 try:os.fsync(fd)
 finally:os.close(fd)
def load(path):return json.loads(Path(path).read_text())
@contextlib.contextmanager
def locked(state=STATE):
 state=Path(state)
 if state.is_symlink() or not state.parent.is_dir():raise RuntimeError('Unsafe or absent persistent state directory')
 state.mkdir(mode=0o700,exist_ok=True)
 if state.stat().st_uid!=os.geteuid() or state.stat().st_mode&0o077:raise RuntimeError('State must be private to its owner')
 if any(p.is_symlink() for p in state.iterdir()):raise RuntimeError('State contains symlinks')
 with (state/'lock').open('a') as f:
  os.chmod(state/'lock',0o600);fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);yield state
def log(state,event):
 with (state/'changes.jsonl').open('a') as out:
  os.chmod(out.name,0o600);out.write(json.dumps(dict(time=time.time(),**event))+chr(10));out.flush();os.fsync(out.fileno())
def recommend(hardware,profile='safe'):
 if profile not in PROFILES:raise ValueError('Unknown profile')
 d=hardware['dac'];changes=[];warnings=[]
 if d['status']!='verified-active':warnings.append('Selected DAC unknown or ambiguous: optimization refused')
 elif d.get('usb'):
  import physical_audio
  u=d['usb']
  identified=len(physical_audio.usb_matches(d.get('identity'),hardware))==1
  if not identified:warnings.append('Live USB connection identity unavailable: USB power change deferred')
  if identified and u['power'].get('control')=='auto':changes.append(dict(parameter='dac_usb_runtime_pm',path=u['sysfs']+'/power/control',before='auto',after='on',device=u['path'],identity=dict(d['identity']),classification='SAFE',evidence='ENGINEERING ASSUMPTION',reason='Prevent runtime suspend on verified active DAC only',reversible=True))
 if profile=='default':changes=[]
 if profile in ['audio-rt','low-activity'] and d.get('status')=='verified-active':
  from advanced import plan
  extra,notes=plan(hardware,profile);changes+=extra;warnings+=notes
 if profile=='t630-fixed':
  from t630 import plan as t630_plan
  extra,notes=t630_plan(hardware);changes+=extra;warnings+=notes
 if profile=='network-stable':
  from network_profiles import plan as network_plan
  extra,notes=network_plan(hardware);changes+=extra;warnings+=notes
 if profile=='isolated-audio':
  from power_policy import isolated_affinity
  extra,notes=isolated_affinity(hardware);changes+=extra;warnings+=notes
 if profile=='experimental':warnings.append('Use explicit power trial and guarded boot-profile commands for experimental policies')
 return dict(profile=profile,hardware_id=hardware['hardware_id'],changes=changes,warnings=warnings,unsupported=['Unknown services remain enabled; optional services require explicit declaration','Missing samples and unsupported audio transports remain unvalidated'])
def guard(change,hardware,restoring=False):
 if change.get('parameter')=='sata_link_pm':
  from storage_policy import guard as storage_guard
  return storage_guard(change,hardware,restoring)

 import runtime_ext
 if change["parameter"] in runtime_ext.PARAMETERS:return runtime_ext.guard(change,hardware,restoring)
 if change['parameter'] in ['cstate_disable','clocksource']:
  from power_policy import guard as power_guard
  return power_guard(change,hardware,restoring)
 if change['parameter']=='network_eee':
  from network_profiles import guard as network_guard
  return network_guard(change,hardware,restoring)
 if change['parameter']=='player_buffer':
  from player import guard as player_guard
  return player_guard(change,hardware,restoring)
 if change['parameter']!='dac_usb_runtime_pm':
  from advanced import guard as advanced_guard
  return advanced_guard(change,hardware,restoring)
 import physical_audio
 matches=physical_audio.usb_matches(change.get('identity'),hardware)
 if len(matches)!=1:raise RuntimeError('Original live USB connection absent or changed; no stale path written')
 if restoring:
  hardware=dict(hardware,dac=dict(status='verified-active',identity=change['identity'],usb=matches[0]))
 d=hardware['dac'];u=d.get('usb') or {}
 if not physical_audio.is_usb(change['identity']):raise RuntimeError('Verified live USB connection identity required')
 if d.get('status')!='verified-active' or d.get('identity')!=change['identity']:raise RuntimeError('DAC identity changed')
 expected=u.get('sysfs','')+'/power/control'
 if change['path']!=expected or not expected.startswith('/sys/devices/') or '/../' in expected:raise RuntimeError('Target is not verified DAC power/control')
 if change['parameter']!='dac_usb_runtime_pm' or change['before']!='auto' or change['after']!='on':raise RuntimeError('Change outside allowlist')
def setting_value(change):
 if change.get('parameter')=='sata_link_pm':
  from storage_policy import value
  return value(change)

 import runtime_ext
 if change["parameter"] in runtime_ext.PARAMETERS:return runtime_ext.value(change)
 if change['parameter'] in ['cstate_disable','clocksource']:
  from power_policy import value
  return value(change)
 if change['parameter']=='network_eee':
  from network_profiles import value
  return value(change)
 if change['parameter']=='player_buffer':
  from player import current
  return current(change)
 if change['parameter']=='dac_usb_runtime_pm':return read(change['path'])
 from advanced import value
 return value(change)

def write_control(change,value):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 if change.get('parameter')=='sata_link_pm':
  from storage_policy import write
  return write(change,value)

 import runtime_ext
 if change["parameter"] in runtime_ext.PARAMETERS:return runtime_ext.write(change,value)
 if change['parameter'] in ['cstate_disable','clocksource']:
  from power_policy import write
  return write(change,value)
 if change['parameter']=='network_eee':
  from network_profiles import write
  return write(change,value)
 if change['parameter']=='player_buffer':
  from player import write
  return write(change,value)
 if change['parameter']!='dac_usb_runtime_pm':
  from advanced import write
  return write(change,value)
 if value not in ['auto','on']:raise ValueError('Invalid power policy')
 fd=os.open(change['path'],os.O_RDWR|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  guard(change,{'dac':{'status':'unknown'},'usb':usb_devices()},restoring=True)
  os.write(fd,(value+chr(10)).encode());os.lseek(fd,0,os.SEEK_SET)
  if os.read(fd,64).decode().strip()!=value:raise RuntimeError('Kernel did not retain setting')
 finally:os.close(fd)
def rollback(state,journal,hardware=None):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 hardware=hardware or {'dac':{'status':'unknown'},'usb':usb_devices()};failed=[]
 from rollback_order import ordered,owned_irq_restore_transition,same_value
 from detection import numbers
 for change in ordered(journal['changes']):
  if not change.get('write_attempted'):continue
  if change['parameter'] in ['thread_affinity','scanner_nice','thread_scheduler','audio_schedule_search'] and journal.get('boot_id') not in [None,boot_id()]:
   log(state,dict(event='original-thread-belongs-to-earlier-boot',tid=change['tid']));continue
  try:
   if change['parameter'] in ['thread_affinity','scanner_nice','thread_scheduler','audio_schedule_search'] and start_ticks(change['tid'])!=change.get('start'):
    log(state,dict(event='original-thread-no-longer-exists',tid=change['tid']));continue
   guard(change,hardware,restoring=True);current=setting_value(change)
   if change['parameter']=='usb_irq_affinity' and change.get('effective_before') and (current in (change['before'],change['after']) or current in change.get('accepted_intermediate',[])):
    from irq_route_restore import restore as restore_irq_route
    change['rollback_irq_hint_attempted']=True
    hint=','.join(str(n) for n in sorted(set(numbers(change['effective_before']))))
    if hint not in change.setdefault('accepted_intermediate',[]):change['accepted_intermediate'].append(hint)
    atomic(state/'transaction.json',journal)
    restore_irq_route(change,write_control,setting_value,read,numbers);change['rollback_irq_write_verified']=True
   elif current==change['after'] or current in change.get('accepted_intermediate',[]):write_control(change,change['before'])
   elif current!=change['before']:
    if owned_irq_restore_transition(change,current,journal['changes'],setting_value,read,numbers):write_control(change,change['before'])
    else:raise RuntimeError('Concurrent setting change')
   elif change['parameter']=='usb_irq_affinity':
    from advanced import verify_irq
    verify_irq(change,change['before'])
   if change['parameter']=='usb_irq_affinity' and (current!=change['before'] or change.get('rollback_irq_write_verified')):change['rollback_irq_write_verified']=True
  except (OSError,RuntimeError,ValueError) as e:failed.append(str(e))
 # Coupled writes can invalidate a value restored earlier in the loop.
 # A success label requires a SECOND read of every surviving owned control.
 for change in journal['changes']:
  if not change.get('write_attempted'):continue
  if change.get('tid') and (journal.get('boot_id') not in (None,boot_id()) or start_ticks(change['tid'])!=change.get('start')):continue
  try:
   guard(change,hardware,restoring=True)
   if not same_value(change,setting_value(change),change['before'],numbers):raise RuntimeError('Final rollback readback differs: '+change['parameter'])
   if change['parameter']=='usb_irq_affinity' and change.get('effective_before') and set(numbers(read('/proc/irq/'+str(change['irq'])+'/effective_affinity_list')))!=set(numbers(change['effective_before'])):raise RuntimeError('Final original IRQ delivery differs')
  except (OSError,RuntimeError,ValueError) as e:failed.append(str(e))
 journal['parameter_restoration_complete']=not failed
 if journal.get('playback_resume'):
  journal['playback_recovery']={'status':'legacy-playback-refused','restored':False,'bookmark_retained':True,'player_commands':0}
  failed.append('Legacy playback recovery unavailable; saved playlist retained in private journal')
 journal['status']='rollback-incomplete' if failed else 'rolled-back';journal['rollback_errors']=failed
 atomic(state/'transaction.json',journal);log(state,dict(event=journal['status'],id=journal['id'],errors=failed))
 if not failed:
  if journal.get('previous_profile'):atomic(state/'current.json',journal['previous_profile'])
  else:
   with contextlib.suppress(FileNotFoundError):(state/'current.json').unlink()
 return journal
def health(hardware):
 d=hardware['dac'];checks=dict(selected_pcm=d['status']=='verified-active',running_pcm='RUNNING' in (d.get('pcm',{}).get('status') or ''),player=any('squeezelite' in x['name'] for x in hardware['squeezelite']))
 import urllib.request
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for key,url in [('web','http://127.0.0.1/'),('lms','http://127.0.0.1:9000/')]:
  try:
   with opener.open(url,timeout=3) as response:checks[key]=response.status==200
  except Exception:checks[key]=False
 try:
  from player import rpc,target
  selected=target(hardware);checks['lms_playback']=rpc(selected['mac'],['status','-','1']).get('mode')=='play'
 except Exception:checks['lms_playback']=False
 temperatures=[int(x['millidegrees']) for x in hardware.get('thermal',[]) if (x.get('millidegrees') or '').isdigit()]
 if temperatures:checks['temperature_below_conservative_85C']=max(temperatures)<85000
 return checks

from measurement_contract import compare
def start_ticks(pid):
 data=read('/proc/'+str(pid)+'/stat')
 if not data:return None
 fields=data[data.rfind(')')+2:].split()
 return fields[19] if len(fields)>19 and fields[0] not in ['Z','X'] else None
def tune(profile,seconds=30,state=STATE,confirmed=False,services=()):
 if os.geteuid()!=0:raise RuntimeError('Tuning requires root')
 if not 30<=seconds<=3000:raise ValueError('Observation duration 30..3000 seconds')
 if profile=='experimental' and not confirmed:raise RuntimeError('Explicit --confirm-experimental required')
 if profile=='experimental':
  import search
  return search.run(seconds=seconds,state=state,confirmed=confirmed,commit=True)
 with locked(state) as state:
  ensure_session_owner(state)
  pending=state/'transaction.json'
  if pending.exists() and load(pending)['status'] in ['pending','rollback-incomplete']:raise RuntimeError('Run dat restore pending first')
  if profile!='default' and (state/'current.json').exists() and load(state/'current.json').get('status')=='committed':raise RuntimeError('Restore previous profile before a new trial')
  h=scan();plan=recommend(h,profile)
  if profile=='default':
   return rollback(state,load(state/'current.json'),h) if (state/'current.json').exists() else dict(status='no-change',reason='No AutoTune transaction active')
  if h['dac']['status']!='verified-active':raise RuntimeError('Selected DAC not verified')
  if services:
   if profile!='low-activity':raise ValueError('Optional services require low-activity profile')
   from advanced import service_plan
   plan['changes']+=service_plan(h,services)
  if not plan['changes']:return dict(status='no-change',plan=plan)
  checks=health(h)
  if not all(checks.values()):raise RuntimeError('Preflight health incomplete: '+str(checks))
  network_before=None
  if profile=='network-stable':
   from network_test import audit as network_audit
   network_before=network_audit()
   if network_before['status']!='pass':raise RuntimeError('NAS baseline incomplete; network trial refused')
  original_id=h['hardware_id'];before=observe(seconds);h=scan()
  if h['hardware_id']!=original_id:raise RuntimeError('Hardware changed during baseline')
  plan=recommend(h,profile)
  if services:plan['changes']+=service_plan(h,services)
  if not plan['changes']:return dict(status='no-change',plan=plan)
  if compare(before,before):raise RuntimeError('Baseline incomplete or unstable')
  stamp='BASELINE-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8];baseline=dict(hardware=h,measurement=before,plan=plan,health=checks)
  atomic(state/(stamp+'.json'),baseline)
  if not (state/'baseline.json').exists():atomic(state/'baseline.json',baseline)
  if not (state/'factory-default.json').exists():atomic(state/'factory-default.json',dict(meaning='captured pre-AutoTune custom Daphile state',baseline=baseline))
  journal=dict(id=stamp,hardware_id=h['hardware_id'],changes=plan['changes'],status='pending',deadline=time.time()+seconds+90,parent_pid=os.getpid(),parent_start=start_ticks(os.getpid()),boot_id=boot_id(),previous_profile=load(state/'current.json') if (state/'current.json').exists() else None)
  journal['session_owner']=session_binding(state)
  atomic(pending,journal)
  watcher=spawn_watchdog(state,journal)
  try:
   for c in journal['changes']:
    fresh=scan()
    if fresh['hardware_id']!=journal['hardware_id']:raise RuntimeError('Hardware changed since baseline')
    guard(c,fresh)
    if setting_value(c)!=c['before']:
     from advanced import irq_thread_side_effect
     if not irq_thread_side_effect(c,setting_value(c),journal['changes']):raise RuntimeError('Setting changed concurrently')
     log(state,dict(event='irq-thread-followed-owned-affinity',tid=c['tid']))
    c['write_attempted']=True;atomic(pending,journal)
    write_control(c,c['after']);log(state,dict(event='applied',**c))
   after=observe(seconds);current=scan();checks=health(current);reasons=compare(before,after)
   if profile=='network-stable':
    network_after=network_audit();journal['network_validation']={'before':network_before,'after':network_after}
    if network_after['status']!='pass':reasons.append('NAS postflight failed or incomplete')
   if current['hardware_id']!=journal['hardware_id']:reasons.append('Hardware changed')
   if not all(checks.values()):reasons.append('Postflight health incomplete')
   if current['dac'].get('pcm',{}).get('hw')!=h['dac'].get('pcm',{}).get('hw'):reasons.append('Audio format changed')
   if current['storage']['mounts']!=h['storage']['mounts']:reasons.append('Mounts changed')
   for c in journal['changes']:
    guard(c,current)
    if setting_value(c)!=c['after']:reasons.append('Setting overridden by another policy: '+c['parameter'])
   if reasons:raise RuntimeError('; '.join(reasons))
   journal.update(status='committed',profile=profile,requested_services=list(services),benchmark=dict(before=before,after=after),validation=dict(status='validated-observation-window-only',health=checks,full_format_stress_test=False))
   atomic(pending,journal);atomic(state/'current.json',journal);atomic(state/'last-known-good.json',journal);log(state,dict(event='committed',id=stamp));return journal
  except BaseException:rollback(state,journal);raise
  finally:
   close_watchdog(watcher,journal)
def restore(which,state=STATE,confirmed=False):
 if os.geteuid()!=0:raise RuntimeError('Restore requires root')
 if which=='pending' and (Path(state)/'activity-session.json').exists():
  import session
  lease=load(Path(state)/'activity-session.json')
  if lease.get('status') in ['running','recovering','recovery-required']:
   if session.alive(lease):raise RuntimeError('Cancel the active audio job before recovering its session')
   session.recover(state,lease)
   return {'status':'recovered','session':load(Path(state)/'activity-session.json').get('playback_recovery')}
 if which=='last-known-good':
  with locked(state) as directory:saved=load(directory/'last-known-good.json')
  if saved.get('status')!='committed':raise RuntimeError('Last known good is not a committed observation')
  if scan()['hardware_id']!=saved['hardware_id']:raise RuntimeError('Last known good belongs to different hardware')
  if saved['profile']=='player-buffer':
   if not confirmed:raise RuntimeError('Last-known-good buffer requires --confirm-audio-test')
   from player import restore_good_buffer
   return restore_good_buffer(saved,state)
  if saved['profile']=='automatic':
   if not confirmed:raise RuntimeError('Automatic last-known-good may restart audio; acknowledgement required')
   from automatic import restore_good
   return restore_good(saved,state)
  return tune(saved['profile'],state=state,services=saved.get('requested_services',[]))
 with locked(state) as state:
  if which=='pending':return rollback(state,load(state/'transaction.json'))
  if which not in ['previous','baseline','default']:raise ValueError('Unknown restore target')
  # Runtime-only changes have one captured original; never substitute guessed factory settings.
  pending=state/'transaction.json'
  if pending.exists() and load(pending).get('status') in ['pending','rollback-incomplete']:
   result=rollback(state,load(pending))
   if result['status']!='rolled-back' or not (state/'current.json').exists():return result
  return rollback(state,load(state/'current.json')) if (state/'current.json').exists() else dict(status='no-change',reason='No AutoTune transaction active')
def boot_id():
 return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def close_recovery_guard(watcher,recovered):
 if not recovered:return False
 with contextlib.suppress(ProcessLookupError):watcher.terminate()
 try:watcher.wait(timeout=3)
 except subprocess.TimeoutExpired:watcher.kill();watcher.wait()
 return True

def close_watchdog(watcher,journal):
 # Failed restoration is an explicit recovery handoff, not a success that
 # disarms the independent process. Its retry loop remains bounded.
 return close_recovery_guard(watcher,journal.get('status') in ['committed','rolled-back'])

def ready_path(state,transaction):
 if not re.fullmatch(r'[A-Za-z0-9-]{1,128}',transaction):raise RuntimeError('Invalid watchdog transaction identifier')
 return Path(state)/('watchdog-ready-'+transaction+'.json')

def watchdog(state,transaction):
 ready=ready_path(state,transaction)
 record=load(Path(state)/'transaction.json')
 if record['id']!=transaction or record['status'] not in ['pending','rollback-incomplete']:return
 atomic(ready,dict(transaction=transaction,pid=os.getpid(),start=start_ticks(os.getpid()),boot_id=boot_id()))
 try:
  while True:
   record=load(Path(state)/'transaction.json')
   if record['id']!=transaction or record['status'] not in ['pending','rollback-incomplete']:return
   delay=record['deadline']-time.time()
   changed_boot=record.get('boot_id') is not None and record['boot_id']!=boot_id()
   handed_off=record['status']=='rollback-incomplete'
   dead=changed_boot or start_ticks(record.get('parent_pid'))!=record.get('parent_start')
   if delay<=0 or dead or handed_off:break
   time.sleep(min(.5,delay))
  pid=record.get('parent_pid')
  if not changed_boot and not handed_off and pid and start_ticks(pid)==record.get('parent_start'):
   with contextlib.suppress(ProcessLookupError):__import__('process_guard').signal_record(record,signal.SIGTERM,'parent_pid','parent_start')
  for attempt in range(10):
   try:
    with locked(state) as directory:
     fresh=load(directory/'transaction.json')
     if fresh['id']!=transaction or fresh['status'] not in ['pending','rollback-incomplete']:return
     result=rollback(directory,fresh)
     if result['status']=='rolled-back':return
    time.sleep(1)
   except BlockingIOError:time.sleep(1)
  raise RuntimeError('Recovery lock unavailable; run dat restore pending')
 finally:ready.unlink(missing_ok=True)


def ensure_idle_transaction(state):
 ensure_session_owner(state)
 for name in ['transaction.json','current.json']:
  p=state/name
  if p.exists() and load(p).get('status') in ['pending','rollback-incomplete','committed']:
   raise RuntimeError('Restore active/pending AutoTune transaction first')

def ensure_session_owner(state):
 lease=Path(state)/'activity-session.json'
 if lease.exists():
  r=load(lease)
  if r.get('status')=='recovery-required':raise RuntimeError('Audio session recovery incomplete; recover the saved session before changing settings')
  if r.get('status') in ['running','recovering'] and (r.get('owner')!=os.getpid() or r.get('start')!=start_ticks(os.getpid()) or r.get('boot_id')!=boot_id()):raise RuntimeError('An independent audio session owns changes; await recovery/completion')
 path=Path(state)/'ab-session.json'
 if path.exists():
  record=load(path)
  if record.get('status')=='recovery-incomplete':raise RuntimeError('A/B recovery incomplete; recover before changing settings')
  if record.get('status') in ['running','recovering'] and (record.get('owner_pid')!=os.getpid() or record.get('owner_start')!=start_ticks(os.getpid()) or record.get('boot_id')!=boot_id()):
   raise RuntimeError('A/B session owns audio changes; await its recovery or completion')

def session_binding(state):
 """Bind writes to an authenticated live owner, never infer journal ownership."""
 for filename,kind,pidkey,startkey in [('activity-session.json','activity','owner','start'),('ab-session.json','ab','owner_pid','owner_start'),('automatic.json','automatic','owner_pid','owner_start')]:
  p=Path(state)/filename
  if not p.exists():continue
  r=load(p)
  if r.get('status') not in ['running','recovering']:continue
  if r.get(pidkey)!=os.getpid() or r.get(startkey)!=start_ticks(os.getpid()) or r.get('boot_id')!=boot_id():raise RuntimeError('Another live session owns this transaction')
  return {'kind':kind,'id':r['id']}
 return None

def recover_owned_transaction(state,owner,include_committed=True):
 """Caller holds the state lock; legacy committed profiles remain untouched."""
 p=Path(state)/'transaction.json'
 if not p.exists():return {'status':'no-change'}
 j=load(p)
 if j.get('status') not in ['pending','rollback-incomplete','committed']:return {'status':'no-change'}
 if j.get('status')=='committed' and not include_committed:return {'status':'no-change','reason':'Successful committed profile retained'}
 if owner and j.get('session_owner')==owner:return rollback(Path(state),j)
 if j.get('status') in ['pending','rollback-incomplete']:raise RuntimeError('Unowned pending transaction: explicit recovery required; no settings replayed')
 return {'status':'no-change','reason':'Preexisting committed profile preserved'}

def begin_journal(state,h,changes,timeout,profile,*,previous_profile=None):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 record=dict(id=uuid.uuid4().hex,hardware_id=h['hardware_id'],profile=profile,changes=changes,status='pending',deadline=time.time()+timeout,parent_pid=os.getpid(),parent_start=start_ticks(os.getpid()),boot_id=boot_id(),previous_profile=previous_profile)
 record['session_owner']=session_binding(state)
 atomic(state/'transaction.json',record);return record

def spawn_watchdog(state,record):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 current_boot=boot_id()
 if record.get('boot_id',current_boot)!=current_boot:raise RuntimeError('Transaction belongs to earlier boot')
 record['boot_id']=current_boot;atomic(Path(state)/'transaction.json',record)
 ready=ready_path(state,record['id'])
 if ready.exists() or ready.is_symlink():raise RuntimeError('Watchdog handshake already exists')
 child=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(state),'watchdog',record['id']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True,**common_kernel_activity.child_contract())
 common_kernel_activity.register_guard(child)
 try:
  end=time.monotonic()+15
  while time.monotonic()<end:
   if child.poll() is not None:raise RuntimeError('Recovery watchdog exited before first write')
   if ready.exists():
    acknowledgement=load(ready)
    if acknowledgement==dict(transaction=record['id'],pid=child.pid,start=start_ticks(child.pid),boot_id=current_boot) and acknowledgement['start']:return child
    raise RuntimeError('Invalid watchdog acknowledgement')
   time.sleep(.02)
  raise RuntimeError('Recovery watchdog did not arm before first write')
 except BaseException:
  child.terminate()
  try:child.wait(timeout=2)
  except subprocess.TimeoutExpired:child.kill();child.wait()
  ready.unlink(missing_ok=True);rollback(state,record);raise

def trial(factory,profile,seconds=30,state=STATE,commit=False,controlled_position=None,observer=None,calibration=False,restore_position=True,replace_current=None):
 """Measured temporary allowlisted changes using the independent watchdog."""
 if os.geteuid()!=0:raise RuntimeError('Trial requires root')
 if restore_position is not False:raise RuntimeError('Explicit passive recovery is required; legacy playback reconstruction is unavailable')
 if controlled_position is not None:raise RuntimeError('Seeking requires the owned V2 observer; the generic trial cannot seek')
 if type(calibration)!=bool or not (10 if calibration else 30)<=seconds<=600:raise ValueError('Trial window30..600 seconds; bounded calibration10seconds minimum')
 with locked(state) as root:
  if type(restore_position)!=bool:raise ValueError('Boolean playback recovery choice required')
  if replace_current is None:ensure_idle_transaction(root)
  else:
   ensure_session_owner(root)
   if profile!='automatic-policy-reapply' or replace_current.get('status')!='committed' or replace_current.get('profile')!='automatic':raise RuntimeError('Only an owned automatic policy can be revalidated')
   if load(root/'current.json')!=replace_current or load(root/'transaction.json')!=replace_current:raise RuntimeError('Concurrent policy or pending recovery; replacement refused')
  h=scan()
  if not all(health(h).values()):raise RuntimeError('Preflight health incomplete')
  from comparison import playback
  from player import target,assert_library_idle
  assert_library_idle(h);mac=target(h)['mac'];audio=playback(mac)
  observe_trial=observer or observe
  if controlled_position is not None:
   from player import rpc
   rpc(mac,['time',str(controlled_position)]);time.sleep(.5)
   if abs(playback(mac)['position']-controlled_position)>2:raise RuntimeError('Controlled baseline seek failed')
  before=observe_trial(seconds);fresh=scan()
  if fresh['hardware_id']!=h['hardware_id'] or playback(mac)['sample_hash']!=audio['sample_hash']:raise RuntimeError('Hardware or material changed during baseline')
  baseline_issues=compare(before,before)
  if baseline_issues:
   atomic(root/'last-rejected-observation.json',before)
   raise RuntimeError('Baseline incomplete or unstable: '+'; '.join(dict.fromkeys(baseline_issues)))
  changes=factory(fresh)
  if not changes:return {'status':'no-change','profile':profile,'baseline':before}
  journal=begin_journal(root,fresh,changes,seconds+120,profile,previous_profile=replace_current)
  from player import playback_snapshot
  if restore_position:
   journal['playback_resume']=playback_snapshot(mac);journal['playback_restore_scope']='position'
  atomic(root/'transaction.json',journal)
  watcher=spawn_watchdog(root,journal)
  try:
   for c in journal['changes']:
    current=scan()
    if current['hardware_id']!=journal['hardware_id']:raise RuntimeError('Hardware changed')
    guard(c,current)
    if setting_value(c)!=c['before']:
     from advanced import irq_thread_side_effect
     if not irq_thread_side_effect(c,setting_value(c),journal['changes']):raise RuntimeError('Setting changed concurrently')
    c['write_attempted']=True;atomic(root/'transaction.json',journal);write_control(c,c['after'])
   if controlled_position is not None:
    from player import rpc
    rpc(mac,['time',str(controlled_position)]);time.sleep(.5)
    if abs(playback(mac)['position']-controlled_position)>2:raise RuntimeError('Controlled candidate seek failed')
   after=observe_trial(seconds);current=scan();final=playback(mac);reasons=compare(before,after)
   if before.get('network_counter_errors') or after.get('network_counter_errors'):reasons.append('Network counter regression')
   if after.get('nas_status') not in [None,'pass','not-applicable']:reasons.append('NAS regression')
   if not all(health(current).values()):reasons.append('Postflight health incomplete')
   if current['hardware_id']!=h['hardware_id'] or current['dac']['pcm']['hw']!=h['dac']['pcm']['hw']:reasons.append('Hardware/format changed')
   if final['sample_hash']!=audio['sample_hash'] or final['volume']!=audio['volume']:reasons.append('Material/volume changed')
   for c in journal['changes']:
    guard(c,current)
    if setting_value(c)!=c['after']:reasons.append('Policy overridden')
   if commit:
    from measurement_contract import confirmation_issues
    reasons.extend(confirmation_issues(before,after))
    reasons=list(dict.fromkeys(reasons))
   journal['benchmark']={'before':before,'after':after};journal['issues']=reasons
   if reasons:raise RuntimeError('; '.join(reasons))
   if commit:
    journal.update(status='committed',profile=profile);atomic(root/'transaction.json',journal);atomic(root/'current.json',journal);atomic(root/'last-known-good.json',journal)
   else:
    journal=rollback(root,journal)
    if journal['status']!='rolled-back':raise RuntimeError('Trial recovery incomplete')
   result={'status':'committed' if commit else 'trial-passed-restored','profile':profile,'benchmark':journal['benchmark'],'issues':[],'restoration':journal['status'],'sound_quality_claim':False};atomic(root/'experimental-trial.json',result);return result
  except BaseException:
   rollback(root,journal);raise
  finally:
   close_watchdog(watcher,journal)

__release_signature__ = 'Tino Zappala'


# P2: every admitted core mutation borrows the same physical global EX.
_core_tune_without_common = tune
_core_trial_without_common = trial
_core_restore_without_common = restore
_core_watchdog_without_common = watchdog

def tune(profile,seconds=30,state=STATE,confirmed=False,services=()):
 from autotune_v2 import common_activity
 if profile in ('default','experimental'):raise RuntimeError('Legacy tune mutation unavailable; use the canonical V2 run/restore workflow')
 return common_activity.run_owned(state,lambda:_core_tune_without_common(profile,seconds,state,confirmed,services),role='native-tune')

def trial(factory,profile,seconds=30,state=STATE,commit=False,controlled_position=None,observer=None,calibration=False,restore_position=True,replace_current=None):
 from autotune_v2 import common_activity
 return common_activity.run_owned(state,lambda:_core_trial_without_common(factory,profile,seconds,state,commit,controlled_position,observer,calibration,restore_position,replace_current),role='native-trial')

def restore(which,state=STATE,confirmed=False):
 from autotune_v2 import common_activity
 if which!='pending':raise RuntimeError('Legacy restore mutation unavailable; use automatic restore or restore pending')
 call=lambda:_core_restore_without_common(which,state,confirmed)
 return common_activity.run_pending_recovery(state,call) if which=='pending' and common_activity.current() is None else common_activity.run_owned(state,call)

def watchdog(state,transaction):
 from autotune_v2 import common_activity
 common_activity.bind_inherited(state,'transaction-guard')
 try:return _core_watchdog_without_common(state,transaction)
 finally:common_activity.guard_exit(state)
