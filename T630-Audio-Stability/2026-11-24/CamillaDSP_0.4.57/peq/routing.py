"""Prepared Loopback PCM rate adapter. No changes to Daphile player or ALSA files."""
import argparse,json,pathlib,re,socket,subprocess,time
from .model import compile_config,validate,RATES,ValidationError
from .engine import WebSocket,Engine,EngineError,engine_transaction

def parse_hw_params(text):
 if text.strip()=='closed':return None
 values=dict(re.findall(r'^(format|channels|rate):\s*(\S+)',text,re.M))
 if set(values)!={'format','channels','rate'}:raise ValidationError('Incomplete PCM hardware parameters')
 rate=int(values['rate'])
 if rate not in RATES or values['format'] not in ('S16_LE','S24_3LE','S24_3_LE','S24_LE','S32_LE') or values['channels']!='2':raise ValidationError('Unsupported PCM stream')
 fmt={'S24_LE':'S24_4_RJ_LE','S24_3LE':'S24_3_LE'}.get(values['format'],values['format'])
 return {'rate':rate,'format':fmt,'channels':2}
class LoopbackSupervisor:
 def __init__(self,engine,profile,card=0,subdevice=6,playback=None,pcm_only=False,port=1234,chunksize=1024,profile_path=None):
  if not pcm_only:raise ValidationError('Upstream PCM-only contract required; native DSD/DoP must use original path')
  if card!=0 or type(subdevice) is not int or subdevice not in (6,7):raise ValidationError('Only explicitly reserved loopback substreams 6/7 supported')
  self.engine=engine;self.profile=validate(profile);self.card=card;self.subdevice=subdevice;self.playback=playback;self.port=port;self.chunksize=chunksize;self.proc=None;self.ws=None;self.current=None;self.fault=None;self.profile_path=pathlib.Path(profile_path) if profile_path else None;self.profile_mtime=None;self.resume_muted=False
 def start(self,log):
  with socket.socket() as probe:probe.bind(('127.0.0.1',self.port))
  self.proc=subprocess.Popen([self.engine,'-w','-m','-p',str(self.port),'-a','127.0.0.1'],stdout=log,stderr=log)
  deadline=time.monotonic()+5
  while time.monotonic()<deadline:
   try:
    self.ws=WebSocket(self.port)
    if self.ws.command('GetVersion')!='4.1.3':raise EngineError('Unqualified engine version')
    return
   except OSError:time.sleep(.03)
  raise EngineError('Rate adapter engine startup failed')
 def step(self,hw_params_text):
  with engine_transaction(self.port):return self._step(hw_params_text)
 def _step(self,hw_params_text):
  if self.proc.poll() is not None:raise EngineError('DSP exited; no automatic direct-path failover')
  params=parse_hw_params(hw_params_text)
  profile_changed=False
  if self.profile_path and self.profile_path.exists():
   stamp=self.profile_path.stat().st_mtime_ns
   if stamp!=self.profile_mtime:
    candidate=json.loads(self.profile_path.read_text())
    self.profile=validate(candidate,params['rate'] if params else 48000);self.profile_mtime=stamp;profile_changed=True
  if params is None:
   if self.current is not None:
    self.resume_muted=self.ws.command('GetMute')
    if not isinstance(self.resume_muted,bool):raise EngineError('Invalid CamillaDSP mute state')
    self.ws.command('SetMute',True,True);self.ws.command('Stop');self.current=None
   return {'state':'Waiting','rate':None}
  if self.current!=params:
   if self.current is not None:self.resume_muted=self.ws.command('GetMute')
   if not isinstance(self.resume_muted,bool):raise EngineError('Invalid CamillaDSP mute state')
   self.ws.command('SetMute',True,True)
   if self.current is not None:self.ws.command('Stop');time.sleep(.1)
   capture={'type':'Alsa','device':f'hw:Loopback,1,{self.subdevice}','channels':2,'format':params['format'],'stop_on_inactive':True}
   cfg=compile_config(self.profile,params['rate'],self.chunksize,capture,self.playback)
   self.ws.command('ValidateConfigJson',json.dumps(cfg),True);self.ws.command('SetConfigJson',json.dumps(cfg),True)
   deadline=time.monotonic()+4
   while time.monotonic()<deadline:
    state=self.ws.command('GetState');active=json.loads(self.ws.command('GetConfigJson'))
    if state=='Running' and Engine.matches(cfg,active):break
    time.sleep(.03)
   else:raise EngineError('Rate change not confirmed; keeping muted')
   self.ws.command('SetMute',self.resume_muted,True);self.current=params
  if profile_changed:
   capture={'type':'Alsa','device':f'hw:Loopback,1,{self.subdevice}','channels':2,'format':params['format'],'stop_on_inactive':True}
   cfg=compile_config(self.profile,params['rate'],self.chunksize,capture,self.playback)
   current=json.loads(self.ws.command('GetConfigJson'))
   if not Engine.matches(cfg,current):Engine(self.port).apply(cfg)
  state=self.ws.command('GetState')
  if state not in ('Running','Paused'):
   self.ws.command('SetMute',True,True);raise EngineError('DSP is not running; no direct-path failover')
  return {'state':state,'rate':params['rate'],'format':params['format'],'clipped_samples':self.ws.command('GetClippedSamples')}
 def close(self):
  if self.ws:
   try:self.ws.command('SetMute',True,True);self.ws.command('Stop')
   except (OSError,EngineError):pass
   self.ws.close()
  if self.proc and self.proc.poll() is None:
   self.proc.terminate()
   try:self.proc.wait(timeout=5)
   except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait(timeout=5)

def main():
 p=argparse.ArgumentParser();p.add_argument('--engine',required=True);p.add_argument('--profile',required=True);p.add_argument('--playback-file',required=True);p.add_argument('--pcm-only',action='store_true');p.add_argument('--subdevice',type=int,default=6);p.add_argument('--port',type=int,default=1234);p.add_argument('--active-profile-file');a=p.parse_args()
 profile=json.loads(pathlib.Path(a.profile).read_text());playback=json.loads(pathlib.Path(a.playback_file).read_text())
 sup=LoopbackSupervisor(a.engine,profile,subdevice=a.subdevice,playback=playback,pcm_only=a.pcm_only,port=a.port,profile_path=a.active_profile_file)
 params=pathlib.Path(f'/proc/asound/card0/pcm0p/sub{a.subdevice}/hw_params')
 try:
  import sys
  sup.start(sys.stderr)
  while True:sup.step(params.read_text());time.sleep(.05)
 except KeyboardInterrupt:pass
 finally:sup.close()
if __name__=='__main__':main()
