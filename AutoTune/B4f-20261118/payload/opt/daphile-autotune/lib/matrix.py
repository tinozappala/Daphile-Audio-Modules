"""Full capability matrix, tested only through the real Daphile/LMS player."""
import json,math,os,re,struct,time,urllib.parse,uuid,wave
from pathlib import Path
from detection import read,scan
from runtime import observe
import engine,player

def inventory(h,dsf_samples=None):
 t=player.target(h);info=read('/run/spacd/info/'+str(t['card'])+'.'+str(h['dac']['device'])) or ''
 rates=[int(x) for x in re.search(r'^PCM_RATES=(.*)$',info,re.M)[1].split()] if re.search(r'^PCM_RATES=(.*)$',info,re.M) else []
 entries=[dict(kind='PCM',rate=rate,bits=bits,channels=2,status='pending') for rate in rates for bits in [16,24,32]]
 dsd=[int(x) for x in re.search(r'^DSD_RATES=(.*)$',info,re.M)[1].split()] if re.search(r'^DSD_RATES=(.*)$',info,re.M) else []
 for rate in dsd:
  for transport in ['DoP','native']:
   known=transport=='DoP' or bool(re.search(r'^DSDFMT=DSD_',info,re.M))
   path=(dsf_samples or {}).get(str(rate))
   entries.append(dict(kind='DSD',rate=rate,transport=transport,path=path,status='pending' if known and path else ('unsupported' if not known else 'missing-sample')))
 if not rates:raise RuntimeError('Daphile PCM capability data unavailable')
 return dict(hardware_id=h['hardware_id'],identity=t['identity'],entries=entries,buffer_candidates=[[100,3],[100,4],[80,4],[60,4],[50,4],[40,4],[30,4],[25,4],[20,4],[15,4],[10,4]],scope='All Daphile-declared PCM rates, source 16/24/32-bit stereo; each DSD rate and both transports explicitly tracked',limitations=['Declared capability is not a successful test','Missing DSD sample remains unvalidated','Native DSD requires declared ALSA DSD format','DoP must be confirmed by transcoder log, not PCM frequency alone'])

def validate_dsf(path,rate):
 from dsf_fixture import owned,inspect
 p=Path(path).resolve(strict=True)
 library=Path('/media/music').resolve()
 if not (p.is_relative_to(library) or owned(p)) or p.suffix.lower()!='.dsf':raise RuntimeError('DSF sample must be an existing library file or owned generated fixture')
 info=inspect(p)
 if info['rate']!=rate:raise RuntimeError('DSF sample does not match matrix entry')
 return p

def native_dsd_rate(hw):
 """ALSA DSD frames contain 8, 16 or 32 DSD bits per channel."""
 match=re.fullmatch(r'DSD_U(8|16_LE|16_BE|32_LE|32_BE)',hw.get('format',''))
 if not match:raise RuntimeError('Native DSD format not observed')
 try:rate=int(hw['rate'].split()[0])
 except (KeyError,ValueError,AttributeError):raise RuntimeError('Native DSD frame rate unavailable')
 if rate<=0:raise RuntimeError('Invalid native DSD frame rate')
 return rate*int(match[1].split('_')[0])

def plan(dsf_samples=None,generate=False,seconds=75,hardware=None,representative=False):
 h=scan() if hardware is None else hardware
 result=inventory(h,dsf_samples);created=[]
 if representative:
  import calibration_budget,audio_setup
  prefs=audio_setup.preferences(Path(player.target(h)['path']).read_text())
  transport='native' if prefs.get('USEDOP')=='native' else 'DoP'
  all_entries=result['entries'];result['entries']=calibration_budget.representative(all_entries,transport)
  result.update(scope='Representative PCM families, highest PCM rate and one supported DSD rate in current transport',exhaustive=False,deferred_entries=[e for e in all_entries if e not in result['entries']])

 if generate:
  from dsf_fixture import create
  try:
   rates=sorted({e['rate'] for e in result['entries'] if e['kind']=='DSD' and e['status']=='missing-sample'})
   for rate in rates:
    fixture=create(rate,seconds);created.append(fixture)
    for entry in result['entries']:
     if entry['kind']=='DSD' and entry['rate']==rate and entry['status']=='missing-sample':entry.update(path=fixture['path'],status='pending',generated_fixture_sha256=fixture['sha256'])
  except BaseException:
   for fixture in created:Path(fixture['path']).unlink(missing_ok=True)
   raise
 result['generated_fixtures']=created;return result


