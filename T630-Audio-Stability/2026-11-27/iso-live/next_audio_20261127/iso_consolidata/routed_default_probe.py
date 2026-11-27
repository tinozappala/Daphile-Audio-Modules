"""Select the tested native default only for .72's verified bound PCM route.

Called by the vendor player's defaults.  It makes no changes and uses exactly
the installed router's selected_card function for the USB/ALSA identity check.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import stat
import sys

BASE=Path('/mnt/hdd/system/daphile-audio-host/native')
PLAYER=Path('/usr/bin/squeezelite')


def regular(path,private=False):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&(0o077 if private else 0o022)
            or info.st_size>1024*1024):
        raise RuntimeError('untrusted router input')
    return info


def trusted_directory(path):
    info=path.lstat()
    return stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022


def selected(card,device,base=BASE,player=PLAYER,proc=Path('/proc/asound'),sysroot=Path('/sys')):
    if os.geteuid()!=0 or os.environ.get('ALSA_CONFIG_PATH') or not str(card).isdigit() or str(device)!='0':return False
    base=Path(base)
    if not trusted_directory(base):return False
    route=base/'route.json';owner=base/'owner.json';router=base/'player-router'
    route_info=regular(route,True)
    if route_info.st_size>4096:return False
    regular(owner,True);router_info=regular(router)
    mounted=Path(player).stat()
    if (router_info.st_dev,router_info.st_ino)!=(mounted.st_dev,mounted.st_ino):return False
    record=json.loads(owner.read_bytes())
    if (record.get('tag')!='DAPHILE_CAMILLA_NATIVE_ROUTER_V1' or
        record.get('files',{}).get('player-router')!=hashlib.sha256(router.read_bytes()).hexdigest()):return False
    actual=json.loads(route.read_bytes())
    functions=runpy.run_path(str(router),run_name='_daphile_route_probe')
    match=functions['selected_card'](['-o','hw:'+str(card)+','+str(device)],actual,proc_root=proc,sys_root=sysroot)
    return match==int(card)


def main():
    try:return 0 if len(sys.argv)==3 and selected(sys.argv[1],sys.argv[2]) else 1
    except (OSError,ValueError,TypeError,KeyError,AttributeError,RuntimeError,ImportError,SyntaxError):return 1


if __name__=='__main__':raise SystemExit(main())
