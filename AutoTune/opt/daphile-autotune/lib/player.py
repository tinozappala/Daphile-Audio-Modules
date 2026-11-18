"""Verified Daphile per-card buffer adapter and local LMS playback control."""
import hashlib,json,os,re,stat,subprocess,sqlite3,time,urllib.request,urllib.parse,uuid
from pathlib import Path
from detection import read,scan,usb_devices,processes

OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def rpc(player,cmd):
 req=urllib.request.Request('http://127.0.0.1:9000/jsonrpc.js',data=json.dumps(dict(id=1,method='slim.request',params=[player,cmd])).encode(),headers={'Content-Type':'application/json'})
 with OPENER.open(req,timeout=5) as response:result=json.loads(response.read(4*1024*1024))
 if result.get('error'):raise RuntimeError('LMS command rejected')
 return result['result']

def assert_library_idle(h=None):
 procs=h.get('squeezelite',[]) if h is not None else processes()
 if any(p.get('scanner') for p in procs):raise RuntimeError('Library scanner active; audio trial deferred before changing playback')
 status=rpc('', ['serverstatus','0','1'])
 if not status.get('version'):raise RuntimeError('Library scan state unavailable; audio trial refused')
 if int(status.get('rescan',0)):raise RuntimeError('LMS scan active; audio trial deferred before changing playback')

def target(h):
 d=h['dac'];u=d.get('usb') or {}
 if d.get('status')!='verified-active' or not __import__('usb_target').identified(__import__('usb_target').identity(u)):raise RuntimeError('DAC USB attivo non identificabile in modo univoco')
 if __import__('usb_target').identity(u)!=d.get('identity'):raise RuntimeError('DAC USB identity differs from discovery')
 card=Path('/run/spacd/c')/str(d['card']);home=(card/'home').resolve(strict=True);path=home/'volume.conf'
 if not str(home).startswith('/mnt/hdd/system/spac/') or path.is_symlink() or not path.is_file():raise RuntimeError('Unverified Daphile preference location')
 macs=list((card/'d').glob('*/home/macaddr'))
 if len(macs)!=1:raise RuntimeError('Ambiguous Daphile player')
 mac=read(macs[0])
 if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac or ''):raise RuntimeError('Invalid LMS player identity')
 return dict(host_uuid=read('/sys/class/dmi/id/product_uuid'),path=str(path),identity=dict(d['identity']),alsa_id=d['alsa_id'],card=d['card'],mac=mac)

def edit(text,ms,periods):
 return __import__('buffer_preferences').edit(text,ms,periods,read('/opt/spac/share/defaults.conf'))

def guard(c,h=None,restoring=False):
 if not c.get('host_uuid') or c['host_uuid']!=read('/sys/class/dmi/id/product_uuid'):raise RuntimeError('Buffer profile belongs to a different host')
 matches=[u for u in usb_devices() if all(u.get(k)==v for k,v in c['identity'].items())]
 if len(matches)!=1 or not __import__('usb_target').identified(c['identity']):raise RuntimeError('Buffer target DAC removed/replaced')
 cards=[p for p in Path('/proc/asound').glob('card[0-9]*') if read(p/'id')==c['alsa_id']]
 if len(cards)!=1:raise RuntimeError('ALSA identity absent or ambiguous')
 card=int(cards[0].name[4:]);home=(Path('/run/spacd/c')/str(card)/'home').resolve(strict=True)
 if c['path']!=str(home/'volume.conf') or not str(home).startswith('/mnt/hdd/system/spac/') or (home/'volume.conf').is_symlink():raise RuntimeError('Preferences moved or replaced')
 if c['parameter']!='player_buffer':raise RuntimeError('Invalid preference action')
 def stripped(s):return __import__('buffer_preferences').non_buffer(s)
 if stripped(c['before'])!=stripped(c['after']):raise RuntimeError('Non-buffer preference change rejected')
 __import__('buffer_preferences').validate_transition(c['before'],c['after'],read('/opt/spac/share/defaults.conf'))
 return card

