"""Local24h observer, bounded induced load, persistent RSS trend evidence."""
import contextlib,math,multiprocessing,os,signal,statistics,time,uuid
from pathlib import Path
import engine,player,runtime,session
from detection import scan,read,numbers

PHASE_SECONDS=600

def rss_snapshot():
 rows={}
 for p in Path('/proc').glob('[0-9]*'):
  pid=int(p.name);start=engine.start_ticks(pid)
  text=read(p/'status') or '';rss=next((int(x.split()[1]) for x in text.splitlines() if x.startswith('VmRSS:')),None)
  if start and rss is not None:rows[str(pid)+':'+start]={'pid':pid,'start':start,'rss_kib':rss,'name':read(p/'comm')}
 return rows

def memory_trends(samples):
 groups={};alerts=[]
 for point in samples:
  for key,row in point['processes'].items():groups.setdefault(key,[]).append((point['time'],row['rss_kib']))
 for key,rows in groups.items():
  if len(rows)<10 or rows[-1][0]-rows[0][0]<1800:continue
  # Medians suppress individual cache/allocation bursts; report suspicion,
  # not a proven leak, and preserve all samples for attribution.
  chunks=[rows[i:i+max(3,len(rows)//5)] for i in range(0,len(rows),max(3,len(rows)//5))]
  trend=[statistics.median(v for _,v in c) for c in chunks if len(c)>=3]
  growth=trend[-1]-trend[0] if len(trend)>=4 else 0
  slope=(rows[-1][1]-rows[0][1])*3600/(rows[-1][0]-rows[0][0])
  if growth>=16384 and slope>=1024 and all(b>=a-1024 for a,b in zip(trend,trend[1:])):alerts.append({'identity':key,'growth_kib':growth,'slope_kib_per_hour':slope,'status':'suspected-growth-requires-attribution'})
 return alerts

def worker(kind,cpus,stop,root,deadline):
 if kind not in ['cpu','memory','disk','network']:raise ValueError('Unknown induced load kind')
 os.nice(19);os.sched_setaffinity(0,cpus);signal.signal(signal.SIGTERM,lambda *_:stop.set())
 p=Path(root)/('owned-stress-'+str(os.getpid())+'-'+uuid.uuid4().hex)
 try:
  while not stop.is_set() and time.monotonic()<deadline:
   if kind=='cpu':
    end=time.monotonic()+.05;n=1
    while time.monotonic()<end:n=(n*1664525+1013904223)&0xffffffff
    stop.wait(.05)
   elif kind=='memory':
    available=next((int(x.split()[1])*1024 for x in (read('/proc/meminfo') or '').splitlines() if x.startswith('MemAvailable:')),0)
    size=min(64*1024*1024,available//10)
    if size<8*1024*1024:stop.wait(1);continue
    buffer=bytearray(size)
    for offset in range(0,size,4096):buffer[offset]=offset%251
    stop.wait(.2);del buffer;stop.wait(.2)
   elif kind=='disk':
    if not p.exists():
     fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
     try:
      for _ in range(8):os.write(fd,b'\0'*(1024*1024))
      os.fsync(fd)
     finally:os.close(fd)
    with p.open('rb') as stream:
     while stream.read(1024*1024) and not stop.is_set():pass
    # Real bounded write/fsync traffic on the owned file, plus rereads.
    fd=os.open(p,os.O_WRONLY|os.O_NOFOLLOW)
    try:os.pwrite(fd,b'\0'*65536,0);os.fsync(fd)
    finally:os.close(fd)
    stop.wait(.5)
   elif kind=='network':
    import network_test
    network_test.audit();stop.wait(5)
 finally:
  if p.exists() and p.parent.resolve()==Path(root).resolve() and p.name.startswith('owned-stress-'):p.unlink()

def _load_entry(kind,cpus,stop,root,deadline,receipt):
 """Record why the child ended, separately from the parent's sample timing."""
 error=None
 try:worker(kind,cpus,stop,root,deadline)
 except BaseException as e:
  error=type(e).__name__+': '+str(e)[:400];raise
 finally:
  finished=time.monotonic()
  reason='exception' if error else 'deadline' if finished>=deadline else 'stopped' if stop.is_set() else 'returned-early'
  try:receipt.send({'kind':kind,'reason':reason,'finished_monotonic':finished,'deadline_monotonic':deadline,'error':error})
  finally:receipt.close()

def _load_start(kind,cpus,root,deadline):
 stop=multiprocessing.Event();receive,send=multiprocessing.Pipe(duplex=False)
 process=multiprocessing.Process(target=_load_entry,args=(kind,cpus,stop,root,deadline,send))
 try:process.start()
 except BaseException:
  receive.close();send.close();raise
 send.close()
 return {'kind':kind,'process':process,'stop':stop,'receive':receive,'deadline':deadline,'completion':None,'issue':None}

def _load_evidence(load,allow_stop=False):
 process=load['process']
 if process.is_alive():return None
 process.join(timeout=0)
 if load['completion'] is None:
  try:
   if load['receive'].poll():load['completion']=load['receive'].recv()
  except (EOFError,OSError):pass
 proof=load['completion'];problem=None
 if process.exitcode!=0:problem='Induced-load process failed: '+load['kind']+' exit='+str(process.exitcode)
 elif not isinstance(proof,dict):problem='Induced-load completion evidence missing: '+load['kind']
 elif proof.get('kind')!=load['kind'] or proof.get('deadline_monotonic')!=load['deadline'] or not isinstance(proof.get('finished_monotonic'),(int,float)) or not math.isfinite(proof['finished_monotonic']):problem='Invalid induced-load completion evidence: '+load['kind']
 elif proof.get('error'):problem='Induced-load process reported an error: '+load['kind']
 elif proof.get('reason')=='deadline' and proof['finished_monotonic']>=load['deadline']:pass
 elif proof.get('reason')=='stopped' and allow_stop:pass
 else:problem='Induced-load process exited prematurely: '+load['kind']
 if problem:load['issue']=load['issue'] or problem
 return load['issue']

def _load_stop(load,events):
 """Close every owned worker and retain its terminal evidence, including the last."""
 _load_evidence(load)
 process=load['process'];load['stop'].set();process.join(timeout=3);forced=None
 if process.is_alive():forced='terminate';process.terminate();process.join(timeout=2)
 if process.is_alive():forced='kill';process.kill();process.join(timeout=2)
 issue=_load_evidence(load,allow_stop=True)
 if process.is_alive():issue='Induced-load process could not be stopped: '+load['kind']
 if forced:issue=issue or 'Induced-load cleanup required '+forced+': '+load['kind']
 events.append({'kind':load['kind'],'status':'stopped','pid':process.pid,'exit':process.exitcode,'time':time.time(),'completion':load['completion'],'forced':forced})
 load['receive'].close()
 return issue

def run(seconds=86400,state=engine.STATE,induced=True,confirmed=False):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 from measurement_contract import measurement_issues
 credited_windows=[]
 def review_observe(request):
  begin=time.monotonic();m=runtime.observe(request);span=max(0,time.monotonic()-begin)
  invalid=measurement_issues(m)
  credited=min(m['duration'],span) if not invalid else 0
  if credited<=0:invalid.append('Observation made no monotonic progress')
  issues.extend(invalid);credited_windows.append(credited)
  return m
 if not confirmed or type(seconds)!=int or not 60<=seconds<=172800:raise ValueError('Confirmed stability duration60..172800 seconds required')
 root=Path(state);h=scan();t=player.target(h);original=player.playback_snapshot(t['mac']);player.assert_library_idle(h)
 online=set(numbers(h['cpu'].get('online')));house=sorted(online-set(numbers(h['cpu'].get('isolated'))))
 if not house:raise RuntimeError('No housekeeping CPUs available for bounded load')
 lease=session.Lease(root,original,seconds+600,'stress',restore_playback=induced);started=time.time();mono_start=time.monotonic();end=mono_start+seconds;segments=[];memory=[];loads=[];issues=[];owned=None;previous_kind=None
 identity=h['dac']['identity'];net_before={x['name']:x['statistics'] for x in h['network']}
 count=0
 result={'status':'running','duration_requested':seconds,'started':started,'started_monotonic':mono_start,'duration_clock':'monotonic','host_uuid':t['host_uuid'],'segments':segments,'load_events':loads,'issues':issues,'memory_samples':memory,'sound_quality_claim':False,'full_observations':'stress-observations.jsonl'}
 try:
  if induced:
   player.rpc(t['mac'],['playlist','repeat',2]);player.rpc(t['mac'],['play'])
  while sum(credited_windows)<seconds:
   now=time.monotonic();end=mono_start+seconds+max(0,now-mono_start-sum(credited_windows));elapsed=now-mono_start;phase_number=int(elapsed//PHASE_SECONDS);phase=phase_number%12
   phase_deadline=min(end,mono_start+(phase_number+1)*PHASE_SECONDS)
   kind=['cpu','memory','disk','network'][phase-6] if induced and 6<=phase<=9 else None
   if kind!=previous_kind:
    if owned:
     issue=_load_stop(owned,loads);owned=None
     if issue:issues.append(issue);break
    if kind:
     owned=_load_start(kind,house,root,phase_deadline)
     loads.append({'kind':kind,'status':'started','pid':owned['process'].pid,'time':time.time(),'started_monotonic':time.monotonic(),'deadline_monotonic':phase_deadline})
    previous_kind=kind
   measured=review_observe(min(30,max(1,end-time.monotonic()),max(1,phase_deadline-time.monotonic())));fresh=scan();health=engine.health(fresh)
   if fresh['dac'].get('identity')!=identity:issues.append('DAC disconnected or replaced')
   if not all(health.values()):
    # Allow bounded natural track transition, then require actual playback.
    try:player.wait_audio(t['mac'],10);fresh=scan();health=engine.health(fresh)
    except Exception:pass
    if not all(health.values()):issues.append('Playback/system health failed')
   if measured['observed_alsa_xrun_states'] or measured['new_kernel_errors']:issues.append('Observed ALSA/kernel error')
   if not measured['kernel_log_observable']:issues.append('Kernel log observation incomplete')
   temps=[int(x['millidegrees']) for x in fresh.get('thermal',[]) if (x.get('millidegrees') or '').isdigit()]
   if temps and max(temps)>=80000:issues.append('Conservative80C stability cutoff')
   if owned:
    issue=_load_evidence(owned)
    if issue:issues.append(issue)
   for x in fresh['network']:
    first=net_before.get(x['name']) or {}
    for key in ['rx_errors','tx_errors','rx_dropped','tx_dropped']:
     a,b=first.get(key),x['statistics'].get(key)
     if a is not None and b is not None and int(b)>int(a):issues.append('Network counter increase: '+x['name']+'/'+key)
   segments.append({'time':time.time(),'phase':kind or 'normal','measurement':measured,'health':health,'format':fresh['dac'].get('pcm',{}).get('hw')});count+=1
   if count%10==1:
    import network_test
    nas=network_test.audit();segments[-1]['NAS']=nas
    if nas.get('nas') and nas['status']!='pass':issues.append('NAS read incomplete or failed')
    point={'time':time.time(),'processes':rss_snapshot()};memory.append(point);result['memory_growth_suspicions']=memory_trends(memory)
    import json
    with (root/'stress-memory.jsonl').open('a') as stream:
     os.chmod(stream.name,0o600);stream.write(json.dumps(point)+'\n');stream.flush();os.fsync(stream.fileno())
   import json
   with (root/'stress-observations.jsonl').open('a') as stream:
    os.chmod(stream.name,0o600);stream.write(json.dumps(segments[-1])+'\n');stream.flush();os.fsync(stream.fileno())
   del segments[:-12]
   result['memory_samples']=memory[-12:]
   result.update(elapsed=time.monotonic()-mono_start,completed_segments=count,issues=sorted(set(issues)));engine.atomic(root/'stress.json',result)
   engine.atomic(root/'stress-progress.json',{'status':'running','elapsed':result['elapsed'],'observed_seconds':sum(credited_windows),'duration':seconds,'phase':kind or 'normal','issues':result['issues'],'completed_segments':count,'memory_growth_suspicions':result.get('memory_growth_suspicions',[])})
   if issues:break
  result['status']='pass-observed-duration' if not issues and sum(credited_windows)>=seconds else 'failed-or-inconclusive'
 except BaseException as e:
  result.update(status='interrupted-or-failed',error=type(e).__name__+': '+str(e));raise
 finally:
  if owned:
   try:
    issue=_load_stop(owned,loads)
    if issue:
     issues.append(issue);result['issues']=sorted(set(issues))
     if result['status']=='pass-observed-duration':result['status']='failed-or-inconclusive'
   except Exception as e:
    # A cleanup/reporting failure must never bypass the playback recovery lease.
    result.update(status='worker-cleanup-required',worker_cleanup_error=type(e).__name__+': '+str(e))
    with contextlib.suppress(Exception):
     owned['stop'].set();process=owned['process'];process.join(timeout=3)
     if process.is_alive():process.terminate();process.join(timeout=2)
     if process.is_alive():process.kill();process.join(timeout=2)
  try:recovery=lease.finish();result['playlist_restoration']=recovery['status']
  except Exception as e:result.update(status='recovery-required',recovery_error=type(e).__name__+': '+str(e))
  result.update(finished=time.time(),finished_monotonic=time.monotonic());engine.atomic(root/'stress.json',result)
  engine.atomic(root/'stress-progress.json',{k:result.get(k) for k in ['status','elapsed','duration_requested','finished','issues','error','playlist_restoration']})
 result.update(observed_seconds=sum(credited_windows),unobserved_seconds=max(0,time.monotonic()-mono_start-sum(credited_windows)),continuous_coverage_validated=False,coverage_scope='cumulative sampled windows; gaps excluded')
 return result

__release_signature__ = 'Tino Zappala'
