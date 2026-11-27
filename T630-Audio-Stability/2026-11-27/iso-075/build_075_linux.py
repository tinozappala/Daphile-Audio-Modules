"""Build a dual-mode .75 ISO from the exact .74, preserving module .74.

The rootfs changes only /version. The initrd remains ISO-only unless an
explicit installed marker and same-device UUID binding pass validation.
No host disk or live machine is accessed; output is new and isolated.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from build_074_linux import Pseudo, inventory, replace_exact
from initrd_dualmode import patch_initrd

BASE=Path('/mnt/c/Build/Daphile-Kernel/iso_candidate_074_dsd_default/Daphile-0.4.74-isolated-live-candidate.iso')
DEST=Path('/mnt/c/Build/Daphile-Kernel/iso_candidate_075_live_install')
WORK=Path('/root/darc_iso_075_live_install')
BASE_SHA='171c0fba440e98b934bf4157374c5bbbd12f0961a085d270754bf441be581e4d'
ROOT_SHA='bdbee7c25096e0e3345892fd486c04f91ee392dadbb20e9d4c7370e6ec37d80e'
ZIP_SHA='c2ef7df33a91df6b5888e13e150c784946f43e575876c922346e806c848fe508'
PLAYER_SHA='d722aa46b602e64bcd1237af2d8dfe67a55f089a187c358ae06d2926b10bfa01'
INITRD_SHA='9558faf314ddd4d5f7b1130c248ccd9b57f298667bb8cc65cec199302091dfd6'
OLD_FW='fw2611270740';NEW_FW='fw2611270750';MARKER=b'#\n# START OF DATA - DO NOT MODIFY\n#\n'
PREFIX='usr/local/share/daphile-release/'


def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def command(args,log,timeout=1200):
    with (WORK/log).open('xb') as output:
        p=subprocess.run([str(a) for a in args],stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
    if p.returncode:raise RuntimeError('command failed: '+str(args[0])+'; inspect '+str(WORK/log))


def recipe(base):
    if base.read('version')!=b'fw2505251550':raise RuntimeError('unexpected original /version')
    if hashlib.sha256(base.read(PREFIX+'plugins/DaphileCamillaDSP/backend/native_player.py')).hexdigest()!=PLAYER_SHA:
        raise RuntimeError('unexpected .74 native player')
    z=base.read(PREFIX+'audio-host/bundles/DaphileCamillaDSP-0.4.74.zip')
    if hashlib.sha256(z).hexdigest()!=ZIP_SHA or base.read('usr/local/share/mediaserver-init/plugins/DaphileCamillaDSP-0.4.74.zip')!=z:
        raise RuntimeError('unexpected .74 archive')
    if json.loads(base.read(PREFIX+'release.json'))['camilla']!='0.4.74':raise RuntimeError('unexpected audio version')
    data=NEW_FW.encode('ascii');row=base.rows['version'];fields=row['fields']
    if row['kind']!='R':raise RuntimeError('version is not a regular file')
    rows={name:node['raw'] for name,node in base.rows.items()}
    rows['version']=(' '.join(['version','R',fields[1].decode(),fields[2].decode(),fields[3].decode(),
                              fields[4].decode(),str(len(data)),str(base.size),'0'])).encode()
    path=WORK/'candidate.pseudo'
    with path.open('xb') as out,base.path.open('rb') as source:
        out.write(b'\n'.join(rows.values())+b'\n'+MARKER)
        source.seek(base.start);shutil.copyfileobj(source,out,1048576);out.write(data)
    return path


def main():
    print('phase: verify exact .74 ISO',flush=True)
    if sha(BASE)!=BASE_SHA:raise RuntimeError('source ISO differs')
    if WORK.exists() or DEST.exists():raise RuntimeError('retain previous candidate paths')
    WORK.mkdir(parents=True,mode=0o700);DEST.mkdir(parents=True)
    tree=WORK/'tree';command(['xorriso','-osirrox','on','-indev',BASE,'-extract','/',tree],'extract.log')
    oldboot=tree/'boot'/OLD_FW
    if sha(oldboot/'rootfs')!=ROOT_SHA or sha(oldboot/'initrd')!=INITRD_SHA:
        raise RuntimeError('source rootfs or protected initrd differs')
    print('phase: build rootfs with aligned /version',flush=True)
    command(['unsquashfs','-pf',WORK/'base.pseudo','-no-progress',oldboot/'rootfs'],'base-unsquashfs.log')
    base=Pseudo(WORK/'base.pseudo');source_recipe=recipe(base)
    empty=WORK/'empty';empty.mkdir();rootfs=WORK/'rootfs.075';root=Pseudo(source_recipe).rows['/']['fields']
    command(['mksquashfs',empty,rootfs,'-pf',source_recipe,'-noappend','-no-progress','-processors','4','-mem','512M',
             '-comp','zstd','-Xcompression-level','6','-reproducible','-mkfs-time',root[1].decode(),
             '-root-time',root[1].decode(),'-root-mode',root[2].decode(),'-root-uid',root[3].decode(),
             '-root-gid',root[4].decode()],'mksquashfs.log')
    print('phase: verify every rootfs node and hardlink',flush=True)
    command(['unsquashfs','-pf',WORK/'readback.pseudo','-no-progress',rootfs],'readback-unsquashfs.log')
    expected,expected_links=inventory(source_recipe);actual,actual_links=inventory(WORK/'readback.pseudo')
    if expected!=actual or expected_links!=actual_links:raise RuntimeError('rootfs readback differs')
    original,old_links=inventory(WORK/'base.pseudo')
    changes={n for n in original.keys()|actual.keys() if original.get(n)!=actual.get(n)}
    if changes!={'version'} or old_links!=actual_links:raise RuntimeError('unexpected rootfs changes: '+repr(changes))
    after=Pseudo(WORK/'readback.pseudo')
    if after.read('version')!=NEW_FW.encode():raise RuntimeError('version readback differs')
    newboot=tree/'boot'/NEW_FW
    if newboot.exists():raise RuntimeError('new boot ID already exists')
    oldboot.rename(newboot);shutil.copyfile(rootfs,newboot/'rootfs')
    initrd=patch_initrd((newboot/'initrd').read_bytes())
    new_initrd_sha=hashlib.sha256(initrd).hexdigest()
    (newboot/'initrd').write_bytes(initrd)
    for file in (newboot/'menu.item',tree/'isolinux/isolinux.cfg'):
        data=file.read_bytes()
        if OLD_FW.encode() not in data:raise RuntimeError('boot reference not present')
        data=data.replace(OLD_FW.encode(),NEW_FW.encode())
        if file.name=='menu.item':
            if data.count(b' ${daphile_opts}')!=1:raise RuntimeError('unexpected installed kernel options')
            data=data.replace(b' ${daphile_opts}',b' daphile_installed=1 ${daphile_opts}')
        file.write_bytes(data)
    for relative,count in (('efi/boot/bootx64.efi',3),('efi/boot/bootia32.efi',3),('efi2505251550.img',6)):
        replace_exact(tree/relative,OLD_FW.encode(),NEW_FW.encode(),count)
    sums=[]
    for name in ('menu.item','initrd','rootfs','kernel'):
        with (newboot/name).open('rb') as f:digest=hashlib.file_digest(f,'md5').hexdigest()
        sums.append(digest+'  '+NEW_FW+'/'+name)
    (newboot/'md5sum.txt').write_text('\n'.join(sums)+'\n',encoding='ascii')
    iso=DEST/'Daphile-0.4.75-live-install-candidate.iso'
    print('phase: create and reread hybrid ISO',flush=True)
    command(['xorriso','-as','mkisofs','-V','DaphileLIVE','-uid','0','-gid','0','-dir-mode','0755','-file-mode','0644',
        '-isohybrid-mbr','--interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:'+str(BASE),
        '-partition_cyl_align','off','-partition_offset','0','--mbr-force-bootable','-iso_mbr_part_type','0x00',
        '-c','isolinux/boot.cat','-b','isolinux/isolinux.bin','-no-emul-boot','-boot-load-size','4','-boot-info-table',
        '-eltorito-alt-boot','-e','efi2505251550.img','-no-emul-boot','-boot-load-size','2880','-isohybrid-gpt-basdat',
        '-o',iso,tree],'iso-build.log')
    for name,expect in (('rootfs',sha(rootfs)),('initrd',new_initrd_sha)):
        reread=WORK/('iso-'+name+'-readback')
        command(['xorriso','-osirrox','on','-indev',iso,'-extract','/boot/'+NEW_FW+'/'+name,reread],name+'-iso-readback.log')
        if sha(reread)!=expect:raise RuntimeError(name+' ISO readback differs')
    command(['xorriso','-indev',iso,'-report_el_torito','plain','-report_system_area','plain'],'iso-boot-catalog.log')
    catalog=(WORK/'iso-boot-catalog.log').read_text(errors='replace')
    if 'BIOS' not in catalog or 'UEFI' not in catalog or sha(BASE)!=BASE_SHA:raise RuntimeError('boot catalog or source changed')
    report={'status':'BUILT_TESTS_PENDING','version':'0.4.75','module_version':'0.4.74','base_iso_sha256':BASE_SHA,
      'iso':str(iso),'iso_sha256':sha(iso),'iso_bytes':iso.stat().st_size,
      'rootfs_sha256':sha(rootfs),'firmware':NEW_FW,'initrd_sha256':new_initrd_sha,
      'module_zip_sha256':ZIP_SHA,'native_player_sha256':PLAYER_SHA,'rootfs_changed_entries':sorted(changes),
      'filesystem_readback':'ALL_NODES_AND_HARDLINKS_IDENTICAL_TO_RECIPE',
      'boot_selection':'default ISO read-only and tmpfs; installed only with explicit marker and verified same-device UUIDs',
      'installed_menu_marker':'daphile_installed=1','source_iso_unchanged':True,'BIOS_UEFI_catalog':True,
      'physical_qualification':'NOT_PERFORMED'}
    (DEST/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
