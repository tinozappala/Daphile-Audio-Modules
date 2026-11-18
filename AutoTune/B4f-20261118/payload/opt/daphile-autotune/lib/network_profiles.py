"""Measured EEE trial on the uniquely identified default-route physical NIC.

No IP, MTU, routing, offload, interrupt moderation or NAS mount changes.
"""
import json,re,subprocess
from pathlib import Path
from detection import command,read

def eee_status(text):
    match=re.search(r'^\s*EEE status:\s*(enabled|disabled)\b',text or '',re.M)
    return {'enabled':'on','disabled':'off'}.get(match[1]) if match else None

def route_interface():
    try:
        routes=json.loads(command(['ip','-j','route','show','default']) or 'null')
        names={r['dev'] for r in routes if r.get('dev') and r.get('type','unicast')=='unicast'}
    except (ValueError,TypeError,KeyError):return None
    return next(iter(names)) if len(names)==1 else None

def nic_identity(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,14}',name):raise RuntimeError('Invalid NIC name')
    path=Path('/sys/class/net')/name
    if not (path/'device').exists():raise RuntimeError('Physical NIC required')
    device=(path/'device').resolve();driver=(path/'device/driver').resolve().name
    mac=read(path/'address')
    if not str(device).startswith('/sys/devices/pci') or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}',mac or ''):raise RuntimeError('NIC identity incomplete')
    return dict(name=name,device=str(device),driver=driver,mac=mac)

def plan(h):
    d=h.get('dac') or {};name=route_interface()
    if d.get('status')!='verified-active' or not __import__('usb_target').identified((d.get('identity') or {})):return [],['Verified active serial-numbered DAC required']
    if not name:return [],['Default-route NIC absent or ambiguous; network trial unavailable']
    try:identity=nic_identity(name)
    except RuntimeError as error:return [],[str(error)]
    interface=next((x for x in h.get('network',[]) if x['name']==name),None)
    if not interface or interface.get('carrier')!='1':return [],['Physical default-route NIC carrier not verified']
    current=eee_status(interface.get('eee'))
    if current is None:return [],['Driver does not report a supported EEE state']
    if current=='off':return [],['EEE already disabled; network profile makes no change']
    host=__import__('host_identity').current(read)
    if not host:return [],['Host identity unavailable']
    return [dict(parameter='network_eee',before='on',after='off',nic=identity,host_uuid=host,identity=d['identity'],classification='CONTROLLED TRIAL',reversible=True,
                 reason='Test disabling EEE wake transitions; retain only with playback and NAS validation')],['No audio quality improvement is inferred from disabling EEE']

def guard(c,h,restoring=False):
    if c.get('host_uuid')!=__import__('host_identity').current(read) or not c.get('host_uuid'):raise RuntimeError('Network profile belongs to different host')
    if c.get('parameter')!='network_eee' or (c.get('before'),c.get('after'))!=('on','off'):raise RuntimeError('Network setting outside allowlist')
    if nic_identity(c['nic']['name'])!=c['nic']:raise RuntimeError('Physical NIC changed')
    matches=[u for u in h.get('usb',[]) if all(u.get(k)==v for k,v in c['identity'].items())]
    if not __import__('usb_target').identified(c['identity']) or len(matches)!=1:raise RuntimeError('Original DAC absent or replaced')
    # Restoration is allowed after a link/route failure, using the same physical NIC.
    if not restoring and (route_interface()!=c['nic']['name'] or read(Path('/sys/class/net')/c['nic']['name']/'carrier')!='1'):raise RuntimeError('Default route or carrier changed')

def value(c):return eee_status(command(['ethtool','--show-eee',c['nic']['name']]))

def write(c,v):
    if v not in ['on','off']:raise ValueError('Invalid EEE value')
    from detection import usb_devices
    guard(c,{'usb':usb_devices()},restoring=v==c['before'])
    subprocess.run(['ethtool','--set-eee',c['nic']['name'],'eee',v],capture_output=True,check=True,timeout=5)
    if value(c)!=v:raise RuntimeError('EEE change not retained')

__release_signature__ = 'Tino Zappala'