def current(c):return Path(c['path']).read_text()
def write(c,text):
 card=guard(c,restoring=True);path=Path(c['path'])
 if path.read_text() not in [c['before'],c['after'],*c.get('accepted_intermediate',[])]:raise RuntimeError('Preferences changed concurrently; no overwrite')
 old=path.stat();temp=path.with_name('.autotune-'+uuid.uuid4().hex)
 try:
  with temp.open('x',encoding='utf-8',newline='') as stream:
   os.chmod(temp,stat.S_IMODE(old.st_mode));os.chown(temp,old.st_uid,old.st_gid);stream.write(text);stream.flush();os.fsync(stream.fileno())
  guard(c,restoring=True);os.replace(temp,path);fd=os.open(path.parent,os.O_DIRECTORY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if temp.exists():temp.unlink()
 # Use the same per-card restart request as native cardconf.cgi; bounded FIFO writer.
 ready=Path('/run/spacd/c')/str(card)/'ready'
 prior_ready=ready.stat().st_mtime_ns if ready.exists() else None
 subprocess.run(['/opt/spac/bin/spac-msg','restart','card'+str(card)],check=True,timeout=5)
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if ready.exists() and ready.stat().st_mtime_ns!=prior_ready:break
  time.sleep(.1)
 else:raise RuntimeError('Daphile card restart did not become ready')
 time.sleep(1)
 if current(c)!=text:raise RuntimeError('Buffer preference readback failed')

def playback_snapshot(mac):
 from playback_contract import snapshot
 return snapshot(mac,rpc)

def lms_item(url):
 # LMS playlistXitemCommand treats file:// as a missing filesystem path and
 # silently skips it while the library scanner is active. Existing decoded
 # absolute paths take its real-file branch without altering playback flags.
 parsed=urllib.parse.urlparse(url)
 if parsed.scheme=='file' and parsed.netloc in ['', 'localhost'] and not parsed.query and not parsed.fragment:
  path=Path(urllib.parse.unquote(parsed.path))
  if path.is_absolute() and path.is_file():return str(path)
 return url

def playlist_key(url):
 parsed=urllib.parse.urlparse(url)
 # Native Volatile.pm maps literal lowercase tmp to the SAME local file.
 # Compare only canonical native local forms, never rewrite saved URLs or RPCs.
 local=urllib.parse.unquote(parsed.path)
 if (url.startswith(('tmp:///','tmp://localhost/')) and parsed.scheme=='tmp'
     and parsed.netloc in ('', 'localhost') and local.startswith('/')
     and not local.startswith('//') and '..' not in local.split('/')
     and '\\' not in local and '\x00' not in local):
  parsed=parsed._replace(scheme='file')
 if parsed.scheme=='file' and parsed.netloc in ['', 'localhost']:
  return (str(Path(urllib.parse.unquote(parsed.path)).resolve()),parsed.query,parsed.fragment)
 return url

def may_reference_fixture(url,fixture,identity=None):
 # DENY-only resource-reference predicate, NEVER an owned-queue/restore grant.
 # Native FILE/Volatile opening can ignore query/fragment or normalize paths;
 # playlist_key deliberately keeps those intentions DISTINCT.
 try:
  if not isinstance(url,str)or not url:return True
  target=Path(fixture).resolve()
  decoded_url=urllib.parse.unquote(url)
  # The unique generated filename anywhere in a URL is sufficient to RETAIN,
  # including an opaque wrapper/query. It never authorizes any operation.
  if target.name in decoded_url:return True
  parsed=urllib.parse.urlparse(url.replace('\\','/'))
  if parsed.scheme.lower()not in ('file','tmp',''):return True
  if parsed.netloc not in ('','localhost'):return True
  local=urllib.parse.unquote(parsed.path)
  if not local.startswith('/')or '\x00'in local:return True
  possible=Path(local).resolve()
  if possible==target:return True
  expected=None
  if identity is not None:
   if not isinstance(identity,dict)or any(type(identity.get(k))is not int or identity[k]<0 for k in('device','inode')):return True
   expected=(identity['device'],identity['inode'])
  else:
   try:
    info=Path(fixture).stat();expected=(info.st_dev,info.st_ino)
   except FileNotFoundError:pass
  if expected is not None:
   try:
    info=possible.stat()
    if (info.st_dev,info.st_ino)==expected:return True
   except FileNotFoundError:pass
  return False
 except (OSError,RuntimeError,ValueError,TypeError):return True


def catalog_ids(urls):
 # Reuse catalog entries during recovery: file rescans can contend with the
 # NAS scanner's transaction. Resolve IDs afresh because rescans may replace IDs.
 database=Path('/mnt/hdd/system/mediaserver/cache/library.db')
 if not database.is_file():return [None]*len(urls)
 db=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True,timeout=3)
 try:
  result=[]
  for url in urls:
   row=db.execute('SELECT id FROM tracks WHERE url=?',(url,)).fetchone() if urllib.parse.urlparse(url).scheme=='file' else None
   result.append(row[0] if row else None)
  return result
 finally:db.close()

