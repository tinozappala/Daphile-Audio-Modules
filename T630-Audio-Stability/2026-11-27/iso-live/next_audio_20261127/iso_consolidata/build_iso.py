"""Build an isolated, non-final stock-based Daphile ISO for .71 or .72.

Inputs are the public stock ISO and public RC6 application code, plus the
specified known LMS packages. No installed machine filesystem or DATA is read.
"""
import argparse
import datetime
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
WORK=HERE/'work'
STOCK=ROOT/'tmp/t620-stock-20261121/daphile-25.05-b251550-x86_64-rt.iso'
STOCK_SHA='623dd475a6cb3f8528bbdb7d2234dfb1fbd582becdd1458d97ec845eeb6b0c15'
STOCK_ROOT=ROOT/'tmp/t620-stock-20261121/image/boot/fw2505251550/rootfs'
STOCK_ROOT_SHA='ba0a378646f25a6d23b7af1ac1ded8c8720ac0c599f6543e3a6a454c0d67816b'
RC6_ROOT=WORK/'rc6-rootfs'
RC6_ROOT_SHA='8e101c92c9161f5975f2e4c8ae22a85303270e965c0d11a8bbd60309a2b6253e'
BIN=ROOT/'vendor/review-kit/daphile-v5-ssh/tools/usr/bin'
ENV=dict(os.environ,PATH=str(BIN)+os.pathsep+os.environ['PATH'])
sys.path.insert(0,str(ROOT/'vendor/review-kit/daphile-v5-ssh/tools/python'))
sys.path.insert(0,str(ROOT/'ram_continuous/support'))
import pycdlib
from pseudo_fs import name_and_rest,MARKER

VERSIONS={
 '0.4.71':(ROOT/'next_audio_20261126/pcm_commit_lab/release_071/operator_stock/forward/generated/DaphileCamillaDSP-0.4.71.zip',
           'c6add62c058907f27c40de0f6af2f599ad61ade645dd02a5c5ee390baf944847','fw2611270710'),
 '0.4.72':(ROOT/'next_audio_20261127/generation_input_lab/operator_stock/forward/generated/DaphileCamillaDSP-0.4.72.zip',
           'acab2f0797c8bc871a2454a411ba68a506a61fd986730889ed00962d0c968b52','fw2611270720')}
RME=Path('C:/Build/Daphile-Next-Audio/05-CamillaDSP-Integration/DATC-LAB-20261124/RME-LMS903-COMPAT-20261124/NATIVE-LMS903-TRIAL-REPO/DaphileRME-0.5.23.zip')
RME_SHA='2c778d58b5547282c6558650faa7e78c09b1b05170dff245f1a8c07099eea601'
RC6_SOURCE=ROOT/'iso_maintenance_20261117/github_autotune_b4f_final_20261118'
EPOCH=int(datetime.datetime(2026,11,27,tzinfo=datetime.timezone.utc).timestamp())


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(tool,args,log,timeout=1200):
    args=[str(x).replace('\\','/') for x in args]
    if tool=='xorriso':args=[re.sub(r'([A-Za-z]):/',lambda m:'/cygdrive/'+m[1].lower()+'/',x) for x in args]
    with log.open('wb') as f:
        p=subprocess.run([str(BIN/(tool+'.exe')),*args],stdout=f,stderr=subprocess.STDOUT,env=ENV,timeout=timeout)
    if p.returncode:raise RuntimeError(tool+' failed; inspect '+str(log))


