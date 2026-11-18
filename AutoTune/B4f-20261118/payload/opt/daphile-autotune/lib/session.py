"""Independent recovery lease for multi-trial and long-running audio jobs."""
import contextlib,os,signal,subprocess,sys,time,uuid
from pathlib import Path
import engine,player

def alive(r):
 if r['boot_id']!=engine.boot_id() or engine.start_ticks(r['owner'])!=r['start']:return False
 import select
 try:fd=os.pidfd_open(r['owner'])
 except ProcessLookupError:return False
 try:return not select.select([fd],[],[],0)[0]
 finally:os.close(fd)

def restore_owned_playback(r,root=None):
 if type(r.get('restore_playback')) is not bool:raise RuntimeError('Exact boolean playback recovery contract required')
 if r['kind']=='automatic-v2':
  if root is None:raise RuntimeError('Owned workload state directory required')
  from autotune_v2.controlled_workload import recover_session
  return recover_session(root,r)
 if r['restore_playback'] is False:return {'status':'untouched-passive-observer','restored':None}
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if r['kind'] in ['search','usb']:
  import playback_guard
  return playback_guard.finish_search(r['playback'])
 player.restore_playback(r['playback'])
 from playback_guard import equivalent_queue
 final=player.playback_snapshot(r['playback']['mac'])
 if not equivalent_queue(r['playback'],final) or any(final[k]!=r['playback'][k] for k in ['index','mode','repeat','shuffle']):raise RuntimeError('Playback restoration readback incomplete')
 return {'status':'full-playback-restored','restored':True}

def verified_playback_recovery(record,result):
 if (record.get('kind')!='automatic-v2' and record.get('restore_playback') is False
     and isinstance(result,dict) and result=={'status':'untouched-passive-observer','restored':None}):return result
 if not isinstance(result,dict) or result.get('restored') is not True:raise RuntimeError('Playback recovery result incomplete; bookmark retained')
 return result

def recover(root,r):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 from detection import read
 if r.get('host_uuid') and r['host_uuid']!=__import__('host_identity').current(read):raise RuntimeError('Audio recovery belongs to another host; no settings replayed')
 with engine.locked(root) as directory:
  latest=engine.load(directory/'activity-session.json')
  if latest['id']!=r['id'] or latest['status'] not in ['running','recovering','recovery-required']:return
  latest['status']='recovering';engine.atomic(directory/'activity-session.json',latest)
  try:
   from autotune_v2 import current_policy
   current_policy.prepare_recovery(directory,latest,engine)
   result=engine.recover_owned_transaction(directory,{'kind':'activity','id':r['id']})
   if result['status'] not in ['rolled-back','no-change']:raise RuntimeError('Session policy recovery incomplete')
   latest['playback_recovery']=verified_playback_recovery(r,restore_owned_playback(r,root))
   current_policy.recover(directory,latest,engine)
  except Exception as e:
   latest.update(status='recovery-required',recovery_error=type(e).__name__+': '+str(e),last_attempt=time.time())
   engine.atomic(directory/'activity-session.json',latest);raise
  finally:resume_companion(r)
  latest.pop('recovery_error',None);latest.update(status='recovered',finished=time.time());engine.atomic(directory/'activity-session.json',latest)

def watch(root,ident):
 from autotune_v2 import common_activity as common_kernel_activity
 common_kernel_activity.require_exclusive()
 root=Path(root);r=engine.load(root/'activity-session.json')
 if r['id']!=ident:return
 if r.get('kind')=='automatic-v2':
  from autotune_v2.budget_guard import watch as bounded_watch
  return bounded_watch(root,ident,sys.modules[__name__])
 engine.atomic(root/('activity-ready-'+ident+'.json'),{'id':ident,'watchdog':os.getpid()})
 while alive(r) and time.time()<r['deadline']:
  latest=engine.load(root/'activity-session.json')
  if latest['id']!=ident or latest['status'] in ['completed','recovered']:return
  if latest['status']=='recovery-required':break
  if latest['status'] not in ['running','recovering']:return
  time.sleep(1)
 latest=engine.load(root/'activity-session.json')
 if latest.get('id')!=ident or latest.get('status') not in ['running','recovering','recovery-required']:return
 if alive(r) and (r.get('kind')=='automatic-v2' or latest.get('status')!='recovery-required'):
  __import__('process_guard').signal_record(r,signal.SIGTERM,'owner');end=time.monotonic()+3
  while alive(r) and time.monotonic()<end:time.sleep(.1)
  if alive(r):__import__('process_guard').signal_record(r,signal.SIGKILL,'owner')
 for _ in range(60):
  try:recover(root,r);return
  except (BlockingIOError,OSError,RuntimeError,ValueError):time.sleep(1)
 raise RuntimeError('Session recovery lock unavailable')

