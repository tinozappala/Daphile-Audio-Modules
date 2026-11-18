"""Compose one targeted maintenance ISO from the verified public RC5.

No live root filesystem or private DATA is a build input. SquashFS pseudo files
preserve Linux inodes, modes, ownership and links on the Windows builder.
"""
from pathlib import Path
import base64,datetime,hashlib,json,os,posixpath,re,shutil,sys,zipfile
import xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent;REPO=HERE.parent
sys.path.insert(0,str(REPO/'ram_continuous'))
import build_iso as tools
SOURCE=REPO/'DAPHILE_RAM_A7_RC5_20261117/Daphile-fw2610191845-RAM-A7-Camilla042-RME057-AutoTuneB4e-RC5-20261117.iso'
SOURCE_SHA='863e583550f354bd88244ede28beae3fee43bf379189afcb8c3cee5715547fcc'
WORK=HERE/'build';OUT=REPO/'DAPHILE_MANUTENZIONE_RC6_20261117'
EPOCH=int(datetime.datetime(2026,11,18,tzinfo=datetime.timezone.utc).timestamp())
ISO=OUT/'Daphile-RAM-A7-Camilla047-RME057-AutoTuneB4e-RC6-20261118.iso'
tools.WORK=WORK;tools.SOURCE=SOURCE;tools.SOURCE_SHA=SOURCE_SHA
digest=tools.digest;command=tools.command;extract=tools.extract

def entry_release():
    entry=json.loads((REPO/'camilla_current/APPROVAL.json').read_text())
    archive=REPO/'camilla_current'/entry['archive'];data=archive.read_bytes()
    assert digest(archive)==entry['sha256']
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        for relative,sha in entry['files'].items():assert hashlib.sha256(z.read('DaphileCamillaDSP/'+relative)).hexdigest()==sha
        xml=ET.fromstring(z.read('DaphileCamillaDSP/install.xml'))
    app=xml.find('targetApplication')
    assert xml.findtext('version')==entry['version']=='0.4.47'
    entry.update(name='DaphileCamillaDSP',bytes=len(data),sha1=hashlib.sha1(data).hexdigest(),minTarget=app.findtext('minVersion'),maxTarget=app.findtext('maxVersion'))
    return entry,data

