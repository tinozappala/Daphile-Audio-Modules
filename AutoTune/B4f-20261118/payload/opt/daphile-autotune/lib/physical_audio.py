"""Observed physical ALSA identities, independent of active PCM or card numbers.

USB keeps its existing serial representation; serial-less devices bind to
their live connection and boot. Other outputs bind
to their actual sysfs route, bus, driver and ALSA ID. Software-only cards never
become physical outputs merely because their PCM is RUNNING.
"""
import errno,os,re,stat,uuid
from pathlib import Path,PurePosixPath
import usb_target

KIND='alsa-physical-v1'
USB_CONNECTION='usb-connection-v1'
USB_FIELDS={'kind','idVendor','idProduct','serial','product','path','sysfs','busnum','devnum','alsa_id','boot_id','device_inode'}
BOOT_ID=Path('/proc/sys/kernel/random/boot_id')
FIELDS={'kind','alsa_id','sysfs','path','bus','driver'}
VIRTUAL_DRIVERS={'snd_aloop','snd-aloop','aloop','snd_dummy','snd-dummy','dummy','snd_virmidi','virmidi'}


def _route(value):
    if not isinstance(value,str) or len(value)>2048 or not value.startswith('/sys/devices/'):
        return False
    parts=PurePosixPath(value).parts
    return ('virtual' not in parts and all(part not in ('.','..') and re.fullmatch(r'[A-Za-z0-9_.:+@,=-]+',part)
                                          for part in parts[1:]))


def is_usb(value):
    return isinstance(value,dict) and value.get('kind')!=KIND and identified(value)


def identified(value):
    if not isinstance(value,dict):return False
    if value.get('kind')==USB_CONNECTION:
        if set(value)!=USB_FIELDS or value.get('serial') not in (None,''):return False
        if not all(isinstance(value[key],str) and re.fullmatch('[0-9a-f]{4}',value[key]) for key in ('idVendor','idProduct')):return False
        if not all(isinstance(value[key],str) and re.fullmatch('[1-9][0-9]{0,3}',value[key]) for key in ('busnum','devnum')):return False
        if (not isinstance(value['path'],str) or not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*',value['path'])
                or not _route(value['sysfs']) or not value['sysfs'].endswith('/'+value['path'])
                or not isinstance(value['alsa_id'],str) or not re.fullmatch(r'[A-Za-z0-9_.:+-]{1,256}',value['alsa_id'])
                or value['product'] is not None and (not isinstance(value['product'],str) or len(value['product'])>256)):
            return False
        inode=value['device_inode']
        if (not isinstance(inode,dict) or set(inode)!={'device','inode','ctime_ns'}
                or any(type(inode[key]) is not int or inode[key]<0 for key in inode) or inode['inode']==0):return False
        try:return str(uuid.UUID(value['boot_id']))==value['boot_id']
        except (ValueError,TypeError,AttributeError):return False
    # Historical serial-less connection records lack a boot/inode witness.
    # They remain readable history and never become current write authority.
    if value.get('kind')!=KIND:return bool(value.get('serial')) and usb_target.identified(value)
    if set(value)!=FIELDS or value['path']!=value['sysfs'] or not _route(value['sysfs']):return False
    if not all(isinstance(value[key],str) and re.fullmatch(r'[A-Za-z0-9_.:+@,=-]{1,256}',value[key])
               for key in ('alsa_id','bus','driver')):return False
    if value['bus'].lower() in ('virtual','usb') or value['driver'].lower() in VIRTUAL_DRIVERS:return False
    return re.fullmatch(r'(?:Loopback|Dummy|VirMIDI)(?:_?\d+)?',value['alsa_id'],re.I) is None


