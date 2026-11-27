"""Test a generic Daphile live ISO with two writable synthetic sentinel disks.

Linux/QEMU KVM only. No physical disk is passed into the VM. Results and
synthetic images are written exclusively under the new --out directory.
"""
import argparse
from http.cookiejar import CookieJar
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET


def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def qmp(path,action,args=None):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(3);sock.connect(str(path));stream=sock.makefile('rwb',buffering=0)
        json.loads(stream.readline())
        def execute(command,arguments=None):
            obj={'execute':command}
            if arguments is not None:obj['arguments']=arguments
            stream.write(json.dumps(obj).encode()+b'\n')
            while True:
                value=json.loads(stream.readline())
                if 'error' in value:raise RuntimeError(str(value['error']))
                if 'return' in value:return value['return']
        execute('qmp_capabilities');return execute(action,args)


def command(*args):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
    if result.returncode:raise RuntimeError('fixture command failed: '+str(args[0])+': '+result.stderr.decode(errors='replace')[-300:])
    return result.stdout


def sentinel(directory,name):
    path=directory/(name+'.raw');payload=directory/(name+'.txt');text=('DAPHILE SYNTHETIC SENTINEL '+name+'\n').encode()
    payload.write_bytes(text)
    with path.open('xb') as stream:stream.truncate(96*1024*1024)
    command('mkfs.ext4','-q','-F','-L','DAPHILE_TEST_'+name.upper(),str(path))
    command('debugfs','-w','-R','write '+str(payload)+' /SENTINEL.txt',str(path))
    if command('debugfs','-R','cat /SENTINEL.txt',str(path))!=text:raise RuntimeError('sentinel seed verification failed')
    return {'path':str(path),'sha256_before':sha(path),'content_before':text.decode().strip()}


def read_status(opener,http,lms):
    state={'http':None,'lms':None,'catalog':None,'camilla':None,'rme':None}
    try:
        with opener.open(f'http://127.0.0.1:{http}/cgi-bin/sysinfo.cgi',timeout=4) as r:state['http']=r.status
    except Exception:pass
    try:
        request=urllib.request.Request(f'http://127.0.0.1:{lms}/jsonrpc.js',
            data=json.dumps({'id':1,'method':'slim.request','params':['',['version','?']]}).encode(),
            headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=4) as r:state['lms']=json.load(r)['result'].get('_version')
    except Exception:pass
    try:
        with opener.open(f'http://127.0.0.1:{http}/cgi-bin/AudioPlugins/extensions.xml',timeout=4) as r:
            root=ET.fromstring(r.read());row=root.find("./plugins/plugin[@name='DaphileCamillaDSP']")
            state['catalog']=row.get('version') if row is not None else 'missing'
    except Exception:pass
    for label,entry in (('camilla','CamillaPlugin'),('rme','RMEPlugin')):
        try:
            with opener.open(f'http://127.0.0.1:{http}/cgi-bin/{entry}/api/session',timeout=4) as r:
                if json.load(r).get('authenticated') is not True:raise RuntimeError('session unavailable')
            with opener.open(f'http://127.0.0.1:{http}/cgi-bin/{entry}/api/status',timeout=4) as r:
                body=json.load(r);state[label]=body.get('module_version') if label=='camilla' else 'READY'
        except urllib.error.HTTPError as exc:state[label]='HTTP_'+str(exc.code)
        except Exception:pass
    return state