def main():
    WORK.mkdir(exist_ok=True);OUT.mkdir(exist_ok=True)
    assert digest(SOURCE)==SOURCE_SHA,'Public RC5 changed'
    report=json.loads((SOURCE.parent/'ISO_RC5_BUILD_REPORT.json').read_text())
    # Reuse verified public caches here; a clean reconstruction needs only the
    # RC5 ISO and metadata, not a second distributed copy of its full rootfs.
    base=REPO/'ram_continuous/build-work/rc5/actual.pseudo'
    root_source=REPO/'ram_continuous/build-work/rc5/rootfs'
    if not root_source.is_file():
        root_source=WORK/'rc5-base-rootfs'
        if not root_source.exists():extract(SOURCE,'/boot/fw2610191845/rootfs',root_source)
    assert digest(root_source)==report['rootfs_sha256']
    if not base.is_file():
        base=WORK/'rc5-base.pseudo'
        if not base.exists():command('unsquashfs',['-pf',base,root_source],'rc5-base-extract.log')
    before=tools.inventory(base)
    assert before==json.loads((SOURCE.parent/'RC5_FULL_FILESYSTEM_TABLE.json').read_text()),'RC5 pseudo/table mismatch'
    header=tools.read_header(base);expected=dict(before);rows={n:r['raw'] for n,r in header['entries'].items()}
    def read(name):
        row=header['entries'][name];assert row['kind']=='R'
        with base.open('rb') as f:f.seek(header['start']+row['offset']);return f.read(row['bytes'])
    overlay={};links={};removed=[]
    def put(name,data,mode=None):
        mode=before.get(name,{}).get('mode',0o644) if mode is None else mode
        if name in rows and name in before and before[name].get('sha256')==hashlib.sha256(data).hexdigest() and before[name]['mode']==mode:return
        overlay[name]=(data,mode)
    entry,data=entry_release();factory='usr/local/share/daphile-release/audio-host/'
    approvals=json.loads(read(factory+'approvals.json'));approvals['DaphileCamillaDSP']=[entry]
    put(factory+'approvals.json',(json.dumps(approvals,sort_keys=True,indent=2)+'\n').encode())
    for directory in (factory+'bundles/','usr/local/share/mediaserver-init/plugins/'):
        for name in list(rows):
            if name.startswith(directory+'DaphileCamillaDSP-') and name.endswith('.zip'):
                removed.append(name);rows.pop(name);expected.pop(name)
        put(directory+entry['archive'],data)
    with zipfile.ZipFile(REPO/'camilla_current'/entry['archive']) as z:
        for relative in entry['files']:
            put('usr/local/share/daphile-release/plugins/DaphileCamillaDSP/'+relative,z.read('DaphileCamillaDSP/'+relative))
    for local,inside in [('bootstrap_release.py','opt/daphile-ram/bootstrap_release.py'),('consolidate_camilla.py','opt/daphile-ram/consolidate_camilla.py')]:
        content=(HERE/local).read_bytes();compile(content,local,'exec');put(inside,content)
    # Additional component deltas are admitted only after their native checks.
    selection=HERE/'COMPONENTI_VERIFICATI.json'
    components=json.loads(selection.read_text())
    if set(components) != {'kernel', 'perl', 'python', 'curl', 'openssh', 'lighttpd', 'ffmpeg'}:
        raise ValueError('La selezione RC6 richiede i sette componenti previsti.')
    for component,value in components.items():
        assert value['native_verification']=='PASS'
        # A Python patch release carries one coherent stdlib, while installed
        # third-party packages remain the verified RC5 inputs.
        for tree in value.get('replace_trees',[]):
            assert (component=='python' and tree=='usr/lib/python3.13') or (component=='kernel' and tree=='usr/lib/modules/6.12.111-rt21-daphile-v6') or (component=='perl' and tree=='usr/lib64/perl5/5.40')
            keep=tree+'/site-packages' if component=='python' else None
            for name in list(rows):
                if (name==tree and component=='kernel' or name.startswith(tree+'/')) and (keep is None or name!=keep and not name.startswith(keep+'/')):
                    rows.pop(name);expected.pop(name);removed.append(name)
        for name in value.get('remove_files',[]):
            assert component=='ffmpeg' and name=='usr/lib64/libavformat.so.60.16.100'
            assert name in rows
            rows.pop(name);expected.pop(name);removed.append(name)
        for item in value['files']+value.get('license_files',[]):
            payload=(HERE/item['source']).read_bytes();assert hashlib.sha256(payload).hexdigest()==item['sha256']
            put(item['destination'],payload,item['mode'])
        for item in value.get('symlinks',[]):
            name=item['destination'];target=item['target']
            assert component in ('perl','ffmpeg') and name.startswith('usr/') and not target.startswith('/')
            assert not posixpath.normpath(posixpath.join(posixpath.dirname(name),target)).startswith('../')
            assert not any(c.isspace() for c in name+target) and '\\' not in name+target
            prior=before.get(name,{})
            if name in rows and prior.get('kind')=='S' and base64.b64decode(prior['symlink_target_base64']).decode()==target:continue
            links[name]=target
    release=json.loads(read('usr/local/share/daphile-release/release.json'))
    release.update(release='RAM-A7-Camilla047-RME057-AutoTuneB4e-RC6-20261118',Camilla_module='0.4.47',upstream_deltas={k:v['version'] for k,v in components.items()})
    put('usr/local/share/daphile-release/release.json',(json.dumps(release,indent=2)+'\n').encode())
    vendor_core=read('etc/daphile-custom-release').decode().splitlines()[0]
    versions=', '.join(k+' '+v['version'] for k,v in components.items())
    put('etc/daphile-custom-release',(vendor_core+'\nDaphile RAM A7 RC6 20261118; Camilla module 0.4.47 / engine 4.1.3; RME 0.5.7; AutoTune B4e.\nSelective maintenance: '+versions+'. Qualification is recorded in the release report.\n').encode())
    put('usr/local/share/daphile-release/MANUTENZIONE_IT.md',(HERE/'LEGGIMI_ISO_IT.md').read_bytes())
    offset=header['data_bytes'];extra=[]
    for name,(content,mode) in sorted(overlay.items()):
        for parent in reversed([Path(name).parent,*Path(name).parent.parents]):
            folder=parent.as_posix()
            if folder=='.' or folder in rows:continue
            rows[folder]=f'{folder} D {EPOCH} 755 0 0'.encode();expected[folder]=dict(kind='D',mtime=EPOCH,mode=0o755,uid=0,gid=0)
        prior=before.get(name,{});uid=prior.get('uid',0);gid=prior.get('gid',0)
        rows[name]=f'{name} R {EPOCH} {mode:o} {uid} {gid} {len(content)} {offset} 0'.encode()
        expected[name]=dict(kind='R',mtime=EPOCH,mode=mode,uid=uid,gid=gid,bytes=len(content),data_flags=0,sha256=hashlib.sha256(content).hexdigest())
        offset+=len(content);extra.append(content)
    for name,target in links.items():
        assert name not in overlay
        for parent in reversed([Path(name).parent,*Path(name).parent.parents]):
            folder=parent.as_posix()
            if folder=='.' or folder in rows:continue
            rows[folder]=f'{folder} D {EPOCH} 755 0 0'.encode();expected[folder]=dict(kind='D',mtime=EPOCH,mode=0o755,uid=0,gid=0)
        rows[name]=f'{name} S {EPOCH} 777 0 0 {target}'.encode()
        expected[name]=dict(kind='S',mtime=EPOCH,mode=0o777,uid=0,gid=0,symlink_target_base64=base64.b64encode(target.encode()).decode())
    recipe=WORK/'maintenance.pseudo'
    with recipe.open('wb') as f:
        f.write(b'\n'.join(rows.values())+b'\n'+tools.MARKER)
        with base.open('rb') as original:original.seek(header['start']);shutil.copyfileobj(original,f,1048576)
        for content in extra:f.write(content)
    empty=WORK/'empty';empty.mkdir(exist_ok=True);root=WORK/'rootfs';root.unlink(missing_ok=True);r=header['entries']['/']
    print(json.dumps(dict(phase='ROOTFS_BUILD',changed=len(overlay),removed_nodes=len(removed))),flush=True)
    command('mksquashfs',[empty,root,'-pf',recipe,'-noappend','-no-progress','-processors','2','-mem','256M','-comp','xz','-b','1048576','-Xdict-size','16384','-reproducible','-mkfs-time',EPOCH,'-root-time',r['mtime'],'-root-mode',format(r['mode'],'o'),'-root-uid',r['uid'],'-root-gid',r['gid']],'mksquashfs.log',1200)
    back=WORK/'readback.pseudo';back.unlink(missing_ok=True);command('unsquashfs',['-pf',back,root],'readback.log')
    actual=tools.inventory(back)
    if actual!=expected:
        delta={n:dict(expected=expected.get(n),actual=actual.get(n)) for n in set(actual)|set(expected) if actual.get(n)!=expected.get(n)}
        (WORK/'READBACK_FAILURE.json').write_text(json.dumps(delta,indent=2));raise RuntimeError('Linux filesystem readback mismatch')
    (OUT/'FILESYSTEM.json').write_text(json.dumps(actual,sort_keys=True,separators=(',',':'))+'\n')
    package(root,report,actual,dict(overlay,**{n:None for n in links}),[name for name in removed if name not in actual],components)