class Pseudo:
    def __init__(self,path):
        self.path=Path(path)
        with self.path.open('rb') as f:prefix=f.read(8*1024*1024)
        index=prefix.find(MARKER)
        if index<0:raise RuntimeError('pseudo header missing or too large')
        self.start=index+len(MARKER);self.size=self.path.stat().st_size-self.start;self.rows={}
        for raw in prefix[:index].splitlines():
            if not raw or raw.startswith(b'#'):continue
            name,rest=name_and_rest(raw)
            if name in self.rows:raise RuntimeError('duplicate pseudo path')
            fields=rest.split();kind=fields[0].decode()
            if kind not in ('D','R','S','C','B','I','L'):raise RuntimeError('unknown pseudo entry')
            self.rows[name]={'raw':raw,'rest':rest,'fields':fields,'kind':kind}
    def read(self,name):
        row=self.rows[name];fields=row['fields']
        if row['kind']!='R' or len(fields)!=8:raise RuntimeError('regular pseudo source required: '+name)
        size,offset=int(fields[5]),int(fields[6])
        if offset<0 or size<0 or offset+size>self.size:raise RuntimeError('pseudo bounds')
        with self.path.open('rb') as f:f.seek(self.start+offset);return f.read(size)


def validate_zip(path,expected,name,version,count):
    if sha(path)!=expected:raise RuntimeError('unexpected release archive: '+str(path))
    raw=Path(path).read_bytes();prefix=name+'/'
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=count or len(set(names))!=count or z.testzip() is not None:raise RuntimeError('invalid ZIP')
        if any(not n.startswith(prefix) or '..' in n.split('/') or '\\' in n or n.endswith('/') for n in names):
            raise RuntimeError('unsafe ZIP member')
        if any(re.search(r'(^|/)(\.ssh|authorized_keys|\.env|credentials?|passwords?|id_rsa)(/|$)',n,re.I) for n in names):
            raise RuntimeError('unexpected credential-like archive member')
        xml=ET.fromstring(z.read(prefix+'install.xml'));app=xml.find('targetApplication')
        if (xml.findtext('version'),app.findtext('minVersion'),app.findtext('maxVersion'))!=(version,'9.0.3','9.0.3'):
            raise RuntimeError('LMS release compatibility mismatch')
        files={n[len(prefix):]:hashlib.sha256(z.read(n)).hexdigest() for n in names}
        entry={'version':version,'archive':name+'-'+version+'.zip','sha256':expected,
               'sha1':hashlib.sha1(raw).hexdigest(),'minTarget':'9.0.3','maxTarget':'9.0.3','files':files}
        contents={n[len(prefix):]:z.read(n) for n in names}
    return raw,entry,contents


def overlay_sources(base,extra,rows,updates):
    for name,row in extra.rows.items():
        if name=='/' or row['kind']=='D' and name in rows:continue
        if row['kind']=='R':
            f=row['fields'];updates[name]=(extra.read(name),int(f[2],8),int(f[3]),int(f[4]))
        elif row['kind']=='S':rows[name]=row['raw']
        elif row['kind']=='D':rows[name]=row['raw']
        else:raise RuntimeError('unsupported selected RC6 component type: '+name)


def patch_lms(base,work):
    paths=('opt/mediaserver/slimserver.pl','opt/mediaserver/Slim/Player/Song.pm',
           'opt/mediaserver/Slim/Player/StreamingController.pm')
    for name in paths:
        target=work/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(base.read(name))
    patches=RC6_SOURCE/'RAM/patches-lms'
    for name in ('slimserver.pl.patch','Song.pm.patch','StreamingController.pm.patch'):
        p=subprocess.run(['C:/msys64/usr/bin/patch.exe','--batch','--fuzz=0','-p1','-i',str(patches/name)],
                         cwd=work,capture_output=True,text=True,timeout=30)
        if p.returncode:raise RuntimeError('stock LMS patch failed: '+name+' '+p.stdout[-500:])
    return {name:(work/name).read_bytes() for name in paths}


def boot_delta(base,name,changes):
    raw=base.read(name)
    if hashlib.sha256(raw).hexdigest()!=changes['input_sha256']:raise RuntimeError('boot script preimage differs: '+name)
    lines=raw.decode().splitlines(keepends=True)
    for edit in sorted(changes['edits'],key=lambda v:v['start_line'],reverse=True):
        i=edit['start_line']-1;lines[i:i+edit['remove_lines']]=[edit['insert_utf8']]
    result=''.join(lines).encode()
    if hashlib.sha256(result).hexdigest()!=changes['output_sha256']:raise RuntimeError('boot script patch postimage differs: '+name)
    return result


