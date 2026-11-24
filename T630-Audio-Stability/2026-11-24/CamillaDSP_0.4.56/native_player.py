"""Bounded native Daphile player control; no audio preference writes."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import urllib.request
import urllib.error

from native_route import NATIVE_SHA256
from player_router import selected_card,usb_identity


_GUARD_CARD_ID='/proc/asound/card0/id'
_GUARD_STATUS='/proc/asound/card0/pcm0p/sub6/status'
_GUARD_HW='/proc/asound/card0/pcm0p/sub6/hw_params'
_GUARD_PROC=b'/proc'
_GUARD_RUNNING=re.compile(rb'^state:\s*RUNNING\s*$',re.M)
_GUARD_OWNER=re.compile(rb'owner_pid\s*:\s*(\d+)')


def _read_guard_bytes(path):
    """Fresh small proc snapshot; retain neither descriptors nor contents."""
    fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC)
    try:
        raw=b''
        while True:
            block=os.read(fd,4097-len(raw))
            if not block:return raw
            raw+=block
            if len(raw)>4096:raise ValueError('Oversized local PCM guard observation')
    finally:os.close(fd)


class NativeIdentityNotReady(RuntimeError):
    """Native per-card restart is still recreating its original runtime files."""


class NativeTopologyUnavailable(RuntimeError):
    """Single-player authority is absent, changed or cannot be observed."""


def parsed_volume(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):
        raise RuntimeError('Volume del player non osservabile')
    if isinstance(value,str) and not re.fullmatch(r'-?[0-9]{1,3}(?:\.[0-9]{1,6})?',value):
        raise RuntimeError('Volume del player non osservabile')
    try:result=float(value)
    except (ValueError,OverflowError):raise RuntimeError('Volume del player non osservabile') from None
    if not math.isfinite(result) or not -100<=result<=100:
        raise RuntimeError('Volume del player non osservabile')
    return result


def observed_integer(value,minimum,maximum,label):
    """LMS emits integers or decimal strings; bool and truncation are unknown."""
    if type(value) is int:result=value
    elif isinstance(value,str) and re.fullmatch(r'-?[0-9]{1,10}',value):result=int(value)
    else:raise RuntimeError(label+' non osservabile')
    if not minimum<=result<=maximum:raise RuntimeError(label+' fuori intervallo')
    return result


def observed_position(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):
        raise RuntimeError('Posizione del player non osservabile')
    if isinstance(value,str) and not re.fullmatch(r'[0-9]{1,12}(?:\.[0-9]{1,18})?',value):
        raise RuntimeError('Posizione del player non osservabile')
    try:result=float(value)
    except (ValueError,OverflowError):raise RuntimeError('Posizione del player non osservabile') from None
    if not math.isfinite(result) or result<0:raise RuntimeError('Posizione del player non osservabile')
    return result


def observed_tracks(value):
    if not isinstance(value,list):raise RuntimeError('Coda audio non osservabile')
    for row in value:
        if not isinstance(row,dict):raise RuntimeError('Riga della coda audio non valida')
        url=row.get('url')
        if not isinstance(url,str) or not url or any(ord(c)<32 for c in url):
            raise RuntimeError('Sorgente audio non osservabile')
    return value


def unique_json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('Duplicate JSON member')
        value[key]=item
    return value


def reject_json_constant(value):
    raise ValueError('Non-finite JSON value')


def nominal_pcm_rate(value):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):return None
    try:rate=float(value)
    except (ValueError,OverflowError):return None
    return int(rate) if math.isfinite(rate) and rate.is_integer() and 8000<=rate<=768000 else None


class NativePlayer:
    def rpc(self,mac,commands):
        request=urllib.request.Request('http://127.0.0.1:9000/jsonrpc.js',
            data=json.dumps(dict(id=1,method='slim.request',params=[mac,commands])).encode(),
            headers={'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request,timeout=5) as response:
                data=response.read(4*1024*1024+1)
        except TimeoutError:
            if commands and commands[0]=='status':raise TimeoutError('LMS source observation timed out') from None
            raise
        except (urllib.error.URLError,ConnectionError):
            if commands and commands[0]=='status':raise TimeoutError('LMS source observation unavailable') from None
            raise
        if len(data)>4*1024*1024:raise RuntimeError('Native player response too large')
        try:value=json.loads(data,object_pairs_hook=unique_json_object,parse_constant=reject_json_constant)
        except (ValueError,UnicodeError):raise RuntimeError('Native player response is not unambiguous JSON') from None
        if not isinstance(value,dict) or value.get('error') or not isinstance(value.get('result'),dict) or value['result'].get('error'):
            raise RuntimeError('Native player command rejected')
        return value['result']

    def idle_library(self):
        value=self.rpc('',['serverstatus','0','0'])
        def idle(observed):
            return observed is False or (type(observed) is int and observed==0) or (type(observed) is str and observed=='0')
        if not isinstance(value.get('version'),str) or not value['version'].strip() or ('rescan' in value and not idle(value['rescan'])):
            raise RuntimeError('Stato della scansione della libreria non verificato')
        # LMS may omit rescan in an idle serverstatus response. Only its
        # explicit progress endpoint can distinguish that case from unknown.
        if not idle(self.rpc('',['rescanprogress']).get('rescan')):
            raise RuntimeError('Attendere la fine della scansione della libreria')
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:argv=path.read_bytes().split(b'\0')
            except OSError:continue
            if any(a.rsplit(b'/',1)[-1]==b'scanner.pl' for a in argv):
                raise RuntimeError('Attendere la fine della scansione della libreria')

    def target(self):
        matches=[]
        for path in Path('/proc').glob('[0-9]*/cmdline'):
            try:
                argv=path.read_bytes().split(b'\0')
                if not argv or Path(os.fsdecode(argv[0])).name!='squeezelite':continue
                if hashlib.sha256(path.with_name('exe').read_bytes()).hexdigest()!=NATIVE_SHA256:continue
                arguments=[os.fsdecode(a) for a in argv[1:] if a]
                for idfile in Path('/proc/asound').glob('card[0-9]*/id'):
                    card=int(idfile.parent.name[4:]);usb=usb_identity(card)
                    if not usb:continue
                    route=dict(version=1,alsa_id=idfile.read_text().strip(),usb=usb)
                    if selected_card(arguments,route)==card:
                        matches.append(dict(card=card,alsa_id=route['alsa_id'],usb=usb))
            except (OSError,ValueError):continue
        if not matches:raise NativeIdentityNotReady('Player nativo temporaneamente non pronto')
        if len(matches)!=1:raise RuntimeError('Occorre un solo player nativo con DAC USB identificato')
        target=matches[0];runtime=Path('/run/spacd/c')/str(target['card'])
        home_link=runtime/'home'
        try:home=home_link.resolve(strict=True)
        except FileNotFoundError:
            candidate=home_link.resolve(strict=False)
            if candidate!=home_link and not candidate.is_relative_to('/mnt/hdd/system/spac'):
                raise RuntimeError('Preferenze native non verificate') from None
            raise NativeIdentityNotReady('Preferenze native temporaneamente non pronte') from None
        prefs=home/'volume.conf'
        if not home.is_relative_to('/mnt/hdd/system/spac') or prefs.is_symlink():
            raise RuntimeError('Preferenze native non verificate')
        try:
            if not stat.S_ISREG(prefs.stat().st_mode):raise RuntimeError('Preferenze native non verificate')
            preferences=prefs.read_bytes()
        except FileNotFoundError:raise NativeIdentityNotReady('Preferenze native temporaneamente non pronte') from None
        macs=list((runtime/'d').glob('*/home/macaddr'))
        if not macs:raise NativeIdentityNotReady('Identità nativa temporaneamente non pronta')
        if len(macs)!=1:raise RuntimeError('Identità LMS nativa ambigua')
        try:mac=macs[0].read_text().strip()
        except FileNotFoundError:raise NativeIdentityNotReady('Identità nativa temporaneamente non pronta') from None
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}',mac):raise RuntimeError('Identità LMS non valida')
        target.update(mac=mac,path=str(prefs),preferences_sha256=hashlib.sha256(preferences).hexdigest())
        return target

    def guard(self,target):
        route=dict(version=1,alsa_id=target['alsa_id'],usb=target['usb'])
        if selected_card(['-o','hw:'+target['alsa_id']+',0'],route)!=target['card']:
            raise RuntimeError('DAC rimosso, sostituito o rinumerato')
        runtime=Path('/run/spacd/c')/str(target['card'])
        macfile=runtime/'d/0/home/macaddr'
        try:current_mac=macfile.read_text().strip()
        except FileNotFoundError:raise NativeIdentityNotReady('Identità nativa temporaneamente non pronta') from None
        if current_mac!=target['mac']:raise RuntimeError('Identità del player cambiata esternamente')
        prefs=Path(target['path'])
        try:home=(runtime/'home').resolve(strict=True)
        except FileNotFoundError:raise NativeIdentityNotReady('Preferenze native temporaneamente non pronte') from None
        if not prefs.exists():raise NativeIdentityNotReady('Preferenze native temporaneamente non pronte')
        if prefs!=home/'volume.conf' or prefs.is_symlink() or hashlib.sha256(prefs.read_bytes()).hexdigest()!=target['preferences_sha256']:
            raise RuntimeError('Configurazione audio cambiata esternamente; nessuna sovrascrittura')

    def wait_ready(self,target,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            try:self.guard(target);return
            except NativeIdentityNotReady:time.sleep(.1)
        raise RuntimeError('Il player nativo non è tornato pronto; recupero conservato')

    def require_standalone(self,target,status=None):
        """Refuse live/inactive peers and persistent reconnect membership.

        The native syncQuery contract explicitly emits '-' only when
        Client::isSynced(allPlayers) is false. prefQuery/addResult retains _p2
        even for undef (JSON null); absence of the key is not that value.
        Only explicit Perl-false native scalars cannot trigger restoreSync.
        Queries and the subsequent command are cooperative, separate RPCs,
        not an atomic topology reservation against another client.
        """
        self.guard(target)
        if status is not None and any(key in status for key in ('sync_master','sync_slaves')):
            raise NativeTopologyUnavailable('Stato LMS sincronizzato o ambiguo; gruppo preservato')
        try:
            live=self.rpc(target['mac'],['sync','?'])
            if not isinstance(live,dict) or type(live.get('_sync')) is not str or live['_sync']!='-':
                raise NativeTopologyUnavailable('Topologia LMS sincronizzata o non osservabile; gruppo preservato')
            persistent=self.rpc(target['mac'],['playerpref','syncgroupid','?'])
            if not isinstance(persistent,dict) or '_p2' not in persistent:
                raise NativeTopologyUnavailable('Topologia persistente LMS non osservabile; gruppo preservato')
            value=persistent['_p2']
            empty=(value is None or type(value) is int and value==0 or type(value) is str and value in ('','0'))
            if not empty:
                raise NativeTopologyUnavailable('Gruppo di sincronizzazione persistente o ambiguo; nessuna commutazione')
        except NativeTopologyUnavailable:
            raise
        except (OSError,RuntimeError,urllib.error.URLError):
            raise NativeTopologyUnavailable('Topologia LMS non osservabile; nessuna commutazione') from None
        return dict(protocol='native-single-player-v1',mac=target['mac'],live_sync='-',persistent_syncgroupid=value)

    def snapshot(self,target):
        self.guard(target)
        status=self.rpc(target['mac'],['status','0','8192','tags:uoTI'])
        count=observed_integer(status.get('playlist_tracks'),0,8192,'Dimensione della coda')
        tracks=observed_tracks(status.get('playlist_loop'))
        if len(tracks)!=count:
            raise RuntimeError('Coda audio non acquisibile integralmente')
        index=observed_integer(status.get('playlist_cur_index'),0 if count else -1,count-1 if count else 0,'Indice della coda')
        position=observed_position(status.get('time'))
        repeat=observed_integer(status.get('playlist repeat'),0,2,'Ripetizione')
        shuffle=observed_integer(status.get('playlist shuffle'),0,2,'Riproduzione casuale')
        mode=status.get('mode')
        if mode not in ('play','pause','stop') or (not count and (mode!='stop' or position!=0)):
            raise RuntimeError('Stato di riproduzione non verificato')
        current=tracks[index] if count else {}
        topology=self.require_standalone(target,status)
        volume=parsed_volume(self.rpc(target['mac'],['mixer','volume','?']).get('_volume'))
        return dict(urls=[t['url'] for t in tracks],index=index,time=position,mode=mode,volume=volume,
                    repeat=repeat,shuffle=shuffle,topology=topology,
                    source=current.get('url'),metadata={key:current.get(key) for key in
                        ('type','content_type','format','audioformat','filetype','samplesize','bits_per_sample','samplerate')})

    def expected_pcm_rate(self,target,snapshot):
        """Preserve declared passthrough rates; never invent a resampling rule."""
        self.guard(target)
        rate=nominal_pcm_rate(snapshot.get('metadata',{}).get('samplerate'))
        if rate is None:return None
        preferences=Path(target['path']).read_text()
        settings=dict(re.findall(r'^(RESAMPLE|RATELIMIT)=(.*)$',preferences,re.M))
        info=Path('/run/spacd/info')/(str(target['card'])+'.0')
        if settings!={'RESAMPLE':'exception','RATELIMIT':'0'} or not info.exists():return None
        declared=re.search(r'^PCM_RATES=(.*)$',info.read_text(),re.M)
        if declared and str(rate) in declared[1].split():return rate
        return None

    def current_source(self,target):
        """One current row for monitoring, not the entire possibly long queue."""
        self.guard(target)
        status=self.rpc(target['mac'],['status','-','1','tags:uoTI'])
        count=observed_integer(status.get('playlist_tracks'),0,2147483647,'Dimensione della coda')
        rows=observed_tracks(status.get('playlist_loop'))
        mode=status.get('mode')
        if len(rows)!=(1 if count else 0) or mode not in ('play','pause','stop') or (not count and mode!='stop'):
            raise RuntimeError('Sorgente corrente non osservabile integralmente')
        current=rows[0] if rows else {}
        return dict(source=current.get('url'),mode=mode,
                    metadata={key:current.get(key) for key in
                        ('type','content_type','format','audioformat','filetype','samplesize','bits_per_sample','samplerate')})

    @staticmethod
    def same_queue(before,after):
        return all(before.get(k)==after.get(k) for k in ('urls','index','repeat','shuffle'))

    def pause(self,target,expected):
        current=self.snapshot(target)
        if not self.same_queue(expected,current):raise RuntimeError('Coda modificata esternamente; intervento annullato')
        if current['mode']!=expected['mode']:
            raise RuntimeError('Stato di riproduzione cambiato esternamente; intervento annullato')
        if current.get('volume')!=expected.get('volume'):
            raise RuntimeError('Volume cambiato durante la commutazione; nessuna sovrascrittura')
        if current['mode']!='play':
            if current.get('time')!=expected.get('time'):raise RuntimeError('La posizione del brano è cambiata; intervento annullato')
            return current
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            # A decoder startup can overwrite an early pause acknowledgement.
            # Explicit pause=1 is idempotent; never use a toggling pause here.
            self.require_standalone(target)
            try:self.rpc(target['mac'],['pause',1])
            except TimeoutError:pass
            time.sleep(.15)
            current=self.snapshot(target)
            if not self.same_queue(expected,current):raise RuntimeError('Coda modificata durante la pausa')
            if current['mode'] in ('pause','stop'):
                time.sleep(.15);settled=self.snapshot(target)
                if not self.same_queue(expected,settled):raise RuntimeError('Coda modificata durante la pausa')
                if settled['mode']==current['mode']:return settled
        raise RuntimeError('Pausa del player non confermata')

    def player_process(self,target):
        """Observe the native process selected by this card's supervisor."""
        try:
            pid=int((Path('/run/spacd/c')/str(target['card'])/'d/0/pid').read_text().strip())
            process=Path('/proc')/str(pid)
            before=(process/'stat').read_text().rsplit(')',1)[1].split()
            if before[0] in ('Z','X'):raise NativeIdentityNotReady('Player nativo in riavvio')
            identity=(pid,before[19])
            if getattr(self,'qualified_process',None)!=identity:
                if hashlib.sha256((process/'exe').read_bytes()).hexdigest()!=NATIVE_SHA256:
                    raise NativeIdentityNotReady('Player nativo in avvio')
                argv=[os.fsdecode(a) for a in (process/'cmdline').read_bytes().split(b'\0') if a]
                route=dict(version=1,alsa_id=target['alsa_id'],usb=target['usb'])
                if not argv or selected_card(argv[1:],route)!=target['card']:
                    raise NativeIdentityNotReady('Player della scheda non ancora pronto')
                after=(process/'stat').read_text().rsplit(')',1)[1].split()
                if after[0] in ('Z','X') or after[19]!=before[19]:raise NativeIdentityNotReady('Player nativo in riavvio')
                self.qualified_process=identity
            return identity
        except (FileNotFoundError,ProcessLookupError,ValueError,IndexError):
            raise NativeIdentityNotReady('Player nativo temporaneamente non pronto') from None

    def restart(self,target):
        self.guard(target)
        previous=self.player_process(target)
        self.require_standalone(target)
        # Daphile restart cardN invokes usbreset (USBDEVFS_RESET) even for
        # routine route transitions. The pcmCND0p restart path stops and
        # restarts only the already-qualified Squeezelite device 0.
        # Retain the full player/USB identity and LMS topology checks.
        devname='pcmC'+str(target['card'])+'D0p'
        subprocess.run(['/opt/spac/bin/spac-msg','restart',devname],check=True,
            timeout=5,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                current=self.player_process(target)
                if current!=previous:
                    self.guard(target)
                    observed=self.rpc(target['mac'],['status','-','1','tags:u'])
                    if observed.get('player_connected') in (1,'1',True) and self.player_process(target)==current:
                        # The general card-ready marker arrives later than
                        # the actual native process and its LMS connection.
                        return
            except NativeIdentityNotReady:pass
            time.sleep(.03)
        raise RuntimeError('Riavvio nativo della scheda non confermato')

    def resume(self,target,expected):
        current=self.snapshot(target)
        if not self.same_queue(expected,current):raise RuntimeError('Coda modificata; ripresa non eseguita')
        if current.get('volume')!=expected.get('volume'):
            raise RuntimeError('Volume cambiato durante la commutazione; nessuna sovrascrittura')
        if expected['mode'] in ('pause','stop'):
            # A disconnected decoder cannot reconstruct a paused cursor
            # without starting the old source. Report failure, retain recovery
            # ownership and never turn that loss into a successful handoff.
            if current['mode']!=expected['mode'] or current['time']!=observed_position(expected.get('time')):
                raise RuntimeError('Stato di riproduzione o posizione del brano non conservati; recupero necessario')
            return current
        if expected['mode']!='play':raise RuntimeError('Stato di riproduzione originale non verificato')
        if expected['mode']=='play':
            from peq.source_format import classify_source
            if classify_source(current.get('source'),current.get('metadata'))['kind']=='DSD':
                self.configured_dsd_source_transport(target)
            # Explicit unpause resumes the current decoder. LMS 'play' can
            # restart the selected track after the card reconnects. Never
            # send it here, or repeat an uncertain acknowledgement.
            try:
                if current['mode']!='play':
                    self.require_standalone(target)
                    self.rpc(target['mac'],['pause',0])
            except TimeoutError:pass
        deadline=time.monotonic()+10
        position_requested=False
        while time.monotonic()<deadline:
            current=self.snapshot(target)
            if not self.same_queue(expected,current):raise RuntimeError('Coda modificata durante la ripresa')
            if current.get('volume')!=expected.get('volume'):
                raise RuntimeError('Volume cambiato durante la commutazione; nessuna sovrascrittura')
            if current['mode']==expected['mode']:
                # 'play' is an intent while RAM preloading/card reconnect is
                # still pending. Observe readiness before the single seek.
                ready=self.rpc(target['mac'],['status','-','1','tags:u'])
                if (ready.get('mode')!='play' or ready.get('waitingToPlay',0) in (1,'1',True)
                        or observed_integer(ready.get('playlist_cur_index'),0,2147483647,'Indice corrente')!=current['index']):
                    time.sleep(.1);continue
                position=expected.get('time')
                if expected['mode']=='play' and position is not None:
                    if isinstance(position,bool) or not isinstance(position,(int,float)) or not math.isfinite(position) or position<0:
                        raise RuntimeError('Posizione originale del brano non verificata')
                    # A native card restart can reset LMS to STOP/position 0.
                    # Restore the captured cursor once, during this handoff
                    # only. Filter application never calls resume or seek.
                    if current['time']<position-.5:
                        if ready.get('can_seek') not in (1,'1',True):
                            time.sleep(.1);continue
                        if position_requested:time.sleep(.1);continue
                        self.require_standalone(target)
                        position_requested=True
                        try:self.rpc(target['mac'],['time',format(position,'.6f')])
                        except TimeoutError:pass
                        time.sleep(.1);continue
                return current
            time.sleep(.1)
        raise RuntimeError('Ripresa del player non confermata')

    def configured_dsd_source_transport(self,target):
        """Respect Daphile's existing DSF output policy on the native path.

        A DSF source can be converted to PCM by the native server. This method
        never changes that choice or authorizes routing DSF through our DSP.
        NativeAudio removes its route before this handoff. Strict raw DSD/DoP
        qualification continues to use require_dsd_transport below.
        """
        self.guard(target)
        values=re.findall(r'^USEDOP=(.*)$',Path(target['path']).read_text(),re.M)
        if values==['off']:return 'native-converted-PCM'
        return self.require_dsd_transport(target)

    def require_dsd_transport(self,target):
        self.guard(target)
        preferences=Path(target['path']).read_text()
        transport=re.findall(r'^USEDOP=(.*)$',preferences,re.M)
        if len(transport)!=1 or transport[0] not in ('on','native'):
            raise RuntimeError('Daphile non è configurato per DSD nativo/DoP; ripresa DSF annullata senza conversione PCM')
        if transport[0]=='native':
            stream=Path('/proc/asound',f"card{target['card']}",'stream0').read_text()
            if not re.search(r'Format: DSD_U',stream):
                raise RuntimeError('Formato DSD nativo non esposto dal DAC USB; scegliere DoP in Daphile')
        return 'DoP' if transport[0]=='on' else 'native'

    def loopback_params(self,target,expected_config):
        from peq.routing import parse_hw_params
        self.guard(target)
        path=Path('/proc/asound/card0/pcm0p/sub6/hw_params')
        params=parse_hw_params(path.read_text())
        if params is None:return None
        status=path.with_name('status').read_text()
        if not re.search(r'^state:\s*RUNNING\s*$',status,re.M):return None
        owner=re.search(r'owner_pid\s*:\s*(\d+)',status)
        if owner is None:raise RuntimeError('Proprietario del flusso PCM non verificato')
        process=Path('/proc')/owner[1]
        if hashlib.sha256((process/'exe').read_bytes()).hexdigest()!=NATIVE_SHA256:
            raise RuntimeError('Il flusso PCM appartiene a un altro programma')
        argv=[os.fsdecode(a) for a in (process/'cmdline').read_bytes().split(b'\0') if a]
        route=dict(version=1,alsa_id=target['alsa_id'],usb=target['usb'])
        if selected_card(argv[1:],route)!=target['card']:
            raise RuntimeError('Il flusso PCM appartiene a un altro DAC')
        environment=(process/'environ').read_bytes().split(b'\0')
        if environment.count(b'ALSA_CONFIG_PATH='+os.fsencode(expected_config))!=1:
            raise RuntimeError('Il player non usa il routing PCM privato verificato')
        self.loopback_owner=(target['mac'],int(owner[1]),(process/'stat').read_text().rsplit(')',1)[1].split()[19])
        return params

    def fast_loopback_params(self,target):
        """Local stop/rate-change guard; no RPC, executable hash or source guess."""
        from peq.routing import parse_hw_params
        known=getattr(self,'loopback_owner',None)
        if not known or known[0]!=target['mac']:return None
        try:
            if _read_guard_bytes(_GUARD_CARD_ID).strip()!=b'Loopback':return None
            status=_read_guard_bytes(_GUARD_STATUS)
            if not _GUARD_RUNNING.search(status):return None
            owner=_GUARD_OWNER.search(status)
            if owner is None or int(owner[1])!=known[1]:return None
            start=_read_guard_bytes(_GUARD_PROC+b'/'+owner[1]+b'/stat').rsplit(b')',1)[1].split()[19]
            if start!=known[2].encode('ascii'):return None
            return parse_hw_params(_read_guard_bytes(_GUARD_HW).decode('ascii'))
        except (OSError,ValueError,IndexError):return None
