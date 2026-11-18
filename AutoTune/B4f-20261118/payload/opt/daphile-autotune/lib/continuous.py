"""Opt-in local event-driven reanalysis and measured policy reapplication.

The saved policy is host/DAC/kernel bound. New topology invalidates it. A new
measured search is serialized, deferred during scans and requires saved consent
for audio tests. Cooldown and event debounce prevent repeated interruptions.
"""
import contextlib,hashlib,json,os,signal,subprocess,sys,time
from pathlib import Path
import engine,player
from detection import scan,read

def key(h):
 topology=[{'cpu':c['cpu'],'topology':c['topology']} for c in h['cpu'].get('cores',[])]
 return hashlib.sha256(json.dumps({'host':h['system'].get('product_uuid'),'hardware':h['hardware_id'],'kernel':h['kernel']['release'],'DAC':h['dac'].get('identity'),'topology':topology,'online':h['cpu'].get('online'),'isolated':h['cpu'].get('isolated'),'irq':h['dac'].get('usb',{}).get('irq')},sort_keys=True).encode()).hexdigest()

def compatible(saved,h):
 return saved.get('host_uuid')==h['system'].get('product_uuid') and saved.get('hardware_id')==h['hardware_id'] and saved.get('kernel')==h['kernel']['release'] and saved.get('binding_key')==key(h)

def load_bucket(h):
 from detection import numbers
 housekeeping=set(numbers(h['cpu'].get('online')))-set(numbers(h['cpu'].get('isolated')))
 normalized=os.getloadavg()[0]/max(1,len(housekeeping))
 return 'high' if normalized>=.7 else 'normal' if normalized>=.25 else 'low'

def configure(enabled=False,audio_consent=False,experimental=False,state=engine.STATE):
 if any(type(x)!=bool for x in [enabled,audio_consent,experimental]):raise ValueError('Boolean automation choices required')
 if enabled and not audio_consent:raise ValueError('Saved consent is required for automatic measured audio tests')
 with engine.locked(state) as root:
  h=scan();r={'enabled':enabled,'audio_consent':audio_consent,'experimental':experimental,'host_uuid':h['system']['product_uuid'],'interval_seconds':60,'debounce_cycles':3,'minimum_retest_seconds':21600}
  engine.atomic(root/'continuous-config.json',r)
 return r

def status(state=engine.STATE):
 root=Path(state);p=root/'continuous-pid.json';r=engine.load(p) if p.exists() else None
 return {'running':bool(r and r['boot_id']==engine.boot_id() and engine.start_ticks(r['pid'])==r['start']),'configuration':engine.load(root/'continuous-config.json') if (root/'continuous-config.json').exists() else {'enabled':False},'latest':engine.load(root/'continuous.json') if (root/'continuous.json').exists() else {'status':'inactive'}}

def start(state=engine.STATE):
 s=status(state)
 if not s['configuration'].get('enabled'):return {'status':'disabled'}
 if s['running']:return {'status':'already-running'}
 p=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(state),'continuous','serve'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 return {'status':'starting','pid':p.pid}

def stop(state=engine.STATE):
 if not status(state)['running']:return {'status':'inactive'}
 r=engine.load(Path(state)/'continuous-pid.json');__import__('process_guard').signal_record(r,signal.SIGTERM);return {'status':'stopping'}

def reapply(spec,state,h):
 import session,search,measure
 lease=session.Lease(state,player.playback_snapshot(player.target(h)['mac']),180,'search',restore_playback=False)
 try:return engine.trial(lambda fresh:search.changes(fresh,spec),'continuous-reapply',30,state,True,observer=measure.observe,restore_position=False)
 finally:lease.finish()