def routed_buffer_defaults(raw):
    """Select 40/4 only for a currently routed card; .72 maps it to 80/8.

    The later native per-card volume.conf remains authoritative.  Unrouted
    cards retain the vendor defaults and neither preferences nor .72 change.
    """
    if raw.count(b'BUFFERTIME=100\n')!=1 or raw.count(b'PERIODCOUNT=3\n')!=1:
        raise RuntimeError('unexpected stock player defaults')
    addition=b'''
# Only a fully verified, bound route receives the tested PCM default.
# The qualified router converts 40/4 to 80/8 on that route; a later
# per-device volume.conf retains its normal priority.
if [ -n "${cardnum:-}" ] && [ -f /mnt/hdd/system/daphile-audio-host/native/route.json ] &&
   /usr/bin/python3.13 -I -B /usr/local/libexec/daphile-route-default "${cardnum}" "${devnum:-}" 2>/dev/null; then
    BUFFERTIME=40
    PERIODCOUNT=4
fi
'''
    return raw.rstrip(b'\n')+b'\n'+addition


def patch_bootstrap(boot):
    old=b"    if sys.argv[1]=='modules':modules()"
    new=(b"    if sys.argv[1]=='modules':\n"
         b"        h=Path('/mnt/hdd/system/daphile-audio-host'); marker=h/'.owned'\n"
         b"        if h.is_symlink() or (h.exists() and not h.is_dir()): raise RuntimeError('AUDIO_HOST_PATH_REFUSED')\n"
         b"        fresh=not h.exists() or not any(h.iterdir())\n"
         b"        if not fresh and (not marker.is_file() or marker.is_symlink() or marker.read_bytes()!=b'DAPHILE_NATIVE_AUDIO_HOST_V1'):\n"
         b"            raise RuntimeError('UNOWNED_AUDIO_HOST_PRESERVED')\n"
         b"        if not fresh:\n"
         b"            a=h/'modules'; s=a/'DaphileCamillaDSP'; d=s/'data'; p=d/'audio-buffer.json'\n"
         b"            if any(x.is_symlink() or not x.is_dir() for x in (a,s,d)) or p.is_symlink() or not p.is_file():\n"
         b"                raise RuntimeError('EXISTING_AUDIO_BUFFER_NOT_VERIFIED')\n"
         b"            info=p.stat()\n"
         b"            if info.st_uid!=0 or info.st_mode&0o077 or info.st_size>1024 or json.loads(p.read_bytes())!={'chunksize':2048,'target_chunks':2}:\n"
         b"                raise RuntimeError('EXISTING_AUDIO_BUFFER_NOT_2048_2')\n"
         b"        modules()\n"
         b"        if fresh:\n"
         b"            a=h/'modules'; s=a/'DaphileCamillaDSP'; d=s/'data'\n"
         b"            for directory in (a,s,d):\n"
         b"                if directory.is_symlink(): raise RuntimeError('AUDIO_STATE_SYMLINK_REFUSED')\n"
         b"                directory.mkdir(mode=0o700,exist_ok=True); os.chmod(directory,0o700)\n"
         b"            p=d/'audio-buffer.json'\n"
         b"            if not p.exists(): publish(p,b'{\\\"chunksize\\\":2048,\\\"target_chunks\\\":2}\\n')")
    if boot.count(old)!=1:raise RuntimeError('unexpected public bootstrap source')
    result=boot.replace(old,new);compile(result,'bootstrap_release.py','exec')
    return result


