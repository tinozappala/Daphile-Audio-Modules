"""Install the Camilla package in the existing audio host, retaining rollback."""
import ctypes,hashlib,http.client,io,json,os,shutil,subprocess,sys,time,zipfile
from pathlib import Path
from tempfile import TemporaryDirectory,mkdtemp

HOST=Path('/mnt/hdd/system/daphile-audio-host')
MODULE='DaphileCamillaDSP'
assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
assert Path('/version').read_text().strip()=='fw2505251550'

def atomic(path,data,mode):
 p=path.with_name('.'+path.name+'.'+os.urandom(8).hex())
 try:
  with p.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  p.chmod(mode);os.replace(p,path)
 finally:
  if p.exists():p.unlink()

def exchange(a,b):
 fn=ctypes.CDLL(None,use_errno=True).renameat2
 fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
 if fn(-100,os.fsencode(a),-100,os.fsencode(b),2):
  code=ctypes.get_errno();raise OSError(code,os.strerror(code))

def sync():
 r=subprocess.run([sys.executable,'-B',str(HOST/'code/hostctl.py'),'sync','--root',str(HOST),'--module',MODULE],capture_output=True,timeout=45)
 if r.returncode:raise RuntimeError('Lifecycle Camilla: '+r.stderr.decode(errors='replace')[:500])
 return json.loads(r.stdout)

def request(path,body=None,cookie=None):
 h={}
 if body is not None:h['Content-Type']='application/json'
 if cookie:h['Cookie']=cookie
 c=http.client.HTTPConnection('127.0.0.1',80,timeout=22)
 try:
  c.request('GET' if body is None else 'POST','/cgi-bin/CamillaPlugin/'+path,None if body is None else json.dumps(body),h)
  r=c.getresponse();b=r.read(2*1024*1024)
  if r.status!=200:raise RuntimeError('CGI Camilla '+path+': '+str(r.status)+' '+b[:160].decode(errors='replace'))
  return b,r.getheader('Set-Cookie')
 finally:c.close()

def session():
 raw,cookie=request('api/session');assert json.loads(raw)['authenticated'] and cookie
 return cookie.split(';')[0]

def get(path,cookie):
 return json.loads(request('api/'+path,cookie=cookie)[0])

archive_bytes=sys.stdin.buffer.read(16*1024*1024)
assert hashlib.sha256(archive_bytes).hexdigest()==ENTRY['sha256']
config=json.loads((HOST/'config.json').read_text())
state=HOST/'modules'/MODULE
private=state/'data'
routefiles=('native-audio-session.json','native-router.json','audio-processes.json')
assert not any((private/name).exists() for name in routefiles),'Percorso Camilla occupato'
native=Path(config['plugins'])/MODULE
assert native.is_dir() and not native.is_symlink()
old=json.loads((state/'current.json').read_text())
assert old['status']=='running' and old['version']=='0.4.50'
catalog_path=HOST/'approvals.json';catalog_bytes=catalog_path.read_bytes();catalog=json.loads(catalog_bytes)
old_approval=next(e for e in catalog[MODULE] if e['sha256']==old['release'])
cookie=session()
audio_before=get('status',cookie)['audio']
assert not audio_before['active'] and (not audio_before['enabled'] or audio_before['mode']=='armed-idle'),audio_before['mode']
before=get('presets',cookie)
active_before=(private/'active_profile.json').read_bytes() if (private/'active_profile.json').exists() else None
intent_before=(private/'peq-intent.json').read_bytes() if (private/'peq-intent.json').exists() else None
backup=Path(mkdtemp(prefix='camilla-0.4.51-',dir=HOST/'recovery'))
atomic(backup/'approvals.json',catalog_bytes,0o600)
atomic(backup/'current.json',json.dumps(old).encode(),0o600)
shutil.copytree(native,backup/MODULE)
for name in ('presets.json','presets.previous.json','active_profile.json','peq-intent.json','preset-slots.json'):
 p=private/name
 if p.is_file():shutil.copy2(p,backup/name)
archive=HOST/'bundles'/ENTRY['archive']
if archive.exists():assert archive.read_bytes()==archive_bytes
else:atomic(archive,archive_bytes,0o600)
with TemporaryDirectory(prefix='.camilla-update-',dir=native.parent) as folder:
 staged=Path(folder)/MODULE;staged.mkdir(mode=native.stat().st_mode&0o777)
 with zipfile.ZipFile(io.BytesIO(archive_bytes)) as z:
  assert z.testzip() is None and sorted(z.namelist())==sorted(MODULE+'/'+n for n in ENTRY['files'])
  for name,digest in ENTRY['files'].items():
   rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
   content=z.read(MODULE+'/'+name);assert hashlib.sha256(content).hexdigest()==digest
   dest=staged/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
 if catalog_path.read_bytes()!=catalog_bytes:raise RuntimeError('Catalogo cambiato durante la preparazione')
 catalog[MODULE].append(ENTRY)
 changed=False
 try:
  atomic(catalog_path,json.dumps(catalog).encode(),0o600)
  exchange(native,staged);changed=True
  observed=sync()
  assert observed['running'] and observed['current']['release']==ENTRY['sha256'],observed
  assert observed['current']['previous']==old_approval['sha256']
  cookie=session()
  deadline=time.monotonic()+20
  while True:
   status=get('status',cookie)
   if status['module_version']==ENTRY['version']:break
   if time.monotonic()>deadline:raise RuntimeError('Nuovo controller Camilla non disponibile')
   time.sleep(.2)
  assert status['audio']['enabled']==audio_before['enabled'] and not status['audio']['active']
  after=get('presets',cookie)
  original={p['id']:p for p in before['presets']}
  latest={p['id']:p for p in after['presets']}
  defaults={p['id']:p for p in DEFAULTS}
  assert all(latest.get(k)==v for k,v in original.items() if k not in defaults)
  assert all(latest.get(k)==v for k,v in defaults.items())
  def settings(profile):
   import copy
   value=copy.deepcopy(profile)
   for k in ('id','name'):value.pop(k)
   for bands in value['peq'].values():
    for band in bands:band.pop('id',None)
   return value
  for key,default in defaults.items():
   old_profile=original[key]
   if settings(old_profile)!=settings(default) or old_profile['name']!=default['name']:
    assert any(k not in original and settings(v)==settings(old_profile) and v['name']==old_profile['name'][:60]+' · personale' for k,v in latest.items())
  assert (private/'active_profile.json').read_bytes() if active_before is not None else not (private/'active_profile.json').exists()
  if active_before is not None:assert (private/'active_profile.json').read_bytes()==active_before
  if intent_before is not None:assert (private/'peq-intent.json').read_bytes()==intent_before
  assert not any((private/name).exists() for name in routefiles)
  assert hashlib.sha256(request('app.js')[0]).hexdigest()==ENTRY['files']['backend/ui/app.js']
  print(json.dumps({'phase':'INSTALLED','version':ENTRY['version'],'sha256':ENTRY['sha256'],
                    'existing_presets_preserved':True,'personal_copies':[v['name'] for k,v in latest.items() if k not in original],'active_profile_unchanged':True,'native_route':status['audio']['mode'],'old_version':old['version']}),flush=True)
 except BaseException:
  if changed:exchange(native,staged)
  atomic(catalog_path,catalog_bytes,0o600)
  recovered=sync()
  print(json.dumps({'phase':'ROLLBACK','version':recovered.get('current',{}).get('version')}),flush=True)
  raise