def package(root,base_report,actual,overlay,removed,components):
    tree=WORK/'iso-tree';tree.mkdir(exist_ok=True)
    reader=tools.pycdlib.PyCdlib();reader.open(str(SOURCE))
    try:
        for folder,dirs,files in reader.walk(rr_path='/'):
            destination=tree/folder.lstrip('/');destination.mkdir(parents=True,exist_ok=True)
            for name in files:
                inside=folder.rstrip('/')+'/'+name
                if inside=='/boot/fw2610191845/rootfs':continue
                reader.get_file_from_iso(str(destination/name),rr_path=inside)
    finally:reader.close()
    boot=tree/'boot/fw2610191845';target=boot/'rootfs';target.unlink(missing_ok=True);os.link(root,target)
    boot_hashes={leaf:base_report[leaf+'_sha256'] for leaf in ('kernel','initrd')}
    for component,value in components.items():
        for leaf,item in value.get('boot_files',{}).items():
            assert component=='kernel' and leaf=='kernel' and value['native_verification']=='PASS'
            source=HERE/item['source'];assert digest(source)==item['sha256']
            shutil.copyfile(source,boot/leaf);boot_hashes[leaf]=item['sha256']
    for leaf in ('kernel','initrd'):assert digest(boot/leaf)==boot_hashes[leaf]
    sums=[]
    for line in (boot/'md5sum.txt').read_bytes().splitlines():
        _,name=line.split(None,1);sums.append(digest(boot/Path(name.decode()).name,'md5').encode()+b'  '+name)
    (boot/'md5sum.txt').write_bytes(b'\r\n'.join(sums)+b'\r\n')
    shutil.copyfile(HERE/'LEGGIMI_ISO_IT.md',tree/'documentazione/LEGGIMI.txt')
    for p in tree.rglob('*'):os.utime(p,(EPOCH,EPOCH))
    ISO.unlink(missing_ok=True)
    command('xorriso',['-as','mkisofs','-V','DaphileLIVE','--modification-date=2026111800000000','--set_all_file_dates','2026111800000000','--gpt_disk_guid','81ad2422d65450eaa289b94725670976','-uid','0','-gid','0','-dir-mode','0755','-file-mode','0644','-isohybrid-mbr','--interval:local_fs:0s-15s:zero_mbrpt,zero_gpt:'+SOURCE.as_posix(),'-partition_cyl_align','off','-partition_offset','0','--mbr-force-bootable','-iso_mbr_part_type','0x00','-c','isolinux/boot.cat','-b','isolinux/isolinux.bin','-no-emul-boot','-boot-load-size','4','-boot-info-table','-eltorito-alt-boot','-e','efi2505251549.img','-no-emul-boot','-boot-load-size','2880','-isohybrid-gpt-basdat','-o',ISO,tree],'xorriso.log')
    back=WORK/'iso-root-readback';back.unlink(missing_ok=True);extract(ISO,'/boot/fw2610191845/rootfs',back)
    assert digest(back)==digest(root)
    command('xorriso',['-indev',ISO,'-report_el_torito','plain','-report_system_area','plain'],'boot-catalog.log')
    catalog=(WORK/'boot-catalog.log').read_text(errors='replace');assert 'BIOS' in catalog and 'UEFI' in catalog
    report=dict(status='COMPOSED_AND_FULL_READBACK_VERIFIED',name=ISO.name,bytes=ISO.stat().st_size,sha256=digest(ISO),rootfs_sha256=digest(root),kernel_sha256=boot_hashes['kernel'],initrd_sha256=boot_hashes['initrd'],baseline_ISO_sha256=SOURCE_SHA,nodes=len(actual),changed_files=sorted(overlay),removed=removed,boot_catalog_BIOS_UEFI=True,physical_boot='PENDING',clean_DATA='PENDING',existing_DATA='PENDING')
    (OUT/'BUILD_REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('changed_files','removed')}),flush=True)

if __name__=='__main__':main()