def protect_disk_automount(name,data):
    if name=='opt/daphile/bin/localmount':
        old=b'\t\t\tadd|addinfo|addbackup)\n'
        new=old+b'\t\t\t\t[[ -e /etc/daphile-live-no-physical-mount ]] && continue\n'
        if data.count(old)!=1:raise RuntimeError('unknown localmount contract')
        return data.replace(old,new)
    if name in ('etc/init.d/lvm','etc/init.d/mdraid'):
        for operation in (b'start',b'stop'):
            old=b'\n'+operation+b'() {'
            if data.count(old)!=1:raise RuntimeError('unknown disk service contract: '+name)
            data=data.replace(old,old+b'\n    [ -e /etc/daphile-live-no-physical-mount ] && return 0')
        return data
    raise ValueError('unexpected disk guard target')


def diagnostic_start(name,data):
    commands={
      'etc/init.d/daphile-modules':b'/usr/bin/python3.13 -I -B /opt/daphile-ram/bootstrap_release.py modules',
      'etc/init.d/daphile-audio-host':b'/usr/bin/python3 /mnt/hdd/system/daphile-audio-host/code/hostctl.py start --root /mnt/hdd/system/daphile-audio-host'}
    old=b'    '+commands[name]+b'\n    eend $?'
    logfile=b'/run/daphile-live-start-'+(b'modules' if name.endswith('modules') else b'audio-host')+b'.log'
    new=(b'    '+commands[name]+b' > '+logfile+b' 2>&1\n'
         b'    local result=$?\n'
         b'    if [ "$result" -ne 0 ]; then\n'
         b'        tail -n 16 '+logfile+b' | logger -t daphile-live-start\n')
    if name.endswith('audio-host'):
        new+=(b'        [ ! -s /mnt/hdd/system/daphile-audio-host/host.log ] || '
              b'tail -n 16 /mnt/hdd/system/daphile-audio-host/host.log | logger -t daphile-live-start\n')
    new+=b'    fi\n    eend $result'
    if data.count(old)!=1:raise RuntimeError('unknown startup diagnostics contract: '+name)
    return data.replace(old,new)


