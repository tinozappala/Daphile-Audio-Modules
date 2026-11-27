"""Owned capture -> DoP gate -> Camilla Stdin pipeline for optional routing."""
import json
import contextlib
import errno
import fcntl
from concurrent.futures import ThreadPoolExecutor
import os
import select
from pathlib import Path
import subprocess
import sys
import time

from common import atomic,started_identity,stop_owned,pin_engine_namespace,identity
from datc_output_owner import verify_output_owner,verify_output_released,OutputNotReady,OutputNotRunning
from datc_pcm_effect import PCMCommitReceipt,PCMCommitError,owner_key,PCMReleaseReceipt,PCMReleaseError
from peq import engine as engine_client
from peq.engine import Engine,WebSocket
from peq.model import compile_config
from peq.buffering import parameters,apply_targets,capture_buffer


class CommitNotReady(RuntimeError):
    """The verified route is temporarily unable to complete this commit."""


class PCMPipeline:
    COMMIT_RECOVERY_SECONDS = .4
    COMMIT_RECHECK_SECONDS = .02

    def __init__(self,release,state,port,subdevice=6):
        if type(subdevice)!=int or subdevice not in (6,7):raise ValueError('Reserved Loopback substream required')
        self.subdevice=subdevice
        self.release=Path(release);self.state=Path(state);self.port=port
        self.children=[];self.records=[];self.namespace_fd=None;self.route=None
        self.input_read_fd=None;self.input_write_fd=None;self.suspended=False
        self.output_guard=None;self.pending_config=None;self.pending_token=None;self.release_playback=None;self.released=None
        self.commit_note=None
        # Linux PDEATHSIG follows the creating thread's lifetime, not merely
        # its thread group. An HTTP handler ends immediately after activation.
        # This dedicated creator remains alive until all audio children stop.
        self.creator=ThreadPoolExecutor(max_workers=1,thread_name_prefix='camilla-child-owner')

    def verify_output(self,playback,rate):
        if self.output_guard is None:raise RuntimeError("Native DAC identity guard required")
        self.output_guard()
        if len(self.records)!=3 or not self.alive():raise OutputNotReady("PCM process set incomplete")
        return verify_output_owner(playback,rate,self.records[0],identity)

    def commit_observation(self,ws,route,config,muted):
        stage='prima di togliere il mute' if muted else 'dopo aver tolto il mute'
        if ws.command('GetMute') is not muted:
            raise PCMCommitError(f'Stato mute PCM non confermato {stage}')
        state=ws.command('GetState')
        if not Engine.matches(config,json.loads(ws.command('GetConfigJson'))):
            raise PCMCommitError(f'Configurazione PCM diversa {stage} (motore {state})')
        if state not in ('Running','Stalled','Paused'):
            raise PCMCommitError(f'CamillaDSP in stato {state} {stage}')
        try:
            owner=owner_key(self.verify_output(route['playback'],route['rate']),route['rate'])
        except OutputNotRunning as exc:
            error=f'ALSA {exc.state}, CamillaDSP {state} {stage}'
            if exc.state not in ('PREPARED','PAUSED','XRUN','SETUP'):
                raise PCMCommitError(error) from exc
            raise CommitNotReady(error) from exc
        if state!='Running':
            raise CommitNotReady(f'CamillaDSP {state} {stage}')
        return owner

    def commit_attempt(self,ws,route,config,fence,token):
        fence()
        before=self.commit_observation(ws,route,config,muted=True)
        fence()
        try:
            ws.command('SetMute',False,True)
            after=self.commit_observation(ws,route,config,muted=False)
            receipt=PCMCommitReceipt(route['rate'],route['playback']['device'],before,after,False,'Running',True,token)
            receipt.verify(route,token)
            fence()
            return receipt
        except BaseException:
            try:
                ws.command('SetMute',True,True)
                if ws.command('GetMute') is not True:
                    raise PCMCommitError('Mute PCM non verificato dopo un commit incompleto')
            except Exception as exc:
                raise PCMCommitError('Ripristino del mute PCM non confermato') from exc
            raise

    def commit_output(self,fence,token):
        """Retry only recoverable startup states, on this same fenced PCM route."""
        if token is None or self.pending_token is not token or self.pending_config is None or self.route is None or self.suspended:
            raise RuntimeError('Nessuna preparazione PCM da confermare')
        route=self.route;config=self.pending_config
        deadline=None
        attempts=0
        with contextlib.closing(WebSocket(self.port)) as ws:
            while True:
                try:
                    receipt=self.commit_attempt(ws,route,config,fence,token)
                    break
                except CommitNotReady as exc:
                    now=time.monotonic()
                    if deadline is None:
                        deadline=min(token.deadline,now+self.COMMIT_RECOVERY_SECONDS)
                        if self.commit_note is not None:
                            self.commit_note('pcm-commit-waiting',rate=route['rate'],reason=str(exc))
                    if now>=deadline:
                        raise PCMCommitError(f'Ripresa del commit PCM non confermata: {exc}') from exc
                    attempts+=1
                    time.sleep(min(self.COMMIT_RECHECK_SECONDS,deadline-now))
        self.pending_config=None;self.pending_token=None
        if attempts and self.commit_note is not None:
            self.commit_note('pcm-commit-recovered',rate=route['rate'],rechecks=attempts)
        return receipt

    def save_children(self):
        atomic(self.state/'audio-processes.json',{'children':self.records})

    def spawn(self,arguments,log,isolated=False,gate_deferred=False,**options):
        output=options.pop('stdout',log)
        # HTTP and monitoring threads already exist. Run namespace/PDEATH
        # setup in a fresh interpreter, never Python preexec_fn after a
        # multithreaded fork (which can inherit permanently locked mutexes).
        launcher='''import ctypes,fcntl,os,signal,socket,struct,sys
parent=int(sys.argv[1]);isolated=sys.argv[2]=='1';deferred=sys.argv[3]=='1'
if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGTERM,0,0,0)!=0:
 raise RuntimeError('Cannot arm audio child parent-death signal')
if os.getppid()!=parent:
 raise RuntimeError('Audio child parent changed before startup')
# Only owned, blocking audio children inherit this modest RT priority.
# Do not change IRQs, the native player, global RT limits or CPU affinity.
if not deferred:
 os.sched_setscheduler(0,os.SCHED_FIFO,os.sched_param(10))
 if os.sched_getscheduler(0)!=os.SCHED_FIFO or os.sched_getparam(0).sched_priority!=10:
  raise RuntimeError('Audio child scheduling readback differs')
if isolated:
 os.unshare(os.CLONE_NEWNET)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
  request=struct.pack('16sH14s',b'lo',0,b'')
  name,flags,padding=struct.unpack('16sH14s',fcntl.ioctl(control,0x8913,request))
  fcntl.ioctl(control,0x8914,struct.pack('16sH14s',name,flags|1,padding))
os.execv(sys.argv[4],sys.argv[4:])
'''
        process=self.creator.submit(subprocess.Popen,
            [sys.executable,'-c',launcher,str(os.getpid()),'1' if isolated else '0','1' if gate_deferred else '0',*arguments],
            stdout=output,stderr=log,**options).result(timeout=10)
        process.args=arguments # identity is journalled only after the final exec
        self.children.append(process)
        record=started_identity(process);self.records.append(record);self.save_children()
        return process,record


    def _launch_stream(self,params,log):
        """Each stream receives a fresh DoP gate; only the qualified engine is kept."""
        sample_format=params['format'];rate=params['rate']
        chunksize,_=parameters(self.state)
        capture_frames,capture_period=capture_buffer(rate,chunksize)
        ready_read,ready_write=os.pipe2(os.O_CLOEXEC)
        try:
            gate,_=self.spawn([sys.executable,'-m','peq.pcm_gate','--format',sample_format,
                               '--fifo-after-init','--ready-fd',str(ready_write)],log,
                stdin=subprocess.PIPE,stdout=self.input_write_fd,cwd=self.release,
                gate_deferred=True,pass_fds=(ready_write,))
            os.close(ready_write);ready_write=None
            if not select.select([ready_read],[],[],3)[0] or os.read(ready_read,1)!=b'R':
                raise RuntimeError('PCM gate FIFO readiness not confirmed')
        finally:
            os.close(ready_read)
            if ready_write is not None:os.close(ready_write)
        alsa_format={'S24_4_RJ_LE':'S24_LE','S24_3_LE':'S24_3LE'}.get(sample_format,sample_format)
        self.spawn(['/usr/bin/arecord','-q','-D','hw:Loopback,1,'+str(self.subdevice),
            '-t','raw','-f',alsa_format,'-r',str(rate),'-c','2',
            '--buffer-size='+str(capture_frames),'--period-size='+str(capture_period)],log,
            stdin=subprocess.DEVNULL,stdout=gate.stdin)
        gate.stdin.close()

    def _stop_stream(self):
        """Stop ONLY owned capture and fresh DoP gate, without exiting the engine."""
        errors=[]
        for i in reversed(range(1,len(self.children))):
            child=self.children[i]
            try:
                if child.poll() is None:stop_owned(self.records[i],timeout=3)
                child.wait(timeout=3)
            except Exception:errors.append('pcm-child-stop-unverified')
        if errors:raise RuntimeError('Cannot verify stopped capture and DoP gate')
        self.children=self.children[:1];self.records=self.records[:1]
        self.save_children()

    def _flush_obsolete_frames(self):
        """Drain all old-rate input with the engine confirmed Inactive.
        Never reinterpret old PCM as a new sample rate.
        """
        if self.input_read_fd is None:raise RuntimeError('No owned PCM pipe')
        flags=fcntl.fcntl(self.input_read_fd,fcntl.F_GETFL)
        total=0
        try:
            fcntl.fcntl(self.input_read_fd,fcntl.F_SETFL,flags|os.O_NONBLOCK)
            while True:
                try:block=os.read(self.input_read_fd,65536)
                except BlockingIOError:break
                if not block:break
                total+=len(block)
                if total>16*1024*1024:
                    raise RuntimeError('Obsolete audio buffer exceeds safe flush limit')
        finally:
            fcntl.fcntl(self.input_read_fd,fcntl.F_SETFL,flags)
        return total

    def _confirm_output_release(self):
        if self.release_playback is None:return 'not-opened'
        deadline=time.monotonic()+.5
        while True:
            try:verify_output_released(self.release_playback);return 'closed'
            except OutputNotReady:
                if time.monotonic()>=deadline:raise PCMReleaseError('Chiusura ALSA non confermata')
                time.sleep(.02)
    def suspend(self,token=None):
        """Leave CamillaDSP process alive but release output and drop old PCM."""
        if self.suspended:
            scope=self._confirm_output_release()
            if len(self.children)!=1 or len(self.records)!=1 or not self.alive():
                raise PCMReleaseError('Motore PCM sospeso non verificato')
            receipt=PCMReleaseReceipt(True,1,scope,False,token);receipt.verify(True,token);return receipt
        if len(self.children)!=3 or len(self.records)!=3 or self.children[0].poll() is not None:
            raise RuntimeError('Cannot suspend unqualified PCM process set')
        # Mute old-rate output before relinquishing the capture writer.
        with contextlib.closing(WebSocket(self.port)) as ws:
            ws.command('SetMute',True,True)
            if ws.command('GetMute') is not True:raise PCMReleaseError('Mute non confermato prima del rilascio PCM')
        self._stop_stream()
        # Keep the old rate active until its processing queue is fully spent.
        # No successor stream is allowed to enter this shared stdin pipe yet.
        deadline=time.monotonic()+3
        with contextlib.closing(WebSocket(self.port)) as ws:
            while time.monotonic()<deadline:
                state=ws.command('GetState')
                if state=='Stalled':break
                if state not in ('Running','Paused','Stalled'):
                    raise RuntimeError('Unexpected old-rate engine state '+str(state))
                time.sleep(.03)
            else:raise RuntimeError('Old-rate PCM failed to drain before rate change')
            chunk=self.route['chunksize'] if self.route else 2048
            rate=self.route['rate'] if self.route else 44100
            time.sleep(max(.06,2*chunk/max(rate,1)))
            ws.command('Stop')
            deadline=time.monotonic()+4
            while time.monotonic()<deadline:
                state=ws.command('GetState')
                if state=='Inactive':break
                time.sleep(.05)
            else:raise RuntimeError('CamillaDSP did not release its old rate: '+str(state))
        self._flush_obsolete_frames()
        scope=self._confirm_output_release()
        receipt=PCMReleaseReceipt(True,1,scope,False,token);receipt.verify(True,token)
        self.route=None;self.suspended=True;self.pending_config=None;self.pending_token=None
        return receipt

    def reconfigure(self,params,playback,profile,token):
        """Retain CamillaDSP PID while re-opening only the PCM stream endpoints."""
        if not self.suspended or len(self.children)!=1 or len(self.records)!=1 or self.children[0].poll() is not None:
            raise RuntimeError('Warm PCM engine not qualified for reuse')
        if self.release_playback!=playback or self._confirm_output_release()!='closed':
            raise PCMReleaseError('Uscita PCM precedente non rilasciata')
        rate=params['rate'];sample_format=params['format']
        chunksize,target_chunks=parameters(self.state)
        capture_frames,capture_period=capture_buffer(rate,chunksize)
        capture=dict(type='Stdin',channels=2,format=sample_format)
        config=compile_config(profile,rate,chunksize,capture,playback)
        route=dict(rate=rate,chunksize=chunksize,capture=capture,playback=playback,verified_pcm=True,
                   capture_ring_frames=capture_frames,capture_period_frames=capture_period)
        if target_chunks>1:route.update(target_level=chunksize*target_chunks,queuelimit=target_chunks+2)
        apply_targets(config,route)
        try:
            assert self._flush_obsolete_frames()==0, 'Unexpected late PCM before new configuration'
            with contextlib.closing(WebSocket(self.port)) as ws:
                if ws.command('GetVersion')!='5.0.0-beta.2' or ws.command('GetState')!='Inactive':
                    raise RuntimeError('CamillaDSP engine is not stopped at the rate boundary')
                ws.command('ValidateConfigJson',json.dumps(config),True)
                ws.command('SetConfigJson',json.dumps(config),True)
                ws.command('SetMute',True,True)
            atomic(self.state/'engine-config.json',config)
            with (self.state/'audio-pipeline.log').open('ab') as log:
                self._launch_stream(params,log)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if not self.alive():raise RuntimeError('Reused PCM process exited unexpectedly')
                with contextlib.closing(WebSocket(self.port)) as ws:
                    state=ws.command('GetState')
                    actual=json.loads(ws.command('GetConfigJson'))
                    if Engine.matches(config,actual) and state=='Running':
                        try:self.verify_output(playback,rate)
                        except OutputNotReady:pass
                        else:
                            if ws.command('GetMute') is not True:
                                raise RuntimeError('Mute PCM non confermato durante preparazione')
                            self.route=route;self.suspended=False;self.pending_config=config;self.pending_token=token
                            return route
                time.sleep(.05)
            raise RuntimeError('Reused CamillaDSP did not confirm Running')
        except BaseException:
            self.stop()
            raise

    def start(self,params,playback,profile,token):
        if self.children or self.released is not None:raise RuntimeError('Pipeline già avviata o chiusa')
        self.release_playback=dict(playback)
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
            self.input_read_fd,self.input_write_fd=os.pipe2(os.O_CLOEXEC)
            with (self.state/'audio-pipeline.log').open('ab') as log:
                engine,record=self.spawn([str(self.release/'bin/camilladsp'),'-w','-m','-p',str(self.port),
                    '-a','127.0.0.1'],log,
                    stdin=self.input_read_fd,isolated=True)
                self.namespace_fd=pin_engine_namespace(engine,record)
                engine_client.ENGINE_NAMESPACE_FD=self.namespace_fd
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    try:
                        with contextlib.closing(WebSocket(self.port)) as ws:
                            if ws.command('GetVersion')!='5.0.0-beta.2':raise RuntimeError('CamillaDSP version changed')
                            ws.command('ValidateConfigJson',json.dumps(config),True)
                            ws.command('SetConfigJson',json.dumps(config),True)
                            ws.command('SetMute',True,True)
                        break
                    except OSError:time.sleep(.05)
                else:raise RuntimeError('CamillaDSP wait-mode not ready')
                # Parent owns BOTH ends: on Stop, obsolete frames are drained
                # from the read end before a new rate can enter the DSP.
                self._launch_stream(params,log)
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                if any(child.poll() is not None for child in self.children):
                    raise RuntimeError('Pipeline PCM terminata durante l’avvio')
                try:
                    ws=WebSocket(self.port)
                    try:
                        actual=json.loads(ws.command('GetConfigJson'))
                        if ws.command('GetState')=='Running' and Engine.matches(config,actual):
                            try:self.verify_output(playback,rate)
                            except OutputNotReady:pass
                            else:
                                if ws.command('GetMute') is not True:
                                    raise RuntimeError('Mute PCM non confermato durante preparazione')
                                self.pending_config=config;self.pending_token=token
                                return self.route
                    finally:ws.close()
                except OSError:pass
                time.sleep(.05)
            raise RuntimeError('Avvio della pipeline PCM non confermato')
        except BaseException:
            self.stop();raise

    def alive(self):return bool(self.children) and all(child.poll() is None for child in self.children)

    def stop(self,token=None):
        if self.released is not None:
            if self.released.token is token:return self.released
            if self.children or self.records or os.path.lexists(self.state/'audio-processes.json'):
                raise PCMReleaseError('Registro residuo dopo arresto')
            # A different transaction must re-observe the physical release.
            return PCMReleaseReceipt(False,0,self._confirm_output_release(),True,token)
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
        if errors or any(child.poll() is None for child in self.children):
            raise PCMReleaseError('Arresto dei processi audio non confermato; recupero conservato')
        self.creator.shutdown(wait=True)
        scope=self._confirm_output_release()
        for fdname in ('input_read_fd','input_write_fd'):
            fd=getattr(self,fdname)
            if fd is not None:os.close(fd);setattr(self,fdname,None)
        engine_client.ENGINE_NAMESPACE_FD=None
        if self.namespace_fd is not None:os.close(self.namespace_fd);self.namespace_fd=None
        (self.state/'audio-processes.json').unlink(missing_ok=True)
        if os.path.lexists(self.state/'audio-processes.json'):raise PCMReleaseError('Registro processi audio non ritirato')
        self.children=[];self.records=[];self.route=None;self.suspended=False;self.pending_config=None;self.pending_token=None
        # Retain the selected device contract for independent repeat verification.
        self.released=PCMReleaseReceipt(False,0,scope,True,token)
        return self.released
