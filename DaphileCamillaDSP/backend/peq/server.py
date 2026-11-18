"""Password-free local/LAN panels with automatic same-origin browser sessions."""
import argparse,copy,datetime,hashlib,hmac,http.cookies,ipaddress,json,os,pathlib,secrets,threading,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from . import __version__
from .model import analysis,level_analysis,compile_config,ValidationError,validate,RATES
from .store import Store,ConflictError
from .slots import Slots
from .rew import preview as rew_preview
from .engine import Engine,EngineError,engine_transaction
from .comparison import Comparison
ROOT=pathlib.Path(__file__).resolve().parents[1]
MAX_BODY=131072
def unique_object(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValidationError('Duplicate JSON member')
  result[key]=value
 return result
class Application:
 def __init__(self,state_dir,route=None,port=1234,audio=None):
  self.state_dir=pathlib.Path(state_dir); self.state_dir.mkdir(parents=True,exist_ok=True)
  self.store=Store(self.state_dir/'presets.json'); self.engine=Engine(port); self.route=route; self.apply_lock=threading.RLock()
  self.slots=Slots(self.state_dir/'preset-slots.json',validate)
  self.store.import_slots_once(self.slots)
  self.audio=audio
  if audio:audio.attach(self)
  self.comparison=Comparison(self)
  tokenfile=self.state_dir/'access.token'
  if not tokenfile.exists():
   fd=os.open(tokenfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
   with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(32))
  self.token=tokenfile.read_text().strip()
 def audit(self,event,result):
  record={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':event,'result':result}
  try:
   fd=os.open(self.state_dir/'audit.jsonl',os.O_APPEND|os.O_CREAT|os.O_WRONLY,0o600)
   with os.fdopen(fd,'a') as f: f.write(json.dumps(record)+'\n')
  except OSError: pass
 def current_profile(self):
  with self.apply_lock:
   path=self.state_dir/'active_profile.json'
   if not path.exists():return self.store.snapshot()['presets'][0]
   if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BODY:raise ValidationError('Profilo corrente non verificato')
   return validate(json.loads(path.read_text()))
 def profile_state(self):
  with self.apply_lock:
   profile=self.current_profile()
   revision=hashlib.sha256(json.dumps(profile,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
   return {'profile':profile,'profile_revision':revision}
 def native_audio_status(self):
  # A broker read must never wait behind a long handoff or query the DSP/LMS.
  # The audio status uses this same RLock, so its nested acquisition is local.
  if not self.apply_lock.acquire(timeout=.1):raise TimeoutError('Native audio state is busy')
  try:return {'pid':os.getpid(),'audio':self.audio.status() if self.audio else None}
  finally:self.apply_lock.release()
 def status(self):
  if not self.audio:return self.audio_status()
  with self.apply_lock:
   audio=self.audio.status();route=self.route
  # Engine status is a bounded network observation. It must not block the
  # local writer-stop guard while the USB buffer is draining.
  result=self.audio_status(audio)
  with self.apply_lock:
   current=self.audio.status()
   if current!=audio or self.route is not route:
    return dict(connected=False,state='Transition',fault=None,xrun=None,rss_bytes=None,cpu_process_pct=None,comparison_active=False,
        module_version=__version__,routing_prepared=self.route is not None,dac=None,sample_rate=None,chunksize=None,
        audio=current,audio_path=current['message'],dsd='Bypass nativo/DoP; nessuna conversione PCM')
  return result
 def audio_status(self,audio=None):
  status=self.engine.status() if audio is None or audio['active'] else {'connected':False,'state':'Disconnected','fault':None,'xrun':None,'rss_bytes':None,'cpu_process_pct':None}
  status.update(comparison_active=False,module_version=__version__,routing_prepared=self.route is not None,dac=None,sample_rate=None,chunksize=None,dsd='NON VERIFICATO',audio_path='Originale: non modificato da questo controller')
  if status['connected']:
   from .engine import WebSocket
   try:
    ws=WebSocket(self.engine.port)
    try: cfg=json.loads(ws.command('GetConfigJson'))
    finally: ws.close()
    if cfg:
     status['sample_rate']=cfg['devices']['samplerate']; status['chunksize']=cfg['devices']['chunksize']; status['dac']=cfg['devices']['playback'].get('device')
     status['audio_path']='CamillaDSP: stato rilevato; routing player da verificare'
     with self.apply_lock:
      status['comparison_active']=bool(self.comparison.active() and not status['fault'] and status['mute'] is False
          and status['state']=='Running' and self.comparison.confirmed_config==cfg)
   except (OSError,ValueError,EngineError,KeyError): pass
  if self.audio:
   status['audio']=audio
   status['audio_path']=audio['message'];status['dsd']='Bypass nativo/DoP; nessuna conversione PCM'
  return status
 def config_for_route(self,profile,current=None):
  """Compile only against the trusted, already selected PCM devices."""
  route=self.route
  if route is None:raise ValidationError('Percorso audio non disponibile.')
  rate=route['rate'];chunk=route['chunksize'];capture=route['capture'];playback=route['playback']
  if route.get('follow_engine_rate',False):
   if current is None:
    from .engine import WebSocket
    ws=WebSocket(self.engine.port)
    try:current=json.loads(ws.command('GetConfigJson'))
    finally:ws.close()
   devices=current['devices']
   if not str(current.get('title','')).startswith('Daphile PEQ ') or devices['capture'].get('device')!=capture.get('device') or devices['capture'].get('type')!=capture.get('type') or not self.engine.matches(playback,devices['playback']):
    raise ValidationError('Active engine does not match the prepared routing ownership')
   if devices['capture'].get('format') not in ('S16_LE','S24_3_LE','S24_4_RJ_LE','S32_LE','F64_LE'):
    raise ValidationError('Active input format is not qualified PCM')
   rate=devices['samplerate'];chunk=devices['chunksize'];capture=devices['capture']
  from .buffering import apply_targets
  return apply_targets(compile_config(profile,rate,chunk,capture,playback),route)

 def post(self,path,data):
  if not isinstance(data,dict):raise ValidationError('JSON object required')
  # Pure preview work must never acquire the native audio/stop-guard lock.
  if path=='/api/levels':return level_analysis(data['profile'],data.get('rate',48000))
  if path=='/api/preview':return analysis(data['profile'],data.get('rate',48000))
  if path=='/api/rew/preview':return rew_preview(data['text'],data.get('channel','L'),data.get('rate',48000))
  if path=='/api/apply' and 'comparison' in data:
   return self.comparison.request(data)
  with self.apply_lock:return self.post_locked(path,data)
 def post_locked(self,path,data):
  if not isinstance(data,dict): raise ValidationError('JSON object required')
  if path in ('/api/profile/prepare','/api/apply') and self.comparison.active():
   raise ConflictError('Termina il confronto in corso prima di cambiare il profilo.')
  if path in ('/api/profile/prepare','/api/apply','/api/audio/enable') and 'expected' in data:
   if data['expected']!=self.profile_state()['profile_revision']:
    raise ConflictError('Impostazioni aggiornate in un’altra finestra. Rileggi dal dispositivo.')
  if path=='/api/profile/prepare':
   # OFF edits persist without a device, route, player or engine command.
   if self.audio is None or self.audio.status().get('enabled'):
    raise ValidationError('Lo stato audio è cambiato; rileggere prima di sincronizzare')
   profile=validate(data['profile']);profile['enabled']=True;profile['bypass']=False
   self.store._atomic(self.state_dir/'active_profile.json',profile)
   return dict(prepared=True,pending=True,profile_revision=self.profile_state()['profile_revision'])
  if path=='/api/audio/enable':
   if self.audio is None:raise ValidationError('Native audio adapter unavailable')
   enabled=data.get('enabled')
   allowed={'enabled','profile'} if enabled else {'enabled'}
   if type(enabled) is not bool or set(data)-{'expected'}!=allowed:
    raise ValidationError('Stato equalizzatore non valido')
   if enabled:
    profile=validate(data['profile']);profile['enabled']=True;profile['bypass']=False
    if self.audio.status().get('enabled'):
     return dict(self.audio.status(),already_enabled=True)
    self.store._atomic(self.state_dir/'active_profile.json',profile)
   try:result=self.audio.set_enabled(enabled)
   except RuntimeError as error:
    self.audit('equalizer_enable' if enabled else 'equalizer_disable','failed')
    raise EngineError(str(error)) from error
   self.audit('equalizer_enable' if enabled else 'equalizer_disable','ok')
   if not enabled:self.comparison.clear()
   return dict(result,profile_revision=self.profile_state()['profile_revision'])
  if path in ('/api/audio/start','/api/audio/bypass'):
   if data:raise ValidationError('Audio control takes no parameters')
   if self.audio is None:raise ValidationError('Native audio adapter unavailable')
   try:result=self.audio.start() if path=='/api/audio/start' else self.audio.bypass(resume=True,disable=True)
   except RuntimeError as error:
    self.audit('native_audio_start' if path.endswith('/start') else 'native_audio_bypass','failed')
    raise EngineError(str(error)) from error
   if path.endswith('/bypass'):self.comparison.clear()
   self.audit('native_audio_start' if path.endswith('/start') else 'native_audio_bypass','ok')
   return result
  if path=='/api/preview': return analysis(data['profile'],data.get('rate',48000))
  if path in ('/api/slots/save','/api/slots/delete'):
   raise ValidationError('I preset sono riuniti nel menu Preset. Ricarica la pagina e usa Salva preset.')
  if path=='/api/presets/save':
   result=self.store.mutate(data['revision'],'save',profile=data['profile']); self.audit('preset_save','ok'); return result
  if path=='/api/presets/delete':
   result=self.store.mutate(data['revision'],'delete',preset_id=data['id']); self.audit('preset_delete','ok'); return result
  if path=='/api/presets/restore':
   result=self.store.mutate(data['revision'],'restore'); self.audit('preset_restore','ok'); return result
  if path=='/api/rew/preview': return rew_preview(data['text'],data.get('channel','L'),data.get('rate',48000))
  if path in ('/api/compile','/api/apply'):
   if self.audio:
    state=self.audio.status()
    if path=='/api/apply' and state.get('enabled') and not state.get('active') and state['mode'] in ('armed-idle','waiting-pcm','dsd-bypass','armed-unsupported'):
     profile=validate(data['profile']);profile['enabled']=True;profile['bypass']=False
     self.store._atomic(self.state_dir/'active_profile.json',profile)
     self.audit('profile_ready_for_pcm','ok')
     return dict(state='Ready',pending=True,profile_revision=self.profile_state()['profile_revision'],message='Modifiche pronte per il prossimo audio compatibile')
    if self.route is None or not state.get('active'):
     raise ValidationError('Routing not prepared; original audio path remains in use')
    # Applying a curve is exclusively a filter transaction. Never run a
    # routing/start/resume step here; the independent monitor owns transitions.
   if self.route is None: raise ValidationError('Routing not prepared; original audio path remains in use')
   with self.apply_lock,engine_transaction(self.engine.port):
    cfg=self.config_for_route(data['profile'])
    rate=cfg['devices']['samplerate']
    if path=='/api/compile': return cfg
    with self.apply_lock,engine_transaction(self.engine.port):
     if not self.route.get('verified_pcm',False): raise ValidationError('PCM path not verified; DSD must bypass')
     try: result=self.engine.apply(cfg)
     except EngineError:
      self.audit('engine_apply','failed'); raise
     try:
      self.store._atomic(self.state_dir/'last_valid_config.json',cfg)
      self.store._atomic(self.state_dir/'active_profile.json',validate(data['profile'],rate))
     except OSError as exc:
      # Persistence failures must not appear as a successful saved activation.
      from .engine import WebSocket
      ws=WebSocket(self.engine.port)
      try: ws.command('SetMute',True,True)
      finally: ws.close()
      raise EngineError('Persistence failed after activation; engine muted') from exc
     self.audit('engine_apply','ok'); return dict(result,profile_revision=self.profile_state()['profile_revision'])
  raise ValidationError('Unknown endpoint')
def handler(app):
 class Handler(BaseHTTPRequestHandler):
  protocol_version='HTTP/1.1'
  def log_message(self,*args): pass  # URLs, cookies and tokens are never logged.
  def send(self,status,body,content='application/json',cookie=None):
   if not isinstance(body,bytes): body=json.dumps(body,allow_nan=False).encode()
   self.send_response(status); self.send_header('Content-Type',content); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Referrer-Policy','no-referrer')
   self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'self'")
   if cookie: self.send_header('Set-Cookie',cookie)
   self.end_headers(); self.wfile.write(body)
  def host_allowed(self): return self.headers.get('Host') in self.server.allowed_hosts
  def authorized(self):
   cookie=http.cookies.SimpleCookie()
   try: cookie.load(self.headers.get('Cookie',''))
   except http.cookies.CookieError: return False
   token=cookie.get('peq_session')
   return bool(token and hmac.compare_digest(token.value,app.token))
  def do_GET(self):
   if not self.host_allowed(): self.send(403,{'error':'Invalid Host'}); return
   path=urllib.parse.urlsplit(self.path).path
   if path=='/api/session':
    if self.headers.get('Sec-Fetch-Site')=='cross-site' or self.headers.get('Origin') not in (None,'','http://'+self.headers.get('Host','')):self.send(403,{'error':'Invalid Origin'});return
    self.send(200,{'authenticated':True,'access':'local-no-password'},cookie='peq_session='+app.token+'; HttpOnly; SameSite=Strict; Path=/');return
   if path.startswith('/api/'):
    if not self.authorized(): self.send(401,{'error':'Authentication required'}); return
    if path=='/api/audio/status':
     if self.client_address[0]!='127.0.0.1':self.send(403,{'error':'Loopback observation required'});return
     try:self.send(200,app.native_audio_status())
     except TimeoutError:self.send(503,{'error':'Native audio state is busy'})
    elif path=='/api/status': self.send(200,app.status())
    elif path=='/api/profile':
     try:self.send(200,app.current_profile())
     except (ValueError,OSError):self.send(503,{'error':'Profilo corrente non disponibile'})
    elif path=='/api/profile/state':
     try:self.send(200,app.profile_state())
     except (ValueError,OSError):self.send(503,{'error':'Profilo corrente non disponibile'})
    elif path=='/api/presets': self.send(200,app.store.snapshot())
    elif path=='/api/slots':
     try:self.send(200,app.slots.snapshot())
     except (ValueError,OSError):self.send(503,{'error':'Preset slots unavailable'})
    else: self.send(404,{'error':'Unknown endpoint'})
    return
   files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/eq-graph.js':('eq-graph.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
   if path not in files: self.send(404,{'error':'Not found'}); return
   name,mime=files[path]; self.send(200,(ROOT/'ui'/name).read_bytes(),mime)
  def do_POST(self):
   if not self.host_allowed(): self.send(403,{'error':'Invalid Host'}); return
   origin=self.headers.get('Origin')
   if origin and origin not in {'http://'+h for h in self.server.allowed_hosts}: self.send(403,{'error':'Invalid Origin'}); return
   if self.headers.get('Sec-Fetch-Site')=='cross-site': self.send(403,{'error':'Cross-site request rejected'}); return
   if self.headers.get('Content-Type','').split(';')[0]!='application/json': self.send(415,{'error':'JSON required'}); return
   if self.headers.get('Transfer-Encoding'): self.send(400,{'error':'Transfer encoding unsupported'}); return
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=MAX_BODY: self.send(413,{'error':'Invalid body size'}); self.close_connection=True; return
    data=json.loads(self.rfile.read(length),object_pairs_hook=unique_object,parse_constant=lambda x: (_ for _ in ()).throw(ValidationError('Non-finite JSON')))
    path=urllib.parse.urlsplit(self.path).path
    if not self.authorized(): self.send(401,{'error':'Authentication required'}); return
    self.send(200,app.post(path,data))
   except ConflictError as e: self.send(409,{'error':str(e)})
   except (ValidationError,ValueError,KeyError,TypeError,OverflowError) as e: self.send(400,{'error':str(e)})
   except EngineError as e: self.send(503,{'error':str(e)})
   except OSError: self.send(503,{'error':'Storage or engine unavailable'})
  def setup(self): super().setup(); self.connection.settimeout(10)
 return Handler

def make_server(app,bind='127.0.0.1',port=8876):
 ip=ipaddress.ip_address(bind)
 if not ip.is_private or ip.is_unspecified or ip.is_multicast or ip.version!=4: raise ValidationError('Bind to a specific private IPv4 interface')
 class PanelServer(ThreadingHTTPServer):
  def server_close(self):
   super().server_close()
   app.comparison.close()
 server=PanelServer((bind,port),handler(app)); actual=server.server_port
 server.allowed_hosts={f'{bind}:{actual}'}
 if ip.is_loopback: server.allowed_hosts.add(f'localhost:{actual}')
 return server

def main():
 p=argparse.ArgumentParser(); p.add_argument('--bind',default='127.0.0.1'); p.add_argument('--port',type=int,default=8876); p.add_argument('--state-dir',default=str(ROOT/'state')); p.add_argument('--routing-file'); p.add_argument('--engine-port',type=int,default=1234); a=p.parse_args()
 route=json.loads(pathlib.Path(a.routing_file).read_text()) if a.routing_file else None
 app=Application(a.state_dir,route,a.engine_port); server=make_server(app,a.bind,a.port)
 print(f'Daphile PEQ {__version__}: http://{a.bind}:{a.port}; DSP disabled until routing preparation',flush=True)
 try: server.serve_forever()
 except KeyboardInterrupt: pass
 finally: server.server_close()
if __name__=='__main__': main()