def settle_mode(mac,mode,timeout=20):
 # Seeking starts the decoder asynchronously; an immediate pause can be lost.
 # Verify twice after the command and retry only the idempotent target state.
 if mode not in ['play','pause','stop']:raise RuntimeError('Unknown saved playback mode')
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  if mode=='pause':rpc(mac,['pause',1])
  elif mode=='stop':rpc(mac,['stop'])
  else:rpc(mac,['play'])
  time.sleep(.5);first=rpc(mac,['status','-','1'])
  if first.get('mode')!=mode:continue
  time.sleep(.5);second=rpc(mac,['status','-','1'])
  if second.get('mode')==mode and (mode!='pause' or abs(float(second.get('time',0))-float(first.get('time',0)))<.2):return second
 raise RuntimeError('Restored playback mode did not settle')

def restore_playback(saved):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if saved.get('mode') in ('stop','pause'):
  return __import__('quiet_playback').restore_inactive(saved)
 ids=catalog_ids(saved['urls'])
 mac=saved['mac'];deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  if rpc(mac,['connected','?']).get('_connected')==1:break
  time.sleep(.5)
 else:raise RuntimeError('LMS player did not reconnect')
 rpc(mac,['stop']);rpc(mac,['playlist','clear']);rpc(mac,['playlist','shuffle',0]);rpc(mac,['playlist','repeat',0])
 for url,ident in zip(saved['urls'],ids):
  if ident is not None:rpc(mac,['playlistcontrol','cmd:add','track_id:'+str(ident)])
  else:rpc(mac,['playlist','add',lms_item(url)])
 restored=rpc(mac,['status','0','1000','tags:u'])
 actual=restored.get('playlist_loop',[])
 if int(restored.get('playlist_tracks',0))!=len(saved['urls']) or [playlist_key(x.get('url','')) for x in actual]!=[playlist_key(x) for x in saved['urls']]:raise RuntimeError('Original playlist count/order not restored')
 try:
  if saved['urls']:
   rpc(mac,['playlist','index',saved['index']]);rpc(mac,['time',saved['time']])
   if saved['mode']=='play':settle_mode(mac,'play')
 finally:
  # Restore flags even when a seek/control request times out.
  try:
   if saved['urls'] and saved['mode']!='play':settle_mode(mac,saved['mode'])
  finally:
   try:rpc(mac,['playlist','repeat',saved['repeat']])
   finally:rpc(mac,['playerpref','shuffle',int(saved['shuffle'])])
 if saved['urls'] and saved['mode']=='play':wait_audio(mac,30)

def wait_audio(mac,timeout=20,expected_rate=None,buffers=None):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  h=scan();d=h['dac'];hw=d.get('pcm',{}).get('hw',{});valid=False
  try:
   rate=int(hw['rate'].split()[0]);valid=expected_rate is None or rate==expected_rate
   if buffers:
    ms,periods=buffers;actual=float(hw['buffer_size'])/rate*1000;count=float(hw['buffer_size'])/float(hw['period_size']);valid=valid and abs(actual-ms)<=max(2,ms*.1) and abs(count-periods)<=.15
  except (KeyError,ValueError,ZeroDivisionError):valid=False
  if valid and d.get('status')=='verified-active' and 'RUNNING' in (d['pcm'].get('status') or '') and rpc(mac,['status','-','1']).get('mode')=='play':return h
  time.sleep(.5)
 raise RuntimeError('Expected playback format/buffering did not resume')

