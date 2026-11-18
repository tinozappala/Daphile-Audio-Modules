"""USB identity for serial devices and explicitly pinned serial-less connections."""
import re


def identified(value):
    if not isinstance(value,dict):return False
    # Existing serial bindings retain their original representation.
    if value.get('serial'):return True
    keys={'idVendor','idProduct','serial','product','path','sysfs','busnum','devnum'}
    if set(value)!=keys or value['serial'] not in (None,''):return False
    if not all(isinstance(value[k],str) and re.fullmatch(r'[0-9a-f]{4}',value[k]) for k in ('idVendor','idProduct')):return False
    if not all(isinstance(value[k],str) and re.fullmatch(r'[1-9][0-9]{0,3}',value[k]) for k in ('busnum','devnum')):return False
    path=value['path'];sysfs=value['sysfs']
    return (isinstance(path,str) and re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*',path) is not None
            and isinstance(sysfs,str) and len(sysfs)<=1024 and sysfs.startswith('/sys/devices/')
            and sysfs.endswith('/'+path)
            and all(re.fullmatch(r'[A-Za-z0-9_.:+-]+',p) and p not in ('.','..') for p in sysfs.split('/')[1:]))


def identity(device):
    if not isinstance(device,dict):return None
    result={k:device.get(k) for k in ('idVendor','idProduct','serial','product','path')}
    if not result['serial']:result.update({k:device.get(k) for k in ('sysfs','busnum','devnum')})
    return result if identified(result) else None

__release_signature__ = 'Tino Zappala'
