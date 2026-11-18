"""Short, process-bound RT ownership shared with the native audio policy.

Only the RT priority block defers. USB power protection remains native.
A dead worker, a changed DAC or native setting immediately invalidates the
claim; a missed renewal expires in <=60 seconds. No setting is written here.
"""
import hashlib,json,os,re,stat,subprocess,time,uuid
from pathlib import Path
import physical_audio

FIELDS={'schema','token','kind','boot_id','pid','start','audio_pid','audio_start','usb_path','vendor','product','native_sha256','player_preferences','player_sha256','issued','expires'}
NATIVE_POLICY='/usr/local/sbin/daphile-audio-policy'
ACK_TIMEOUT=8


def ticks(pid,proc=Path('/proc')):
    try:return (proc/str(pid)/'stat').read_text().rsplit(') ',1)[1].split()[19]
    except (OSError,IndexError):return None


def valid(record,environment):
    if not isinstance(record,dict) or set(record)!=FIELDS or type(record.get('schema')) is not int or record['schema']!=1 or record.get('kind') not in ('measurement','verified-policy'):return False
    if not isinstance(record.get('token'),str) or not re.fullmatch('[a-f0-9]{32}',record['token']):return False
    if any(not isinstance(record.get(k),str) or not re.fullmatch('[a-f0-9]{64}',record[k]) for k in ('native_sha256','player_sha256')):return False
    if any(type(record.get(k)) is not int or record[k]<=1 for k in ('pid','audio_pid')):return False
    if any(not isinstance(record.get(k),str) or not re.fullmatch('[0-9]+',record[k]) for k in ('start','audio_start')):return False
    if any(not isinstance(record.get(k),str) or not re.fullmatch('[a-f0-9]{4}',record[k]) for k in ('vendor','product')):return False
    if not isinstance(record.get('usb_path'),str) or not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*',record['usb_path']):return False
    try:
        if str(uuid.UUID(record['boot_id']))!=record['boot_id']:return False
    except (ValueError,KeyError,AttributeError,TypeError):return False
    if any(type(record.get(k)) not in (int,float) for k in ('issued','expires')):return False
    now=environment['monotonic']
    if not record['issued']<=now<record['expires']<=record['issued']+60:return False
    return all(record.get(k)==environment.get(k) for k in ('boot_id','pid','start','audio_pid','audio_start','usb_path','vendor','product','native_sha256','player_preferences','player_sha256'))


