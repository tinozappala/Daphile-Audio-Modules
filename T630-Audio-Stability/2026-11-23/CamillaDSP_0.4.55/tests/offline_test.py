#!/usr/bin/python3
"""Isolated Camilla 4.1.3 warm-rate transitions; never touches the USB DAC."""
import json,os,sys,time,pathlib,traceback
root=pathlib.Path('/mnt/hdd/system/daphile-maintenance/20261123-warm-engine-candidate')
up=pathlib.Path('/mnt/hdd/system/daphile-audio-host/modules/DaphileCamillaDSP/releases/d1ea771e66ba4844e141c5eae81b6c7b0bf1b3c52e69998236883669a0d9169d')
sys.path[:0]=[str(root),str(up)]
from pcm_pipeline import PCMPipeline
from peq.model import new_profile
from peq.engine import WebSocket
from peq.pcm_gate import PCMGate,GateError
state=root/'isolated'
assert not (state/'audio-processes.json').exists()
producer='''import os,sys,time,signal
import struct
rate=int(sys.argv[1]);n=rate//100
level={44100:268435456,96000:536870912,192000:805306368}[rate]
chunk=struct.pack('<ii',level,-level)*n
while True:
 try:os.write(1,chunk)
 except BrokenPipeError:break
 time.sleep(.01)
'''
class LabPipeline(PCMPipeline):
 def _launch_stream(self,params,log):
  rate=params['rate']
  gate,_=self.spawn([sys.executable,'-m','peq.pcm_gate','--format',params['format']],log,
      stdin=__import__('subprocess').PIPE,stdout=self.input_write_fd,cwd=self.release)
  self.spawn([sys.executable,'-u','-c',producer,str(rate)],log,
      stdin=__import__('subprocess').DEVNULL,stdout=gate.stdin)
  gate.stdin.close()

results={}
m=PCMGate('S32_LE')
assert m.feed(bytes(32))==b''
try:
 pat=bytearray()
 for i in range(24):
  marker=0x05 if i%2==0 else 0xfa
  frame=bytes((0,0,0,marker))*2
  pat.extend(frame)
 g=PCMGate('S32_LE')
 try:g.feed(bytes(pat));raise RuntimeError('DoP gate failed to reject marker sequence')
 except GateError as e:assert str(e)=='unsupported-dop'
 results['dop_gate']='passed'
except Exception as e:
 print('DOP_GATE_FAILED',str(e),flush=True)
 raise
pipeline=LabPipeline(up,state,19093)
try:
 profile=new_profile('Flat')
 for i,rate in enumerate((44100,96000,192000)):
  output=state/('output-%d.raw'%rate)
  assert not output.exists()
  playback=dict(type='File',filename=str(output),channels=2,format='S32_LE')
  params=dict(rate=rate,format='S32_LE')
  t0=time.monotonic()
  route=pipeline.start(params,playback,profile) if i==0 else pipeline.reconfigure(params,playback,profile)
  pid=pipeline.children[0].pid
  if i==0:initial_pid=pid
  assert pid==initial_pid,'Engine PID changed'
  assert route['rate']==rate and pipeline.alive()
  time.sleep(.35)
  ws=WebSocket(pipeline.port,timeout=3)
  try:
   actual=json.loads(ws.command('GetConfigJson'))
   state_=ws.command('GetState')
  finally:ws.close()
  assert actual['devices']['samplerate']==rate and state_=='Running',(actual['devices']['samplerate'],state_)
  results[str(rate)]=dict(engine_pid=pid,rate=rate,elapsed_s=round(time.monotonic()-t0,3),state=state_)
  if i<2:
   pipeline.suspend()
   assert pipeline.suspended and len(pipeline.children)==1
   assert pipeline._flush_obsolete_frames()==0
   ws=WebSocket(pipeline.port,timeout=3)
   try:assert ws.command('GetState')=='Inactive'
   finally:ws.close()
  print('RATE_OK',rate,'PID',pid,'elapsed',results[str(rate)]['elapsed_s'],flush=True)
 results['status']='passed'
finally:
 try:pipeline.stop()
 except Exception as e:print('FINAL_CLEANUP_ERROR',type(e).__name__,str(e),flush=True)
 (root/'isolated-test-report.json').write_text(json.dumps(results,indent=2)+'\\n')
print('PROBE_SUCCESS',json.dumps(results),flush=True)