def run(iso,expected,mode,boot_media,out,timeout):
    if os.geteuid()!=0 or not Path('/dev/kvm').exists():raise RuntimeError('KVM and administrator rights required')
    if sha(iso)!=expected:raise RuntimeError('ISO SHA256 mismatch')
    out.mkdir(parents=True,exist_ok=False);out=out.resolve()
    disks=[sentinel(out,'internal'),sentinel(out,'removable')]
    ports=[port(),port()]
    if len(set(ports))!=2:raise RuntimeError('temporary ports overlap')
    # Keep the control socket on the Linux filesystem even when receipts and
    # synthetic disks are in persistent DrvFS for post-WSL inspection.
    http,lms=ports;control=Path(tempfile.mkdtemp(prefix='daphile-iso-qmp-'))/'monitor.sock'
    argv=['qemu-system-x86_64','-name','daphile-live-isolation-'+mode,
        '-machine','q35,accel=kvm','-cpu','host','-m','3072','-smp','4',
        '-display','none','-monitor','none','-serial','file:'+str(out/'serial.log'),
        '-qmp','unix:'+str(control)+',server=on,wait=off',
        '-nic',f'user,restrict=on,model=e1000,hostfwd=tcp:127.0.0.1:{http}-:80,hostfwd=tcp:127.0.0.1:{lms}-:9000',
        '-drive',f'file={disks[0]["path"]},format=raw,if=virtio',
        '-device','qemu-xhci,id=usbcontroller','-drive',f'file={disks[1]["path"]},format=raw,if=none,id=usbdisk',
        '-device','usb-storage,bus=usbcontroller.0,drive=usbdisk']
    if boot_media=='cdrom':
        argv+=['-drive',f'file={iso},format=raw,media=cdrom,readonly=on','-boot','order=d,strict=on']
    else:
        argv+=['-drive',f'file={iso},format=raw,if=none,id=bootusb,readonly=on',
               '-device','usb-storage,bus=usbcontroller.0,drive=bootusb,bootindex=1','-boot','strict=on']
    if mode=='uefi':
        code=Path('/usr/share/OVMF/OVMF_CODE_4M.fd');variables=Path('/usr/share/OVMF/OVMF_VARS_4M.fd')
        if not code.is_file() or not variables.is_file():raise RuntimeError('OVMF unavailable')
        shutil.copyfile(variables,out/'vars.fd')
        argv+=['-drive',f'if=pflash,unit=0,format=raw,readonly=on,file={code}',
               '-drive',f'if=pflash,unit=1,format=raw,file={out/"vars.fd"}']
    result={'status':'RUNNING','mode':mode,'boot_media':boot_media,'acceleration':'KVM','iso_sha256':expected,
            'scope':'ISO readonly; no physical disks or audio; restricted user networking',
            'sentinels':disks,'observations':[]}
    receipt=out/'RESULT.json'
    def save():receipt.write_text(json.dumps(result,indent=2)+'\n')
    with (out/'qemu.log').open('xb') as log:process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    result['pid']=process.pid;save();start=time.monotonic();sent_enter=False;last=None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(CookieJar()))
    try:
        while time.monotonic()-start<timeout:
            if process.poll() is not None:raise RuntimeError('QEMU exited before inspection')
            elapsed=round(time.monotonic()-start,1)
            if elapsed>12 and not sent_enter:
                try:qmp(control,'human-monitor-command',{'command-line':'sendkey ret'});sent_enter=True
                except (OSError,RuntimeError):pass
            state=read_status(opener,http,lms);state['elapsed_seconds']=elapsed
            key=tuple(state[k] for k in ('http','lms','catalog','camilla','rme'))
            if key!=last:result['observations'].append(state);save();print(json.dumps(state),flush=True);last=key
            if state['http']==200 and state['lms']=='9.0.3' and state['catalog']=='0.4.74' and state['camilla']=='0.4.74' and state['rme']=='READY':
                result['application_status']='APP_PASS';break
            time.sleep(5)
        else:result['application_status']='APP_TIMEOUT_NOT_QUALIFIED'
        try:
            with opener.open(f'http://127.0.0.1:{http}/cgi-bin/logread.cgi',timeout=8) as r:log=r.read(1000000).decode(errors='replace')
            result['startup_diagnostics']=[x[:500] for x in log.splitlines() if 'daphile-live-start' in x][-30:]
        except Exception:result['startup_diagnostics']=['log unavailable']
        try:qmp(control,'screendump',{'filename':str(out/'screen.ppm')})
        except Exception:pass
    except Exception as exc:result['application_status']='APP_ERROR_NOT_QUALIFIED';result['error']=str(exc)
    finally:
        if process.poll() is None:
            try:qmp(control,'quit')
            except Exception:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for disk in disks:
            p=Path(disk['path']);disk['sha256_after']=sha(p)
            disk['content_after']=command('debugfs','-R','cat /SENTINEL.txt',str(p)).decode().strip()
            disk['unchanged']=disk['sha256_after']==disk['sha256_before'] and disk['content_after']==disk['content_before']
        result['sentinel_status']='DISKS_UNCHANGED' if all(d['unchanged'] for d in disks) else 'DISK_CHANGED'
        result['iso_sha256_after']=sha(iso);result['elapsed_seconds']=round(time.monotonic()-start,1)
        result['status']='PASS' if result['application_status']=='APP_PASS' and result['sentinel_status']=='DISKS_UNCHANGED' and result['iso_sha256_after']==expected else 'NOT_QUALIFIED'
        save();print(json.dumps({'status':result['status'],'receipt':str(receipt)}),flush=True)
    return result['status']=='PASS'


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--iso',type=Path,required=True)
    p.add_argument('--sha256',required=True);p.add_argument('--mode',choices=('bios','uefi'),required=True)
    p.add_argument('--boot-media',choices=('cdrom','usb'),default='cdrom')
    p.add_argument('--out',type=Path,required=True);p.add_argument('--timeout',type=int,default=360)
    a=p.parse_args()
    if not 60<=a.timeout<=900:raise ValueError('bounded timeout required')
    raise SystemExit(0 if run(a.iso.resolve(strict=True),a.sha256,a.mode,a.boot_media,a.out,a.timeout) else 1)
