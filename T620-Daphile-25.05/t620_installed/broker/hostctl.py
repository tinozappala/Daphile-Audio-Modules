"""Administrator installer/service control for the small native audio host."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import platform
import signal
import stat
import shutil
import shlex
import socket
import subprocess
import sys
import time
import uuid

from common import identity, stop_owned
from broker import MARKER


def direct_absolute(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Canonical absolute host destination required')
    return path


def safe_directory(info,ancestor=False):
    if stat.S_ISLNK(info.st_mode):raise RuntimeError('Indirect host destination refused')
    if not stat.S_ISDIR(info.st_mode):raise RuntimeError('Host destination parent is not a directory')
    # A root-owned sticky ancestor such as /tmp cannot have a root-owned child
    # replaced by an unrelated user. The actual destination is never writable
    # by others; newly created children are checked again through descriptors.
    sticky_ancestor=ancestor and info.st_mode&stat.S_ISVTX
    if info.st_uid!=0 or (info.st_mode&0o022 and not sticky_ancestor):
        raise RuntimeError('Unsafe host destination ownership')


def checked_directory(path):
    """Read-only preflight of every existing component, without resolve()."""
    path=direct_absolute(path)
    for item in reversed((path,*path.parents)):
        try:info=item.lstat()
        except FileNotFoundError:continue
        safe_directory(info,ancestor=item!=path)
    return path


def safe_file(info):
    if stat.S_ISLNK(info.st_mode):raise RuntimeError('Indirect host destination refused')
    if not stat.S_ISREG(info.st_mode):raise RuntimeError('Host destination is not a regular file')
    if info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('Unsafe host destination ownership')


def checked_destination(root,path):
    root=direct_absolute(root);path=direct_absolute(path)
    if not path.is_relative_to(root):raise RuntimeError('Host destination escapes its directory')
    checked_directory(path.parent)
    try:info=path.lstat()
    except FileNotFoundError:return
    safe_file(info)


@contextlib.contextmanager
def directory_handle(path,create=False):
    """Traverse from / using O_NOFOLLOW; pin the directory used for each write."""
    path=checked_directory(path)
    flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
    fd=os.open(path.anchor,flags)
    parts=path.parts[1:]
    try:
        safe_directory(os.fstat(fd),ancestor=bool(parts))
        for index,part in enumerate(parts):
            try:child=os.open(part,flags,dir_fd=fd)
            except FileNotFoundError:
                if not create:raise
                try:os.mkdir(part,0o755,dir_fd=fd)
                except FileExistsError:pass
                os.fsync(fd)
                child=os.open(part,flags,dir_fd=fd)
            try:safe_directory(os.fstat(child),ancestor=index<len(parts)-1)
            except BaseException:
                os.close(child);raise
            os.close(fd);fd=child
        yield fd
    finally:os.close(fd)


def mkdir_checked(path):
    with directory_handle(path,create=True):pass


def file_state(directory,name):
    try:info=os.stat(name,dir_fd=directory,follow_symlinks=False)
    except FileNotFoundError:return None
    safe_file(info)
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,
            info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def replace_bytes(path,data,mode=0o644):
    from host_fresh_transaction_j3 import directory, read_at, stat_image, same_parent, owned_image, sha, MAX_FILE
    if type(data) is not bytes or len(data)>MAX_FILE or mode not in (0o600,0o644,0o755):
        raise RuntimeError('Invalid bounded host replacement payload')
    path=direct_absolute(path);checked_destination(path.parent,path)
    with directory(path.parent) as parent:
        original=read_at(parent,path.name)[1]
        temporary='.'+path.name+'.'+uuid.uuid4().hex+'.new';prepared=None
        try:
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,mode,dir_fd=parent)
            with os.fdopen(fd,'wb') as stream:
                stream.write(data);stream.flush();os.fchmod(stream.fileno(),mode);os.fsync(stream.fileno())
                prepared=stat_image(os.fstat(stream.fileno()));prepared['sha256']=sha(data)
            same_parent(path.parent,parent)
            if read_at(parent,path.name)[1]!=original:
                raise RuntimeError('Host destination file changed during write')
            if read_at(parent,temporary)[1]!=prepared:
                raise RuntimeError('Host prepared temporary changed during write')
            # A dirfd prevents redirection through a replaced path component.
            # This is not a filesystem-wide CAS against another administrator.
            os.replace(temporary,path.name,src_dir_fd=parent,dst_dir_fd=parent)
            os.fsync(parent)
            if not owned_image(read_at(parent,path.name)[1],prepared):
                raise RuntimeError('Published host file differs from prepared inode')
        finally:
            if prepared is not None and read_at(parent,temporary)[1]==prepared:
                os.unlink(temporary,dir_fd=parent);os.fsync(parent)


def atomic(path,value):
    replace_bytes(path,(json.dumps(value,allow_nan=False)+'\n').encode(),0o600)


def request(root, module, action):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
        client.settimeout(60); client.connect(str(Path(root)/'control.sock'))
        client.sendall(json.dumps({'module':module,'action':action}).encode()+b'\n')
        data=b''
        while not data.endswith(b'\n') and len(data)<65536:
            block=client.recv(65536-len(data))
            if not block: raise RuntimeError('Host response interrupted')
            data+=block
        return json.loads(data)


def service(root, action):
    root=Path(root)
    if (root/'.owned').read_text()!=MARKER: raise RuntimeError('Unowned host')
    path=root/'process.json'
    if action=='stop':
        if path.exists(): stop_owned(json.loads(path.read_text()),timeout=50)
        return
    if path.exists():
        try:
            record=json.loads(path.read_text());actual=identity(record['pid'])
            if actual==record: return
            if actual['boot']==record['boot'] and actual['start']==record['start']: raise RuntimeError('Host process identity mismatch')
            path.unlink()
        except (ProcessLookupError,FileNotFoundError): path.unlink(missing_ok=True)
    with (root/'host.log').open('ab') as log:
        child=subprocess.Popen([sys.executable,'-B',str(root/'code/broker.py'),'--root',str(root)],
                               stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    deadline=time.monotonic()+10
    while time.monotonic()<deadline and child.poll() is None:
        if path.exists() and (root/'control.sock').exists() and json.loads(path.read_text())==identity(child.pid): return
        time.sleep(.05)
    if child.poll() is None: stop_owned(identity(child.pid))
    raise RuntimeError('Audio host startup failed')


def install(root, bundle_dir, expected, plugins, prefs, cgi, rme_state, uid, hooks=None, repo_base_url=None):
    if os.geteuid()!=0: raise RuntimeError('Administrator installation required')
    if sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64') or not all(hasattr(os,n) for n in ('pidfd_open','unshare','setns','CLONE_NEWNET')) or not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('Qualified Linux x86_64 and pidfd runtime required')
    import yaml  # Fail before mutation if the native preferences reader is absent.
    root=Path(root)
    if root.is_symlink() or not root.is_absolute() or root in (Path('/'),Path('/mnt'),Path('/mnt/hdd'),Path('/opt'),Path('/tmp')):
        raise RuntimeError('Unsafe host root')
    if any(parent.is_symlink() for parent in root.parents):raise RuntimeError('Indirect host root ancestor')
    root=checked_directory(root)
    # This entry point creates defaults only for a new, empty host.
    # The existing-host updater binds and preserves the complete config bytes.
    if root.exists() and any(root.iterdir()):
        raise RuntimeError('Existing audio host requires the preserving updater')
    cgi_dir=checked_directory(cgi)
    hook_dir=checked_directory(hooks) if hooks else None
    bundle_dir=Path(bundle_dir)
    manifest=bundle_dir/'host-bundle.json'
    manifest_bytes=manifest.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest()!=expected: raise RuntimeError('External host manifest pin mismatch')
    contents=json.loads(manifest_bytes)
    # Read and verify every byte before installing any executable or approval.
    verified={}
    for relative,digest in contents['files'].items():
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or path.as_posix()!=relative or not re.fullmatch(r'[A-Za-z0-9_./-]+',relative): raise ValueError('Unsafe host member')
        source=bundle_dir/path
        if source.is_symlink() or not source.is_file(): raise RuntimeError('Host file not regular')
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest: raise RuntimeError('Host bundle byte mismatch')
        verified[relative]=data
    if root.exists() and any(root.iterdir()) and not (root/'.owned').exists(): raise RuntimeError('Nonempty unowned prefix')
    if root.exists() and (root.stat().st_uid!=0 or root.stat().st_mode&0o022):raise RuntimeError('Unsafe existing host ownership')
    for relative in (*verified,'.owned','config.json','process.json'):
        checked_destination(root,root/relative)
    if (root/'.owned').exists() and (root/'.owned').read_text()!=MARKER: raise RuntimeError('Host marker mismatch')
    hook_content={}
    if hook_dir is not None:
        for action in ('start','stop'):
            path=hook_dir/('97_daphile_audio_host.'+action)
            content=('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(root/'code/hostctl.py'))+' '+action+' --root '+shlex.quote(str(root))+'\n').encode()
            hook_content[path]=content
    repository_code=None;repository_xml=None
    if repo_base_url:
        for name in ('repository.xml','repository-cgi.json'):
            checked_destination(root,root/name)
        repository_namespace={'__name__':'_verified_fresh_host_repository_j1','__file__':str(bundle_dir/'code/repository.py')}
        exec(compile(verified['code/repository.py'],repository_namespace['__file__'],'exec'),repository_namespace)
        repository_xml=repository_namespace['catalog'](json.loads(verified['approvals.json']),repo_base_url)
        template=verified['code/repository.py'].decode()
        if template.count("Path('/__DAPHILE_AUDIO_HOST__')")!=1:raise RuntimeError('Unknown repository CGI contract')
        repository_code=('#!/usr/bin/python3\n'+template.replace("Path('/__DAPHILE_AUDIO_HOST__')",'Path('+repr(str(root))+')')).encode()
    def external_preflight():
        checked_directory(cgi_dir)
        if hook_dir is not None:
            checked_directory(hook_dir)
            for path,content in hook_content.items():
                checked_destination(hook_dir,path)
                if path.exists() and path.read_bytes()!=content:raise RuntimeError('Existing host hook differs')
        if repository_code is not None:
            destination=cgi_dir/'AudioPlugins';record=root/'repository-cgi.json'
            checked_destination(cgi_dir,destination)
            if destination.exists():
                if not record.exists() or hashlib.sha256(destination.read_bytes()).hexdigest()!=json.loads(record.read_text())['sha256']:
                    raise RuntimeError('Repository CGI belongs to another component')

    def preflight():
        checked_directory(root)
        if root.exists() and any(root.iterdir()):
            raise RuntimeError('Existing audio host requires the preserving updater')
        for relative in (*verified,'.owned','config.json','process.json'):
            checked_destination(root,root/relative)
        if repository_code is not None:
            for name in ('repository.xml','repository-cgi.json'):checked_destination(root,root/name)
        external_preflight()

    # Complete validation precedes even creation of the recovery stage. The
    # transaction refuses a running host: stopping its broker also invokes
    # audio recovery and cannot be used to manufacture an installable baseline.
    preflight()
    from host_fresh_transaction_j3 import apply as apply_transaction
    rme_states={str(direct_absolute(rme_state))}
    if (root/'config.json').exists():
        previous=json.loads((root/'config.json').read_text())
        if type(previous.get('rme_state')) is not str:
            raise RuntimeError('Existing host recovery state is unknown')
        rme_states.add(str(direct_absolute(previous['rme_state'])))
    payloads=[]
    for relative,data in verified.items():
        payloads.append(dict(path=root/relative,data=data,mode=0o644))
    external=[]
    if repository_code is not None:
        destination=cgi_dir/'AudioPlugins';external.append(destination)
        payloads.append(dict(path=destination,data=repository_code,mode=0o755))
        payloads.append(dict(path=root/'repository.xml',data=repository_xml,mode=0o644))
        record=dict(sha256=hashlib.sha256(repository_code).hexdigest(),base_url=repo_base_url)
        payloads.append(dict(path=root/'repository-cgi.json',data=(json.dumps(record,allow_nan=False)+'\n').encode(),mode=0o600))
    config=dict(plugins=str(Path(plugins)),prefs=str(Path(prefs)),cgi=str(cgi_dir),
                rme_state=str(Path(rme_state)),lms_uid=uid,
                ports={'DaphileRME':8891,'DaphileCamillaDSP':8892})
    payloads.append(dict(path=root/'config.json',data=(json.dumps(config,allow_nan=False)+'\n').encode(),mode=0o600))
    if hook_dir is not None:
        for path,content in hook_content.items():
            external.append(path)
            payloads.append(dict(path=path,data=content,mode=0o755))
    payloads.append(dict(path=root/'.owned',data=MARKER.encode(),mode=0o644))
    apply_transaction(root,payloads,directories=[root,cgi_dir]+([hook_dir] if hook_dir else []),
                      external=external,rme_states=sorted(rme_states),
                      expected_preimages={str(item['path']):None for item in payloads})
    return root


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['install','start','stop','status','sync','restart'])
    parser.add_argument('--root',required=True);parser.add_argument('--module',choices=['DaphileRME','DaphileCamillaDSP'])
    parser.add_argument('--bundle-dir');parser.add_argument('--expected-sha256')
    parser.add_argument('--plugins',default='/var/daphile/mediaserver/cache/InstalledPlugins/Plugins')
    parser.add_argument('--prefs',default='/var/daphile/mediaserver/prefs/plugin/state.prefs')
    parser.add_argument('--cgi',default='/var/www/localhost/cgi-bin');parser.add_argument('--rme-state',default='/mnt/hdd/daphile-autotune')
    parser.add_argument('--lms-uid',type=int,default=1000);parser.add_argument('--hooks')
    parser.add_argument('--repository-base-url')
    args=parser.parse_args()
    if args.action=='install':
        install(args.root,args.bundle_dir,args.expected_sha256,args.plugins,args.prefs,args.cgi,args.rme_state,args.lms_uid,args.hooks,args.repository_base_url)
    elif args.action in ('start','stop'): service(args.root,args.action)
    elif args.module:
        result=request(args.root,args.module,args.action)
        print(json.dumps(result))
        if result.get('error'): raise SystemExit(1)
    else: print((Path(args.root)/'status.json').read_text())


if __name__=='__main__':main()
