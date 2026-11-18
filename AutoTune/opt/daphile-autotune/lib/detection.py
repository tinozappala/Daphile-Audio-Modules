"""Read-only Linux discovery. Missing observations stay unknown, never zero."""
import hashlib, json, os, re, shutil, subprocess, time
from pathlib import Path

VERSION = '0.9.10'

def read(path):
    try: return Path(path).read_text(errors='replace').strip()
    except OSError: return None

def command(args, timeout=5):
    if not shutil.which(args[0]): return None
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired): return None

def fields(path, names): return {name: read(Path(path)/name) for name in names}

def numbers(text):
    result = []
    for part in (text or '').split(','):
        if re.fullmatch(r'\d+(-\d+)?', part):
            ends = list(map(int, part.split('-')))
            result.extend(range(ends[0], ends[-1]+1))
    return sorted(set(result))

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

def process_role(name, executable, args):
    if name == 'squeezelite' and executable == '/usr/bin/squeezelite': return 'audio-player'
    if name == 'spacd' and executable and executable.startswith('/opt/spac/'): return 'player-control'
    if executable and re.fullmatch(r'/usr/bin/perl(?:[0-9.]+)?', executable):
        if '/opt/mediaserver/slimserver.pl' in args: return 'lms'
        if '/opt/mediaserver/scanner.pl' in args: return 'scanner'
    return None

def processes():
    result=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            args=(p/'cmdline').read_bytes().decode(errors='replace').split('\0')
            name=read(p/'comm') or ''
            try:exe=os.readlink(p/'exe')
            except OSError:exe=None
            if not re.search(r'squeezelite|spacd|perl|scanner|smbd|nmbd|avahi|bluetooth|irq/|kworker/|ksoftirqd|lighttpd|ntpd',name):continue
            # Only recognized audio flags are exported; generic arguments can contain credentials.
            options={}; allowed={'-a','-b','-p','-r','-D','-R','-C','-o','-u','-x','-e','-c'}
            if 'squeezelite' in name:
                for i,arg in enumerate(args):
                    if arg in allowed:
                        values=[]
                        for value in args[i+1:]:
                            if value.startswith('-') and not re.fullmatch(r'-[0-9.]+',value):break
                            if value:values.append(value)
                        options[arg]=values or True
            threads=[]
            for q in (p/'task').glob('[0-9]*'):
                try:
                    tid=int(q.name)
                    threads.append(dict(tid=tid,name=read(q/'comm'),policy=os.sched_getscheduler(tid),
                      priority=os.sched_getparam(tid).sched_priority,affinity=sorted(os.sched_getaffinity(tid))))
                except OSError:pass
            result.append(dict(pid=int(p.name),name=name,executable=exe,audio_options=options,
                               scanner=any('scanner.pl' in a for a in args),runtime_role=process_role(name,exe,args),threads=threads))
        except OSError:pass
    return result

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

def _native_selected_dac(cards, procs, usb):
    # Active PCM ownership is stronger evidence than a card number in an old config.
    players={tid for p in procs if 'squeezelite' in p['name'] for tid in [p['pid'], *[t['tid'] for t in p.get('threads',[])]]}
    candidates=[]
    for card in cards:
        for pcm in card['pcms']:
            owner=re.search(r'owner_pid\s*:\s*(\d+)',pcm['status'] or '')
            if pcm['active'] and owner and int(owner[1]) in players:
                candidates.append((card,pcm,int(owner[1])))
    if len(candidates)!=1:return dict(status='unknown' if not candidates else 'ambiguous',candidates=len(candidates))
    card,pcm,pid=candidates[0]; device=next((u for u in usb if u['path']==card['usb_path']),None)
    return dict(status='verified-active',card=card['card'],alsa_id=card['id'],device=pcm['device'],
                owner_pid=pid,pcm=pcm,usb=device,
                identity=__import__('usb_target').identity(device) if device else
                {'alsa_id':card['id'],'sysfs':card['sysfs']})

