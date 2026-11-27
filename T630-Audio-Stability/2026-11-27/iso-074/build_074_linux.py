"""Build an isolated .74 ISO from the verified .73 live ISO on Linux.

Only the DSD default patch and release metadata are changed relative to .73. No physical disk,
credential, live host or network transfer is used by this builder.
"""
import hashlib
import io
import json
import mmap
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict

BASE=Path('/mnt/c/Build/Daphile-Kernel/iso_candidate_073_empty_queue/Daphile-0.4.73-isolated-live-candidate.iso')
DEST=Path('/mnt/c/Build/Daphile-Kernel/iso_candidate_074_dsd_default')
WORK=Path('/root/darc_iso_074_dsd_default')
PATCH=DEST/'usedop_default_on_073.patch'
BASE_SHA='90dfb2f42e82104e794e52d0650d9539b46c0c23db07e066276c9ddcad3c08eb'
ROOT_SHA='3c41b5176afabc1641c15e265e33fdda8f704664204422360ade2b3ee0aac50a'
ZIP_SHA='c4a393e699a74ca963a66f49a1833748281d6e9fcbfc5485d44afcd836b74d49'
SOURCE_SHA='83f9498a2c2d4151bba3268ac5fc63714f36f16243fd21459fde1118a7160cc2'
CANDIDATE_SHA='d722aa46b602e64bcd1237af2d8dfe67a55f089a187c358ae06d2926b10bfa01'
PATCH_SHA='7164670034dc047a35476d4488f3fa770e78bb7b9d06bdb984690835fda5d0a9'
OLD_FW='fw2611270730';NEW_FW='fw2611270740'
MARKER=b'#\n# START OF DATA - DO NOT MODIFY\n#\n'
PREFIX='usr/local/share/daphile-release/plugins/DaphileCamillaDSP/'
ARCHIVES=('usr/local/share/daphile-release/audio-host/bundles/',
          'usr/local/share/mediaserver-init/plugins/')
