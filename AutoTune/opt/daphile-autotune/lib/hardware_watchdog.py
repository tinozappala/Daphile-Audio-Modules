"""Explicit optional Linux watchdog supervision; nowayout devices refused."""
import fcntl,os,signal,struct,time
from pathlib import Path
import engine
from detection import read
GETSUPPORT=0x80285700;SETOPTIONS=0x80045704;KEEPALIVE=0x80045705;SETTIMEOUT=0xc0045706

def available():
 return [{'name':p.name,'identity':read(p/'identity'),'nowayout':read(p/'nowayout'),'eligible':read(p/'nowayout')=='0' and Path('/dev/'+p.name).exists()} for p in Path('/sys/class/watchdog').glob('watchdog[0-9]*')]

def serve(state=engine.STATE,confirmed=False):
 if not confirmed:raise RuntimeError('Explicit hardware watchdog activation required')
 rows=[x for x in available() if x['eligible']]
 if len(rows)!=1:raise RuntimeError('One unambiguous stoppable hardware watchdog required')
 row=rows[0];fd=os.open('/dev/'+row['name'],os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW);armed=False;running=True
 def stop(*_):
  nonlocal running
  running=False
 old=signal.signal(signal.SIGTERM,stop)
 try:
  info=bytearray(40);fcntl.ioctl(fd,GETSUPPORT,info,True);options=struct.unpack('I',info[:4])[0]
  if not options&0x100:raise RuntimeError('Magic-close support is required')
  timeout=bytearray(struct.pack('I',120));fcntl.ioctl(fd,SETTIMEOUT,timeout,True);actual=struct.unpack('I',timeout)[0]
  if actual<30:raise RuntimeError('Hardware timeout too short')
  armed=True;engine.atomic(Path(state)/'hardware-watchdog.json',{'status':'armed','device':row['name'],'timeout':actual,'owner':os.getpid(),'start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id()})
  while running:
   # Stop feeding on total userspace health loss; a hung process may still be
   # present, so successful web/LMS probes must precede each heartbeat.
   import urllib.request
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));healthy=True
   for url in ['http://127.0.0.1/','http://127.0.0.1:9000/']:
    try:
     with opener.open(url,timeout=3) as response:healthy=healthy and response.status==200
    except OSError:healthy=False
   if healthy:fcntl.ioctl(fd,KEEPALIVE,struct.pack('I',0))
   time.sleep(min(5,actual/4))
 finally:
  # Refuse unsafe persistent operation. Normal termination disarms; SIGKILL
  # or a CPU hang leaves the hardware counter armed as explicitly requested.
  with_exception=None
  try:
   fcntl.ioctl(fd,SETOPTIONS,struct.pack('I',1));os.write(fd,b'V')
  except OSError as e:with_exception=e
  finally:os.close(fd);signal.signal(signal.SIGTERM,old)
  engine.atomic(Path(state)/'hardware-watchdog.json',{'status':'stopped' if with_exception is None else 'disarm-unverified','device':row['name']})
  if with_exception:raise RuntimeError('Hardware watchdog disarm could not be verified') from with_exception