def irq_map():
    raw=read('/proc/interrupts'); result=[]
    for line in (raw or '').splitlines():
        m=re.match(r'\s*(\d+):\s*(.*)',line)
        if not m:continue
        parts=m[2].split();counts=[]
        while parts and parts[0].isdigit():counts.append(int(parts.pop(0)))
        p=Path('/proc/irq')/m[1]
        result.append(dict(irq=int(m[1]),counts=counts,description=' '.join(parts),
                          affinity=read(p/'smp_affinity_list'),effective=read(p/'effective_affinity_list')))
    return result if raw is not None else None

def scan():
    cpuinfo=read('/proc/cpuinfo') or ''; cpu=[]
    for p in sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*')):
        cpu.append(dict(cpu=int(p.name[3:]),topology=fields(p/'topology',
          ['physical_package_id','core_id','thread_siblings_list','core_siblings_list']),
          cpufreq=fields(p/'cpufreq',['scaling_driver','scaling_governor','scaling_available_governors',
          'scaling_cur_freq','scaling_min_freq','scaling_max_freq']),
          cstates=[fields(s,['name','desc','latency','disable','usage','time']) for s in (p/'cpuidle').glob('state*')],
          caches=[fields(s,['level','type','size','shared_cpu_list']) for s in (p/'cache').glob('index*')]))
    pci=[]
    for p in sorted(Path('/sys/bus/pci/devices').glob('*')):
        pci.append(dict(address=p.name,driver=(p/'driver').resolve().name if (p/'driver').exists() else None,
          **fields(p,['vendor','device','class','irq','numa_node']),power=power(p)))
    network=[]
    for p in sorted(Path('/sys/class/net').glob('*')):
        network.append(dict(name=p.name,**fields(p,['operstate','speed','duplex','carrier']),
          driver=(p/'device/driver').resolve().name if (p/'device/driver').exists() else None,
          statistics=fields(p/'statistics',['rx_bytes','tx_bytes','rx_errors','tx_errors','rx_dropped','tx_dropped']),
          offload=command(['ethtool','-k',p.name]),eee=command(['ethtool','--show-eee',p.name]),
          moderation=command(['ethtool','-c',p.name])))
    procs=processes(); usb=usb_devices(); cards=alsa_cards(); dac=selected_dac(cards,procs,usb)
    for player in procs:
        if player['name']=='squeezelite' and player['executable']=='/usr/bin/squeezelite':
            try:
                help_result=subprocess.run([player['executable'],'-?'],capture_output=True,text=True,timeout=3)
                help_text=help_result.stdout+help_result.stderr
                player['version']=next((line for line in help_text.splitlines() if line.startswith('Squeezelite v')),None)
                player['declared_syntax']=[line for line in help_text.splitlines() if re.match(r'\s+-(?:a|b|p|r|D|R|u|C|o)\b',line)]
            except (OSError,subprocess.TimeoutExpired):player['version']=None
    lms_version=None
    try:
        import urllib.request
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request('http://127.0.0.1:9000/jsonrpc.js',data=json.dumps({'id':1,'method':'slim.request','params':['',['serverstatus','0','1']]}).encode(),headers={'Content-Type':'application/json'})
        with opener.open(request,timeout=2) as response:lms_version=json.loads(response.read(65536))['result'].get('version')
    except (OSError,ValueError,KeyError):pass
    board=fields('/sys/class/dmi/id',['sys_vendor','product_name','board_name','board_vendor','product_uuid'])
    fingerprint_input=dict(board=board,cpu=re.findall(r'^(?:vendor_id|model name)\s*:\s*(.+)$',cpuinfo,re.M),
      pci=[(x['address'],x['vendor'],x['device']) for x in pci],dac=dac.get('identity'))
    fingerprint=hashlib.sha256(json.dumps(fingerprint_input,sort_keys=True).encode()).hexdigest()
    # Stable identity excludes ALSA enumeration and USB port; topology is checked separately before action.
    stable=dict(fingerprint_input)
    if stable['dac']:stable['dac']={k:v for k,v in stable['dac'].items() if k!='path'}
    fingerprint=hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()
    physical_cores=len({(c['topology']['physical_package_id'],c['topology']['core_id']) for c in cpu if c['topology']['core_id'] is not None}) or None
    conf={}
    for path in ['/opt/spac/share/defaults.conf','/var/daphile/conf/config.sys']:
        data=read(path)
        conf[path]={'sha256':hashlib.sha256(data.encode()).hexdigest() if data is not None else None,
          'audio_values':{k:v for k,v in re.findall(r'^([\w]+)=(.*)$',data or '',re.M)
                          if k in {'BUFFERTIME','PERIODCOUNT','STREAM_BUFFER','OUTPUT_BUFFER','hw_audio_rt','hw_audio_radio'}}}
    services={}
    for p in procs:
        name=p['name']; services[name]='REQUIRED' if re.search(r'squeezelite|spacd|lighttpd|perl|ntpd',name) else 'UNKNOWN'
    return dict(engine_version=VERSION,created=time.time(),hardware_id=fingerprint,system=board,
      cpu=dict(model=re.findall(r'^model name\s*:\s*(.+)$',cpuinfo,re.M)[:1],vendor=re.findall(r'^vendor_id\s*:\s*(.+)$',cpuinfo,re.M)[:1],
       architecture=os.uname().machine,logical_cpus=len(cpu),physical_cores=physical_cores,smt=any(len(numbers(c['topology']['thread_siblings_list']))>1 for c in cpu),scanner_affinity=sorted(os.sched_getaffinity(0)),cores=cpu,online=read('/sys/devices/system/cpu/online'),
       isolated=read('/sys/devices/system/cpu/isolated'),numa=[p.name for p in Path('/sys/devices/system/node').glob('node[0-9]*')],
       boost=read('/sys/devices/system/cpu/cpufreq/boost'),clocksource=fields('/sys/devices/system/clocksource/clocksource0',
        ['current_clocksource','available_clocksource'])),
      kernel=dict(release=os.uname().release,version=read('/proc/version'),cmdline=redact(read('/proc/cmdline')),
       realtime=read('/sys/kernel/realtime'),modules=read('/proc/modules'),
       rt_budget=fields('/proc/sys/kernel',['sched_rt_runtime_us','sched_rt_period_us'])),
      thermal=[dict(sensor=str(p),millidegrees=read(p)) for p in Path('/sys/class/hwmon').glob('hwmon*/temp*_input')],memory=read('/proc/meminfo'),pci=pci,usb=usb,dac=dac,alsa=dict(cards=cards,summary=read('/proc/asound/cards')),
      network=network,storage=dict(mounts=redact_mounts(read('/proc/self/mountinfo')),devices=[dict(name=p.name,**fields(p,
       ['size','ro','removable']),model=read(p/'device/model')) for p in Path('/sys/class/block').glob('*')]),
      irq=irq_map(),services=services,openrc=command(['rc-status','--nocolor']),squeezelite=procs,
      configuration=conf,daphile_version=read('/version'),lms_version=lms_version,
      limitations=['No electrical bit-perfect test','No physical socket labels without user port mapping',
       'No per-process wakeup or scheduling-latency values without trace instrumentation',
       'No XRUN counter inferred from RUNNING status','Services UNKNOWN are never disabled'])

def redact_mounts(text):
    # Hide NAS source and mount options; retain local mount locations and filesystem type.
    if text is None:return None
    result=[]
    for line in text.splitlines():
        a,sep,b=line.partition(' - ');left=a.split();right=b.split()
        if sep and len(left)>4 and right:result.append({'mountpoint':left[4],'filesystem':right[0],
            'source':'[network source redacted]' if right[0] in ['cifs','nfs','nfs4'] else (right[1] if len(right)>1 else None)})
    return result


def redact(text):
    if text is None:return None
    return re.sub(r'(?i)(password|passwd|token|secret|username|credential|key)=([^\s,]+)',r'\1=[redacted]',text)


def selected_dac(cards,procs,usb):
    native=_native_selected_dac(cards,procs,usb)
    from audio_context import select
    external=select(cards,usb)
    if external.get('status')=='ambiguous':return external
    if external.get('status')=='verified-active':
        if native.get('status')=='ambiguous':return native
        if native.get('status')=='verified-active' and native.get('usb'):
            return dict(status='ambiguous',candidates=2)
        # The native player's Loopback PCM is the feed, not a second USB DAC.
        return external
    return native

__release_signature__ = 'Tino Zappala'