class _USBLease:
    def __init__(self,hardware,target,kind='measurement',root=Path('/run/daphile-audio-policy')):
        if os.name!='posix' or os.geteuid()!=0:raise RuntimeError('Root-owned Linux RT coordination required')
        if kind not in ('measurement','verified-policy'):raise ValueError('Known RT owner kind required')
        self.root=Path(root);self.path=self.root/'autotune-rt.json';self.token=uuid.uuid4().hex;self.kind=kind
        if self.root.is_symlink() or not self.root.is_dir() or self.root.stat().st_uid!=0 or stat.S_IMODE(self.root.stat().st_mode)&0o022:raise RuntimeError('Private native policy directory required')
        self.hardware=hardware;self.target=target;self.record=None;self.acknowledged=False

    def environment(self):
        h=self.hardware;usb=h['dac']['usb'];audio=h['dac']['owner_pid'];preferences=Path(self.target['path'])
        if (h['dac'].get('status')!='verified-active' or preferences.is_symlink()
                or h['dac'].get('identity')!=self.target.get('identity')
                or len(physical_audio.matches(self.target.get('identity'),h))!=1):raise RuntimeError('Verified native audio identity required')
        return dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),pid=os.getpid(),start=ticks(os.getpid()),audio_pid=audio,audio_start=ticks(audio),
            usb_path=usb['path'],vendor=usb['idVendor'],product=usb['idProduct'],native_sha256=hashlib.sha256(Path('/var/daphile/conf/config.sys').read_bytes()).hexdigest(),
            player_preferences=str(preferences),player_sha256=hashlib.sha256(preferences.read_bytes()).hexdigest(),monotonic=time.monotonic())

    def renew(self,hardware=None):
        from autotune_v2.common_activity import require_exclusive
        require_exclusive()
        import fcntl
        try:from autotune_v2.reference_state import atomic
        except ImportError:from .revision2.reference_state import atomic
        if hardware is not None:self.hardware=hardware
        lock=self.root/'lock'
        if lock.is_symlink() or not lock.is_file():raise RuntimeError('Native RT coordination lock unavailable')
        with lock.open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            env=self.environment();now=env.pop('monotonic')
            old=None
            if self.path.is_symlink():raise RuntimeError('Indirect RT claim refused')
            if self.path.exists():
                if self.path.stat().st_uid!=0 or stat.S_IMODE(self.path.stat().st_mode)!=0o600:raise RuntimeError('Unknown RT claim permissions')
                old=json.loads(self.path.read_text())
                if old.get('token')!=self.token:
                    # Only a known expired/dead claim can be superseded.
                    if set(old)!=FIELDS or old.get('schema')!=1:raise RuntimeError('Unknown RT claim retained')
                    alive=old.get('boot_id')==env['boot_id'] and ticks(old.get('pid'))==old.get('start')
                    if alive and old.get('expires',now+1)>now:raise RuntimeError('Another live RT owner retained')
            record=dict(env,schema=1,token=self.token,kind=self.kind,issued=now,expires=now+60)
            if not valid(record,dict(env,monotonic=now)):raise RuntimeError('Incomplete RT claim refused')
            if self.record and any(record[k]!=self.record[k] for k in ('boot_id','native_sha256','player_preferences','player_sha256','usb_path','vendor','product')):raise RuntimeError('Native context changed; RT claim not renewed')
            context=tuple(key for key in FIELDS if key not in ('token','issued','expires'))
            needs_ack=(not self.acknowledged or old!=self.record or not self.record
                       or self.record['expires']<=now or any(record[key]!=self.record[key] for key in context))
            if needs_ack:
                if self.record:self.token=uuid.uuid4().hex;record['token']=self.token
                self.acknowledged=False
            atomic(self.path,record);self.record=record
        # The native executable owns the same mutex. Call it only after our
        # publication lock is closed, and only for a new/expired/changed claim.
        # Its exact token ACK proves consumption rather than mere JSON save.
        if needs_ack:
            try:
                result=subprocess.run([NATIVE_POLICY,'--json'],capture_output=True,timeout=ACK_TIMEOUT,check=False)
            except subprocess.TimeoutExpired as error:raise RuntimeError('Native RT receiver acknowledgment timed out') from error
            if result.returncode!=0 or len(result.stdout)>1024*1024:raise RuntimeError('Native RT receiver acknowledgment failed')
            try:ack=json.loads(result.stdout)
            except (ValueError,UnicodeError) as error:raise RuntimeError('Native RT receiver returned no valid JSON acknowledgment') from error
            if (not isinstance(ack,dict) or ack.get('rt_coordinator')!=self.kind
                    or ack.get('rt_coordinator_token')!=self.token or ack.get('errors')!=[]):
                raise RuntimeError('Native RT receiver did not accept this exact claim')
            with lock.open('a') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX)
                env=self.environment()
                if (self.path.is_symlink() or not self.path.exists() or json.loads(self.path.read_text())!=record
                        or not valid(record,env)):raise RuntimeError('RT claim context changed before acknowledgment')
                self.acknowledged=True
        return dict(status='coordinated',expires_in_seconds=max(0,self.record['expires']-time.monotonic()),
                    native_receiver_acknowledged=True,usb_power_policy_deferred=False)

    def close(self):
        from autotune_v2.common_activity import require_exclusive
        require_exclusive()
        import fcntl
        lock=self.root/'lock'
        if lock.is_symlink():raise RuntimeError('Indirect RT lock refused')
        with lock.open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            if self.path.exists():
                if self.path.is_symlink():raise RuntimeError('Indirect RT claim preserved')
                current=json.loads(self.path.read_text())
                if current.get('token')==self.token:self.path.unlink()
        self.acknowledged=False


class _PhysicalLease:
    """Native USB RT deferral has no policy file for other physical outputs."""
    def __init__(self,hardware,target):
        self.hardware=hardware;self.target=target;self.closed=False
        self._check(hardware)

    def _check(self,hardware):
        identity=self.target.get('identity')
        if (self.closed or physical_audio.is_usb(identity) or not physical_audio.identified(identity)
                or len(physical_audio.matches(identity,hardware))!=1
                or hardware.get('dac',{}).get('status')!='verified-active'
                or hardware['dac'].get('identity')!=identity):
            raise RuntimeError('Physical ALSA coordination target absent or changed')

    def renew(self,hardware=None):
        from autotune_v2.common_activity import require_exclusive
        require_exclusive()
        if hardware is None:
            from detection import live_scan as scan
            hardware=scan()
        self.hardware=hardware
        self._check(self.hardware)
        return dict(status='not-required',reason='native-rt-policy-is-usb-only',identity_verified=True,policy_writes=0)

    def close(self):self.closed=True


def Lease(hardware,target,kind='measurement',root=Path('/run/daphile-audio-policy')):
    if kind not in ('measurement','verified-policy'):raise ValueError('Known RT owner kind required')
    if physical_audio.is_usb(target.get('identity')):return _USBLease(hardware,target,kind,root)
    return _PhysicalLease(hardware,target)