APPROVAL='usr/local/share/daphile-release/audio-host/approvals.json'
RELEASE='usr/local/share/daphile-release/release.json'
CHANGED=('backend/native_player.py','backend/peq/__init__.py','install.xml')


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def command(args,log,timeout=1200):
    with (WORK/log).open('xb') as output:
        p=subprocess.run([str(a) for a in args],stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
    if p.returncode:raise RuntimeError('command failed: '+str(args[0])+'; inspect '+str(WORK/log))


def name_and_rest(raw):
    name=bytearray();i=0
    while i<len(raw):
        c=raw[i]
        if c==92:
            i+=1
            if i>=len(raw):raise ValueError('dangling pseudo escape')
            name.append(raw[i]);i+=1
        elif c in (32,9):break
        else:name.append(c);i+=1
    if not name:raise ValueError('empty pseudo path')
    return name.decode('utf8','surrogateescape'),raw[i:].lstrip()


class Pseudo:
    def __init__(self,path):
        self.path=Path(path)
        with self.path.open('rb') as f:prefix=f.read(8*1024*1024)
        at=prefix.find(MARKER)
        if at<0:raise ValueError('pseudo marker unavailable')
        self.start=at+len(MARKER);self.size=self.path.stat().st_size-self.start;self.rows={}
        for raw in prefix[:at].splitlines():
            if not raw or raw.startswith(b'#'):continue
            name,rest=name_and_rest(raw);fields=rest.split();kind=fields[0].decode()
            if name in self.rows or kind not in ('D','R','S','C','B','I','L'):raise ValueError('invalid pseudo entry')
            if kind=='R' and (len(fields)!=8 or int(fields[5])+int(fields[6])>self.size):raise ValueError('invalid extent')
            self.rows[name]={'raw':raw,'rest':rest,'fields':fields,'kind':kind}
    def read(self,name):
        row=self.rows[name];f=row['fields']
        if row['kind']!='R':raise ValueError('regular file required')
        with self.path.open('rb') as src:
            src.seek(self.start+int(f[6]));return src.read(int(f[5]))


def inventory(path):
    p=Pseudo(path);result={};links={}
    with p.path.open('rb') as f,mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as data:
        for name,row in p.rows.items():
            kind=row['kind'];fields=row['fields']
            if kind=='L':
                target,remaining=name_and_rest(row['rest'][2:])
                if remaining:raise ValueError('bad hardlink')
                links[name]=target
            elif kind=='R':
                start=p.start+int(fields[6]);size=int(fields[5]);h=hashlib.sha256()
                for pos in range(start,start+size,1048576):h.update(data[pos:min(pos+1048576,start+size)])
                result[name]=('R',*(x.decode() for x in fields[1:6]),h.hexdigest())
            else:result[name]=(kind,row['rest'].decode('utf8','surrogateescape'))
    def resolve(name,seen=()):
        if name in seen:raise ValueError('hardlink cycle')
        return resolve(links[name],seen+(name,)) if name in links else name
    groups=defaultdict(set)
    for name in links:
        target=resolve(name)
        if target not in result or result[target][0]!='R':raise ValueError('hardlink target missing')
        result[name]=result[target];groups[target].update((name,target))
    return result,{tuple(sorted(v)) for v in groups.values()}


def build_zip(original):
    if hashlib.sha256(original).hexdigest()!=ZIP_SHA:raise RuntimeError('base release archive differs')
    outfile=io.BytesIO();changed=[]
    with zipfile.ZipFile(io.BytesIO(original)) as old,zipfile.ZipFile(outfile,'w') as new:
        if old.testzip() is not None or len(old.namelist())!=51:raise RuntimeError('base ZIP invalid')
        for info in old.infolist():
            data=old.read(info.filename);prefix='DaphileCamillaDSP/'
            if not info.filename.startswith(prefix):raise ValueError('unexpected ZIP root')
            rel=info.filename[len(prefix):]
            if rel=='backend/native_player.py':
                if hashlib.sha256(data).hexdigest()!=SOURCE_SHA:raise RuntimeError('base native_player differs')
                d=WORK/'patch-root/backend';d.mkdir(parents=True,exist_ok=False)
                (d/'native_player.py').write_bytes(data)
                p=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(PATCH)],cwd=d.parent,
                                 stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                if p.returncode:raise RuntimeError('patch did not apply exactly')
                data=(d/'native_player.py').read_bytes()
                if hashlib.sha256(data).hexdigest()!=CANDIDATE_SHA:raise RuntimeError('patch postimage differs')
                changed.append(rel)
            elif rel in ('backend/peq/__init__.py','install.xml'):
                if data.count(b'0.4.73')!=1:raise RuntimeError('unexpected version preimage')
                data=data.replace(b'0.4.73',b'0.4.74');changed.append(rel)
            new.writestr(info,data)
    if set(changed)!=set(CHANGED):raise RuntimeError('release change set differs')
    raw=outfile.getvalue()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        if z.testzip() is not None or len(z.namelist())!=51:raise RuntimeError('new ZIP invalid')
        contents={n.removeprefix('DaphileCamillaDSP/'):z.read(n) for n in z.namelist()}
        if any(b'0.4.73' in v for k,v in contents.items() if k in CHANGED):raise RuntimeError('old version retained')
    return raw,contents


