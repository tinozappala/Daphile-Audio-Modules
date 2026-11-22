"""Bounded RFC6455 client for official CamillaDSP commands, loopback only."""
import base64,contextlib,hashlib,json,math,os,pathlib,socket,struct,tempfile,time,copy,threading
class EngineError(RuntimeError): pass
_locks={}
_lock_map_guard=threading.Lock()
_local=threading.local()
# Set only by the trusted autonomous worker, before starting HTTP threads.
# Holding this descriptor prevents PID reuse from selecting another namespace.
ENGINE_NAMESPACE_FD=None
def connection(port,timeout):
 if ENGINE_NAMESPACE_FD is None:return socket.create_connection(('127.0.0.1',port),timeout=timeout)
 original=os.open('/proc/thread-self/ns/net',os.O_RDONLY|os.O_CLOEXEC)
 try:
  os.setns(ENGINE_NAMESPACE_FD,os.CLONE_NEWNET)
  return socket.create_connection(('127.0.0.1',port),timeout=timeout)
 finally:
  os.setns(original,os.CLONE_NEWNET);os.close(original)
@contextlib.contextmanager
def engine_transaction(port):
 with _lock_map_guard: lock=_locks.setdefault(port,threading.RLock())
 with lock:
  depths=getattr(_local,'depths',{});_local.depths=depths
  if depths.get(port,0):
   depths[port]+=1
   try:yield
   finally:depths[port]-=1
   return
  fd=None
  try:
   if os.name=='posix':
    import fcntl
    path=pathlib.Path(tempfile.gettempdir())/f'daphile-peq-ws-{port}.lock'
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    deadline=time.monotonic()+10
    while True:
     try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
     except BlockingIOError:
      if time.monotonic()>deadline:raise EngineError('Another DSP transaction is busy')
      time.sleep(.02)
   depths[port]=1
   yield
  finally:
   depths.pop(port,None)
   if fd is not None:os.close(fd)