def serve(state=engine.STATE):
 root=Path(state);running=True;pending=None;seen=0;worker=None
 previous=engine.load(root/'continuous.json') if (root/'continuous.json').exists() else {}
 last_trial=previous.get('last_trial',0);last_load=previous.get('last_load')
 def end(*_):
  nonlocal running
  running=False
 signal.signal(signal.SIGTERM,end);signal.signal(signal.SIGINT,end)
 lock=(root/'continuous-daemon.lock').open('a')
 import fcntl
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 engine.atomic(root/'continuous-pid.json',{'pid':os.getpid(),'start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id()})
 try:
  while running:
   config=engine.load(root/'continuous-config.json')
   if not config['enabled']:break
   report={'status':'monitoring','time':time.time(),'changes_performed':False,'last_trial':last_trial,'last_load':last_load}
   try:
    h=scan()
    if h['system']['product_uuid']!=config['host_uuid']:raise RuntimeError('Saved automation belongs to another host')
    if h['dac'].get('status')!='verified-active':raise RuntimeError('No active DAC: defer tests')
    bucket=load_bucket(h);fingerprint=key(h)+':'+bucket;seen=seen+1 if pending==fingerprint else 1;pending=fingerprint;report.update(binding_key=key(h),load_bucket=bucket,debounce_observations=seen)
    saved=engine.load(root/'search.json') if (root/'search.json').exists() else {}
    active=root/'current.json';current=engine.load(active) if active.exists() else {}
    if current.get('profile')=='automatic' and (root/'automatic-policy.json').exists() and engine.load(root/'automatic-policy.json').get('persistent'):
     raise RuntimeError('Persistent automatic policy is managed by policy_restore; legacy reapplication deferred')
    if worker and worker.poll() is None:report['status']='measured-search-running'
    elif seen>=config['debounce_cycles']:
     with engine.locked(root) as directory:engine.ensure_session_owner(directory)
     player.assert_library_idle(h)
     if current.get('status')=='committed':
      from comparison import profile_issues
      issues=profile_issues(current,h)
      report.update(status='policy-current' if not issues else 'policy-drift',issues=issues)
      # An unexpected manual write is preserved; only exited/recreated audio
      # processes are eligible for saved-policy reapplication.
      departed=any(c.get('tid') and engine.start_ticks(c['tid'])!=c.get('start') for c in current.get('changes',[]))
      bound=compatible(saved,h)
      owned=current.get('profile') in ['automatic','search-combined','continuous-reapply']
      load_changed=last_load is not None and bucket!=last_load and time.time()-last_trial>=config['minimum_retest_seconds']
      if not bound:report.update(status='saved-policy-invalidated',reason='Hardware, DAC, topology or kernel differs; old policy is not replayed')
      elif owned and ((issues and departed) or (not issues and load_changed)):
       with engine.locked(root) as directory:
        old=engine.load(active)
        if old.get('playback_resume'):raise RuntimeError('Legacy playback recovery is not owned by passive reapplication')
        report.update(prior_policy_recovery_attempted=True,changes_performed=None)
        restored=engine.rollback(directory,old)
        report['old_policy_recovery']={'status':restored.get('status')}
        if restored['status']!='rolled-back':raise RuntimeError('Old process policy recovery incomplete')
       import search
       spec=saved.get('combined_spec')
       if spec and not load_changed:
        applied=reapply(spec,root,h)
        if not isinstance(applied,dict):raise RuntimeError('Reapplication result unavailable')
        report['reapplication']=applied
        if applied.get('status')=='committed':
         if applied.get('restoration')!='committed' or applied.get('issues')!=[] or not isinstance(applied.get('benchmark'),dict):raise RuntimeError('Committed reapplication evidence incomplete')
         report.update(status='reapplied-verified',changes_performed=True,reapplication_changes_performed=True)
        elif applied.get('status')=='no-change':
         report.update(status='reapplication-no-change',reapplication_changes_performed=False)
        else:raise RuntimeError('Reapplication was not committed or unchanged')
      if not issues and bound and last_load is None:last_load=bucket;report['last_load']=bucket
     # Re-enter on the next debounced cycle after rolling back for a stable
     # load change. Child PID ownership and the daemon lock serialize launches.
     elif time.time()-last_trial>=config['minimum_retest_seconds'] and config['audio_consent']:
      command=[sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(root),'automatic','run','--confirm-audio-test']
      if config['experimental']:command+=['--experimental']
      error=(root/'continuous-worker.err').open('a')
      worker=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=error,stderr=error,start_new_session=True);error.close();last_trial=time.time();last_load=bucket;report.update(status='measured-search-started',pid=worker.pid,pid_start=engine.start_ticks(worker.pid),boot_id=engine.boot_id(),last_trial=last_trial,last_load=last_load)
   except (RuntimeError,BlockingIOError,ValueError) as e:report.update(status='deferred',reason=str(e))
   engine.atomic(root/'continuous.json',report)
   deadline=time.monotonic()+config['interval_seconds']
   while running and time.monotonic()<deadline:time.sleep(min(1,deadline-time.monotonic()))
 finally:
  if worker and worker.poll() is None:
   worker.terminate()
   try:worker.wait(timeout=5)
   except subprocess.TimeoutExpired:worker.kill();worker.wait()
  lock.close()

__release_signature__ = 'Tino Zappala'
