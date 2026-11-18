"""Opt-in local diagnostics and reversible playback-priority scanner policy."""
import contextlib,os,signal,subprocess,sys,time
from pathlib import Path
import engine,player
from detection import scan,processes

def ready_path(root,record):
 return Path(root)/('automation-ready-'+str(record['pid'])+'-'+str(record['start'])+'.json')

def configure(enabled=False,priority=False,interval=300,state=engine.STATE):
 if type(enabled)!=bool or type(priority)!=bool or type(interval)!=int or not 60<=interval<=3600:raise ValueError('Invalid automation choices/interval')
 with engine.locked(state) as root:
  r={'enabled':enabled,'playback_priority':priority,'interval_seconds':interval,'profile_autostart':False,'automatic_audio_tests':False};engine.atomic(root/'automation-config.json',r)
 return r

def status(state=engine.STATE):
 root=Path(state);config=engine.load(root/'automation-config.json') if (root/'automation-config.json').exists() else {'enabled':False,'profile_autostart':False}
 pid=engine.load(root/'automation-pid.json') if (root/'automation-pid.json').exists() else None
 return {'configuration':config,'running':bool(pid and pid.get('boot_id')==engine.boot_id() and engine.start_ticks(pid['pid'])==pid['start'])}

def restore_priorities(root):
 p=root/'automation-priorities.json'
 if not p.exists():return
 record=engine.load(p);errors=[]
 for c in record.get('changes',[]):
  if c['boot_id']!=engine.boot_id() or engine.start_ticks(c['pid'])!=c['start']:continue
  try:
   if os.getpriority(os.PRIO_PROCESS,c['pid'])==19:os.setpriority(os.PRIO_PROCESS,c['pid'],c['before'])
   elif os.getpriority(os.PRIO_PROCESS,c['pid'])!=c['before']:raise RuntimeError('Concurrent scanner priority change')
  except Exception as e:errors.append(str(e))
 engine.atomic(p,{'changes':[] if not errors else record['changes'],'errors':errors})
 if errors:raise RuntimeError('Scanner recovery incomplete: '+str(errors))

def cycle(root,priority):
 h=scan();playing=False
 try:
  target=player.target(h);playing=player.rpc(target['mac'],['status','-','1']).get('mode')=='play'
 except Exception:pass
 p=root/'automation-priorities.json';record=engine.load(p) if p.exists() else {'changes':[]}
 if not priority or not playing:restore_priorities(root)
 else:
  existing={(c['pid'],c['start']) for c in record['changes']}
  for proc in processes():
   if not proc.get('scanner'):continue
   pid=proc['pid'];start=engine.start_ticks(pid)
   if not start or (pid,start) in existing:continue
   prior=os.getpriority(os.PRIO_PROCESS,pid)
   c={'pid':pid,'start':start,'before':prior,'boot_id':engine.boot_id()};record['changes'].append(c);engine.atomic(p,record)
   if engine.start_ticks(pid)!=start:raise RuntimeError('Scanner PID changed before priority update')
   os.setpriority(os.PRIO_PROCESS,pid,19)
 engine.atomic(root/'automation-health.json',{'time':time.time(),'playing':playing,'dac_status':h['dac']['status'],'logical_cpus':h['cpu']['logical_cpus'],'thermal':h['thermal'],'network':h['network'],'profile_applied':False})

def serve(state=engine.STATE):
 if os.geteuid()!=0:raise RuntimeError('Automation requires root')
 root=Path(state);running=True
 def stop(*_):
  nonlocal running
  running=False
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 with engine.locked(root) as directory:
  if status(root)['running']:raise RuntimeError('Automation already running')
  restore_priorities(directory);owner={'pid':os.getpid(),'start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id()};engine.atomic(directory/'automation-pid.json',owner)
 ready=ready_path(root,owner)
 if ready.exists():raise RuntimeError('Automation recovery handshake already exists')
 watcher=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(root),'automation','watchdog'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 try:
  end=time.monotonic()+10
  while not ready.exists() and watcher.poll() is None and time.monotonic()<end:time.sleep(.05)
  if not ready.exists() or engine.load(ready)!={'pid':watcher.pid,'owner':owner}:raise RuntimeError('Automation independent recovery failed to arm')
  while running:
   config=engine.load(root/'automation-config.json')
   if not config['enabled']:break
   try:
    with engine.locked(root) as directory:
     # Do not overlap a measured profile, A/B or matrix trial.
     engine.ensure_idle_transaction(directory);session=directory/'ab-session.json'
     if not session.exists() or engine.load(session)['status'] not in ['running','recovering']:cycle(directory,config['playback_priority'])
   except BlockingIOError:pass
   except RuntimeError as e:
    with contextlib.suppress(BlockingIOError):
     with engine.locked(root) as directory:engine.log(directory,{'event':'automation-deferred','reason':str(e)})
   deadline=time.monotonic()+config['interval_seconds']
   while running and time.monotonic()<deadline:time.sleep(min(1,deadline-time.monotonic()))
 finally:
  for _ in range(20):
   try:
    with engine.locked(root) as directory:restore_priorities(directory)
    break
   except BlockingIOError:time.sleep(.5)
  watcher.terminate()
  try:watcher.wait(timeout=3)
  except subprocess.TimeoutExpired:watcher.kill();watcher.wait()

def watchdog(state=engine.STATE):
 root=Path(state);record=engine.load(root/'automation-pid.json');ready=ready_path(root,record);engine.atomic(ready,{'pid':os.getpid(),'owner':record})
 try:
  while record['boot_id']==engine.boot_id() and engine.start_ticks(record['pid'])==record['start']:time.sleep(.5)
  for _ in range(30):
   try:
    with engine.locked(root) as directory:
     if engine.load(root/'automation-pid.json')!=record:return
     restore_priorities(directory)
    return
   except BlockingIOError:time.sleep(1)
  raise RuntimeError('Automation recovery lock unavailable')
 finally:ready.unlink(missing_ok=True)

def start(state=engine.STATE):
 if not status(state)['configuration'].get('enabled'):return {'status':'disabled'}
 if status(state)['running']:return {'status':'already-running'}
 p=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(state),'automation','serve'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 return {'status':'starting','pid':p.pid}

def stop(state=engine.STATE):
 r=status(state)
 if not r['running']:return {'status':'inactive'}
 pid=engine.load(Path(state)/'automation-pid.json');__import__('process_guard').signal_record(pid,signal.SIGTERM)
 return {'status':'stopping','scanner_priority_restore_pending':True}

__release_signature__ = 'Tino Zappala'