def _connection_observation(path):
    """Bounded reads from one opened live USB directory, with inode recheck."""
    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=os.fstat(descriptor)
        def same_named():
            named=os.stat(path,follow_symlinks=False)
            return (stat.S_ISDIR(named.st_mode) and named.st_uid==0 and not named.st_mode&0o022
                    and (named.st_dev,named.st_ino,named.st_ctime_ns)==(before.st_dev,before.st_ino,before.st_ctime_ns))
        if not stat.S_ISDIR(before.st_mode) or before.st_uid!=0 or before.st_mode&0o022 or not same_named():
            raise RuntimeError('Protected live USB device directory required')
        def read(name,optional=False):
            try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=descriptor)
            except OSError as error:
                if optional and error.errno==errno.ENOENT:return None
                raise
            try:
                info=os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
                    raise RuntimeError('Protected USB attribute required')
                raw=os.read(fd,1025)
                if len(raw)>1024:raise RuntimeError('USB attribute exceeds observation bound')
                return raw.decode('utf8').strip()
            finally:os.close(fd)
        def values():
            return {name:read(name,name in ('serial','product')) for name in ('idVendor','idProduct','serial','product','busnum','devnum')}
        first=values();boot=BOOT_ID.read_text().strip();second=values()
        after=os.fstat(descriptor)
        if (first!=second or not same_named() or
                (after.st_dev,after.st_ino,after.st_ctime_ns)!=(before.st_dev,before.st_ino,before.st_ctime_ns)):
            raise RuntimeError('USB connection changed during observation')
        return dict(values=first,boot_id=boot,device_inode=dict(device=before.st_dev,inode=before.st_ino,ctime_ns=before.st_ctime_ns))
    finally:os.close(descriptor)


def _usb_connection(card,device):
    path=device.get('sysfs');card_path=card.get('sysfs')
    if (not _route(path) or not _route(card_path)
            or card_path!=path and not card_path.startswith(path+'/')):return None
    try:observed=_connection_observation(path)
    except (OSError,RuntimeError,ValueError,UnicodeError):return None
    actual=observed['values']
    if actual.get('serial') or any(actual.get(key)!=device.get(key) for key in ('idVendor','idProduct','busnum','devnum','product')):
        return None
    result=dict(actual,kind=USB_CONNECTION,path=device.get('path'),sysfs=path,alsa_id=card.get('id'),
                boot_id=observed['boot_id'],device_inode=observed['device_inode'])
    return result if identified(result) else None


def device_metadata(sysfs):
    """Observe driver/subsystem on the physical node or its nearest parent."""
    if not _route(sysfs):return {}
    bus=driver=None
    path=Path(sysfs)
    for node in (path,*path.parents):
        if str(node) in ('/sys/devices','/sys','/'):break
        if bus is None and (node/'subsystem').is_symlink():
            try:bus=(node/'subsystem').resolve(strict=True).name
            except OSError:pass
        if driver is None and (node/'driver').is_symlink():
            try:driver=(node/'driver').resolve(strict=True).name
            except OSError:pass
        if bus and driver:break
    return dict(bus=bus,driver=driver)


def identity(card,usb_devices):
    if not isinstance(card,dict):return None
    if card.get('usb_path'):
        matches=[device for device in usb_devices if device.get('path')==card['usb_path']]
        if len(matches)!=1:return None
        return usb_target.identity(matches[0]) if matches[0].get('serial') else _usb_connection(card,matches[0])
    sysfs=card.get('sysfs')
    if not _route(sysfs):return None
    metadata=dict(bus=card.get('bus'),driver=card.get('driver'))
    if not all(metadata.values()):
        observed=device_metadata(sysfs)
        metadata={key:metadata[key] or observed.get(key) for key in metadata}
    result=dict(kind=KIND,alsa_id=card.get('id'),sysfs=sysfs,path=sysfs,**metadata)
    return result if identified(result) else None


def matches(value,hardware=None):
    """Return current matching physical cards; no PCM or playback is required."""
    if not identified(value):return []
    if hardware is None or not isinstance(hardware.get('alsa',{}).get('cards'),list):
        from detection import alsa_cards,usb_devices
        cards,devices=alsa_cards(),usb_devices()
    else:
        cards,devices=hardware['alsa']['cards'],hardware.get('usb',[])
    return [card for card in cards if identity(card,devices)==value]


def usb_matches(value,hardware=None):
    """Actual USB device for this identity, including serial-less witnesses."""
    if not is_usb(value):return []
    if hardware is None or not isinstance(hardware.get('alsa',{}).get('cards'),list):
        from detection import alsa_cards,usb_devices
        hardware=dict(alsa=dict(cards=alsa_cards()),usb=usb_devices())
    cards=matches(value,hardware)
    if len(cards)!=1:return []
    devices=[device for device in hardware.get('usb',[]) if device.get('path')==cards[0].get('usb_path')]
    return devices if len(devices)==1 else []


__release_signature__ = 'Tino Zappala'
