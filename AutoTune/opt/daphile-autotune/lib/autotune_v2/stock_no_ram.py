"""Observed stock0509-without-RAM profile. No QEMU/keeper surrogate grant."""
import hashlib,json,os,re,select,stat
from pathlib import Path
import audio_activity_lease as kernel

ROOTS=('/usr/local/lib/daphile-ramplay-20261028a','/mnt/hdd/daphile-ramplay-private-20261028a',
       '/run/daphile-ram-startup','/opt/daphile-ram-startup/validation','/etc/init.d/daphile-ram-owner',
       '/etc/daphile-ramplay-provision','/etc/daphile-ramplay',
       '/var/daphile/ramplay-20261023j','/dev/shm/daphile-ramplay-20261023j')


def need(ok,reason):kernel.need(ok,reason)


def digest(raw):return hashlib.sha256(raw).hexdigest()


def bounded(path,limit):return kernel.read_bounded(str(path),limit)


def token(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def protected(path,expected,*,readonly=False):
    path=Path(path);parent=kernel.directory(str(path.parent));fd=None
    try:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=parent)
        info=os.fstat(fd);kernel.attributes(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0 and info.st_nlink==1 and
             stat.S_IMODE(info.st_mode)==expected['mode'] and info.st_size==expected['bytes'],
             'STOCK_EXACT_SOURCE_DAC')
        need(bool(os.fstatvfs(fd).f_flag&os.ST_RDONLY)is readonly,'STOCK_REAL_SOURCE_MOUNT_FLAGS')
        raw=os.pread(fd,expected['bytes']+1,0)
        need(len(raw)==expected['bytes'] and digest(raw)==expected['sha256'] and
             token(info)==token(os.fstat(fd))==token(os.stat(path.name,dir_fd=parent,follow_symlinks=False)),
             'STOCK_EXACT_SOURCE_CHANGED')
        return dict(dev=info.st_dev,ino=info.st_ino,mount_id=kernel.mount_id(fd),sha256=digest(raw))
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


def pins():
    path=Path(__file__).with_name('STOCK_NATIVE_PINS.json')
    parent=kernel.directory(str(path.parent));fd=None
    try:
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        info=os.fstat(fd);kernel.attributes(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0 and info.st_nlink==1 and
             not info.st_mode&0o022 and info.st_size<65536,'STOCK_SOURCE_PIN_DOCUMENT')
        raw=os.pread(fd,65537,0);need(token(info)==token(os.fstat(fd)),'STOCK_PIN_DOCUMENT_CHANGED')
        return json.loads(raw)
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)


def no_residue():
    need(all(not os.path.lexists(p) for p in ROOTS),'STOCK_RAM_COMPONENT_OR_RESIDUE_PRESENT')
    rows=bounded('/proc/self/mountinfo',1048576).splitlines()
    for row in rows:
        fields=row.split();need(len(fields)>=6,'STOCK_KERNEL_MOUNT_ROW')
        path=fields[4].replace(b'\\040',b' ').decode()
        need(not any(path==r or path.startswith(r+'/') for r in ROOTS),'STOCK_RAM_MOUNT_PRESENT')
    processes=list(Path('/proc').glob('[0-9]*/cmdline'));need(len(processes)<=4096,'STOCK_PROCESS_CENSUS_BOUND')
    for path in processes:
        try:args=bounded(path,262144).split(b'\0')
        except (FileNotFoundError,ProcessLookupError):continue
        need(not any(any(a==os.fsencode(r) or a.startswith(os.fsencode(r+'/')) for r in ROOTS)
                     or a.startswith(b'ramplay_review.') for a in args if a),'STOCK_RAM_PROCESS_PRESENT')
    plugins=Path('/opt/mediaserver/Plugins')
    need(plugins.is_dir() and not plugins.is_symlink(),'STOCK_PLUGIN_ROOT')
    need(not any('ramplay' in p.name.lower() or ('daphile' in p.name.lower() and 'ram' in p.name.lower())
                 for p in plugins.iterdir()),'STOCK_RAM_PLUGIN_PRESENT')
    return dict(ram_runtime_components=0,ram_mounts=0,ram_processes=0,ram_plugins=0)


def process(pid):
    raw=bounded('/proc/'+str(pid)+'/stat',8192);fields=raw.rsplit(b') ',1)[1].split()
    status=bounded('/proc/'+str(pid)+'/status',65536)
    uid=re.findall(rb'(?m)^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$',status)
    gid=re.findall(rb'(?m)^Gid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$',status)
    need(len(uid)==len(gid)==1 and uid[0]==gid[0]==(b'1000',)*4,'STOCK_NATIVE_PROCESS_CREDENTIALS')
    return dict(pid=pid,start=fields[19].decode(),parent=int(fields[1]))


