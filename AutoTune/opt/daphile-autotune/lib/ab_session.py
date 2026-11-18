"""Balanced randomized profile trials, blinded public progress, crash recovery."""
import contextlib,hashlib,os,secrets,signal,subprocess,sys,time,uuid
from pathlib import Path
import engine,player,comparison
from detection import scan
ALLOWED={'default','safe','audio-rt','low-activity'}

def public(record,reveal=False):
 result={k:record.get(k) for k in ['id','status','blind','conditions','completed','total','current_condition','issues','votes','restoration']}
 if reveal and record.get('status') in ['finished-restored','cancelled-restored']:result['mapping']=record['mapping'];result['trials']=record.get('trials',[])
 return result

def choose(profiles,repeats):
 if not isinstance(profiles,list) or not 2<=len(profiles)<=4 or any(not isinstance(x,str) or x not in ALLOWED for x in profiles) or len(set(profiles))!=len(profiles):raise ValueError('Choose2..4different runtime profiles')
 if type(repeats)!=int or not 2<=repeats<=6:raise ValueError('Balanced repetitions2..6')
 random=secrets.SystemRandom();profiles=profiles[:];random.shuffle(profiles);mapping={chr(65+i):p for i,p in enumerate(profiles)};sequence=[]
 for _ in range(repeats):
  block=list(mapping);random.shuffle(block);sequence+=block
 return mapping,sequence

def recover(root,record):
 if record.get('host_uuid') and record['host_uuid']!=__import__('detection').read('/sys/class/dmi/id/product_uuid'):raise RuntimeError('A/B recovery belongs to another host')
 if record.get('owner_pid') not in [None,os.getpid()] and record.get('boot_id')==engine.boot_id() and engine.start_ticks(record['owner_pid'])==record.get('owner_start'):raise RuntimeError('A/B parent still running; cancel before recovery')
 errors=[]
 try:
  with engine.locked(root) as directory:restored=engine.recover_owned_transaction(directory,{'kind':'ab','id':record['id']})
  if restored.get('status') not in ['rolled-back','no-change']:raise RuntimeError('Profile restoration status: '+str(restored.get('status')))
 except Exception as e:errors.append('Profile recovery: '+str(e))
 record['playback_recovery']={'status':'legacy-playback-refused','restored':False,'bookmark_retained':True,'player_commands':0}
 errors.append('Legacy playback recovery unavailable; saved playlist retained without replay')
 record.update(restoration='incomplete' if errors else 'restored',status='recovery-incomplete' if errors else 'cancelled-restored',issues=record.get('issues',[])+errors)
 with engine.locked(root) as state:engine.atomic(state/'ab-session.json',record)
 return record

def watchdog(root,ident):
 path=Path(root)/'ab-session.json';r=engine.load(path)
 if r['id']!=ident or r['status'] not in ['running','recovering','recovery-incomplete']:return
 ready=Path(root)/('ab-ready-'+ident+'.json');engine.atomic(ready,{'pid':os.getpid(),'id':ident})
 try:
  while True:
   r=engine.load(path)
   if r['id']!=ident or r['status'] not in ['running','recovering','recovery-incomplete']:return
   dead=r['boot_id']!=engine.boot_id() or engine.start_ticks(r['owner_pid'])!=r['owner_start']
   if dead or r['status']=='recovery-incomplete' or time.time()>r['deadline']:break
   time.sleep(.5)
  if not dead:
   stop_owner(r)
  for _ in range(20):
   try:
    result=recover(Path(root),r)
    if result['restoration']=='restored':return
   except BlockingIOError:pass
   time.sleep(.5)
  raise RuntimeError('A/B recovery lock busy')
 finally:ready.unlink(missing_ok=True)

def stop_owner(record):
 """Wait for a timed-out owner to exit before taking its recovery lock."""
 def alive():return record['boot_id']==engine.boot_id() and engine.start_ticks(record['owner_pid'])==record['owner_start']
 if not alive():return
 with contextlib.suppress(ProcessLookupError):__import__('process_guard').signal_owner(record,signal.SIGTERM)
 deadline=time.monotonic()+3
 while alive() and time.monotonic()<deadline:time.sleep(.05)
 if alive():
  # Recheck boot and start time immediately before the stronger signal.
  with contextlib.suppress(ProcessLookupError):__import__('process_guard').signal_owner(record,signal.SIGKILL)
  deadline=time.monotonic()+3
  while alive() and time.monotonic()<deadline:time.sleep(.05)
 if alive():raise RuntimeError('Timed-out A/B parent did not exit; recovery remains armed')

