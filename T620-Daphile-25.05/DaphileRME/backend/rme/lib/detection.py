import re
from pathlib import Path


def read(path):
    try: return Path(path).read_text(errors='replace').strip()
    except OSError: return None

def fields(path, names): return {name: read(Path(path)/name) for name in names}

def power(path):
    return fields(Path(path)/'power', ['control','autosuspend_delay_ms','runtime_status',
                                         'runtime_active_time','runtime_suspended_time'])

def usb_devices():
    result = []
    for p in sorted(Path('/sys/bus/usb/devices').glob('*')):
        if ':' in p.name or not (p/'idVendor').exists(): continue
        real = p.resolve()
        pci = [q.name for q in real.parents if re.fullmatch(r'\w{4}:\w{2}:\w{2}\.\w', q.name)]
        roots = [q.name for q in [real, *real.parents] if re.fullmatch(r'usb\d+',q.name)]
        controller = pci[0] if pci else None
        item = fields(p, ['idVendor','idProduct','serial','manufacturer','product','speed',
                          'bcdUSB','bDeviceClass','busnum','devnum','maxchild'])
        item.update(path=p.name, sysfs=str(real), controller=controller,
                    root_hub=roots[0] if roots else None, power=power(p))
        item['parents'] = [q.name for q in real.parents if (q/'idVendor').exists()]
        item['interfaces'] = [fields(q,['bInterfaceClass','bInterfaceSubClass','bInterfaceProtocol']) for q in p.parent.glob(p.name+':*')]
        item['irq'] = read('/sys/bus/pci/devices/'+controller+'/irq') if controller else None
        item['competing_devices'] = []
        result.append(item)
    for item in result:
        item['competing_devices'] = [x['path'] for x in result if x['controller']==item['controller']
             and x['path'] != item['path'] and x['bDeviceClass'] != '09'
             and x['path'] not in item['parents']]
        # Transparent heuristic, not an audio-quality or physical-port measurement.
        item['topology_score'] = max(0,100-10*len(item['competing_devices'])-5*len(
            [x for x in item['parents'] if not x.startswith('usb')])) if controller_known(item) else None
    return result

def controller_known(item): return item['controller'] is not None

def alsa_cards():
    result=[]
    for p in sorted(Path('/proc/asound').glob('card[0-9]*')):
        card=int(p.name[4:]); dev=Path('/sys/class/sound')/p.name/'device'
        real=dev.resolve(); usb=next((q for q in [real,*real.parents] if (q/'idVendor').exists()),None)
        pcms=[]
        for sub in p.glob('pcm*p/sub*'):
            hw=read(sub/'hw_params'); status=read(sub/'status')
            params=dict(re.findall(r'^([\w]+):\s*(.+)$',hw or '',re.M))
            pcms.append(dict(device=int(sub.parent.name[3:-1]),subdevice=int(sub.name[3:]),
                             hw=params,status=status,active=bool(hw and hw!='closed')))
        streams=[read(s) for s in p.glob('stream*')]
        # Playback capabilities only: do not infer native DSD from PCM rates.
        playback=[(s or '').split('Capture:')[0] for s in streams]
        rates=set()
        for s in playback:
            for rate_line in re.findall(r'Rates:\s*([^\n]+)',s):
                if '-' not in rate_line:rates.update(map(int,re.findall(r'\d+',rate_line)))
        result.append(dict(card=card,id=read(p/'id'),usb_path=usb.name if usb else None,
          sysfs=str(real),pcms=pcms,stream_descriptors=streams,declared_pcm_rates=sorted(rates),
          declared_formats=sorted(set(re.findall(r'Format:\s*(\S+)', '\n'.join(playback)))),
          dsd_capability='declared ALSA DSD format' if any('DSD_' in s for s in playback) else 'unknown',
          dop_capability='unknown; requires DAC documentation or observed DoP stream'))
    return result
