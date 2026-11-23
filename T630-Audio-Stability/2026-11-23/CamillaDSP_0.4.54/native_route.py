"""Owned reversible bind for the optional native Squeezelite PCM router.

This adapter changes no audio preferences and does not restart the card. The
coordinator must stop DSP, restore this bind, then use the native card lifecycle.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

TAG='DAPHILE_CAMILLA_NATIVE_ROUTER_V1'
NATIVE_SHA256='7555832f4a930e00e11ebd7529a9ecab5a4d1e11a979c003bfb8ea641c4f0846'


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path,data,mode=0o600):
    path=Path(path)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        if os.name=='posix':os.fchmod(fd,mode)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        if os.name=='posix':
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


def atomic_json(path,value):atomic(path,(json.dumps(value,sort_keys=True)+'\n').encode())


class NativeRoute:
    def __init__(self,state,native_path='/usr/bin/squeezelite',expected_binary_sha256=NATIVE_SHA256):
        self.state=Path(state).absolute()
        if self.state.parent.name!='DaphileCamillaDSP' or self.state.parents[1].name!='modules':
            raise RuntimeError('Owned Camilla module state required')
        self.base=self.state.parents[2]/'native'
        self.guard_directories()
        self.target=Path(native_path).resolve(strict=True)
        self.expected=expected_binary_sha256
        self.record=self.state/'native-router.json'

    def guard_directories(self):
        for path in (self.state,self.state.parent,self.state.parents[1],self.state.parents[2]):
            if path.is_symlink():raise RuntimeError('Indirect native router state refused')
            if path.exists():
                info=path.stat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                    raise RuntimeError('Untrusted native router state directory')

    def bound(self):
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields=line.split()
            if len(fields)>5:
                mount=fields[4].replace('\\040',' ').replace('\\011','\t').replace('\\134','\\')
                if mount==str(self.target):return True
        return False

    def command(self,*args):
        subprocess.run(list(args),check=True,timeout=10,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

    def ownership(self):
        self.guard_directories()
        if not self.record.is_file() or self.record.is_symlink():raise RuntimeError('Router recovery record missing')
        record=json.loads(self.record.read_text())
        if record.get('tag')!=TAG or record.get('target')!=str(self.target) or record.get('native_sha256')!=self.expected:
            raise RuntimeError('Router recovery ownership differs')
        return record

    def guard_files(self):
        owner=self.base/'owner.json'
        if owner.is_symlink() or not owner.is_file():raise RuntimeError('Native router file ownership missing')
        value=json.loads(owner.read_text())
        if value.get('tag')!=TAG or set(value.get('files',{}))!={'squeezelite','player-router','alsa-route.conf'}:
            raise RuntimeError('Native router file ownership differs')
        if any((self.base/name).is_symlink() or digest(self.base/name)!=sha for name,sha in value['files'].items()):
            raise RuntimeError('Native router files changed externally')
        if digest(self.base/'squeezelite')!=self.expected:raise RuntimeError('Native backup changed externally')

    def own_bind(self,record):
        original=(self.base/'player-router').stat();bound=self.target.stat()
        return (self.bound() and (original.st_dev,original.st_ino)==(bound.st_dev,bound.st_ino)
                and digest(self.target)==record['router_sha256'])

    def activate(self,alsa_id,usb,subdevice=6):
        self.guard_directories()
        if type(subdevice)!=int or subdevice not in (6,7):raise ValueError('Reserved Loopback stream required')
        if self.record.exists() or self.bound():raise RuntimeError('Existing router or mount needs recovery first')
        if digest(self.target)!=self.expected or self.target.read_bytes()[:4]!=b'\x7fELF':
            raise RuntimeError('Unqualified native player binary; routing not changed')
        route={'version':1,'alsa_id':alsa_id,'usb':usb}
        from player_router import selected_card
        # Validate the selected device through the same contract as exec routing.
        if selected_card(['-o','hw:'+alsa_id+',0'],route) is None:
            raise RuntimeError('Selected USB DAC identity not verified')
        self.state.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.base.mkdir(mode=0o755,exist_ok=True)
        owner=self.base/'owner.json'
        if self.base.is_symlink() or self.base.stat().st_uid!=0 or self.base.stat().st_mode&0o022:
            raise RuntimeError('Untrusted native router directory')
        if owner.exists():
            self.guard_files()
        elif any(self.base.iterdir()):raise RuntimeError('Native router directory belongs to another component')
        template=Path(__file__).with_name('player_router.py').read_text()
        if template.count('"__DAPHILE_NATIVE_ROUTER_BASE__"')!=1:raise RuntimeError('Router template changed')
        router=template.replace('"__DAPHILE_NATIVE_ROUTER_BASE__"',repr(str(self.base))).encode()
        config=('</usr/share/alsa/alsa.conf>\npcm.!hw {\n @args [ CARD DEV SUBDEV ]\n @args.CARD { type string }\n @args.DEV { type integer default 0 }\n @args.SUBDEV { type integer default -1 }\n type hw\n card "Loopback"\n device 0\n subdevice '+str(subdevice)+'\n}\n').encode()
        files={'squeezelite':self.target.read_bytes(),'player-router':router,'alsa-route.conf':config}
        for name,data in files.items():atomic(self.base/name,data,0o644 if name.endswith('.conf') else 0o755)
        atomic_json(owner,{'tag':TAG,'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}})
        route_bytes=(json.dumps(route,sort_keys=True)+'\n').encode()
        record={'tag':TAG,'phase':'prepared','target':str(self.target),'native_sha256':self.expected,'router_sha256':digest(self.base/'player-router'),'route_sha256':hashlib.sha256(route_bytes).hexdigest()}
        atomic_json(self.record,record)
        try:
            self.command('mount','--bind',str(self.base/'player-router'),str(self.target))
            if not self.own_bind(record):
                raise RuntimeError('Native router bind not verified')
            atomic(self.base/'route.json',route_bytes)
            record['phase']='bound';atomic_json(self.record,record)
            return {'status':'router-bound','preferences_changed':False}
        except BaseException:
            self.deactivate()
            raise

    def deactivate(self):
        record=self.ownership()
        self.guard_files()
        if self.bound() and not self.own_bind(record):
            raise RuntimeError('External bind modification; recovery refused')
        route=self.base/'route.json'
        if route.exists():
            if route.is_symlink() or digest(route)!=record['route_sha256']:
                raise RuntimeError('External route change; recovery refused')
            route.unlink()
        if self.bound():self.command('umount',str(self.target))
        if self.bound() or digest(self.target)!=self.expected:
            raise RuntimeError('Original native player not verified after unbind')
        self.record.unlink()
        return {'status':'native-binary-restored','native_binary_sha256':self.expected,'preferences_changed':False}