def build_recipe(base):
    defaults=base.read('opt/spac/share/defaults.conf')
    if hashlib.sha256(defaults).hexdigest()!='d0e679cf12914ca44a5d260a7b67e6e699a5e67cf4fd16df227f8126edabc2ea':raise RuntimeError('native defaults differ')
    if re.findall(r'^(?!\s*#).*?\bUSEDOP\b.*$',defaults.decode(),re.M)!=['USEDOP=off']:
        raise RuntimeError('native default does not satisfy candidate parser')
    original=base.read(ARCHIVES[0]+'DaphileCamillaDSP-0.4.73.zip')
    if base.read(ARCHIVES[1]+'DaphileCamillaDSP-0.4.73.zip')!=original:raise RuntimeError('base archives differ')
    new_zip,contents=build_zip(original)
    zip_path=DEST/'DaphileCamillaDSP-0.4.74-candidate.zip'
    with zip_path.open('xb') as f:f.write(new_zip)
    approvals=json.loads(base.read(APPROVAL));entries=approvals['DaphileCamillaDSP']
    if len(entries)!=1 or entries[0]['version']!='0.4.73' or entries[0]['sha256']!=ZIP_SHA:
        raise RuntimeError('base approval record differs')
    entry=copy_entry=dict(entries[0]);entry.update(version='0.4.74',archive='DaphileCamillaDSP-0.4.74.zip',
        sha256=hashlib.sha256(new_zip).hexdigest(),sha1=hashlib.sha1(new_zip).hexdigest(),
        files={name:hashlib.sha256(data).hexdigest() for name,data in contents.items()})
    approvals['DaphileCamillaDSP']=[entry]
    release=json.loads(base.read(RELEASE))
    if release['camilla']!='0.4.73':raise RuntimeError('base release metadata differs')
    release['camilla']='0.4.74'
    updates={PREFIX+name:contents[name] for name in CHANGED}
    updates[APPROVAL]=(json.dumps(approvals,sort_keys=True,indent=2)+'\n').encode()
    updates[RELEASE]=(json.dumps(release,indent=2)+'\n').encode()
    rows={name:row['raw'] for name,row in base.rows.items()}
    for directory in ARCHIVES:
        old=directory+'DaphileCamillaDSP-0.4.73.zip';new=directory+'DaphileCamillaDSP-0.4.74.zip'
        if old not in rows or new in rows:raise RuntimeError('archive path mismatch')
        del rows[old];updates[new]=new_zip
    offset=base.size;payload=[]
    for name,data in sorted(updates.items()):
        previous=name if name in base.rows else name.replace('0.4.74.zip','0.4.73.zip')
        row=base.rows[previous];fields=row['fields']
        if row['kind']!='R':raise RuntimeError('unexpected update node kind')
        rows[name]=(' '.join([name,'R',fields[1].decode(),fields[2].decode(),fields[3].decode(),
                             fields[4].decode(),str(len(data)),str(offset),'0'])).encode()
        offset+=len(data);payload.append(data)
    recipe=WORK/'candidate.pseudo'
    with recipe.open('xb') as out,base.path.open('rb') as original_file:
        out.write(b'\n'.join(rows.values())+b'\n'+MARKER)
        original_file.seek(base.start);shutil.copyfileobj(original_file,out,1048576)
        for data in payload:out.write(data)
    return recipe,updates,zip_path


def replace_exact(path,old,new,count):
    data=path.read_bytes()
    if len(old)!=len(new) or data.count(old)!=count:raise RuntimeError('unexpected boot preimage '+str(path))
    path.write_bytes(data.replace(old,new))


