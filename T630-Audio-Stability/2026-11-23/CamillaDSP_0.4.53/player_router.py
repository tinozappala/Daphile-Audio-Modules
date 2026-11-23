#!/usr/bin/python3
"""Optional native-player exec router. Other DACs and callers keep their path."""
import json
import os
from pathlib import Path
import re
import stat
import sys

ROUTER_BASE = "__DAPHILE_NATIVE_ROUTER_BASE__"


def valid_usb_identity(value):
    if not isinstance(value,dict):return False
    keys=set(value)
    if keys not in ({'vendor','product','serial'},{'vendor','product','port','connection'}):return False
    if not all(isinstance(v,str) and v for v in value.values()):return False
    if not all(re.fullmatch(r'[0-9a-f]{4}',value[k]) for k in ('vendor','product')):return False
    if 'serial' in value:return len(value['serial'])<=256
    # Sysfs topology, never a card number or the transient USB device address.
    port=value['port']
    return (re.fullmatch(r'[1-9][0-9]{0,3}:[1-9][0-9]{0,3}',value['connection']) is not None
            and len(port)<=1024 and port.startswith('devices/')
            and all(re.fullmatch(r'[A-Za-z0-9_.:+-]+',p) and p not in ('.','..') for p in port.split('/'))
            and re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*',port.split('/')[-1]) is not None)


def usb_identity(card, sys_root=Path('/sys')):
    device=(sys_root/'class/sound'/('card'+str(card))/'device').resolve()
    for parent in (device,*device.parents):
        if (parent/'idVendor').is_file() and (parent/'idProduct').is_file():
            identity={key:(parent/file).read_text().strip().lower() for key,file in
                      (('vendor','idVendor'),('product','idProduct'))}
            try:serial=(parent/'serial').read_text().strip()
            except FileNotFoundError:serial=''
            if serial:identity['serial']=serial
            else:
                try:identity['port']=parent.relative_to(sys_root.resolve()).as_posix()
                except ValueError:return None
                identity['connection']=':'.join((parent/name).read_text().strip() for name in ('busnum','devnum'))
            return identity if valid_usb_identity(identity) else None
    return None


def selected_card(arguments, route, proc_root=Path('/proc/asound'), sys_root=Path('/sys')):
    if (set(route)!={'version','alsa_id','usb'} or route['version']!=1
        or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',route['alsa_id'])
        or not valid_usb_identity(route['usb'])):
        return None
    if arguments.count('-o')!=1:
        return None
    at=arguments.index('-o')
    if at+1>=len(arguments):return None
    match=re.fullmatch(r'hw:([A-Za-z0-9_-]+),0(?:,0)?',arguments[at+1])
    if not match:return None
    token=match[1]
    cards=[]
    for path in proc_root.glob('card[0-9]*/id'):
        card=int(path.parent.name[4:])
        if card>1024:continue
        if (token==str(card) or token==route['alsa_id']) and path.read_text().strip()==route['alsa_id']:
            cards.append(card)
    if len(cards)!=1 or usb_identity(cards[0],sys_root)!=route['usb']:
        return None
    return cards[0]


def main():
    base=Path(ROUTER_BASE)
    native=base/'squeezelite'
    try:
        route_path=base/'route.json';info=route_path.lstat()
        if (os.geteuid()==0 and stat.S_ISREG(info.st_mode) and info.st_uid==0
            and not info.st_mode&0o077 and info.st_size<=4096
            and not os.environ.get('ALSA_CONFIG_PATH')):
            route=json.loads(route_path.read_text())
            if selected_card(sys.argv[1:],route) is not None:
                config=base/'alsa-route.conf';metadata=config.lstat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid!=0 or metadata.st_mode&0o022:
                    raise RuntimeError('Untrusted private ALSA configuration')
                os.environ['ALSA_CONFIG_PATH']=str(config)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):
        # The native executable is retained independently of module versions.
        # No argv, credentials, music URLs or private settings are logged.
        pass
    os.execv(native,[sys.argv[0],*sys.argv[1:]])


if __name__=='__main__':main()