def compose_pseudo(version,work):
    if sha(STOCK_ROOT)!=STOCK_ROOT_SHA or sha(RC6_ROOT)!=RC6_ROOT_SHA:raise RuntimeError('source rootfs digest differs')
    stock=work/'stock.pseudo';run('unsquashfs',['-pf',stock,'-no-progress',STOCK_ROOT],work/'stock-extract.log')
    components=work/'components.pseudo'
    paths=['opt/daphile-ram','opt/daphile-autotune','opt/mediaserver/Daphile/Ram',
           'usr/local/share/daphile-release/audio-host/code','etc/init.d/daphile-modules',
           'etc/init.d/daphile-identity','etc/init.d/daphile-audio-host','etc/init.d/daphile-autotune-gui',
           'etc/init.d/ramcache-prepare','etc/init.d/ramcache-control',
           'etc/runlevels/default/daphile-modules','etc/runlevels/boot/daphile-identity',
           'etc/runlevels/default/daphile-audio-host','etc/runlevels/default/daphile-autotune-gui',
           'etc/runlevels/default/ramcache-prepare','etc/runlevels/default/ramcache-control',
           'var/www/localhost/cgi-bin/RamNavigation','var/www/localhost/cgi-bin/RamReadStatus',
           'var/www/localhost/cgi-bin/AutoTune','usr/local/bin/dat']
    run('unsquashfs',['-pf',components,'-no-progress',RC6_ROOT,*paths],work/'components-extract.log')
    base=Pseudo(stock);selected=Pseudo(components);rows={n:r['raw'] for n,r in base.rows.items()};updates={}
    overlay_sources(base,selected,rows,updates)
    for name in ('etc/init.d/daphile-modules','etc/init.d/daphile-audio-host'):
        data,mode,uid,gid=updates[name];updates[name]=(diagnostic_start(name,data),mode,uid,gid)
    for name,data in patch_lms(base,work/'lms-patch').items():
        f=base.rows[name]['fields'];updates[name]=(data,int(f[2],8),int(f[3]),int(f[4]))
    changes=json.loads((RC6_SOURCE/'RAM/platform/boot-changes.json').read_text())
    for change in changes:
        if change['file']=='etc/init.d/lighttpd':continue
        name=change['file'];f=base.rows[name]['fields']
        updates[name]=(boot_delta(base,name,change),int(f[2],8),int(f[3]),int(f[4]))
    # Public stock root login is retained locally; remote password logins are disabled.
    ssh=base.read('etc/ssh/sshd_config')
    if ssh.count(b'PermitRootLogin yes')!=1:raise RuntimeError('unknown stock SSH configuration')
    ssh=ssh.replace(b'PermitRootLogin yes',b'PermitRootLogin prohibit-password')+b'\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n'
    updates['etc/ssh/sshd_config']=(ssh,0o644,0,0)
    name='opt/spac/share/defaults.conf';f=base.rows[name]['fields']
    updates[name]=(routed_buffer_defaults(base.read(name)),int(f[2],8),int(f[3]),int(f[4]))
    updates['usr/local/libexec/daphile-route-default']=((HERE/'routed_default_probe.py').read_bytes(),0o755,0,0)
    # The trial is deliberately live-only: it must not activate, mount or
    # replay journals on any connected persistent disk.
    updates['etc/daphile-live-no-physical-mount']=(b'Generic live trial: persistent disks are not mounted.\n',0o644,0,0)
    for name in ('opt/daphile/bin/localmount','etc/init.d/lvm','etc/init.d/mdraid'):
        f=base.rows[name]['fields'];updates[name]=(protect_disk_automount(name,base.read(name)),int(f[2],8),int(f[3]),int(f[4]))
    # /etc overrides same-named system udev rules and avoids event activation.
    for name in ('64-md-raid-assembly.rules','69-dm-lvm.rules','69-md-clustered-confirm-device.rules'):
        updates['etc/udev/rules.d/'+name]=(b'# Disabled for isolated live trial; no persistent disk activation.\n',0o644,0,0)
    archives=[('DaphileCamillaDSP',version,*VERSIONS[version][:2],51),('DaphileRME','0.5.23',RME,RME_SHA,32)]
    approvals={}
    for name,ver,path,expected,count in archives:
        raw,entry,content=validate_zip(path,expected,name,ver,count);approvals[name]=[entry]
        for directory in ('usr/local/share/daphile-release/audio-host/bundles/',
                          'usr/local/share/mediaserver-init/plugins/'):
            updates[directory+entry['archive']]=(raw,0o644,0,0)
        for rel,data in content.items():
            updates['usr/local/share/daphile-release/plugins/'+name+'/'+rel]=(data,0o644,0,0)
    updates['usr/local/share/daphile-release/audio-host/approvals.json']=((json.dumps(approvals,sort_keys=True,indent=2)+'\n').encode(),0o644,0,0)
    updates['usr/local/share/daphile-release/release.json']=((json.dumps({'kind':'isolated-trial',
        'camilla':version,'rme':'0.5.23','base':'25.05-b251550','private_data_included':False},indent=2)+'\n').encode(),0o644,0,0)
    # Do not treat an existing unowned DATA directory as a blank factory host.
    name='opt/daphile-ram/bootstrap_release.py';boot=updates[name][0]
    updates[name]=(patch_bootstrap(boot),0o755,0,0)
    existing={n for n,row in base.rows.items() if row['kind']=='L'}
    linked_targets={name_and_rest(base.rows[n]['rest'][2:])[0] for n in existing}
    if set(updates)&linked_targets:raise RuntimeError('refusing to replace a stock hardlink target')
    offset=base.size;payload=[]
    for name,(data,mode,uid,gid) in sorted(updates.items()):
        if ' ' in name or '\\' in name or name.startswith('/') or '..' in name.split('/'):
            raise RuntimeError('invalid new pseudo path')
        parent=Path(name).parent
        for directory in reversed([parent,*parent.parents]):
            text=directory.as_posix()
            if text=='.' or text in rows:continue
            rows[text]=f'{text} D {EPOCH} 755 0 0'.encode()
        rows[name]=f'{name} R {EPOCH} {mode:o} {uid} {gid} {len(data)} {offset} 0'.encode()
        payload.append(data);offset+=len(data)
    recipe=work/'composed.pseudo'
    with recipe.open('xb') as f:
        f.write(b'\n'.join(rows.values())+b'\n'+MARKER)
        with stock.open('rb') as original:original.seek(base.start);shutil.copyfileobj(original,f,1024*1024)
        for data in payload:f.write(data)
    return recipe,rows,updates,approvals


