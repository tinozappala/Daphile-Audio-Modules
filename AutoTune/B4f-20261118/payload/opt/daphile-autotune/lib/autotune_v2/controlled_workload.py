"""Owned native PCM playlist, recovered by the automatic session watchdog.

This adapter never guesses ownership from a familiar music URL. A different
queue is preserved, and inactive originals are restored without play/seek.
"""
import copy,hashlib,json,os,re,stat,types,uuid,wave
from pathlib import Path
try:from playback_contract import append_capacity,gains,replaceable,same_state,standalone,validate
except ImportError:from ..playback_contract import append_capacity,gains,replaceable,same_state,standalone,validate

MARKER='DAPHILE_AUTOTUNE_V2_WORKLOAD_V1'


def _record_fingerprint(record):
    return hashlib.sha256(json.dumps(record,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def initial_workload(root,engine):
    """Capture ordinary completed workload state before publishing a new lease."""
    path=Path(root)/'controlled-workload.json'
    if path.is_symlink():raise RuntimeError('Indirect initial workload refused')
    if not path.exists():return dict(present=False)
    record=engine.load(path)
    fixture=record.get('workload',{}).get('path')
    if (record.get('marker')!=MARKER or record.get('phase')!='restored'
            or not isinstance(fixture,str) or not Path(fixture).is_absolute() or os.path.lexists(fixture)):
        raise RuntimeError('Previous controlled workload recovery is not complete')
    return dict(present=True,sha256=_record_fingerprint(record))


def verify_untouched_session(root,owner,api=None):
    """Read-only proof for death before this lease created any workload."""
    api=api or native_api();path=Path(root)/'controlled-workload.json'
    if Path(root).is_symlink() or path.is_symlink():raise RuntimeError('Indirect workload recovery refused')
    if owner.get('boot_id')!=api.boot_id():raise RuntimeError('Untouched session belongs to another boot')
    captured=owner.get('initial_controlled_workload')
    if path.exists():
        record=json.loads(path.read_text())
        fixture=record.get('workload',{}).get('path')
        if (not isinstance(captured,dict) or captured.get('present') is not True
                or captured.get('sha256')!=_record_fingerprint(record)
                or record.get('marker')!=MARKER or record.get('phase')!='restored'
                or record.get('owner_id')==owner.get('id') or not isinstance(fixture,str)
                or not Path(fixture).is_absolute() or os.path.lexists(fixture)):
            raise RuntimeError('Captured initial workload changed; untouched recovery refused')
    elif captured is not None and captured!={'present':False}:
        raise RuntimeError('Captured initial workload disappeared; untouched recovery refused')
    original=owner['playback'];mac=original['mac']
    standalone(mac,api.player.rpc,getattr(api,'verify_restore',None))
    if (not same_state(original,api.player.playback_snapshot(mac),api.player.playlist_key)
            or api.playback(mac)!=dict(volume=original['volume'],mute=original['mute'])):
        raise RuntimeError('Untouched playback changed; exact baseline is not restored')
    return dict(status='no-controlled-workload-created',restored=True,queue_restored=True,player_commands=0)


def native_api():
    import engine,player
    from playback_start import start_verified
    from quiet_playback import restore_inactive,qualified_lms
    def playback(mac):return gains(mac,player.rpc)
    def verify_target(target):
        from detection import live_scan as scan
        from .native_target import resolve
        if resolve(scan(),player=player)!=target:raise RuntimeError('Current DAC or native player changed')
    return types.SimpleNamespace(boot_id=engine.boot_id,atomic=engine.atomic,
        player=player,playback=playback,start_verified=start_verified,restore_inactive=restore_inactive,
        verify_target=verify_target,verify_restore=qualified_lms)


def recover_session(root,owner,api=None,folder=Path('/mnt/hdd/daphile-autotune-samples')):
    """Independent session watchdog entry; never selects a source or starts a test."""
    root=Path(root);path=root/'controlled-workload.json'
    if root.is_symlink() or path.is_symlink():raise RuntimeError('Indirect workload recovery refused')
    api=api or native_api()
    if not path.exists():
        return verify_untouched_session(root,owner,api)
    record=json.loads(path.read_text())
    if record.get('owner_id')!=owner.get('id'):
        return verify_untouched_session(root,owner,api)
    if record.get('marker')!=MARKER or record.get('owner_id')!=owner['id'] or record.get('boot_id')!=owner['boot_id'] or record.get('boot_id')!=api.boot_id():
        raise RuntimeError('Workload is not owned by this live-boot session')
    if record.get('original_playback')!=owner['playback']:
        raise RuntimeError('Workload bookmark differs from independent recovery lease')
    folder=Path(folder)
    if folder.is_symlink() or not folder.is_dir():raise RuntimeError('Unknown fixture directory retained')
    if os.name=='posix' and (folder.stat().st_uid!=os.geteuid() or stat.S_IMODE(folder.stat().st_mode)&0o022):
        raise RuntimeError('Externally writable fixture directory retained')
    fixture=Path(record['workload']['path'])
    if fixture.parent!=Path(folder) or fixture.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.wav',fixture.name):
        raise RuntimeError('Unowned controlled sample retained')
    workload=Workload(root,dict(id=owner['id'],original_playback=owner['playback']),api,folder)
    workload.record=record
    result=workload.restore()
    workload.cleanup()
    # The independent lease must not mark an incomplete restoration completed.
    # External intent is preserved and its explicit failure remains inspectable.
    if result.get('restored') is not True:
        raise RuntimeError('Controlled playback restoration incomplete; newer user intent preserved')
    return result


def equivalent(a,b,player):
    return [player.playlist_key(u) for u in a['urls']]==[player.playlist_key(u) for u in b['urls']] and a['index']==b['index']


def restore_owned(record,current,api,on_owned=lambda:None):
    original=record['original_playback'];fixture=Path(record['workload']['path'])
    validate(original);validate(current)
    same_urls=lambda a,b:[api.player.playlist_key(u) for u in a]==[api.player.playlist_key(u) for u in b]
    complete=same_state(original,current,api.player.playlist_key) and api.playback(original['mac'])==dict(volume=original['volume'],mute=original['mute'])
    if complete:
        return dict(status='original-state-already-current',restored=True,queue_restored=True,player_commands=0,external_mode_preserved=True)
    # Every selection step journals its before/after expectation BEFORE the
    # command. Interrupted flag changes never grant ownership over a different
    # queue, mode, cursor, gain or power setting.
    selecting=(record.get('selection_protocol')=='append-owned-fixture-v1'
        and record.get('phase') in ('selecting-flags','appending-fixture')
        and any(isinstance(v,dict) and same_state(v,current,api.player.playlist_key)
                for v in (record.get('selection_expected'),record.get('selection_pending'))))
    entry=record.get('restoration_entry')
    entry_owned=(isinstance(entry,dict) and validate(entry) is not None
        and (same_urls(entry['urls'],original['urls']+[fixture.resolve().as_uri()])
             or any(isinstance(v,dict) and same_state(v,entry,api.player.playlist_key)
                    for v in (record.get('selection_expected'),record.get('selection_pending')))))
    restoring=(record.get('selection_protocol')=='append-owned-fixture-v1' and record.get('phase')=='restoring'
        and entry_owned and same_urls(current['urls'],original['urls'])
        and current['mode']=='stop' and current['time']<.25
        and all(current[k]==original[k] for k in ('mac','volume','mute','power'))
        and current['index'] in (0,max(0,len(original['urls'])-1),original['index'])
        and current['repeat'] in (record.get('fixture_repeat',0),original['repeat'])
        and current['shuffle'] in (0,original['shuffle']))
    if equivalent(original,current,api.player) and not selecting and not restoring:
        return dict(status='original-state-already-current' if complete else 'original-queue-newer-state-preserved',
            restored=complete,queue_restored=True,player_commands=0,external_mode_preserved=True)
    fixture_key=api.player.playlist_key(fixture.resolve().as_uri())
    appended=(record.get('selection_protocol')=='append-owned-fixture-v1'
        and same_urls(current['urls'],original['urls']+[fixture.resolve().as_uri()])
        and current['mac']==original['mac'] and current['power']==original['power']==1
        and current['repeat']==record.get('fixture_repeat',0) and current['shuffle']==0
        and current['index'] in (original['index'],len(original['urls']))
        and (current['mode']=='stop' or (current['mode']=='play' and current['index']==len(original['urls']))))
    legacy=(record.get('selection_protocol') is None and len(current['urls'])==1
        and current['index']==0 and api.player.playlist_key(current['urls'][0])==fixture_key and current['mode']!='pause'
        and current['power']==original['power']==1 and current['mac']==original['mac'])
    if not (appended or legacy or selecting or restoring):
        return dict(status='external-queue-preserved',restored=False,queue_restored=False,player_commands=0,external_queue_preserved=True,restore_available=False)
    # Historical records with PLAY intent receive only quiet queue recovery.
    # Never reconstruct PLAY/PAUSE through replay or seek of an old playlist.
    quiet=original if original['mode'] in ('pause','stop') else dict(original,mode='stop',time=0)
    on_owned()
    result=api.restore_inactive(quiet)
    after=api.player.playback_snapshot(original['mac'])
    validate(after)
    complete=same_state(original,after,api.player.playlist_key) and api.playback(original['mac'])==dict(volume=original['volume'],mute=original['mute'])
    return dict(result,restored=complete,queue_restored=equivalent(original,after,api.player),
        status='original-state-restored' if complete else 'original-queue-restored-bookmark-or-newer-state-retained',
        original_bookmark_retained=not complete,no_play_or_seek_commands=True,playback_not_automatically_resumed=True)


class Workload:
    def __init__(self,root,owner,api,folder=Path('/mnt/hdd/daphile-autotune-samples')):
        self.root=Path(root);self.owner=owner;self.api=api;self.folder=Path(folder);self.record=None

    def create(self,target,rate=192000):
        replaceable(self.owner['original_playback'])
        if not callable(getattr(self.api,'verify_restore',None)):raise RuntimeError('Native restoration compatibility preflight required')
        self.api.verify_restore();self.api.verify_target(target)
        if type(rate) is not int or rate not in (44100,48000,88200,96000,176400,192000,352800,384000):raise ValueError('Declared PCM sample rate required')
        topology=standalone(target['mac'],self.api.player.rpc,self.api.verify_restore)
        capacity=append_capacity(self.api.player.rpc,len(self.owner['original_playback']['urls']))
        if self.folder.is_symlink():raise RuntimeError('Indirect fixture folder refused')
        self.folder.mkdir(mode=0o755,parents=True,exist_ok=True)
        metadata=self.folder.stat()
        if os.name=='posix' and (metadata.st_uid!=os.geteuid() or stat.S_IMODE(metadata.st_mode)&0o022):raise RuntimeError('Only the fixture owner may write its directory')
        sample=self.folder/(uuid.uuid4().hex+'.wav')
        # Journal the unique path BEFORE allocating potentially hundreds of
        # MiB. A killed producer must not leave an unrecorded, unowned sample.
        # The exclusive inode identity is journaled before the first PCM byte.
        self.record=dict(marker=MARKER,owner_id=self.owner['id'],boot_id=self.api.boot_id(),original_playback=self.owner['original_playback'],
            original_volume=self.owner['original_playback']['volume'],original_mute=self.owner['original_playback']['mute'],target=target,
            preferences_sha256=hashlib.sha256(Path(target['path']).read_bytes()).hexdigest(),
            workload=dict(path=str(sample),sha256=None,rate=rate,bits=32,channels=2,scope='owned-native-pcm'),
            fixture_complete=False,fixture_identity=None,append_capacity_before_creation=capacity,sync_before_creation=topology,
            selection_protocol='append-owned-fixture-v1',fixture_repeat=1,phase='creating-fixture')
        self.api.atomic(self.root/'controlled-workload.json',self.record)
        fd=os.open(sample,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        # A frame stays inside the same generated source across all seeks.
        # Digital silence changes no gain or volume setting in the system.
        with os.fdopen(fd,'wb') as output:
            info=os.fstat(output.fileno())
            self.record['fixture_identity']=dict(device=info.st_dev,inode=info.st_ino,uid=info.st_uid)
            self.api.atomic(self.root/'controlled-workload.json',self.record)
            with wave.open(output,'wb') as stream:
                stream.setparams((2,4,rate,0,'NONE','not compressed'))
                for _ in range(90):stream.writeframesraw(bytes(rate*8))
            output.flush();os.fchmod(output.fileno(),0o644);os.fsync(output.fileno())
        digest=hashlib.sha256()
        with sample.open('rb') as stream:
            while block:=stream.read(1024*1024):digest.update(block)
        self.record['workload']['sha256']=digest.hexdigest()
        self.record.update(fixture_complete=True,phase='prepared-before-playlist-write')
        self.api.atomic(self.root/'controlled-workload.json',self.record)
        return self.record['workload']

    def select(self):
        r=self.record
        if r is None:raise RuntimeError('Fixture must be journaled before selecting it')
        if r.get('fixture_complete') is False:raise RuntimeError('Incomplete fixture cannot be selected')
        replaceable(r['original_playback'])
        if not callable(getattr(self.api,'verify_restore',None)):raise RuntimeError('Native restoration compatibility preflight required')
        self.api.verify_restore();self.api.verify_target(r['target'])
        if hashlib.sha256(Path(r['target']['path']).read_bytes()).hexdigest()!=r['preferences_sha256']:
            raise RuntimeError('External preferences changed before controlled selection')
        current=self.api.player.playback_snapshot(r['target']['mac'])
        replaceable(current)
        if not same_state(r['original_playback'],current,self.api.player.playlist_key) or self.api.playback(r['target']['mac'])!=dict(volume=r['original_volume'],mute=r['original_mute']):
            raise RuntimeError('External playback intent changed before controlled selection')
        r['sync_before_selection']=standalone(r['target']['mac'],self.api.player.rpc,self.api.verify_restore)
        r['append_capacity_before_selection']=append_capacity(self.api.player.rpc,len(current['urls']))
        mac=r['target']['mac'];expected=copy.deepcopy(current)
        r['selection_protocol']='append-owned-fixture-v1'
        def step(command,after,phase):
            if not same_state(expected,self.api.player.playback_snapshot(mac),self.api.player.playlist_key):
                raise RuntimeError('External player change preserved before controlled selection step')
            r.update(phase=phase,selection_expected=copy.deepcopy(expected),selection_pending=copy.deepcopy(after))
            self.api.atomic(self.root/'controlled-workload.json',r)
            r['sync_before_step']=standalone(mac,self.api.player.rpc,self.api.verify_restore)
            if command[:2]==['playlist','add']:
                # Read immediately before mutation, after earlier flag writes.
                # LMS offers no atomic capacity reservation: external clients
                # can still race this observation and must remain a limit.
                r['append_capacity_before_add']=append_capacity(self.api.player.rpc,len(expected['urls']))
            self.api.player.rpc(mac,command)
            observed=self.api.player.playback_snapshot(mac)
            if not same_state(after,observed,self.api.player.playlist_key):
                raise RuntimeError('Controlled selection step readback differs')
            expected.clear();expected.update(observed)
            r.update(selection_expected=copy.deepcopy(expected),selection_pending=None)
            self.api.atomic(self.root/'controlled-workload.json',r)
        # Shuffle must already be disabled: changing its preference before
        # append does not preserve LMS's internal queue permutation. Repeat
        # has no permutation and its exact value is journaled for recovery.
        # Repeat the controlled source. With repeat disabled LMS's native
        # Don't Stop The Music may append a random source at this last item.
        # This also keeps the same PCM source alive beyond its first 90 s.
        if expected['repeat']!=r['fixture_repeat']:
            step(['playlist','repeat',r['fixture_repeat']],dict(expected,repeat=r['fixture_repeat']),'selecting-flags')
        step(['playlist','add',r['workload']['path']],dict(expected,urls=expected['urls']+[Path(r['workload']['path']).resolve().as_uri()]),'appending-fixture')
        step(['playlist','index',len(r['original_playback']['urls']),0,1],dict(expected,index=len(r['original_playback']['urls']),time=0),'selecting-fixture')
        r['phase']='starting-fixture';self.api.atomic(self.root/'controlled-workload.json',r)
        r['sync_before_start']=standalone(mac,self.api.player.rpc,self.api.verify_restore)
        self.api.start_verified(mac,30)
        self.api.player.wait_audio(mac,30,expected_rate=r['workload']['rate'])
        observed=self.api.player.playback_snapshot(mac)
        if observed['mode']!='play' or not equivalent(expected,observed,self.api.player) or any(observed[k]!=expected[k] for k in ('repeat','shuffle','volume','mute','power')):
            raise RuntimeError('Owned fixture playback state differs after start')
        standalone(mac,self.api.player.rpc,self.api.verify_restore)
        r['phase']='controlled-playback';self.api.atomic(self.root/'controlled-workload.json',r)

    def restore(self):
        r=self.record
        original=r['original_playback'] if r else self.owner['original_playback']
        standalone(original['mac'],self.api.player.rpc,getattr(self.api,'verify_restore',None))
        if not r:
            return dict(status='not-created',player_commands=0,
                restored=same_state(original,self.api.player.playback_snapshot(original['mac']),self.api.player.playlist_key))
        if r['owner_id']!=self.owner['id'] or r['boot_id']!=self.api.boot_id():raise RuntimeError('Controlled workload belongs to another owner or boot')
        self.api.verify_target(r['target'])
        if hashlib.sha256(Path(r['target']['path']).read_bytes()).hexdigest()!=r['preferences_sha256']:raise RuntimeError('External preferences changed; controlled restore refused')
        current=self.api.player.playback_snapshot(r['target']['mac'])
        def journal():
            r.setdefault('restoration_entry',copy.deepcopy(current));r['phase']='restoring'
            self.api.atomic(self.root/'controlled-workload.json',r)
        result=restore_owned(r,current,self.api,on_owned=journal)
        r.update(phase='restored' if result.get('restored') else 'external-queue-preserved',restoration=result)
        self.api.atomic(self.root/'controlled-workload.json',r)
        return result

    def cleanup(self):
        r=self.record
        if not r:return
        if r['phase'] not in ('restored','external-queue-preserved'):raise RuntimeError('Recover playlist before removing its fixture')
        standalone(r['target']['mac'],self.api.player.rpc,getattr(self.api,'verify_restore',None))
        path=Path(r['workload']['path'])
        if path.parent!=self.folder or path.is_symlink() or not re.fullmatch(r'[a-f0-9]{32}\.wav',path.name):raise RuntimeError('Unknown fixture retained')
        current=self.api.player.playback_snapshot(r['target']['mac'])
        if any(self.api.player.may_reference_fixture(url,path,r.get('fixture_identity')) for url in current['urls']):
            raise RuntimeError('Controlled fixture is still referenced by the current playlist; retained')
        if path.exists():
            info=path.stat();identity=r.get('fixture_identity')
            if 'fixture_identity' in r:
                expected=dict(device=info.st_dev,inode=info.st_ino,uid=info.st_uid)
                if identity!=expected or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o022:
                    raise RuntimeError('Fixture inode ownership is unproven or changed; retained')
            if r.get('fixture_complete') is False:
                if not identity:raise RuntimeError('Incomplete fixture ownership is unproven; retained')
                path.unlink()
                return
            digest=hashlib.sha256()
            with path.open('rb') as stream:
                while block:=stream.read(1024*1024):digest.update(block)
            if digest.hexdigest()!=r['workload']['sha256']:raise RuntimeError('Modified fixture preserved')
            path.unlink()

__release_signature__ = 'Tino Zappala'
