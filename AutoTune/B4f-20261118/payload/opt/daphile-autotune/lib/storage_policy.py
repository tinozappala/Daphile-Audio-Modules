"""Measured SATA ALPM trials, limited to native Daphile's two policy values.

No block-device writes, mounting, formatting or global cache eviction. A trial
must use the normal engine journal/watchdog and verify I/O and audio latency.
"""
import copy
import os
from pathlib import Path
import re
try:
    from .usb_target import identified
except ImportError:
    from usb_target import identified

PARAMETER='sata_link_pm'
POLICIES={'max_performance','min_power'}


def read(path):
    try:return Path(path).read_text().strip()
    except OSError:return None


def inventory(sys_root=Path('/sys')):
    sys_root=Path(sys_root);rows=[]
    for policy in sorted((sys_root/'class/scsi_host').glob('host*/link_power_management_policy')):
        host=policy.parent.resolve()
        if host.parent.name!='scsi_host' or host.parent.parent.name!=policy.parent.name:continue
        anchor=host.parent.parent;disks=[]
        for disk in sorted((sys_root/'block').glob('*')):
            device=(disk/'device').resolve()
            if not device.is_relative_to(anchor):continue
            identity=dict(name=disk.name,wwid=read(disk/'wwid') or read(disk/'device/wwid'),
                model=read(disk/'device/model'),size=read(disk/'size'),dev=read(disk/'dev'),
                rotational=read(disk/'queue/rotational'))
            disks.append(identity)
        eligible=bool(disks and read(policy) in POLICIES and all(
            d['wwid'] and d['model'] and d['size'] and d['size'].isdigit() and int(d['size'])>0
            and d['dev'] and re.fullmatch(r'[0-9]+:[0-9]+',d['dev']) and d['rotational']=='0' for d in disks))
        rows.append(dict(path=str(policy),resolved_path=str(policy.resolve()),anchor=str(anchor),
            policy=read(policy),disks=disks,eligible=eligible))
    return rows


def plan(hardware,desired,rows=None):
    if desired not in POLICIES:raise ValueError('SATA policy outside native Daphile choices')
    dac=hardware.get('dac',{});host=hardware.get('system',{}).get('product_uuid')
    if not host or dac.get('status')!='verified-active' or not identified(dac.get('identity',{})):
        raise RuntimeError('Verified host and active serial-numbered DAC required')
    changes=[]
    for row in inventory() if rows is None else rows:
        if not row.get('eligible'):continue
        if row['policy']==desired:continue
        changes.append(dict(parameter=PARAMETER,path=row['path'],before=row['policy'],after=desired,
            host_uuid=host,identity=dict(dac['identity']),storage_identity=copy.deepcopy({k:row[k] for k in
                ('resolved_path','anchor','disks')}),classification='MEASURED STORAGE TRIAL',reversible=True,
            evidence='PAIRED AUDIO AND DISK I/O OBSERVATIONS REQUIRED'))
    return changes


def validate(change,row,host_uuid,usb):
    if change.get('parameter')!=PARAMETER or not re.fullmatch(
            r'/sys/class/scsi_host/host[0-9]+/link_power_management_policy',change.get('path','')):
        raise RuntimeError('Invalid SATA target')
    if not host_uuid or change.get('host_uuid')!=host_uuid:raise RuntimeError('SATA trial belongs to another host')
    ident=change.get('identity',{})
    if not identified(ident) or len([u for u in usb if all(u.get(k)==v for k,v in ident.items())])!=1:
        raise RuntimeError('Original DAC absent or ambiguous')
    if change.get('before') not in POLICIES or change.get('after') not in POLICIES:
        raise RuntimeError('SATA policy outside transaction')
    if not row or not row.get('eligible') or row.get('path')!=change['path'] or any(
            row.get(k)!=change.get('storage_identity',{}).get(k) for k in ('resolved_path','anchor','disks')):
        raise RuntimeError('SATA controller or storage identity changed')
    if row.get('policy') not in (change['before'],change['after']):
        raise RuntimeError('External SATA policy change; no overwrite')


def guard(change,hardware,restoring=False):
    row=next((r for r in inventory() if r['path']==change.get('path')),None)
    validate(change,row,__import__('host_identity').current(read),hardware.get('usb',[]))


def value(change):return read(change['path'])


def write(change,desired):
    from detection import usb_devices
    if desired not in (change['before'],change['after']):raise ValueError('Value outside SATA transaction')
    guard(change,{'usb':usb_devices()},restoring=desired==change['before'])
    fd=os.open(change['path'],os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd);current=os.stat(change['path'],follow_symlinks=False)
        if (info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino):raise RuntimeError('SATA target replaced during open')
        guard(change,{'usb':usb_devices()},restoring=desired==change['before'])
        os.write(fd,(desired+'\n').encode())
    finally:os.close(fd)
    if value(change)!=desired:raise RuntimeError('SATA policy readback failed')

__release_signature__ = 'Tino Zappala'