class WebSocket:
 def __init__(self,port=1234,timeout=3):
  if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:raise ValueError('Positive finite websocket timeout required')
  self.timeout=timeout; self.buffer=b''; deadline=time.monotonic()+timeout
  self.sock=connection(port,timeout)
  try:
   key=base64.b64encode(os.urandom(16)).decode()
   request=f'GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n'
   self.remaining(deadline); self.sock.sendall(request.encode())
   while b'\r\n\r\n' not in self.buffer:
    self.remaining(deadline); chunk=self.sock.recv(4096)
    if not chunk:raise EngineError('Engine closed during handshake')
    self.buffer+=chunk
    if len(self.buffer)>16384: raise EngineError('Invalid websocket handshake')
   self.remaining(deadline)
   header,self.buffer=self.buffer.split(b'\r\n\r\n',1)
   accept=base64.b64encode(hashlib.sha1((key+'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
   fields={s.split(b':',1)[0].lower():s.split(b':',1)[1].strip() for s in header.split(b'\r\n')[1:] if b':' in s}
   if not header.startswith(b'HTTP/1.1 101 ') or fields.get(b'sec-websocket-accept')!=accept:raise EngineError('Websocket handshake rejected')
  except BaseException:
   self.sock.close();raise
 def remaining(self,deadline):
  remaining=deadline-time.monotonic()
  if remaining<=0:raise EngineError('Engine response deadline exceeded')
  self.sock.settimeout(remaining)
 def close(self):
  try:
   deadline=time.monotonic()+.2
   self.send(struct.pack('!H',1000),8,deadline)
   try: self.receive(deadline)
   except (EngineError,OSError,ValueError): pass
  except (EngineError,OSError): pass
  finally: self.sock.close()
 def read(self,n,deadline):
  while len(self.buffer)<n:
   self.remaining(deadline)
   chunk=self.sock.recv(max(4096,n-len(self.buffer)))
   if not chunk: raise EngineError('Engine connection closed')
   self.buffer+=chunk
  data,self.buffer=self.buffer[:n],self.buffer[n:]; return data
 def send(self,data,opcode=1,deadline=None):
  if isinstance(data,str): data=data.encode()
  n=len(data); mask=os.urandom(4); header=bytes([0x80|opcode,0x80|(n if n<126 else (126 if n<65536 else 127))])
  if n>=126: header+=struct.pack('!H' if n<65536 else '!Q',n)
  self.remaining(time.monotonic()+self.timeout if deadline is None else deadline)
  self.sock.sendall(header+mask+bytes(v^mask[i%4] for i,v in enumerate(data)))
 def receive(self,deadline=None):
  if deadline is None:deadline=time.monotonic()+self.timeout
  parts=[]; total=0; fragmented=False
  while True:
   self.remaining(deadline)
   a,b=self.read(2,deadline); opcode=a&15; n=b&127
   if n==126: n=struct.unpack('!H',self.read(2,deadline))[0]
   elif n==127: n=struct.unpack('!Q',self.read(8,deadline))[0]
   if n>4*1024*1024 or b&128 or a&112: raise EngineError('Invalid or oversized websocket frame')
   if opcode>=8 and (n>125 or not a&128):raise EngineError('Invalid websocket control frame')
   data=self.read(n,deadline); self.remaining(deadline)
   if opcode==8: raise EngineError('Engine closed websocket')
   if opcode==9: self.send(data,10,deadline); continue
   if opcode==10: continue
   if opcode not in (0,1) or (opcode==0 and not fragmented) or (opcode==1 and fragmented): raise EngineError('Expected text frame or valid continuation')
   fragmented=True
   parts.append(data); total+=n
   if total>4*1024*1024: raise EngineError('Oversized response')
   if a&128: return b''.join(parts).decode()
 def command(self,name,value=None,argument=False):
  deadline=time.monotonic()+self.timeout
  self.send(json.dumps({name:value} if argument else name,allow_nan=False),deadline=deadline)
  reply=json.loads(self.receive(deadline))
  if not isinstance(reply,dict) or not isinstance(reply.get(name),dict):raise EngineError('Invalid CamillaDSP response to '+name)
  result=reply[name]
  if result.get('result')!='Ok': raise EngineError('CamillaDSP rejected '+name)
  return result.get('value')
class Engine:
 def __init__(self,port=1234): self.port=port; self.lock=threading.RLock(); self.fault=None
 def status(self):
  try:
   ws=WebSocket(self.port)
   try: return {'connected':True,'version':ws.command('GetVersion'),'state':ws.command('GetState'),'stop_reason':ws.command('GetStopReason'),'processing_load_pct':ws.command('GetProcessingLoad'),'clipped_samples':ws.command('GetClippedSamples'),'mute':ws.command('GetMute'),'fault':self.fault,'xrun':None,'rss_bytes':None,'cpu_process_pct':None}
   finally: ws.close()
  except (OSError,ValueError,EngineError): return {'connected':False,'state':'Disconnected','fault':self.fault,'xrun':None,'rss_bytes':None,'cpu_process_pct':None}
 @staticmethod
 def matches(request,actual):
  if isinstance(request,dict): return isinstance(actual,dict) and all(k in actual and Engine.matches(v,actual[k]) for k,v in request.items())
  if isinstance(request,list): return isinstance(actual,list) and len(request)==len(actual) and all(Engine.matches(a,b) for a,b in zip(request,actual))
  if isinstance(request,bool) or isinstance(actual,bool):return type(request) is type(actual) and request==actual
  if isinstance(request,float) and isinstance(actual,(int,float)) and not isinstance(actual,bool):
   import math
   return math.isclose(request,actual,rel_tol=1e-13,abs_tol=1e-15)
  return request==actual
 def apply(self,cfg):
  with self.lock,engine_transaction(self.port):
   ws=WebSocket(self.port); old=None; muted=False
   try:
    version=ws.command('GetVersion')
    if version!='4.1.3': raise EngineError('Unqualified engine version')
    ws.command('ValidateConfigJson',json.dumps(cfg),True)
    old=json.loads(ws.command('GetConfigJson'))
    if not old or not str(old.get('title','')).startswith('Daphile PEQ '): raise EngineError('Refusing to control an unowned DSP pipeline')
    # Config changes may never open a different device or change sample rate through this endpoint.
    if not self.matches(cfg['devices'],old['devices']): raise EngineError('Routing/rate change requires separate prepared adapter')
    # Camilla compares the entire normalized devices object when deciding
    # whether to reopen I/O. Preserve it byte-for-value, including defaults
    # which are absent from the editor's compiled filter configuration.
    cfg=copy.deepcopy(cfg);cfg['devices']=copy.deepcopy(old['devices'])
    ws.command('ValidateConfigJson',json.dumps(cfg),True)
    # Metadata-only edits need persistence, not an audible mute/reload.
    if not self.fault and self.matches(cfg,old):
     state=ws.command('GetState');mute=ws.command('GetMute')
     if state in ('Running','Paused') and mute is False:
      return {'applied':True,'state':state,'muted':False,'unchanged':True}
    oldmute=ws.command('GetMute')
    if not isinstance(oldmute,bool):raise EngineError('Invalid CamillaDSP mute state')
    ws.command('SetMute',True,True); muted=True
    time.sleep(max(.15,3*cfg['devices']['chunksize']/cfg['devices']['samplerate']))
    ws.command('SetConfigJson',json.dumps(cfg),True)
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
     active=json.loads(ws.command('GetConfigJson')); state=ws.command('GetState')
     if self.matches(cfg,active) and state in ('Running','Paused','Stalled'): break
     time.sleep(.05)
    else: raise EngineError('No confirmed running configuration')
    if state=='Stalled': raise EngineError('Input stalled; keeping audio muted')
    ws.command('SetMute',oldmute,True); self.fault=None
    return {'applied':True,'state':state,'muted':oldmute}
   except Exception as exc:
    if muted and old:
     try: ws.command('SetMute',True,True); ws.command('SetConfigJson',json.dumps(old),True)
     except Exception: pass
    self.fault='Apply failed; previous config requested; muted state requires inspection' if muted else 'Apply rejected before mutation'
    raise EngineError(self.fault) from exc
   finally: ws.close()