def main():
    print('phase: validate base and patch',flush=True)
    if sha(BASE)!=BASE_SHA or sha(PATCH)!=PATCH_SHA:raise RuntimeError('base ISO or patch digest differs')
    if WORK.exists():raise RuntimeError('candidate work path exists; preserve prior results')
    WORK.mkdir(mode=0o700,parents=True);DEST.mkdir(parents=True,exist_ok=True)
    print('phase: extract exact base ISO',flush=True)
    tree=WORK/'tree';command(['xorriso','-osirrox','on','-indev',BASE,'-extract','/',tree],'extract.log')
    oldboot=tree/'boot'/OLD_FW;rootfs=oldboot/'rootfs'
    if sha(rootfs)!=ROOT_SHA:raise RuntimeError('base rootfs digest differs')
    command(['unsquashfs','-pf',WORK/'base.pseudo','-no-progress',rootfs],'base-unsquashfs.log')
    print('phase: build archive and rootfs recipe',flush=True)
    base=Pseudo(WORK/'base.pseudo');recipe,updates,zip_path=build_recipe(base)
    empty=WORK/'empty';empty.mkdir();built=WORK/'rootfs.073';root=Pseudo(recipe).rows['/']['fields']
    print('phase: build squashfs',flush=True)
    command(['mksquashfs',empty,built,'-pf',recipe,'-noappend','-no-progress','-processors','4','-mem','512M',
             '-comp','zstd','-Xcompression-level','6','-reproducible','-mkfs-time',root[1].decode(),
             '-root-time',root[1].decode(),'-root-mode',root[2].decode(),'-root-uid',root[3].decode(),
             '-root-gid',root[4].decode()],'mksquashfs.log')
    print('phase: verify full squashfs contents and metadata',flush=True)
    command(['unsquashfs','-pf',WORK/'readback.pseudo','-no-progress',built],'readback-unsquashfs.log')
    expected,expected_links=inventory(recipe);actual,actual_links=inventory(WORK/'readback.pseudo')
    if expected!=actual or expected_links!=actual_links:raise RuntimeError('full rootfs readback differs')
    old,old_links=inventory(WORK/'base.pseudo')
    changed={name for name in old.keys()|actual.keys() if old.get(name)!=actual.get(name)}
    wanted=set(updates)|{directory+'DaphileCamillaDSP-0.4.73.zip' for directory in ARCHIVES}
    if changed!=wanted or old_links!=actual_links:
        raise RuntimeError('rootfs change set or hardlinks differ: '+repr(sorted(changed^wanted)[:20]))
    newboot=tree/'boot'/NEW_FW
    if newboot.exists():raise RuntimeError('new boot ID already exists')
    oldboot.rename(newboot);shutil.copyfile(built,newboot/'rootfs')
    for file in (newboot/'menu.item',tree/'isolinux/isolinux.cfg'):
        data=file.read_bytes()
        if OLD_FW.encode() not in data:raise RuntimeError('boot reference not present')
        file.write_bytes(data.replace(OLD_FW.encode(),NEW_FW.encode()))
    for relative,count in (('efi/boot/bootx64.efi',3),('efi/boot/bootia32.efi',3),('efi2505251550.img',6)):
        replace_exact(tree/relative,OLD_FW.encode(),NEW_FW.encode(),count)
    sums=[]
    for name in ('menu.item','initrd','rootfs','kernel'):
        with (newboot/name).open('rb') as f:digest=hashlib.file_digest(f,'md5').hexdigest()
        sums.append(digest+'  '+NEW_FW+'/'+name)
    (newboot/'md5sum.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    iso=DEST/'Daphile-0.4.74-isolated-live-candidate.iso'
    if iso.exists():raise RuntimeError('candidate ISO exists; preserve it')
    print('phase: create and verify hybrid ISO',flush=True)
    command(['xorriso','-as','mkisofs','-V','DaphileLIVE','-uid','0','-gid','0','-dir-mode','0755','-file-mode','0644',
        '-isohybrid-mbr','--interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:'+str(BASE),
        '-partition_cyl_align','off','-partition_offset','0','--mbr-force-bootable','-iso_mbr_part_type','0x00',
        '-c','isolinux/boot.cat','-b','isolinux/isolinux.bin','-no-emul-boot','-boot-load-size','4','-boot-info-table',
        '-eltorito-alt-boot','-e','efi2505251550.img','-no-emul-boot','-boot-load-size','2880','-isohybrid-gpt-basdat',
        '-o',iso,tree],'iso-build.log')
    reread=WORK/'iso-rootfs-readback'
    command(['xorriso','-osirrox','on','-indev',iso,'-extract','/boot/'+NEW_FW+'/rootfs',reread],'iso-readback.log')
    if sha(reread)!=sha(built):raise RuntimeError('ISO rootfs readback differs')
    command(['xorriso','-indev',iso,'-report_el_torito','plain','-report_system_area','plain'],'iso-boot-catalog.log')
    catalog=(WORK/'iso-boot-catalog.log').read_text(errors='replace')
    if 'BIOS' not in catalog or 'UEFI' not in catalog:raise RuntimeError('BIOS/UEFI catalog not verified')
    if sha(BASE)!=BASE_SHA:raise RuntimeError('base ISO changed')
    report={'status':'BUILT_KVM_PENDING','version':'0.4.74','scope':'LMS empty STOP queue and omitted native USEDOP default; no latency claim',
      'base_iso_sha256':BASE_SHA,'iso':str(iso),'iso_sha256':sha(iso),'iso_bytes':iso.stat().st_size,
      'base_rootfs_sha256':ROOT_SHA,'new_rootfs_sha256':sha(built),'firmware':NEW_FW,
      'zip':str(zip_path),'zip_sha256':sha(zip_path),'native_player_sha256':CANDIDATE_SHA,'patch_sha256':PATCH_SHA,
      'filesystem_readback':'ALL_NODES_AND_HARDLINKS_IDENTICAL_TO_RECIPE',
      'rootfs_changed_entries':sorted(changed),'source_iso_unchanged':True,
      'BIOS_UEFI_catalog':True,'physical_qualification':'NOT_PERFORMED'}
    (DEST/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
