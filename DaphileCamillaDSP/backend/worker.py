"""One independently packaged controller; never restarts or configures the player."""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import threading

from common import atomic, identity, recover_rme, stop_owned, started_identity, pin_engine_namespace


def parent_death():
    parent = os.getpid()
    def setup():
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent: os._exit(1)
    return setup


def isolated_engine():
    parent_setup=parent_death()
    def setup():
        parent_setup()
        os.unshare(os.CLONE_NEWNET)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
            request=struct.pack('16sH14s',b'lo',0,b'')
            result=fcntl.ioctl(control,0x8913,request)
            name,flags,padding=struct.unpack('16sH14s',result)
            fcntl.ioctl(control,0x8914,struct.pack('16sH14s',name,flags|1,padding))
    return setup


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--module', choices=['DaphileRME','DaphileCamillaDSP'], required=True)
    parser.add_argument('--state', required=True); parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--rme-state')
    args = parser.parse_args(); release = Path(__file__).resolve().parent
    state = Path(args.state)
    transaction_state = Path(args.rme_state) if args.rme_state else state
    engine = None; server = None; stopping = False; namespace_fd=None;audio=None;audio_monitor=None;audio_pcm_monitor=None
    def shutdown(*_):
        nonlocal stopping
        stopping = True
        if server: threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown); signal.signal(signal.SIGINT, shutdown)
    try:
        if args.module == 'DaphileRME':
            recover_rme(release, transaction_state)
            from rme_server import make_server
            server = make_server(release, state, args.port, transaction_state)
        else:
            from peq.server import Application, make_server
            route_path = state/'routing.json'
            route = json.loads(route_path.read_text()) if route_path.exists() else None
            if route is not None:
                # Administrator-only PCM routing. No player rewrite or DSD claim.
                from peq.model import compile_config, new_profile, RATES
                if set(route) != {'rate','chunksize','capture','playback','verified_pcm'} or route['verified_pcm'] is not True or route['rate'] not in RATES:
                    raise ValueError('Qualified PCM routing required')
                for role in ('capture','playback'):
                    device=route[role]
                    if device.get('type') not in (('RawFile','Alsa') if role=='capture' else ('File','Alsa')) or device.get('format') not in ('S16_LE','S24_3_LE','S24_4_RJ_LE','S32_LE','F64_LE'):
                        raise ValueError('PCM RawFile/File/ALSA route required')
                    if device['type'] in ('RawFile','File') and not Path(device['filename']).resolve().is_relative_to(state.resolve()):
                        raise ValueError('File route must stay in private module state')
                profile_path = state/'active_profile.json'
                profile = json.loads(profile_path.read_text()) if profile_path.exists() else new_profile('Flat')
                config = compile_config(profile, route['rate'], route['chunksize'], route['capture'], route['playback'])
                atomic(state/'engine-config.json', config)
                with (state/'engine.log').open('ab') as log:
                    engine = subprocess.Popen([str(release/'bin/camilladsp'),'-p',str(args.port+100),str(state/'engine-config.json')],
                                              stdin=subprocess.DEVNULL,stdout=log,stderr=log,preexec_fn=isolated_engine())
                record=started_identity(engine)
                atomic(state/'engine-process.json',record)
                namespace_fd=pin_engine_namespace(engine,record)
                from peq import engine as engine_client
                engine_client.ENGINE_NAMESPACE_FD=namespace_fd
            if route is None:
                from native_recover import recover
                recover(state)
                from native_audio import NativeAudio
                from pcm_pipeline import PCMPipeline
                audio=NativeAudio(state,lambda:PCMPipeline(release,state,args.port+100))
            server = make_server(Application(state, route, args.port+100,audio=audio), '127.0.0.1',args.port)
            server.daemon_threads = False
            if audio:
                audio_monitor=threading.Thread(target=audio.monitor,daemon=True);audio_monitor.start()
                audio_pcm_monitor=threading.Thread(target=audio.monitor_pcm,daemon=True);audio_pcm_monitor.start()
        if stopping: return
        atomic(state/'ready.json', dict(module=args.module, process=identity(os.getpid()), port=server.server_port))
        server.serve_forever(poll_interval=.1)
    finally:
        (state/'ready.json').unlink(missing_ok=True)
        if server: server.server_close()
        if audio:
            audio.close()
            if audio_monitor:audio_monitor.join(timeout=10)
            if audio_pcm_monitor:audio_pcm_monitor.join(timeout=10)
        if engine:
            if engine.poll() is None: stop_owned(json.loads((state/'engine-process.json').read_text()))
            engine.wait(timeout=15)
            (state/'engine-process.json').unlink(missing_ok=True)
        if namespace_fd is not None:os.close(namespace_fd)
        if args.module == 'DaphileRME': recover_rme(release,transaction_state)


if __name__ == '__main__': main()