def patch_efi(data,firmware,expected):
    old=b'fw2505251550';new=firmware.encode()
    if len(new)!=len(old) or data.count(old)!=expected:raise RuntimeError('unexpected EFI boot identifier count')
    return data.replace(old,new)


def patch_efi_options(data,expected):
    old=b'splash quiet panic=1 console=tty2 vt.global_cursor_default=0 i915.enable_fbc=0 ${daphile_opts}'
    new=b'quiet panic=1 console=tty2 vt.global_cursor_default=0 i915.enable_fbc=0 raid=noautodetect'
    if len(new)>len(old) or data.count(old)!=expected:raise RuntimeError('unexpected EFI boot option layout')
    return data.replace(old,new+b' '*(len(old)-len(new)))


def build(version):
    if sha(STOCK)!=STOCK_SHA:raise RuntimeError('stock ISO digest differs')
    if not STOCK_ROOT.is_file() or not RC6_ROOT.is_file():raise RuntimeError('verified source rootfs missing')
    work=Path(tempfile.mkdtemp(prefix='trial-'+version.replace('.','')+'-',dir=WORK))
    print(json.dumps({'phase':'build','workspace':str(work),'version':version}),flush=True)
    recipe,rows,updates,approvals=compose_pseudo(version,work)
    empty=work/'empty';empty.mkdir();rootfs=work/'rootfs'
    root=Pseudo(recipe).rows['/']['fields']
    run('mksquashfs',[empty,rootfs,'-pf',recipe,'-noappend','-no-progress','-processors','2','-mem','256M',
        '-comp','zstd','-Xcompression-level','6','-reproducible','-mkfs-time',EPOCH,
        '-root-time',root[1].decode(),'-root-mode',root[2].decode(),'-root-uid',root[3].decode(),'-root-gid',root[4].decode()],work/'mksquashfs.log')
    check=work/'readback.pseudo';run('unsquashfs',['-pf',check,'-no-progress',rootfs],work/'readback.log')
    from verify_pseudo import compare
    full_readback=compare(recipe,check)
    actual=Pseudo(check)
    if set(actual.rows)!=set(rows):raise RuntimeError('rootfs node set differs after readback')
    for name,(data,mode,uid,gid) in updates.items():
        r=actual.rows[name];f=r['fields']
        if r['kind']!='R' or actual.read(name)!=data or (int(f[2],8),int(f[3]),int(f[4]))!=(mode,uid,gid):
            raise RuntimeError('rootfs changed file mismatch: '+name)
    firmware=VERSIONS[version][2];tree=work/'iso-tree';tree.mkdir()
    reader=pycdlib.PyCdlib();reader.open(str(STOCK))
    try:
        for folder,dirs,files in reader.walk(rr_path='/'):
            dest=tree/folder.lstrip('/');dest.mkdir(parents=True,exist_ok=True)
            for name in files:reader.get_file_from_iso(str(dest/name),rr_path=folder.rstrip('/')+'/'+name)
    finally:reader.close()
    oldboot=tree/'boot/fw2505251550';newboot=tree/'boot'/firmware;newboot.mkdir()
    shutil.copyfile(oldboot/'kernel',newboot/'kernel')
    from protect_live_initrd import protect
    (newboot/'initrd').write_bytes(protect((oldboot/'initrd').read_bytes()))
    shutil.copyfile(rootfs,newboot/'rootfs')
    menu=(oldboot/'menu.item').read_bytes().replace(b'fw2505251550',firmware.encode());(newboot/'menu.item').write_bytes(menu)
    sums=[]
    for name in ('menu.item','initrd','rootfs','kernel'):
        with (newboot/name).open('rb') as f:md5=hashlib.file_digest(f,'md5').hexdigest()
        sums.append(f'{md5}  {firmware}/{name}')
    (newboot/'md5sum.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    config=tree/'isolinux/isolinux.cfg';data=config.read_bytes().replace(b'fw2505251550',firmware.encode())
    old=b'${daphile_opts} live';new=b'raid=noautodetect live'
    if data.count(old)!=1:raise RuntimeError('unknown BIOS boot option layout')
    config.write_bytes(data.replace(old,new))
    for filename,count,option_count in (('efi/boot/bootx64.efi',3,1),('efi/boot/bootia32.efi',3,1),('efi2505251550.img',6,2)):
        p=tree/filename;p.write_bytes(patch_efi_options(patch_efi(p.read_bytes(),firmware,count),option_count))
    iso=HERE/('Daphile-'+version+'-isolated-live-v2-trial.iso')
    if iso.exists():raise RuntimeError('trial ISO already exists; retain previous evidence')
    run('xorriso',['-as','mkisofs','-V','DaphileLIVE','-uid','0','-gid','0','-dir-mode','0755','-file-mode','0644',
        '-isohybrid-mbr','--interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:'+STOCK.as_posix(),
        '-partition_cyl_align','off','-partition_offset','0','--mbr-force-bootable','-iso_mbr_part_type','0x00',
        '-c','isolinux/boot.cat','-b','isolinux/isolinux.bin','-no-emul-boot','-boot-load-size','4','-boot-info-table',
        '-eltorito-alt-boot','-e','efi2505251550.img','-no-emul-boot','-boot-load-size','2880','-isohybrid-gpt-basdat',
        '-o',iso,tree],work/'xorriso.log')
    reread=work/'iso-rootfs-readback';reader=pycdlib.PyCdlib();reader.open(str(iso))
    try:
        with reread.open('xb') as f:reader.get_file_from_iso_fp(f,rr_path='/boot/'+firmware+'/rootfs')
    finally:reader.close()
    if sha(reread)!=sha(rootfs):raise RuntimeError('ISO rootfs readback differs')
    run('xorriso',['-indev',iso,'-report_el_torito','plain','-report_system_area','plain'],work/'boot-catalog.log')
    catalog=(work/'boot-catalog.log').read_text(errors='replace')
    if 'BIOS' not in catalog or 'UEFI' not in catalog:raise RuntimeError('boot catalog missing BIOS or UEFI')
    report={'status':'TRIAL_BUILT_BOOT_TEST_PENDING','iso':str(iso),'sha256':sha(iso),'bytes':iso.stat().st_size,
        'version':version,'release_sha256':VERSIONS[version][1],'firmware':firmware,'base_iso_sha256':STOCK_SHA,
        'source_rootfs_sha256':STOCK_ROOT_SHA,'rootfs_sha256':sha(rootfs),'nodes':len(rows),
        'full_rootfs_readback':full_readback,'changed_entries_verified':len(updates),
        'stock_firmware_retained':True,'BIOS_UEFI_catalog':True,
        'candidate_initrd_sha256':sha(newboot/'initrd'),
        'live_disk_guards':'iso9660 boot read-only, DATA tmpfs, no local mounts or LVM/MD autoactivation',
        'private_DATA_or_music_input':False,'remote_password_auth_disabled':True,'physical_qualification':'NOT_PERFORMED'}
    (work/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--version',choices=tuple(VERSIONS),required=True)
    args=p.parse_args();WORK.mkdir(parents=True,exist_ok=True);build(args.version)