def observe_native_without_ram(mac=None):
    """Read-only; can run before create-once bootstrap, confers no authority."""
    kernel.root();need(kernel.namespaces()==kernel.namespaces(1),'STOCK_REAL_INIT_NAMESPACES')
    p=pins();need(os.uname().release==p['kernel'],'STOCK_OBSERVED_KERNEL_PROFILE')
    boot=bounded('/proc/sys/kernel/random/boot_id',64)
    need(digest(boot.strip())==p['observed_boot_sha256'],'STOCK_GENUINELY_OBSERVED_BOOT_PROFILE')
    images={}
    for path,expected in p['images'].items():
        effective=protected(path,expected);lower=protected('/mnt/root-ro'+path,expected,readonly=True)
        need(effective['mount_id']!=lower['mount_id'],'STOCK_DISTINCT_IMMUTABLE_LOWER')
        images[path]=effective
    need(digest(bounded('/proc/1/exe',1048576))==p['init_sha256'] and
         digest(bounded('/proc/1/cmdline',8192))==p['init_argv_sha256'],'STOCK_ACTUAL_INIT_IDENTITY')
    no_residue()
    pidpath=Path('/run/mediaserver/mediaserver.pid')
    fd=os.open(pidpath,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    watch=None
    try:
        info=os.fstat(fd);kernel.attributes(fd);raw=os.pread(fd,33,0)
        need(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0 and info.st_nlink==1 and
             stat.S_IMODE(info.st_mode)==0o644 and re.fullmatch(rb'[1-9][0-9]{0,9}\n',raw),
             'STOCK_NATIVE_PIDFILE_IMAGE')
        pid=int(raw);watch=os.pidfd_open(pid);actual=process(pid)
        need(actual['parent']==1 and not select.select([watch],[],[],0)[0],'STOCK_ORIGINAL_DAEMON_LINEAGE')
        need(kernel.namespaces(pid)==kernel.namespaces(1),'STOCK_NATIVE_PID_MOUNT_NAMESPACES')
        command=bounded('/proc/'+str(pid)+'/cmdline',8192)
        need(digest(command)==p['native_argv_sha256'],'STOCK_REAL_NATIVE_ARGUMENTS')
        env=bounded('/proc/'+str(pid)+'/environ',262144).split(b'\0')
        need(not any(v.startswith(b'DAPHILE_RAM') or v.startswith(b'DAPHILE_AUDIO_ACTIVITY_') for v in env),
             'STOCK_RAM_OR_BORROWED_ENVIRONMENT')
        native_exe=os.open('/proc/'+str(pid)+'/exe',os.O_RDONLY|os.O_CLOEXEC)
        try:
            need(digest(os.pread(native_exe,1048576,0))==p['images']['/usr/bin/perl']['sha256'] and
                 os.fstat(native_exe).st_ino==images['/usr/bin/perl']['ino'] and
                 os.fstat(native_exe).st_dev==images['/usr/bin/perl']['dev'], 'STOCK_ACTUAL_NATIVE_EXECUTABLE')
        finally:os.close(native_exe)
        listeners=set()
        for table in ('tcp','tcp6'):
            for line in bounded('/proc/net/'+table,1048576).splitlines()[1:]:
                fields=line.split()
                if len(fields)>=10 and fields[1].rsplit(b':',1)[-1]==b'2328' and fields[3]==b'0A':
                    listeners.add(fields[9].decode())
        owned=set()
        for f in Path('/proc/'+str(pid)+'/fd').iterdir():
            try:owned.add(os.readlink(f))
            except FileNotFoundError:pass # An unrelated closed socket is not a witness.
        need(listeners and any('socket:['+i+']' in owned for i in listeners),'STOCK_NATIVE_OWNS_LMS_LISTENER')
        if mac is not None:
            import player
            need(player.rpc(mac,['connected','?']).get('_connected')==1,'STOCK_ACTUAL_PLAYER_CONNECTED')
        need(process(pid)==actual and not select.select([watch],[],[],0)[0] and
             token(info)==token(os.fstat(fd))==token(pidpath.lstat()) and
             bounded('/proc/sys/kernel/random/boot_id',64)==boot,'STOCK_NATIVE_CHANGED_DURING_PROOF')
        no_residue()
        return dict(status='PROVEN_STOCK_WITHOUT_RAM_COMPONENT',source_profile=p['source_profile'],
                    native=actual,source_seal=digest(json.dumps(images,sort_keys=True).encode()),
                    root_boot_sha256=digest(boot.strip()),ram_mutation_authority=False,
                    mode='stock-without-RAM',hardware_qualified=False)
    finally:
        if watch is not None:os.close(watch)
        os.close(fd)


def prove(mac,description):
    kernel.root();d=description
    need(isinstance(d,dict) and d.get('schema')=='daphile-audio-activity-ex-observation-v1' and
         d.get('path')==kernel.PATH and d.get('mode')=='EX','STOCK_ACTUAL_EX_DESCRIPTION')
    lease=kernel.Lease.inherit(os.dup(d['fd']),d['token'],'EX')
    try:
        lease.check();parent=kernel.layout()
        try:
            with kernel.admission(parent):
                group,rows=kernel.members(parent)
                try:
                    need(set(rows)=={d['token']} and rows[d['token']]['mode']=='EX' and
                         digest(kernel.member_raw(group,d['token']))==d['member_sha256'],
                         'STOCK_EX_WITH_FOREIGN_OR_ORPHAN_MEMBER')
                    facts=observe_native_without_ram(mac);lease.check();return facts
                finally:os.close(group)
        finally:os.close(parent)
    finally:lease.close_copy()