PERL_HELPER=r'''
# Deferral applies solely to a live, root-owned AutoTune RT claim. Native
# USB suspend/LPM protection above continues to run on every invocation.
my $autotune_rt_token='';
sub autotune_rt_owner {
 my $path="$run/autotune-rt.json";
 return '' unless -f $path && !-l $path;
 my @meta=stat($path);return '' unless @meta && $meta[4]==0 && ($meta[2]&0777)==0600 && $meta[7]<16384;
 my $s;eval {$s=decode_json(readval($path));};return '' if $@ || ref($s) ne 'HASH';
 my @keys=qw(schema token kind boot_id pid start audio_pid audio_start usb_path vendor product native_sha256 player_preferences player_sha256 issued expires);
 return '' unless join(',',sort keys %$s) eq join(',',sort @keys) && $s->{schema}==1;
 return '' unless $s->{token}=~/^[a-f0-9]{32}$/ && $s->{kind}=~/^(measurement|verified-policy)$/;
 return '' unless @dacs==1 && $s->{usb_path} eq $dacs[0]{usb_path} && $s->{vendor} eq $dacs[0]{vendor} && $s->{product} eq $dacs[0]{product};
 return '' unless $s->{native_sha256}=~/^[a-f0-9]{64}$/ && $s->{player_sha256}=~/^[a-f0-9]{64}$/;
 return '' unless length($s->{player_preferences})<=4096 && $s->{player_preferences}=~m{\A/mnt/hdd/system/spac/(?:(?!\.{1,2}/)[^/\x00-\x1f\x7f]+/)+volume\.conf\z};
 return '' unless $s->{boot_id} eq readval("$proc/sys/kernel/random/boot_id");
 for my $which (['pid','start'],['audio_pid','audio_start']) {
  my ($pid,$start)=@{$s}{@$which};return '' unless $pid=~/^[1-9][0-9]+$/ && defined $start && $start=~/^[0-9]+$/;
  my $v=readval("$proc/$pid/stat");return '' unless $v=~/^\d+ \(.*\) (.*)$/;
  my @f=split /\s+/,$1;return '' unless @f>19 && $f[19] eq $start;
 }
 return '' unless $s->{issued}=~/^[0-9]+(?:\.[0-9]+)?$/ && $s->{expires}=~/^[0-9]+(?:\.[0-9]+)?$/;
 my $now=Time::HiRes::clock_gettime(Time::HiRes::CLOCK_MONOTONIC());
 return '' unless $s->{issued}<=$now && $now<$s->{expires} && $s->{expires}<=$s->{issued}+60;
 return '' unless Digest::SHA::sha256_hex(readval_raw("$test/var/daphile/conf/config.sys")) eq $s->{native_sha256};
 return '' unless Digest::SHA::sha256_hex(readval_raw("$test$s->{player_preferences}")) eq $s->{player_sha256};
 $autotune_rt_token=$s->{token};return $s->{kind};
}
sub readval_raw { my ($p)=@_;open my $f,'<',$p or return '';binmode $f;local $/;return <$f>; }
my $autotune_rt_owner=autotune_rt_owner();
'''


def patch_native(text):
    before="if(!$ports_only && $rt eq 'on' && @dacs){"
    if text.count(before)!=1:raise ValueError('Native RT source contract changed')
    text=text.replace('use Fcntl qw(:flock);','use Fcntl qw(:flock);\nuse Digest::SHA ();\nuse Time::HiRes ();')
    text=text.replace(before,PERL_HELPER+"\nif(!$ports_only && $rt eq 'on' && @dacs && !$autotune_rt_owner){")
    text=text.replace('rt_policy=>$rt,','rt_policy=>$rt,rt_coordinator=>$autotune_rt_owner,')
    text=text.replace('rt_coordinator=>$autotune_rt_owner,','rt_coordinator=>$autotune_rt_owner,rt_coordinator_token=>$autotune_rt_token,')
    text=text.replace("my $boot = grep { $_ eq '--boot' } @ARGV;", "my $json = grep { $_ eq '--json' } @ARGV;\nmy $boot = grep { $_ eq '--boot' } @ARGV;")
    text=text.replace('flock($lock,LOCK_EX|LOCK_NB) or exit 0;', 'flock($lock,LOCK_EX|($json?0:LOCK_NB)) or exit 0;')
    text=text.replace(r'print encode_json($report),"\n" if $test;', r'print encode_json($report),"\n" if $test || $json;')
    return text

__release_signature__ = 'Tino Zappala'