def run(profiles,repeats=2,seconds=30,blind=True,state=engine.STATE,confirmed=False):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if not confirmed or os.geteuid()!=0:raise RuntimeError('Root and explicit audio/profile trial acknowledgement required')
 if type(seconds)!=int or not 30<=seconds<=120:raise ValueError('A/B window30..120 seconds')
 mapping,sequence=choose(profiles,repeats);root=Path(state)
 with engine.locked(root) as directory:
  engine.ensure_idle_transaction(directory);h=scan();player.assert_library_idle(h);t=player.target(h);original=player.playback_snapshot(t['mac']);audio=comparison.playback(t['mac']);s=player.rpc(t['mac'],['status','-','1','tags:u'])
  duration=float(s.get('duration',0));position=audio['position']
  if duration-position<seconds*3+15:position=0
  if duration-position<seconds*3+15:raise RuntimeError('Current track too short for controlled profile baselines and listening window')
  record={'id':uuid.uuid4().hex,'status':'running','blind':bool(blind),'mapping':mapping,'conditions':list(mapping),'sequence':sequence,'completed':0,'total':len(sequence),'issues':[],'votes':[],'trials':[],'playback_resume':original,'reference':audio,'position':position,'hardware_id':h['hardware_id'],'host_uuid':t['host_uuid'],'owner_pid':os.getpid(),'owner_start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id(),'deadline':time.time()+len(sequence)*(seconds*4+90)+120}
  engine.atomic(directory/'ab-session.json',record)
 ready=root/('ab-ready-'+record['id']+'.json')
 watcher=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(root),'session-watchdog',record['id']],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
 try:
  end=time.monotonic()+10
  while not ready.exists() and watcher.poll() is None and time.monotonic()<end:time.sleep(.05)
  if not ready.exists() or engine.load(ready)!={'pid':watcher.pid,'id':record['id']}:raise RuntimeError('A/B independent recovery did not arm')
  for condition in sequence:
   record['current_condition']=condition
   with engine.locked(root) as directory:engine.atomic(directory/'ab-session.json',record)
   if scan()['hardware_id']!=record['hardware_id']:raise RuntimeError('Hardware changed during A/B session')
   player.assert_library_idle()
   with engine.locked(root) as directory:
    restored=engine.recover_owned_transaction(directory,{'kind':'ab','id':record['id']})
    if restored['status'] not in ['rolled-back','no-change']:raise RuntimeError('A/B settings recovery incomplete')
   player.rpc(t['mac'],['playlist','index',original['index']]);player.rpc(t['mac'],['time',position]);player.settle_mode(t['mac'],'play')
   profile=mapping[condition]
   application=engine.tune(profile,seconds,root) if profile!='default' else {'status':'original','changes':[]}
   player.rpc(t['mac'],['time',position]);time.sleep(.5)
   current=comparison.playback(t['mac'])
   if current['sample_hash']!=audio['sample_hash'] or current['volume']!=audio['volume'] or abs(current['position']-position)>2:raise RuntimeError('Controlled material/volume/start position mismatch')
   capture=comparison.capture('ab',condition,seconds,root)
   if capture['status']!='observed-window':raise RuntimeError('A/B capture incomplete: '+str(capture['issues']))
   record['trials'].append({'condition':condition,'capture':capture,'application':{'status':application['status'],'profile':profile,'effective_changes':len(application.get('changes',[]))}});record['completed']+=1
   with engine.locked(root) as directory:
    latest=engine.load(directory/'ab-session.json');record['votes']=latest.get('votes',[]);engine.atomic(directory/'ab-session.json',record)
  restored=recover(root,record)
  if restored['restoration']!='restored':raise RuntimeError('A/B recovery incomplete')
  record.update(status='finished-restored',current_condition=None)
  with engine.locked(root) as directory:engine.atomic(directory/'ab-session.json',record)
 except BaseException as e:
  record['issues'].append(type(e).__name__+': '+str(e));recover(root,record);raise
 finally:
  engine.close_recovery_guard(watcher,record.get('restoration')=='restored')
 return public(record,reveal=not blind)

def status(state=engine.STATE,reveal=False):
 path=Path(state)/'ab-session.json'
 return public(engine.load(path),reveal) if path.exists() else {'status':'inactive'}

def vote(condition,state=engine.STATE):
 with engine.locked(state) as root:
  r=engine.load(root/'ab-session.json')
  if condition not in r['conditions'] or r['status'] not in ['running','finished-restored']:raise ValueError('Unknown condition or inactive session')
  r['votes'].append({'condition':condition,'time':time.time()});engine.atomic(root/'ab-session.json',r)
 return {'status':'vote-recorded','condition':condition}

__release_signature__ = 'Tino Zappala'