def pcm_sample(rate,bits,seconds):
 directory=Path('/mnt/hdd/daphile-autotune-samples')
 if directory.is_symlink():raise RuntimeError('Unsafe test sample directory')
 directory.mkdir(mode=0o755,exist_ok=True)
 if directory.stat().st_uid!=0 or directory.stat().st_mode&0o022:raise RuntimeError('Sample directory not root controlled')
 os.chmod(directory,0o755)
 needed=rate*2*(bits//8)*seconds
 st=os.statvfs(directory)
 if needed+64*1024*1024>st.f_bavail*st.f_frsize:raise RuntimeError('Insufficient sample disk space')
 p=directory/(uuid.uuid4().hex+'.wav')
 try:
  with p.open('xb') as f:
   with wave.open(f,'wb') as wav:
    wav.setnchannels(2);wav.setsampwidth(bits//8);wav.setframerate(rate);remaining=rate*seconds;block=b'\0'*(rate*2*(bits//8))
    while remaining:
     frames=min(rate,remaining);wav.writeframesraw(block[:frames*2*(bits//8)]);remaining-=frames
  os.chmod(p,0o644);return p
 except BaseException:
  if p.exists():p.unlink()
  raise

def run(manifest,seconds=30,state=engine.STATE,margin=1.5):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if isinstance(margin,bool) or not isinstance(margin,(int,float)) or not math.isfinite(margin) or not 1.1<=margin<=5:raise ValueError('Safety margin1.1..5 required')
 if os.geteuid()!=0:raise RuntimeError('Root required')
 if not 10<=seconds<=600:raise ValueError('Matrix observation 10..600 seconds per entry')
 with engine.locked(state) as root:
  engine.ensure_idle_transaction(root);h=scan();player.assert_library_idle(h);t=player.target(h)
  if h['hardware_id']!=manifest['hardware_id'] or t['identity']!=manifest['identity']:raise RuntimeError('Matrix belongs to another hardware/DAC')
  saved=player.playback_snapshot(t['mac']);original=Path(t['path']).read_text();baseline=observe(30)
  if engine.compare(baseline,baseline):raise RuntimeError('Baseline unstable/inconclusive')
  count=sum(e['status']=='pending' for e in manifest['entries']);c=dict(t,parameter='player_buffer',before=original,after=original,write_attempted=False)
  journal=engine.begin_journal(root,h,[c],len(manifest['buffer_candidates'])*count*(seconds+90)+120,'player-matrix');journal['playback_resume']=saved;engine.atomic(root/'transaction.json',journal);watcher=engine.spawn_watchdog(root,journal)
  results=[];failure=None;format_baselines={};retained_samples=[]
  try:
   for ms,periods in manifest['buffer_candidates']:
    if not __import__('buffer_preferences').matrix_candidate(ms,periods,original,read('/opt/spac/share/defaults.conf')):raise RuntimeError('Invalid matrix buffer candidate')
    player.assert_library_idle()
    c.setdefault('accepted_intermediate',[]).append(c['after']);c['after']=player.edit(original,ms,periods);player.guard(c);c['write_attempted']=True;engine.atomic(root/'transaction.json',journal);player.write(c,c['after']);time.sleep(3)
    for entry in manifest['entries']:
     record=dict(entry,buffer_ms=ms,periods=periods)
     if entry['status']!='pending':results.append(record);continue
     sample=None;stage='sample-preparation';cell_started=time.monotonic()
     try:
      stage='library-idle';player.assert_library_idle()
      stage='sample-preparation'
      if entry['kind']=='PCM':sample=pcm_sample(entry['rate'],entry['bits'],seconds+45);path=sample
      else:
       mode=re.search(r'^USEDOP=(.*)$',original,re.M)
       if not mode or mode[1]!=('native' if entry['transport']=='native' else 'on'):
        record.update(status='unvalidated',reason='Current Daphile DSD transport differs; no non-buffer preferences changed');results.append(record);continue
       path=validate_dsf(entry['path'],entry['rate'])
       if entry.get('generated_fixture_sha256'):
        from dsf_fixture import verify_owned
        verify_owned(path,entry['generated_fixture_sha256'])
      dop_log_path='/run/spacd/c/'+str(t['card'])+'/d/'+str(h['dac']['device'])+'/log/squeezelite.log'
      dop_before=read(dop_log_path) or ''
      stage='set-repeat';player.rpc(t['mac'],['playlist','repeat',0])
      stage='set-shuffle';player.rpc(t['mac'],['playlist','shuffle',0])
      stage='playlist-play';player.rpc(t['mac'],['playlist','play',str(path)]);expected_rate=entry['rate'] if entry['kind']=='PCM' else (entry['rate']//16 if entry['transport']=='DoP' else None)
      stage='wait-audio';fresh=player.wait_audio(t['mac'],30,expected_rate=expected_rate,buffers=(ms,periods))
      if fresh['dac'].get('identity')!=t['identity']:raise RuntimeError('DAC changed')
      hw=fresh['dac']['pcm']['hw'];actual_rate=int(hw['rate'].split()[0]);actual_ms=float(hw['buffer_size'])/actual_rate*1000;actual_periods=float(hw['buffer_size'])/float(hw['period_size'])
      if abs(actual_ms-ms)>max(2,ms*.1) or abs(actual_periods-periods)>.15:raise RuntimeError('Requested buffer/periods not applied')
      if entry['kind']=='PCM' and actual_rate!=entry['rate']:raise RuntimeError('PCM output was resampled or rate unsupported')
      if entry['kind']=='DSD':
       if entry['transport']=='native' and native_dsd_rate(hw)!=entry['rate']:raise RuntimeError('Native DSD rate differs from source')
       if entry['transport']=='DoP':
        log=read(dop_log_path) or '';new_log=log[len(dop_before):] if log.startswith(dop_before) else log
        if actual_rate!=entry['rate']//16 or not re.search(r'(?i)file contains DOP',new_log):raise RuntimeError('DoP transport not confirmed')
      stage='position-before';start_position=float(player.rpc(t['mac'],['status','-','1']).get('time',0))
      stage='observation';measured=observe(seconds)
      stage='scan-after';end=scan()
      stage='position-after';end_position=float(player.rpc(t['mac'],['status','-','1']).get('time',0));signature=(entry['kind'],entry['rate'],entry.get('bits'),entry.get('transport'))
      reference=format_baselines.setdefault(signature,measured);reasons=engine.compare(reference,measured)
      if end_position-start_position<seconds*.7:reasons.append('Track did not advance through the observation window')
      if not all(engine.health(end).values()) or end['dac'].get('identity')!=t['identity'] or end['dac'].get('pcm',{}).get('hw')!=hw:reasons.append('Playback/format changed during observation')
      record.update(status='pass-observed-window' if not reasons else 'failed',actual_hw=hw,measurement=measured,reasons=reasons)
     except Exception as exc:
      pcm='/proc/asound/card'+str(t['card'])+'/pcm'+str(h['dac']['device'])+'p/sub0/'
      record.update(status='failed',reason=str(exc),error_type=type(exc).__name__,failure_stage=stage,elapsed_seconds=round(time.monotonic()-cell_started,3),failure_alsa={name:read(pcm+name) for name in ['hw_params','status']})
     finally:
      stopped=False
      try:player.rpc(t['mac'],['stop']);stopped=True
      except Exception as exc:record.update(status='failed',cleanup_error_type=type(exc).__name__,cleanup_error=str(exc))
      finally:
       if sample and sample.exists():
        if stopped:sample.unlink()
        else:retained_samples.append(sample);record['retained_sample']=str(sample)
     results.append(record)
     engine.atomic(root/'matrix.json',dict(status='running',results=results,manifest=manifest,full_validation=False))
     if record['status']=='failed':break
    if any(x['status']=='failed' and x['buffer_ms']==ms and x['periods']==periods for x in results):break
  except BaseException as exc:failure=str(exc);raise
  finally:
   recovered=engine.rollback(root,journal);engine.close_watchdog(watcher,journal)
   if recovered['status']=='rolled-back':
    for sample in retained_samples:
     if sample.exists():sample.unlink()
   groups=[]
   for ms,periods in manifest['buffer_candidates']:
    rows=[x for x in results if x['buffer_ms']==ms and x['periods']==periods]
    complete=len(rows)==len(manifest['entries']) and all(x['status'] in ['pass-observed-window','unsupported'] for x in rows)
    groups.append(dict(buffer_ms=ms,periods=periods,complete=complete))
   passed=[x['buffer_ms'] for x in groups if x['complete']];minimum=min(passed) if passed else None
   recommendation=next((ms for ms in sorted({x['buffer_ms'] for x in groups if x['complete']}) if minimum and ms>=minimum*margin),None)
   report=dict(scope=manifest.get('scope'),exhaustive=manifest.get('exhaustive',True),deferred_entries=manifest.get('deferred_entries',[]),status='finished' if failure is None else 'failed',results=results,groups=groups,minimum_tested_ms=minimum,recommended_margin_ms=recommendation,safety_margin=margin,recommended_candidate=next((g for g in groups if g['complete'] and g['buffer_ms']==recommendation),None),matrix_completed=bool(passed),full_validation=False,error=failure,restoration=recovered['status'],sound_quality_claim=False)
   engine.atomic(root/'matrix.json',report)
  return report

__release_signature__ = 'Tino Zappala'
