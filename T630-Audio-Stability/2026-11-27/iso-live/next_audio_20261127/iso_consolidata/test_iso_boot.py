"""Boot exact trial ISO without writable disks; verify LMS and audio catalog."""
import argparse
from http.cookiejar import CookieJar
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

Q=Path('C:/Tools/QEMU-Q2')


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def qmp(port,command,args=None):
    with socket.create_connection(('127.0.0.1',port),timeout=3) as sock:
        f=sock.makefile('rwb',buffering=0);json.loads(f.readline())
        def call(name,arguments=None):
            obj={'execute':name}
            if arguments is not None:obj['arguments']=arguments
            f.write(json.dumps(obj).encode()+b'\n')
            while True:
                answer=json.loads(f.readline())
                if 'error' in answer:raise RuntimeError(str(answer['error']))
                if 'return' in answer:return answer['return']
        call('qmp_capabilities');return call(command,args)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iso',type=Path,required=True);p.add_argument('--sha256',required=True)
    p.add_argument('--version',choices=('0.4.71','0.4.72'),required=True)
    p.add_argument('--mode',choices=('bios','uefi'),required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--timeout-seconds',type=int,default=900)
    args=p.parse_args();iso=args.iso.resolve(strict=True)
    if sha(iso)!=args.sha256:raise RuntimeError('trial ISO hash differs')
    if not 60<=args.timeout_seconds<=1200:raise ValueError('bounded timeout required')
    args.out.mkdir(parents=True,exist_ok=False);case=args.out.resolve()
    qmp_port,http_port,lms_port=port(),port(),port()
    if len({qmp_port,http_port,lms_port})!=3:raise RuntimeError('temporary ports overlap')
    argv=[str(Q/'qemu-system-x86_64.exe'),'-name','daphile-iso-qa-'+args.mode,'-L',str(Q/'share'),
          '-machine','q35','-accel','tcg,tb-size=128','-m','3072','-smp','4','-cpu','max,avx2=off',
          '-display','none','-monitor','none','-serial','file:'+str(case/'serial.log'),
          '-qmp',f'tcp:127.0.0.1:{qmp_port},server=on,wait=off',
          '-nic',f'user,restrict=on,model=e1000,hostfwd=tcp:127.0.0.1:{http_port}-:80,hostfwd=tcp:127.0.0.1:{lms_port}-:9000',
          '-drive','file='+str(iso)+',format=raw,media=cdrom,readonly=on','-boot','order=d,strict=on']
    if args.mode=='uefi':
        vars_path=case/'vars.fd';vars_path.write_bytes((Q/'share/edk2-i386-vars.fd').read_bytes())
        argv+=['-drive','if=pflash,unit=0,format=raw,readonly=on,file='+str(Q/'share/edk2-x86_64-code.fd'),
               '-drive','if=pflash,unit=1,format=raw,file='+str(vars_path)]
    result={'status':'RUNNING','mode':args.mode,'version':args.version,'iso_sha256':args.sha256,
            'scope':'local QEMU TCG, readonly ISO, no DATA disk, isolated user networking, no physical audio',
            'observations':[],'http_port':http_port,'lms_port':lms_port,'qmp_port':qmp_port}
    report=case/'BOOT_RESULT.json'
    def save():report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    env=dict(os.environ,PATH=str(Q)+os.pathsep+os.environ['PATH']);start=time.monotonic()
    with (case/'qemu.log').open('xb') as log:
        process=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env=env,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    result['pid']=process.pid;save();print(json.dumps({'status':'STARTED','pid':process.pid,'out':str(case)}),flush=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(CookieJar()))
    last=None;sent_enter=False
    try:
        while time.monotonic()-start<args.timeout_seconds:
            elapsed=time.monotonic()-start
            if process.poll() is not None:raise RuntimeError('QEMU exited before boot: '+str(process.returncode))
            if elapsed>15 and not sent_enter:
                try:qmp(qmp_port,'human-monitor-command',{'command-line':'sendkey ret'});sent_enter=True
                except OSError:pass
            state={'elapsed_seconds':round(elapsed,1),'http':'unavailable','lms':'unavailable','catalog':'unavailable','camilla':'unavailable'}
            try:
                with opener.open(f'http://127.0.0.1:{http_port}/cgi-bin/sysinfo.cgi',timeout=8) as response:
                    state['http']=response.status
            except urllib.error.HTTPError as exc:state['http']='HTTP_'+str(exc.code)
            except Exception as exc:state['http']=type(exc).__name__
            try:
                request=urllib.request.Request(f'http://127.0.0.1:{lms_port}/jsonrpc.js',
                    data=json.dumps({'id':1,'method':'slim.request','params':['',['version','?']]}).encode(),
                    headers={'Content-Type':'application/json'})
                with opener.open(request,timeout=8) as response:state['lms']=json.load(response)['result'].get('_version')
            except urllib.error.HTTPError as exc:state['lms']='HTTP_'+str(exc.code)
            except Exception as exc:state['lms']=type(exc).__name__
            try:
                with opener.open(f'http://127.0.0.1:{http_port}/cgi-bin/AudioPlugins/extensions.xml',timeout=8) as response:
                    root=ET.fromstring(response.read());row=root.find("./plugins/plugin[@name='DaphileCamillaDSP']")
                    state['catalog']=row.get('version') if row is not None else 'missing'
            except urllib.error.HTTPError as exc:state['catalog']='HTTP_'+str(exc.code)
            except Exception as exc:state['catalog']=type(exc).__name__
            try:
                with opener.open(f'http://127.0.0.1:{http_port}/cgi-bin/CamillaPlugin/api/session',timeout=8) as response:
                    if json.load(response).get('authenticated') is not True:raise RuntimeError('session unavailable')
                with opener.open(f'http://127.0.0.1:{http_port}/cgi-bin/CamillaPlugin/api/status',timeout=8) as response:
                    status=json.load(response);state['camilla']=status.get('module_version')
            except urllib.error.HTTPError as exc:state['camilla']='HTTP_'+str(exc.code)
            except Exception as exc:state['camilla']=type(exc).__name__
            key=tuple(state[k] for k in ('http','lms','catalog','camilla'))
            if key!=last:
                result['observations'].append(state);save();print(json.dumps(state),flush=True);last=key
            if state['http']==200 and state['lms']=='9.0.3' and state['catalog']==args.version and state['camilla']==args.version:
                result['status']='BOOT_LMS_CATALOG_CAMILLA_PASS';break
            time.sleep(8)
        else:result['status']='TIMEOUT_NOT_QUALIFIED'
        try:qmp(qmp_port,'screendump',{'filename':str(case/'screen.ppm')});result['screenshot']='screen.ppm'
        except Exception as exc:result['screenshot_error']=type(exc).__name__
    except Exception as exc:
        result['status']='ERROR_NOT_QUALIFIED';result['error']=type(exc).__name__+': '+str(exc)
    finally:
        if process.poll() is None:
            try:qmp(qmp_port,'quit')
            except Exception:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
        result['qemu_exit']=process.returncode;result['elapsed_seconds']=round(time.monotonic()-start,1)
        result['iso_sha256_after']=sha(iso)
        if result['iso_sha256_after']!=args.sha256:result['status']='ISO_CHANGED_NOT_QUALIFIED'
        save();print(json.dumps({'status':result['status'],'receipt':str(report)}),flush=True)
    return 0 if result['status']=='BOOT_LMS_CATALOG_CAMILLA_PASS' else 1


if __name__=='__main__':raise SystemExit(main())