def tune_buffer(ms,periods=4,seconds=30,state=None):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 import engine
 if os.geteuid()!=0:raise RuntimeError('Root required')
 if not 30<=seconds<=3000:raise ValueError('Observation 30..3000 seconds')
 with engine.locked(state or engine.STATE) as root:
  engine.ensure_idle_transaction(root)
  h=scan();t=target(h);assert_library_idle(h);before_text=Path(t['path']).read_text();after_text=edit(before_text,ms,periods)
  if before_text==after_text:return dict(status='no-change',buffer_ms=ms,periods=periods)
  if not all(engine.health(h).values()):raise RuntimeError('Playback health incomplete; buffer trial refused')
  before=engine.observe(seconds)
  assert_library_idle()
  if engine.compare(before,before):raise RuntimeError('Unstable/inconclusive baseline')
  saved=playback_snapshot(t['mac']);c=dict(t,parameter='player_buffer',before=before_text,after=after_text,write_attempted=False)
  journal=engine.begin_journal(root,h,[c],seconds+120,'player-buffer');journal['playback_resume']=saved;engine.atomic(root/'transaction.json',journal)
  watcher=engine.spawn_watchdog(root,journal)
  try:
   guard(c);c['write_attempted']=True;engine.atomic(root/'transaction.json',journal);write(c,after_text)
   time.sleep(3);rpc(t['mac'],['play']);rpc(t['mac'],['time',saved['time']]);fresh=wait_audio(t['mac'],expected_rate=int(h['dac']['pcm']['hw']['rate'].split()[0]),buffers=(ms,periods))
   if fresh['dac']['identity']!=t['identity']:raise RuntimeError('DAC changed')
   hw=fresh['dac']['pcm']['hw'];rate=int(hw['rate'].split()[0]);actual=float(hw['buffer_size'])/rate*1000;actual_periods=float(hw['buffer_size'])/float(hw['period_size'])
   if abs(actual-ms)>max(2,ms*.1) or abs(actual_periods-periods)>.15:raise RuntimeError('Player did not apply requested ALSA parameters')
   after=engine.observe(seconds);reasons=engine.compare(before,after)
   end=scan()
   if end['hardware_id']!=h['hardware_id'] or end['dac'].get('pcm',{}).get('hw')!=fresh['dac']['pcm']['hw']:reasons.append('Hardware or audio format changed')
   if reasons or not all(engine.health(end).values()):raise RuntimeError('Post-change observation failed: '+str(reasons))
   journal.update(status='committed',profile='player-buffer',benchmark=dict(before=before,after=after),actual_alsa=dict(buffer_ms=actual,periods=actual_periods),validation='observed-window-only')
   engine.atomic(root/'transaction.json',journal);engine.atomic(root/'current.json',journal);engine.atomic(root/'last-known-good.json',journal);engine.log(root,dict(event='buffer-committed',id=journal['id']));return dict(status='committed',actual_alsa=journal['actual_alsa'],full_matrix_validated=False)
  except BaseException:engine.rollback(root,journal);raise
  finally:engine.close_watchdog(watcher,journal)


def restore_good_buffer(saved,state):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if saved.get('status')!='committed' or saved.get('profile')!='player-buffer':raise RuntimeError('No committed last-known-good buffer')
 changes=[c for c in saved.get('changes',[]) if c.get('parameter')=='player_buffer']
 if len(changes)!=1:raise RuntimeError('Ambiguous saved buffer target')
 c=changes[0];h=scan();t=target(h)
 if h['hardware_id']!=saved['hardware_id'] or t['host_uuid']!=c.get('host_uuid') or t['identity']!=c.get('identity'):raise RuntimeError('Last-known-good buffer belongs to different hardware/DAC')
 # Replay only the buffer numbers, never old volume/filter settings or thread IDs.
 text=c['after'];values={}
 for key in ['BUFFERTIME','PERIODCOUNT']:
  matches=re.findall(r'^'+key+r'=(\d+)$',text,re.M)
  if len(matches)!=1:raise RuntimeError('Invalid saved buffer preference')
  values[key]=int(matches[0])
 edit(text,values['BUFFERTIME'],values['PERIODCOUNT'])
 return tune_buffer(values['BUFFERTIME'],values['PERIODCOUNT'],30,state)

__release_signature__ = 'Tino Zappala'