class Lease:
 def __init__(self,root,playback,seconds,kind,restore_playback=True):
  from autotune_v2 import common_activity as common_kernel_activity
  common_kernel_activity.require_exclusive()
  if type(restore_playback) is not bool:raise RuntimeError('Exact boolean playback recovery contract required')
  if kind!='automatic-v2' and restore_playback is not False:raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
  self.root=Path(root);self.record={'id':uuid.uuid4().hex,'status':'running','owner':os.getpid(),'start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id(),'deadline':time.time()+seconds,'playback':playback,'kind':kind}
  from detection import read
  self.record['host_uuid']=__import__('host_identity').current(read)
  self.record['restore_playback']=restore_playback
  # The installed fixed-affinity companion would overwrite a candidate every
  #10seconds. Pause only that identified daemon, preserving its original state;
  # independent recovery resumes it even after the search parent is killed.
  p=Path('/run/daphile-t630-affinity/pid')
  if kind=='search' and p.exists():
   pid=int(p.read_text());start=engine.start_ticks(pid)
   argv=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0') if start else []
   if start and b'/mnt/hdd/add-ons/95_hp_t630_audio_affinity.py' in argv:
    status=(Path('/proc')/str(pid)/'status').read_text();was_stopped=any(line.startswith('State:') and line.split()[1] in ['T','t'] for line in status.splitlines())
    self.record['companion']={'pid':pid,'start':start,'boot_id':engine.boot_id(),'was_stopped':was_stopped}
  with engine.locked(self.root) as directory:
   engine.ensure_session_owner(directory)
   p=directory/'transaction.json'
   initial=engine.load(p) if p.exists() else None
   if initial and initial.get('status') in ['pending','rollback-incomplete']:raise RuntimeError('Pending settings recovery: no new session started')
   self.record['initial_transaction_id']=(initial or {}).get('id')
   self.record['parent_session_owner']=engine.session_binding(directory)
   if kind=='automatic-v2':
    from autotune_v2.controlled_workload import initial_workload
    self.record['initial_controlled_workload']=initial_workload(directory,engine)
    from autotune_v2.budget_guard import bind_activity_deadline
    bind_activity_deadline(directory,self.record,engine,seconds)
   engine.atomic(directory/'activity-session.json',self.record)
  self.child=None
  try:
   self.child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),str(self.root),self.record['id']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,**common_kernel_activity.child_contract())
   common_kernel_activity.register_guard(self.child)
   end=time.monotonic()+10;ready=self.root/('activity-ready-'+self.record['id']+'.json')
   while not ready.exists() and self.child.poll() is None and time.monotonic()<end:time.sleep(.02)
   if not ready.exists() or engine.load(ready)!={'id':self.record['id'],'watchdog':self.child.pid}:
    raise RuntimeError('Independent session recovery did not arm')
  except BaseException as error:
   if self.child is not None and self.child.poll() is None:
    self.child.terminate()
    try:self.child.wait(timeout=3)
    except subprocess.TimeoutExpired:self.child.kill();self.child.wait()
   with engine.locked(self.root) as directory:
    latest=engine.load(directory/'activity-session.json')
    if latest.get('id')!=self.record['id']:raise RuntimeError('Newer activity preserved after guard startup failure') from error
    try:
     current=engine.load(directory/'transaction.json') if (directory/'transaction.json').exists() else None
     if current!=initial:raise RuntimeError('Transaction changed before session guard startup completed')
     if kind=='automatic-v2':
      from autotune_v2.controlled_workload import verify_untouched_session
      result=verify_untouched_session(directory,self.record)
     else:result=restore_owned_playback(self.record,directory)
     self.record['playback_recovery']=verified_playback_recovery(self.record,result)
     self.record.update(status='completed',finished=time.time(),startup_error=type(error).__name__+': '+str(error))
    except BaseException as recovery:
     self.record.update(status='recovery-required',recovery_error=type(recovery).__name__+': '+str(recovery),last_attempt=time.time())
    engine.atomic(directory/'activity-session.json',self.record)
   raise
  if self.record.get('companion') and not self.record['companion'].get('was_stopped'):__import__('process_guard').signal_record(self.record['companion'],signal.SIGSTOP)
 def finish(self,restore=True):
  from autotune_v2 import common_activity as common_kernel_activity
  common_kernel_activity.require_exclusive()
  try:
   if self.record.get('kind')=='automatic-v2':
    from autotune_v2.workflow import guard_lease
    with engine.locked(self.root):guard_lease(self.root,self.record,engine)
   # A restored playlist does not imply that settings were recovered. Keep
   # the independent guard armed until every pending policy is restored.
   pending=self.root/'transaction.json'
   if pending.exists():
    with engine.locked(self.root) as directory:
     recovered=engine.recover_owned_transaction(directory,{'kind':'activity','id':self.record['id']},include_committed=False)
     if recovered.get('status') not in ['rolled-back','no-change']:raise RuntimeError('Session settings recovery incomplete')
   if restore:self.record['playback_recovery']=verified_playback_recovery(self.record,restore_owned_playback(self.record,self.root))
  except Exception as e:
   self.record.update(status='recovery-required',recovery_error=type(e).__name__+': '+str(e))
   engine.atomic(self.root/'activity-session.json',self.record);raise
  finally:resume_companion(self.record)
  self.record.update(status='completed',finished=time.time());engine.atomic(self.root/'activity-session.json',self.record)
  if self.record.get('kind')!='automatic-v2':engine.close_recovery_guard(self.child,True)
  (self.root/('activity-ready-'+self.record['id']+'.json')).unlink(missing_ok=True)
  return self.record.get('playback_recovery',{'status':'not-requested','restored':None})

def resume_companion(record):
 c=record.get('companion')
 if c and not c.get('was_stopped') and c['boot_id']==engine.boot_id() and engine.start_ticks(c['pid'])==c['start']:
  with contextlib.suppress(ProcessLookupError):__import__('process_guard').signal_record(c,signal.SIGCONT)

if __name__=='__main__':
 from autotune_v2 import common_activity
 common_activity.bind_inherited(sys.argv[1],'activity-guard')
 try:watch(sys.argv[1],sys.argv[2])
 finally:common_activity.guard_exit(sys.argv[1])

__release_signature__ = 'Tino Zappala'
