"""Development RAM cache: real native services, atomic intent, no code provenance gates."""
import argparse,contextlib,fcntl,hashlib,http.client,json,os,re,select,signal,socket,stat,struct,subprocess,threading,time,traceback,urllib.parse,uuid
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from rolling import RollingCache
from live_switch import LiveSwitch

CONFIG='/mnt/hdd/system/daphile-ram/config.json'
def need(value,code):
    if not value:raise RuntimeError(code)
def load(path,default=None):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    except FileNotFoundError:return default
    with os.fdopen(fd,'rb')as f:
        raw=f.read(16777217);need(len(raw)<=16777216,'STATE_SIZE');return json.loads(raw)
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    need(not path.parent.is_symlink(),'WRITE_PARENT_SYMLINK')
    name='.tmp-'+uuid.uuid4().hex;fd=os.open(path.parent/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode();need(os.write(fd,raw)==len(raw),'STATE_SHORT_WRITE');os.fsync(fd)
    finally:os.close(fd)
    os.replace(path.parent/name,path);directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(directory)
    finally:os.close(directory)
def proc(pid):
    p=Path('/proc')/str(pid);raw=(p/'stat').read_text();v=raw[raw.rfind(')')+2:].split();uid=next(x for x in(p/'status').read_text().splitlines()if x.startswith('Uid:')).split()[1:]
    return dict(parent=int(v[1]),birth=int(v[19]),state=v[0],uid=[int(x)for x in uid])
@contextlib.contextmanager
def native_process(cfg,optional=False):
    try:
        p=Path(cfg.get('native_pidfile','/run/mediaserver/mediaserver.pid'));pid=int(p.read_text());fd=os.pidfd_open(pid);value=proc(pid)
        need(value['state']not in('Z','X','x')and value['uid']==[cfg.get('native_uid',1000)]*4 and not select.select([fd],[],[],0)[0],'NATIVE_UID_OR_PIDFD')
    except(FileNotFoundError,ProcessLookupError):
        if optional:yield None;return
        raise RuntimeError('NATIVE_NOT_RUNNING')from None
    try:yield dict(pid=pid,pidfd=fd,**value)
    finally:os.close(fd)
def rpc(cfg,player,command,deadline=None):
    timeout=cfg.get('native_rpc_seconds',30)
    if deadline is not None:timeout=min(timeout,deadline-time.monotonic());need(timeout>0,'NATIVE_RPC_DEADLINE')
    c=http.client.HTTPConnection('127.0.0.1',cfg.get('http_port',9000),timeout=timeout)
    try:
        c.request('POST','/jsonrpc.js',json.dumps(dict(id='ramcache-native',method='slim.request',params=[player,command])),{'Content-Type':'application/json'})
        r=c.getresponse();raw=r.read(1048577);need(r.status==200 and r.getheader('WWW-Authenticate')is None and len(raw)<=1048576,'NATIVE_NOAUTH_RPC');value=json.loads(raw);need(not value.get('error'),'NATIVE_RPC_ERROR');return value['result']
    finally:c.close()
def read_rpc(cfg,player,command,deadline=None):
    while True:
        try:return rpc(cfg,player,command,deadline)
        except OSError:
            if deadline is None or time.monotonic()>=deadline:raise
            time.sleep(.25)
def same_audio_url(left,right):
    if left==right:return True
    def local(value):
        parsed=urllib.parse.urlsplit(value)
        if parsed.scheme=='file'and parsed.netloc in('','localhost'):return Path(urllib.parse.unquote(parsed.path))
        return Path(value)if value.startswith('/')else None
    a,b=local(left),local(right)
    if a is None or b is None:return False
    try:x,y=a.stat(),b.stat();return(x.st_dev,x.st_ino)==(y.st_dev,y.st_ino)
    except OSError:return False
def same_queue_urls(left,right):return len(left)==len(right)and all(same_audio_url(a,b)for a,b in zip(left,right))
def partial_queue_subsequence(current,saved):
    """A strictly shorter ordered subsequence, retaining duplicate counts."""
    if not 0<len(current)<len(saved):return False
    index=0
    for url in saved:
        if same_audio_url(current[index],url):
            index+=1
            if index==len(current):return True
    return False
class NativeCLI:
    """Original LMS CLI, one serialized NoAuth connection; no login is sent."""
    def __init__(self,cfg):self.cfg=cfg;self.sock=None;self.file=None;self.lock=threading.RLock()
    def close(self):
        if self.file is not None:self.file.close()
        if self.sock is not None:self.sock.close()
        self.file=self.sock=None
    def call(self,player,command,deadline=None):
        query=command[-1]=='?';limit=deadline if deadline is not None else time.monotonic()+30
        with self.lock:
            while True:
                timeout=min(30,limit-time.monotonic());need(timeout>0,'NATIVE_CLI_DEADLINE')
                try:
                    if self.sock is None:
                        self.sock=socket.create_connection(('127.0.0.1',self.cfg.get('native_cli_port',9090)),timeout=timeout);self.file=self.sock.makefile('rb')
                    self.sock.settimeout(timeout);words=([player]if player else[])+[str(x)for x in command];line=' '.join(urllib.parse.quote(x,safe=':')for x in words)+'\n';need(len(line)<=16384,'NATIVE_CLI_FRAME');self.sock.sendall(line.encode());raw=self.file.readline(16385)
                    if not raw:raise ConnectionResetError('NATIVE_CLI_EOF')
                    need(raw.endswith(b'\n')and len(raw)<=16384,'NATIVE_CLI_RESPONSE');tokens=[urllib.parse.unquote(x)for x in raw.decode().strip().split(' ')]
                    need(not any(x.lower()in('login_required','loginrequired')for x in tokens),'NATIVE_CLI_REQUIRES_NOAUTH')
                    if query:
                        prefix=words[:-1];need(tokens[:len(prefix)]==prefix and len(tokens)>len(prefix),'NATIVE_CLI_QUERY_RESPONSE');key='_p2'if command[0]=='pref'else'_path'if command[:2]==['playlist','path']else'_'+str(command[-2]);return{key:' '.join(tokens[len(prefix):])}
                    return{}
                except OSError:
                    self.close()
                    if not query or time.monotonic()>=limit:raise
                    time.sleep(.1)
STOCK_RAM_TARGET='/srv/mediaserver/music/RAM Drive/RAM play cache'
STOCK_MUSIC_ROOT='/srv/mediaserver/music'

def local_audio_path(url):
    """Volatile.pm changes only tmp -> file before resolving the local URL."""
    if type(url)is not str:return None
    if url.startswith('/'):return Path(url)
    uri=urllib.parse.urlsplit(url)
    if uri.scheme not in('file','tmp')or uri.netloc not in('','localhost'):return None
    path=Path(urllib.parse.unquote(uri.path))
    return path if path.is_absolute()else None

def stock_original(url,known_urls=(),target=STOCK_RAM_TARGET,mroot=STOCK_MUSIC_ROOT):
    """Reverse only ramplay.sh's explicit filename rules, with a real source.

    A cache filename is not enough to disambiguate different source files. Known
    original URL spelling is retained when it denotes the same actual source.
    No audio, metadata, playlist or original file is written by this function.
    """
    path=local_audio_path(url)
    need(path is not None,'LOCAL_QUEUE_REQUIRED')
    if not str(path).startswith(target+'/'):
        normalized='file'+url[3:]if url.startswith('tmp:')else url
        return normalized,dict(kind='volatile-original'if url.startswith('tmp:')else'original',observed_url=url,original_url=normalized)
    need(path.is_file(),'STOCK_RAM_CACHE_UNAVAILABLE')
    relative=str(path)[len(target):]
    converted=re.fullmatch(r'(.+) \(([A-Za-z0-9_-]+)\) \[(?:[0-9]+_[0-9]+|DSD[0-9]+(?:-DoP)?)\]\.(?:wav|dsf)',relative)
    native=re.fullmatch(r'(.+) \[DSD[0-9]+\]\.(dsf|dff)',relative)
    match=converted or native
    need(match is not None,'STOCK_RAM_NAME_UNRECOGNIZED')
    stem,extension=match.groups()
    # A cut encoded only in the output name loses the original time precision.
    # Do not silently turn that segment into a different, complete track.
    need(re.search(r' #[0-9:.]+-[0-9:.]+$',stem)is None,'STOCK_RAM_SEGMENT_UNRESOLVED')
    suffix=stem+'.'+extension
    candidates=[(suffix,None),(mroot+suffix,None)]
    for known in known_urls:
        original=local_audio_path(known)
        if original is None:continue
        text=str(original);tail=text[len(mroot):]if text.startswith(mroot)else text
        if tail.rsplit('.',1)[0]==stem and text.rsplit('.',1)[-1]==extension:
            candidates.insert(0,(text,known))
    found=[]
    for text,known in candidates:
        original=Path(text)
        try:info=original.stat()
        except OSError:continue
        if stat.S_ISREG(info.st_mode):found.append((original,known,(info.st_dev,info.st_ino)))
    need(bool(found),'STOCK_RAM_ORIGINAL_UNRESOLVED')
    need(len({item[2]for item in found})==1,'STOCK_RAM_ORIGINAL_AMBIGUOUS')
    original,known,_=found[0]
    normalized=known if known is not None else original.as_uri()
    if normalized.startswith('tmp:'):normalized='file'+normalized[3:]
    return normalized,dict(kind='stock-ramplay',observed_url=url,original_url=normalized)

def normalize_queue(queue,roots,known_urls=(),target=STOCK_RAM_TARGET,mroot=STOCK_MUSIC_ROOT):
    """Keep native data intact and build a separate next-boot representation."""
    normalized=dict(queue);normalized['urls']=[];mappings=[];paths=[];total=0
    roots=[Path(root).resolve()for root in roots]
    for url in queue['urls']:
        original,mapping=stock_original(url,known_urls,target,mroot)
        path=local_audio_path(original)
        need(path is not None and path.is_absolute()and not path.is_symlink(),'SOURCE_PATH')
        canonical=path.resolve();need(any(canonical.is_relative_to(root)for root in roots),'SOURCE_OUTSIDE_LIBRARY')
        if original.startswith('/'):original=Path(original).as_uri();mapping['original_url']=original
        normalized['urls'].append(original);mappings.append(mapping)
        if str(canonical)in paths:continue
        fd=os.open(canonical,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(fd);need(stat.S_ISREG(info.st_mode),'SOURCE_NOT_REGULAR');total+=info.st_size
        finally:os.close(fd)
        paths.append(str(canonical))
    return normalized,mappings,paths,total

def intent_update(old,value):
    need(type(value.get('requested_ram'))is bool and type(value.get('expected_version'))is int and type(value.get('request_id'))is str and re.fullmatch('[a-f0-9-]{16,64}',value['request_id']),'INTENT_SCHEMA')
    if old.get('request_id')==value['request_id']:
        need(old['requested_ram']==value['requested_ram'],'DUPLICATE_ID_CONFLICT');return old,False
    need(old['version']==value['expected_version'],'VERSION_CONFLICT')
    if old['requested_ram']==value['requested_ram']:return old,False
    return dict(version=old['version']+1,requested_ram=value['requested_ram'],request_id=value['request_id']),True

def saved_stock_queue_matches(saved,current_urls):
    """Saved normalization data, not a reason to clear a different/new queue."""
    observed=(saved.get('observed_queue')or{}).get('urls',[])
    originals=(saved.get('queue')or{}).get('urls',[])
    mappings=saved.get('url_mappings')
    return bool(observed and current_urls==observed and originals!=observed
                and type(mappings)is list and len(mappings)==len(observed)==len(originals)
                and all(type(row)is dict and row.get('observed_url')==raw
                        and row.get('original_url')==original
                        and row.get('kind')in('stock-ramplay','volatile-original','original')
                        for row,raw,original in zip(mappings,observed,originals))
                and any(row.get('kind')=='stock-ramplay'for row in mappings))

def snapshot_error_code(error):
    if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and re.fullmatch('[A-Z_]{1,80}',error.args[0]):return error.args[0]
    return 'QUEUE_SNAPSHOT_UNAVAILABLE'

class Manager:
    def __init__(self,cfg):
        need(os.geteuid()==0,'ROOT_SERVICE_REQUIRED');self.cfg=cfg;self.client=NativeCLI(cfg);self.state=Path(cfg['state_root']);self.run=Path(cfg.get('run_root','/run/daphile-ram'));self.cache=self.run/'cache'
        for p in(self.state,self.run):
            p.mkdir(parents=True,exist_ok=True,mode=0o700);s=p.lstat();need(stat.S_ISDIR(s.st_mode)and s.st_uid==0 and not s.st_mode&0o022,'PROTECTED_WRITE_DIRECTORY')
        os.chown(self.run,0,cfg.get('native_gid',1000));os.chmod(self.run,0o750)
        self.intent=self.state/'intent.json';self.runtime=self.run/'runtime.json';self.last=self.state/'last-complete.json';self.boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.rolling=RollingCache(self,load,atomic,local_audio_path)
        self.live=LiveSwitch(self,load,atomic,rpc,native_process)
        self._timing_lock=threading.Lock()
    def boot_time(self,phase):
        with self._timing_lock:
            path=self.run/'boot-timing.json';value=load(path,{})
            if value.get('boot')!=self.boot:value=dict(boot=self.boot,seconds={})
            value['seconds'].setdefault(phase,round(time.monotonic(),3));atomic(path,value)
    def state_value(self):return load(self.intent,dict(version=0,requested_ram=False,request_id=None))
    def cache_is_tmpfs(self):
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            before,after=line.split(' - ',1)
            if before.split()[4]==str(self.cache)and after.split()[0]=='tmpfs':return True
        return False
    def effective(self):
        run=load(self.runtime,{})
        if run.get('boot')!=self.boot or not run.get('cache_ready')or not self.cache_is_tmpfs():return False
        for row in run.get('mounts',[]):
            try:s=Path(row['target']).stat()
            except OSError:return False
            if [s.st_dev,s.st_ino]!=row['cache_identity']:return False
        return True
    def status(self):
        value=self.state_value();run=load(self.runtime,{})
        return dict(version=value['version'],requested_ram=value['requested_ram'],request_id=value.get('request_id'),effective_ram=self.effective(),error=self.live.status().get('error')or run.get('error')or load(self.run/'snapshot-error.json',{}).get('error'),queue_restore=load(self.run/'queue-restore.json',{}).get('status'),last_complete=load(self.last),continuous=self.rolling.status(),live=self.live.status(),development=True)
    def set(self,value):
        new,changed=intent_update(self.state_value(),value)
        if changed:atomic(self.intent,new)
        result=self.status();result['intent_changed']=changed;return result
    @contextlib.contextmanager
    def queue_locked(self,blocking=True):
        fd=os.open(self.state/'queue.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB));yield
        finally:os.close(fd)
    def native_queue(self,player,deadline=None):
        def get(command,key):return self.client.call(player,command+['?'],deadline).get(key)
        count=int(get(['playlist','tracks'],'_tracks')or 0);need(count<=self.cfg.get('max_queue_tracks',10000),'QUEUE_SIZE');urls=[]
        for index in range(count):
            url=get(['playlist','path',str(index)],'_path')
            need(type(url)is str and url,'NATIVE_QUEUE_PATH');urls.append(url)
        raw=dict(power=get(['power'],'_power'),shuffle=get(['playlist','shuffle'],'_shuffle'),repeat=get(['playlist','repeat'],'_repeat'),volume=get(['mixer','volume'],'_volume'))
        def number(value):
            if type(value)is str and re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?',value):return float(value)if'.'in value else int(value)
            return value
        return dict(urls=urls,index=get(['playlist','index'],'_index'),mode=get(['mode'],'_mode'),time=get(['time'],'_time'),raw_scalar_fields=raw,**{k:number(v)for k,v in raw.items()})
    def snapshot(self):
        with self.queue_locked():return self._snapshot()
    def _snapshot(self):
        with native_process(self.cfg)as native:
            previous=load(self.state/'queue-snapshot.json')
            need(self.client.call('',['pref','authorize','?']).get('_p2')in(0,'0',False),'NOAUTH_REQUIRED');player=self.cfg.get('player_id')or(previous or{}).get('player_id')
            if player:
                if self.client.call(player,['connected','?']).get('_connected')not in(1,'1',True):player=None
            else:
                players=[x for x in rpc(self.cfg,'',['players','0','100']).get('players_loop',[])if x.get('connected')in(1,'1',True)];player=players[0]['playerid']if len(players)==1 else None
            if not player:
                if previous is not None:return dict(player_absent=True,previous_snapshot_retained=True)
                snapshot=dict(paths=[],player_id=None,playlist_tracks=0,total_bytes=0,queue=None);atomic(self.state/'queue-snapshot.json',snapshot);return snapshot
            observed_queue=self.native_queue(player)
            identity=dict(boot=self.boot,pid=native['pid'],birth=native['birth'])
            atomic(self.state/'queue-last-observed.json',dict(queue=observed_queue,native=identity,player_id=player))
            queue,mappings,paths,total=normalize_queue(observed_queue,self.cfg['source_roots'],(previous or{}).get('queue',{}).get('urls',[])if(previous or{}).get('queue')else[])
            restoration=load(self.run/'queue-restore.json',{});identity=dict(boot=self.boot,pid=native['pid'],birth=native['birth'])
            if previous and restoration.get('status')in('RESTORING','RESTORE_FAILED_SAVED_QUEUE_RETAINED'):
                return dict(incomplete_restore=True,previous_snapshot_retained=True)
            completed_here=restoration.get('native')==identity and restoration.get('status')=='RESTORED_WITH_NATIVE_API'
            if previous and previous.get('native')!=identity and not completed_here and partial_queue_subsequence(queue['urls'],(previous.get('queue')or{}).get('urls',[])):
                return dict(partial_native_queue=True,previous_complete_snapshot_retained=True,previous_snapshot_retained=True)
            restored_current=restoration.get('native')==identity and restoration.get('status')in('RESTORED_WITH_NATIVE_API','NATIVE_QUEUE_RETAINED','EMPTY_QUEUE_VALID')
            if not queue['urls']and previous and(previous.get('queue')or{}).get('urls')and previous.get('native')!=identity and not restored_current:
                return dict(empty_during_incomplete_restore=True,previous_snapshot_retained=True)
            snapshot=dict(paths=paths,player_id=player,playlist_tracks=len(queue['urls']),total_bytes=total,queue=queue,observed_queue=observed_queue,url_mappings=mappings,native=identity)
            atomic(self.state/'queue-snapshot.json',snapshot);return snapshot
    def restore_queue(self,deadline=None):
        deadline=deadline if deadline is not None else time.monotonic()+120;previous_restore=load(self.run/'queue-restore.json',{});writer=os.getpid();atomic(self.run/'queue-restore.json',dict(status='WAITING_NATIVE_PLAYER',writer_pid=writer))
        if not(load(self.state/'queue-snapshot.json',{}).get('queue')or{}).get('urls'):atomic(self.run/'queue-restore.json',dict(status='EMPTY_QUEUE_VALID',writer_pid=writer));return
        while time.monotonic()<deadline:
            saved=load(self.state/'queue-snapshot.json',{});player=saved.get('player_id')or self.cfg.get('player_id')
            try:
                if player:
                    if self.client.call(player,['connected','?'],deadline).get('_connected')in(1,'1',True):break
                    players=[]
                else:players=[x for x in read_rpc(self.cfg,'',['players','0','100'],deadline).get('players_loop',[])if x.get('connected')in(1,'1',True)]
            except OSError:
                # Startup/read-only transport failures do not lose the saved
                # queue. Retry only this read, under the same absolute budget.
                if time.monotonic()>=deadline:atomic(self.run/'queue-restore.json',dict(status='NATIVE_QUERY_NOT_READY_SAVED_QUEUE_RETAINED',writer_pid=writer));return
                atomic(self.run/'queue-restore.json',dict(status='WAITING_NATIVE_PLAYER',writer_pid=writer,read_retry=True));time.sleep(.25);continue
            if any(x['playerid']==player for x in players):break
            if saved.get('player_id')is None and len(players)==1:player=players[0]['playerid'];break
            time.sleep(.5)
        else:atomic(self.run/'queue-restore.json',dict(status='NO_PLAYER_SAVED_QUEUE_RETAINED',writer_pid=writer));return
        with self.queue_locked(),native_process(self.cfg)as native:
            identity=dict(boot=self.boot,pid=native['pid'],birth=native['birth'])
            saved=load(self.state/'queue-snapshot.json',{});queue=saved.get('queue');current=self.native_queue(player,deadline)
            resume=previous_restore.get('status')in('RESTORING','RESTORE_FAILED_SAVED_QUEUE_RETAINED')and previous_restore.get('native')==identity and queue and same_queue_urls(current['urls'],queue['urls'][:len(current['urls'])])
            same_existing=queue and same_queue_urls(current['urls'],queue['urls'])
            stock_recovery=saved.get('native')!=identity and saved_stock_queue_matches(saved,current['urls'])
            if previous_restore.get('mutator_outcome_ambiguous')and previous_restore.get('native')==identity:
                pending_count=previous_restore.get('pending_count',0)
                # A timed-out append may still be completing in this LMS.
                # Observe its result; never resend it on the same birth.
                while len(current['urls'])<pending_count:
                    need(time.monotonic()<deadline,'NATIVE_APPEND_OUTCOME_STILL_UNKNOWN');time.sleep(.25);current=self.native_queue(player,deadline)
                need(same_queue_urls(current['urls'],queue['urls'][:len(current['urls'])]),'NATIVE_APPEND_OUTCOME_CONFLICT')
            partial_recovery=previous_restore.get('partial_recovery')if previous_restore.get('native')==identity else None
            if partial_recovery and not partial_recovery.get('clear_confirmed'):
                # A previous clear may have lost its response. Observe its
                # outcome; never send another clear or append blindly.
                need(not current['urls'],'NATIVE_PARTIAL_CLEAR_OUTCOME_UNKNOWN')
                partial_recovery['clear_confirmed']=True
            if current['urls']and not resume and not same_existing:
                if saved.get('native')!=identity and queue and(stock_recovery or partial_queue_subsequence(current['urls'],queue['urls'])):
                    backup=self.state/'queue-recovery-backups'/(uuid.uuid4().hex+'.json')
                    reason='SAVED_STOCK_RAM_QUEUE_AFTER_NEW_BIRTH'if stock_recovery else'PARTIAL_NATIVE_QUEUE_AFTER_NEW_BIRTH'
                    atomic(backup,dict(reason=reason,native=identity,queue=current,saved_tracks=len(queue['urls'])))
                    partial_recovery=dict(backup=str(backup),current_tracks=len(current['urls']),saved_tracks=len(queue['urls']),clear_confirmed=False)
                    atomic(self.run/'queue-restore.json',dict(status='RESTORING',phase='NATIVE_PARTIAL_CLEAR_SENT',native=identity,writer_pid=writer,partial_recovery=partial_recovery))
                    try:self.client.call(player,['playlist','clear'],deadline)
                    except OSError:
                        current=self.native_queue(player,deadline)
                        need(not current['urls'],'NATIVE_PARTIAL_CLEAR_OUTCOME_UNKNOWN')
                    current=self.native_queue(player,deadline);need(not current['urls'],'NATIVE_PARTIAL_CLEAR_NOT_EMPTY');partial_recovery['clear_confirmed']=True
                else:
                    status='NATIVE_QUEUE_RETAINED'if saved.get('native')==identity else'FOREIGN_NATIVE_QUEUE_RETAINED_SNAPSHOT_PRESERVED'
                    atomic(self.run/'queue-restore.json',dict(status=status,native=identity,writer_pid=writer,current_tracks=len(current['urls']),saved_tracks=len(queue['urls'])if queue else 0,difference=True));return
            if not queue or not queue['urls']or(saved.get('native')==identity and not resume):
                atomic(self.run/'queue-restore.json',dict(status='NATIVE_QUEUE_RETAINED'if current['urls']else'EMPTY_QUEUE_VALID',native=identity,writer_pid=writer));return
            # Restore original URLs (including order and duplicates) using only
            # native mutators. Cache path canonicalization never edits URLs.
            atomic(self.run/'queue-restore.json',dict(status='RESTORING',native=identity,writer_pid=writer,partial_recovery=partial_recovery))
            self.client.call(player,['mixer','volume',0],deadline)
            self.client.call(player,['playlist','shuffle',0],deadline)
            history=[]
            for index in range(len(current['urls']),len(queue['urls'])):
                original_url=queue['urls'][index];parsed=urllib.parse.urlsplit(original_url)
                # Native Commands.pm tests -e before decoding a file URI. During
                # rescan, a file:// item can take its missing-playlist branch and
                # return success without adding a track. Send the real local
                # path to that API; keep the saved original URL unchanged.
                item=urllib.parse.unquote(parsed.path)if parsed.scheme=='file'and parsed.netloc in('','localhost')else original_url
                atomic(self.run/'queue-restore.json',dict(status='RESTORING',phase='NATIVE_APPEND_SENT',native=identity,append_history=history,writer_pid=writer,partial_recovery=partial_recovery,pending_count=index+1))
                self.client.call(player,['playlist','add',item],deadline)
                atomic(self.run/'queue-restore.json',dict(status='RESTORING',phase='NATIVE_APPEND_ACKED_READBACK',native=identity,append_history=history,writer_pid=writer,partial_recovery=partial_recovery,pending_count=index+1))
                actual=self.native_queue(player,deadline);wanted=queue['urls'][:index+1];divergent=next((i for i,(a,b)in enumerate(zip(actual['urls'],wanted))if not same_audio_url(a,b)),None)
                projection=dict(ordinal=index+1,expected_count=index+1,actual_count=len(actual['urls']),first_divergent_index=divergent,expected_URL_sha256=[hashlib.sha256(x.encode()).hexdigest()for x in wanted],actual_URL_sha256=[hashlib.sha256(x.encode()).hexdigest()for x in actual['urls']]);history.append(projection)
                atomic(self.run/'queue-restore.json',dict(status='RESTORING',phase='AFTER_NATIVE_APPEND',native=identity,append_history=history,writer_pid=writer,partial_recovery=partial_recovery))
                need(same_queue_urls(actual['urls'],wanted),'NATIVE_APPEND_EFFECT_MISMATCH')
            self.restore_native_state(player,queue,deadline)
            actual=self.native_queue(player,deadline);need(same_queue_urls(actual['urls'],queue['urls']),'NATIVE_QUEUE_RESTORE_ORDER');atomic(self.run/'queue-state-final.json',dict(wanted={k:queue.get(k)for k in ['index','mode','time','power','shuffle','repeat','volume']},actual={k:actual.get(k)for k in ['index','mode','time','power','shuffle','repeat','volume']},matches={k:actual[k]==queue[k]for k in ['index','mode','power','shuffle','repeat','volume']}));need(all(actual[k]==queue[k]for k in ['index','mode','power','shuffle','repeat','volume']),'NATIVE_QUEUE_RESTORE_STATE');need(not select.select([native['pidfd']],[],[],0)[0],'NATIVE_EXIT_DURING_QUEUE_RESTORE');atomic(self.run/'queue-restore.json',dict(status='RESTORED_WITH_NATIVE_API',tracks=len(actual['urls']),exact_URLs_order_duplicates=actual['urls']==queue['urls'],equivalent_audio_identity_order_duplicates=True,native=identity,append_history=history,writer_pid=writer,partial_recovery=partial_recovery))
    def restore_native_state(self,player,queue,deadline):
        # Native commands enqueue events: their reply is not an observed state.
        # All queries/mutators below inherit the ONE caller deadline.
        started=time.monotonic();observed={};last_record=None;phase='BEGIN'
        commands=dict(index=(['playlist','index'],'_index'),mode=(['mode'],'_mode'),time=(['time'],'_time'),power=(['power'],'_power'),shuffle=(['playlist','shuffle'],'_shuffle'),repeat=(['playlist','repeat'],'_repeat'),volume=(['mixer','volume'],'_volume'))
        wanted={key:queue.get(key)for key in commands}
        def number(value):
            try:return float(value)
            except(TypeError,ValueError):return None
        def equal(actual,expected):
            if actual==expected:return True
            a,b=number(actual),number(expected)
            return a is not None and b is not None and a==b
        def record(error=None,error_kind=None):
            nonlocal last_record
            value=dict(phase=phase,wanted=wanted,actual=dict(observed),error=error,error_kind=error_kind)
            if value!=last_record:
                atomic(self.run/'queue-state-restore.json',dict(value,elapsed_ms=round((time.monotonic()-started)*1000,3),remaining_ms=round((deadline-time.monotonic())*1000,3)));last_record=value
        def wait_fields(fields,label):
            nonlocal phase
            phase=label
            while True:
                need(time.monotonic()<deadline,'NATIVE_QUEUE_STATE_SETTLE_DEADLINE')
                for field,expected in fields.items():
                    command,key=commands[field];observed[field]=self.client.call(player,command+['?'],deadline).get(key)
                record()
                if all(equal(observed[field],value)for field,value in fields.items()):return
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
        def wait_play_ready(label,seek=None,sent=None):
            nonlocal phase
            phase=label
            while True:
                need(time.monotonic()<deadline,'NATIVE_QUEUE_STATE_SETTLE_DEADLINE')
                value=read_rpc(self.cfg,player,['status','-','1','tags:u'],deadline)
                observed.update(mode=value.get('mode'),index=value.get('playlist_cur_index'),time=value.get('time'),waitingToPlay=value.get('waitingToPlay',0),can_seek=value.get('can_seek',0));record()
                ready=value.get('mode')=='play'and value.get('waitingToPlay',0)not in(1,'1',True)and(queue.get('index')is None or equal(value.get('playlist_cur_index'),queue['index']))
                if ready:
                    if seek is None:return value
                    position=number(value.get('time'))
                    # Native seek is approximate; allow its normal 1s precision
                    # plus legitimate clock progress since the single command.
                    if position is not None and seek-1<=position<=seek+1+max(0,time.monotonic()-sent):return
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
        try:
            # Keep silent throughout selection, seek and final pause. This also
            # makes a native index command's automatic power-on inaudible.
            self.client.call(player,['mixer','volume',0],deadline);wait_fields(dict(volume=0),'MUTED')
            for key,command in [('repeat',['playlist','repeat']),('shuffle',['playerpref','shuffle'])]:
                if queue.get(key)is not None:self.client.call(player,command+[queue[key]],deadline);wait_fields({key:queue[key]},'SET_'+key.upper())
            # Power may mask controller mode. If saved power is off, index will
            # turn it on anyway; expose the real controller state until the end.
            self.client.call(player,['power',1],deadline);wait_fields(dict(power=1),'POWER_FOR_SELECTION')
            self.client.call(player,['stop'],deadline);wait_fields(dict(mode='stop'),'STOP_OBSERVED')
            if queue.get('index')is not None:
                self.client.call(player,['playlist','index',queue['index'],0,1],deadline);wait_fields(dict(index=queue['index'],mode='stop'),'INDEX_OBSERVED')
            if queue.get('mode')in('play','pause'):
                self.client.call(player,['play'],deadline);ready=wait_play_ready('PLAY_READY')
                if queue.get('time')is not None:
                    seek=number(queue['time']);need(seek is not None,'NATIVE_QUEUE_SAVED_TIME')
                    if seek>0:need(ready.get('can_seek')in(1,'1',True),'NATIVE_QUEUE_SEEK_UNAVAILABLE')
                    sent=time.monotonic();self.client.call(player,['time',queue['time']],deadline);wait_play_ready('SEEK_OBSERVED',seek,sent)
                if queue['mode']=='pause':
                    self.client.call(player,['pause',1],deadline);wait_fields(dict(mode='pause'),'PAUSE_OBSERVED')
            elif queue.get('time')is not None:
                # A stopped native player reports zero; keep the original
                # command semantics without pretending an unobserved seek.
                self.client.call(player,['time',queue['time']],deadline)
            if queue.get('volume')is not None:
                self.client.call(player,['mixer','volume',queue['volume']],deadline);wait_fields(dict(volume=queue['volume']),'VOLUME_OBSERVED')
            if queue.get('power')is not None:
                if not equal(queue['power'],1):self.client.call(player,['power',queue['power']],deadline)
                wait_fields(dict(power=queue['power']),'POWER_FINAL_OBSERVED')
            wait_fields({key:queue[key]for key in ['index','mode','power','shuffle','repeat','volume']if queue.get(key)is not None},'FINAL_SCALARS_OBSERVED')
        except Exception as error:
            code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and re.fullmatch('[A-Z_]{1,80}',error.args[0])else'NATIVE_QUEUE_STATE_OBSERVATION_UNAVAILABLE'
            record(code,type(error).__name__);raise
    def restore_queue_optional(self,deadline=None):
        try:self.restore_queue(deadline)
        except Exception as error:
            old=load(self.run/'queue-restore.json',{});code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and type(error.args[0])is str and re.fullmatch('[A-Z_]{1,80}',error.args[0])else None
            old.update(status='RESTORE_FAILED_SAVED_QUEUE_RETAINED',error_kind=type(error).__name__,error=code,mutator_outcome_ambiguous=isinstance(error,OSError)and old.get('phase')in('NATIVE_APPEND_SENT','NATIVE_APPEND_ACKED_READBACK'));atomic(self.run/'queue-restore.json',old)
            fd=os.open(self.run/'queue-restore-error.private.log',os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w')as log:log.write(''.join(traceback.format_exception(error,limit=8))[-8192:])
    def snapshot_optional(self):
        try:
            with self.queue_locked(blocking=False):value=self._snapshot()
            atomic(self.run/'snapshot-error.json',dict(error=None));return value
        except BlockingIOError:
            atomic(self.run/'snapshot-error.json',dict(error='QUEUE_RECOVERY_BUSY_PREVIOUS_SNAPSHOT_RETAINED'));return dict(previous_snapshot_retained=True,reason='QUEUE_RECOVERY_BUSY')
        except Exception as error:
            code=snapshot_error_code(error);atomic(self.run/'snapshot-error.json',dict(error=code,error_kind=type(error).__name__));return dict(previous_snapshot_retained=True,error=code,error_kind=type(error).__name__)
    def native_stop(self):
        with native_process(self.cfg,optional=True)as current:
            if current is None:return
            q=subprocess.run(['/usr/bin/rc-service','mediaserver','stop'],capture_output=True,timeout=45)
            (self.run/'native-stop.private.log').write_bytes(q.stdout+q.stderr);os.chmod(self.run/'native-stop.private.log',0o600)
            end=time.monotonic()+35
            while not select.select([current['pidfd']],[],[],0)[0]:
                need(time.monotonic()<end,'NATIVE_STOP_DEADLINE');time.sleep(.1)
            need(q.returncode==0,'NATIVE_SERVICE_STOP_REFUSED')
    def native_start(self):
        self.client.close();q=subprocess.run(['/usr/bin/rc-service','mediaserver','start'],capture_output=True,timeout=100);need(q.returncode==0,'NATIVE_SERVICE_START_REFUSED');end=time.monotonic()+120
        while time.monotonic()<end:
            try:
                with native_process(self.cfg)as current:
                    self.client.call('',['version','?'],end);result=dict(pid=current['pid'],birth=current['birth'])
                self.restore_queue_optional()
                return result
            except(RuntimeError,OSError,ValueError):time.sleep(.5)
        raise RuntimeError('NATIVE_START_DEADLINE')
    def detach(self):
        run=load(self.runtime,{})
        if run.get('boot')!=self.boot:return
        for row in reversed(run.get('mounts',[])):
            p=Path(row['target'])
            try:s=p.stat()
            except FileNotFoundError:continue
            if [s.st_dev,s.st_ino]!=row['cache_identity']:continue
            q=subprocess.run(['umount',str(p)],capture_output=True,timeout=15);need(q.returncode==0,'CACHE_UNMOUNT_BUSY')
            s=p.stat();need([s.st_dev,s.st_ino]==row['original_identity'],'ORIGINAL_FILE_IDENTITY')
        atomic(self.runtime,dict(boot=self.boot,mounts=[],effective_ram=False))
        if os.path.ismount(self.cache):
            q=subprocess.run(['umount',str(self.cache)],capture_output=True,timeout=15);need(q.returncode==0,'CACHE_TMPFS_UNMOUNT_BUSY')
    def prepare(self):
        value=self.state_value();self.detach()
        if not value['requested_ram']:
            atomic(self.runtime,dict(boot=self.boot,mounts=[],version=value['version'],request_id=value.get('request_id')));return
        need(value.get('request_id'),'ON_REQUEST_ID');tx=self.state/'transactions'/value['request_id'];old=load(tx/'activation.json')
        # A previous boot's tmpfs no longer exists. Recreate the empty bounded
        # cache from the persistent intent, even after an interrupted switch.
        self.cache.mkdir(mode=0o755,exist_ok=True)
        if not os.path.ismount(self.cache):
            q=subprocess.run(['mount','-t','tmpfs','-o','nosuid,nodev,noexec,noswap,size='+str(self.cfg.get('cache_bytes',67108864))+',mode=0755','ramcache',str(self.cache)],capture_output=True,timeout=15);need(q.returncode==0,'TMPFS_MOUNT')
        # Startup establishes only the bounded tmpfs. Native streaming waits
        # for its own verified copy; queue prefetch runs after the server is up.
        # A slow/offline NAS must not serialize the entire boot on 17+ copies.
        atomic(tx/'activation.json',dict(boot=self.boot,request_id=value['request_id'],version=value['version'],complete=False))
        atomic(self.runtime,dict(boot=self.boot,mounts=[],cache_ready=True,streaming_gate_required=True,version=value['version'],request_id=value['request_id']))
    def complete(self):
        value=self.state_value();run=load(self.runtime,{})
        if run.get('error'):return
        # A newer intent is for a future boot, not this existing activation.
        if run.get('version')!=value['version']:return
        need(self.effective()==value['requested_ram'],'ACTUAL_MODE_MISMATCH')
        with native_process(self.cfg)as native:
            record=dict(version=value['version'],requested_ram=value['requested_ram'],request_id=value.get('request_id'),boot=self.boot,native_pid=native['pid'],native_birth=native['birth'],complete=True)
        if value.get('request_id'):atomic(self.state/'transactions'/value['request_id']/'activation.json',record)
        atomic(self.last,record)
    def boot_prepare(self):
        self.boot_time('prepare_started')
        try:self.prepare()
        except Exception as error:
            # Original intent and unfinished journal stay intact. Native OFF is
            # a recovery outcome, never an invented successful ON transaction.
            self.detach();atomic(self.runtime,dict(boot=self.boot,mounts=[],error='RAM_PREPARE_FAILED_RETAINED_OFF',error_kind=type(error).__name__,version=self.state_value()['version']))
        self.boot_time('prepare_finished')
        return self.status()
    def apply(self):
        value=self.state_value()
        self.snapshot()
        self.native_stop()
        try:self.prepare()
        finally:self.native_start()
        self.complete();return self.status()
    def initialize_native(self):
        deadline=time.monotonic()+int(self.cfg.get('native_readiness_seconds',600))
        error_path=self.run/'snapshot-error.json'
        while time.monotonic()<deadline:
            try:self.client.call('',['version','?'],deadline);break
            except(RuntimeError,OSError,ValueError):time.sleep(.5)
        else:
            error=load(error_path,{})
            if error.get('error')in(None,'NATIVE_START_DEADLINE'):
                error['error']='NATIVE_START_DEADLINE';atomic(error_path,error)
            return
        # Native answered: clear only the stale readiness error. Snapshot/cache
        # errors stay intact, and cache completion is not a readiness retry.
        error=load(error_path,{})
        if error.get('error')=='NATIVE_START_DEADLINE':
            error['error']=None;atomic(error_path,error)
        self.complete()
        self.boot_time('native_rpc_ready')
        self.restore_queue_optional(time.monotonic()+int(self.cfg.get('native_queue_restore_seconds',120)))
        self.boot_time('queue_restore_finished')
    def serve(self,resume_current=False):
        self.live.reconcile_startup()
        path=self.run/'control.sock'
        if path.exists():need(stat.S_ISSOCK(path.lstat().st_mode),'SOCKET_PATH');path.unlink()
        server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path));os.chown(path,0,self.cfg.get('native_uid',1000));os.chmod(path,0o660);server.listen(4);server.settimeout(1)
        required=self.run/'continuous-required'
        if self.rolling.active()or self.state_value()['requested_ram']:
            required.write_text('Original-byte RAM streaming required\n');os.chmod(required,0o644)
        else:required.unlink(missing_ok=True)
        threading.Thread(target=self.rolling.run,args=(native_process,rpc),daemon=True).start()
        self.boot_time('control_listening')
        # Accept local UI requests immediately: native startup/queue I/O must
        # never block the server that the native HTTP handler is waiting for.
        if not resume_current:threading.Thread(target=self.initialize_native,daemon=True).start()
        while True:
            try:connection,_=server.accept()
            except socket.timeout:continue
            refresh=False
            with connection:
                connection.settimeout(20)
                try:
                    pid,uid,gid=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                    if uid!=0:
                        with native_process(self.cfg)as current:need(uid==self.cfg.get('native_uid',1000)and pid==current['pid'],'NATIVE_PEER_REQUIRED')
                    blob=bytearray()
                    while b'\n'not in blob:
                        part=connection.recv(16385-len(blob));need(part,'REQUEST_EOF');blob.extend(part);need(len(blob)<=16384,'REQUEST_SIZE')
                    need(blob.count(b'\n')==1 and blob.endswith(b'\n'),'REQUEST_FRAME');value=json.loads(blob)
                    if value.get('op')=='status':result=self.status()
                    elif value.get('op')in('ensure','release'):
                        with native_process(self.cfg)as current:
                            result=self.rolling.request(value,current)if value['op']=='ensure'else self.rolling.release(value,current)
                    elif value.get('op')=='set':
                        lock=os.open(self.run/'operation.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
                        handed_off=False
                        try:
                            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            except BlockingIOError:raise RuntimeError('OPERATION_BUSY')from None
                            current=self.state_value();candidate,changed=intent_update(current,value)
                            result=self.status();result['intent_changed']=False
                            retry=self.live.status().get('phase')=='FAILED'
                            if changed or self.effective()!=value['requested_ram']:
                                candidate=dict(candidate,expected_version=current['version'])
                                handed_off=self.live.start(candidate,lock)
                                result=self.status();result['intent_changed']=False
                        finally:
                            if not handed_off:os.close(lock)
                    else:raise RuntimeError('UNKNOWN_OPERATION')
                    reply=dict(ok=True,state=result)
                except BaseException as error:
                    code=error.args[0]if type(error)is RuntimeError and len(error.args)==1 and re.fullmatch('[A-Z_]{1,80}',str(error.args[0]))else type(error).__name__;reply=dict(ok=False,error=code)
                try:connection.sendall((json.dumps(reply,separators=(',',':'))+'\n').encode())
                except OSError:pass # A lost response is resolved by request-id and status.
            if refresh:
                threading.Thread(target=self.snapshot_optional,daemon=True).start()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','serve','snapshot','snapshot-optional','prepare-boot','apply','set']);parser.add_argument('--config',default=CONFIG);parser.add_argument('--requested',choices=['on','off']);parser.add_argument('--version',type=int);parser.add_argument('--request-id');parser.add_argument('--resume-current',action='store_true');args=parser.parse_args();manager=Manager(load(args.config));lock=os.open(manager.run/'operation.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    if args.action=='serve':manager.serve(resume_current=args.resume_current);return
    if args.action=='set':
        os.close(lock)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)as channel:
            channel.settimeout(20);channel.connect(str(manager.run/'control.sock'))
            channel.sendall((json.dumps(dict(op='set',requested_ram=args.requested=='on',expected_version=args.version,request_id=args.request_id))+'\n').encode())
            raw=channel.makefile('rb').readline(16385);reply=json.loads(raw);need(reply.get('ok'),reply.get('error','RAM_OPERATION_REFUSED'))
        print(json.dumps(reply['state'],sort_keys=True));return
    fcntl.flock(lock,fcntl.LOCK_EX)
    try:
        if args.action=='status':value=manager.status()
        elif args.action=='snapshot':value=manager.snapshot()
        elif args.action=='snapshot-optional':value=manager.snapshot_optional()
        elif args.action=='set':value=manager.set(dict(requested_ram=args.requested=='on',expected_version=args.version,request_id=args.request_id))
        elif args.action=='apply':value=manager.apply()
        else:value=manager.boot_prepare()
        print(json.dumps(value,sort_keys=True))
    finally:os.close(lock)
if __name__=='__main__':main()
