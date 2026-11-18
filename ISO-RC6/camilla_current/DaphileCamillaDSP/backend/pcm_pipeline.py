"""Owned capture -> DoP gate -> Camilla Stdin pipeline for optional routing."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
import time

from common import atomic,started_identity,stop_owned,pin_engine_namespace
from peq import engine as engine_client
from peq.engine import Engine,WebSocket
from peq.model import compile_config
from peq.buffering import parameters,apply_targets,capture_buffer


class PCMPipeline:
    def __init__(self,release,state,port,subdevice=6):
        if type(subdevice)!=int or subdevice not in (6,7):raise ValueError('Reserved Loopback substream required')
        self.subdevice=subdevice
        self.release=Path(release);self.state=Path(state);self.port=port
        self.children=[];self.records=[];self.namespace_fd=None;self.route=None
        # Linux PDEATHSIG follows the creating thread's lifetime, not merely
        # its thread group. An HTTP handler ends immediately after activation.
        # This dedicated creator remains alive until all audio children stop.
        self.creator=ThreadPoolExecutor(max_workers=1,thread_name_prefix='camilla-child-owner')

    def save_children(self):
        atomic(self.state/'audio-processes.json',{'children':self.records})

    def spawn(self,arguments,log,isolated=False,**options):
        output=options.pop('stdout',log)
        # HTTP and monitoring threads already exist. Run namespace/PDEATH
        # setup in a fresh interpreter, never Python preexec_fn after a
        # multithreaded fork (which can inherit permanently locked mutexes).
        launcher='''import ctypes,fcntl,os,signal,socket,struct,sys
parent=int(sys.argv[1]);isolated=sys.argv[2]=='1'
if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)!=0:
 raise RuntimeError('Cannot arm audio child parent-death signal')
if os.getppid()!=parent:
 raise RuntimeError('Audio child parent changed before startup')
# Only owned, blocking audio children inherit this modest RT priority.
# Do not change IRQs, the native player, global RT limits or CPU affinity.
os.sched_setscheduler(0,os.SCHED_FIFO,os.sched_param(10))
if os.sched_getscheduler(0)!=os.SCHED_FIFO or os.sched_getparam(0).sched_priority!=10:
 raise RuntimeError('Audio child scheduling readback differs')
if isolated:
 os.unshare(os.CLONE_NEWNET)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
  request=struct.pack('16sH14s',b'lo',0,b'')
  name,flags,padding=struct.unpack('16sH14s',fcntl.ioctl(control,0x8913,request))
  fcntl.ioctl(control,0x8914,struct.pack('16sH14s',name,flags|1,padding))
os.execv(sys.argv[3],sys.argv[3:])
'''
        process=self.creator.submit(subprocess.Popen,
            [sys.executable,'-c',launcher,str(os.getpid()),'1' if isolated else '0',*arguments],
            stdout=output,stderr=log,**options).result(timeout=10)
        process.args=arguments # identity is journalled only after the final exec
        self.children.append(process)
        record=started_identity(process);self.records.append(record);self.save_children()
        return process,record

    def start(self,params,playback,profile):
        if self.children:raise RuntimeError('Pipeline già avviata')
        rate=params['rate'];sample_format=params['format']
        chunksize,target_chunks=parameters(self.state)
        capture_frames,capture_period=capture_buffer(rate,chunksize)
        capture=dict(type='Stdin',channels=2,format=sample_format)
        config=compile_config(profile,rate,chunksize,capture,playback)
        self.route=dict(rate=rate,chunksize=chunksize,capture=capture,playback=playback,verified_pcm=True)
        self.route.update(capture_ring_frames=capture_frames,capture_period_frames=capture_period)
        if target_chunks>1:self.route.update(target_level=chunksize*target_chunks,queuelimit=target_chunks+2)
        apply_targets(config,self.route);atomic(self.state/'engine-config.json',config)
        try:
            with (self.state/'audio-pipeline.log').open('ab') as log:
                engine,record=self.spawn([str(self.release/'bin/camilladsp'),'-m','-p',str(self.port),
                    '-a','127.0.0.1',str(self.state/'engine-config.json')],log,
                    stdin=subprocess.PIPE,isolated=True)
                self.namespace_fd=pin_engine_namespace(engine,record)
                engine_client.ENGINE_NAMESPACE_FD=self.namespace_fd
                # Pipes are connected directly; no decoded audio is written to
                # a file or exposed through the HTTP control interface.
                gate,_=self.spawn([sys.executable,'-m','peq.pcm_gate','--format',sample_format],log,
                    stdin=subprocess.PIPE,stdout=engine.stdin,cwd=self.release)
                engine.stdin.close()
                alsa_format={'S24_4_RJ_LE':'S24_LE'}.get(sample_format,sample_format)
                capture_process,_=self.spawn(['/usr/bin/arecord','-q','-D','hw:Loopback,1,'+str(self.subdevice),
                    '-t','raw','-f',alsa_format,'-r',str(rate),'-c','2',
                    '--buffer-size='+str(capture_frames),'--period-size='+str(capture_period)],log,
                    stdin=subprocess.DEVNULL,stdout=gate.stdin)
                gate.stdin.close()
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if any(child.poll() is not None for child in self.children):
                    raise RuntimeError('Pipeline PCM terminata durante l’avvio')
                try:
                    ws=WebSocket(self.port)
                    try:
                        actual=json.loads(ws.command('GetConfigJson'))
                        if ws.command('GetState')=='Running' and Engine.matches(config,actual):
                            ws.command('SetMute',False,True)
                            return self.route
                    finally:ws.close()
                except OSError:pass
                time.sleep(.05)
            raise RuntimeError('Avvio della pipeline PCM non confermato')
        except BaseException:
            self.stop();raise

    def alive(self):return bool(self.children) and all(child.poll() is None for child in self.children)

    def stop(self):
        errors=[]
        if self.namespace_fd is not None:
            try:
                ws=WebSocket(self.port)
                try:
                    ws.command('SetMute',True,True)
                    ws.command('Exit')
                finally:ws.close()
            except Exception:pass
        # Stop the producer before the consumer. The engine is muted while
        # the byte gate flushes any remaining qualified PCM frames on EOF.
        for index in reversed(range(min(len(self.children),len(self.records)))):
            child,record=self.children[index],self.records[index]
            try:
                # Once arecord closes stdout, let the gate finish and the
                # engine consume EOF naturally. Signalling an engine already
                # handling EOF can race its shutdown control channel.
                if index<len(self.children)-1 and child.poll() is None:
                    try:child.wait(timeout=3)
                    except subprocess.TimeoutExpired:pass
                if child.poll() is None:stop_owned(record,timeout=5)
                child.wait(timeout=2)
            except Exception:errors.append('owned-child-stop-failed')
        # A child whose identity could not be committed is still held by its
        # Popen object. Never signal an unrelated PID from a stale integer.
        for child in self.children[len(self.records):]:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:errors.append('uncommitted-child-stop-failed')
        if errors:raise RuntimeError('Arresto dei processi audio non confermato; recupero conservato')
        self.creator.shutdown(wait=True)
        self.children=[];self.records=[];self.route=None
        engine_client.ENGINE_NAMESPACE_FD=None
        if self.namespace_fd is not None:os.close(self.namespace_fd);self.namespace_fd=None
        (self.state/'audio-processes.json').unlink(missing_ok=True)
