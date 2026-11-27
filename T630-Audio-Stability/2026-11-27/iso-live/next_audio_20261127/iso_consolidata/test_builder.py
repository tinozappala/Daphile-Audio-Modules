"""Focused tests for the stock-based image's factory and routing defaults."""
import ast
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock
import zipfile

import build_iso as builder
import protect_live_initrd as live
import routed_default_probe as probe


class MemoryPath:
    rows={}
    def __init__(self,name):self.name=str(name)
    def __truediv__(self,name):return self.__class__(self.name+'/'+name)
    def is_symlink(self):return self.rows.get(self.name,{}).get('symlink',False)
    def exists(self):return self.name in self.rows
    def is_dir(self):return self.rows.get(self.name,{}).get('kind')=='dir'
    def is_file(self):return self.rows.get(self.name,{}).get('kind')=='file'
    def iterdir(self):
        prefix=self.name+'/';return iter([self.__class__(n) for n in self.rows if n.startswith(prefix) and '/' not in n[len(prefix):]])
    def read_bytes(self):return self.rows[self.name]['data']
    def stat(self):
        row=self.rows[self.name]
        return types.SimpleNamespace(st_uid=row.get('uid',0),st_mode=row.get('mode',0o600),st_size=len(row.get('data',b'')))
    def mkdir(self,mode=0o777,parents=False,exist_ok=False):self.rows[self.name]={'kind':'dir','mode':mode}


class BootstrapTests(unittest.TestCase):
    host='/mnt/hdd/system/daphile-audio-host'
    def setUp(self):
        MemoryPath.rows={};self.calls=[]
        source=builder.patch_bootstrap(b"if __name__=='__main__':\n    if sys.argv[1]=='modules':modules()\n    elif sys.argv[1]=='ram-config':ram_config()\n")
        self.code=compile(source,'fixture-bootstrap','exec')
    def run_boot(self):
        def modules():
            self.calls.append('modules');MemoryPath(self.host).mkdir(exist_ok=True)
            MemoryPath.rows[self.host+'/.owned']={'kind':'file','data':b'DAPHILE_NATIVE_AUDIO_HOST_V1'}
        def publish(path,data):
            self.calls.append('publish');MemoryPath.rows[path.name]={'kind':'file','data':data,'mode':0o600}
        def chmod(path,mode):MemoryPath.rows[path.name]['mode']=mode
        g={'__name__':'__main__','sys':types.SimpleNamespace(argv=['bootstrap','modules']),
           'Path':MemoryPath,'os':types.SimpleNamespace(chmod=chmod),'json':json,'modules':modules,'publish':publish}
        exec(self.code,g)
    def existing(self,buffer=b'{"chunksize":2048,"target_chunks":2}\n'):
        for suffix in ('','/modules','/modules/DaphileCamillaDSP','/modules/DaphileCamillaDSP/data'):
            MemoryPath.rows[self.host+suffix]={'kind':'dir','mode':0o700}
        MemoryPath.rows[self.host+'/.owned']={'kind':'file','data':b'DAPHILE_NATIVE_AUDIO_HOST_V1'}
        MemoryPath.rows[self.host+'/modules/DaphileCamillaDSP/data/audio-buffer.json']={'kind':'file','data':buffer,'mode':0o600}
    def test_fresh_private_2048_2(self):
        self.run_boot();p=self.host+'/modules/DaphileCamillaDSP/data/audio-buffer.json'
        self.assertEqual(json.loads(MemoryPath.rows[p]['data']),{'chunksize':2048,'target_chunks':2})
        for suffix in ('/modules','/modules/DaphileCamillaDSP','/modules/DaphileCamillaDSP/data'):
            self.assertEqual(MemoryPath.rows[self.host+suffix]['mode'],0o700)
    def test_existing_2048_2_is_preserved(self):
        self.existing();before=dict(MemoryPath.rows);self.run_boot()
        p=self.host+'/modules/DaphileCamillaDSP/data/audio-buffer.json'
        self.assertEqual(MemoryPath.rows[p],before[p]);self.assertNotIn('publish',self.calls)
    def test_unowned_and_invalid_are_refused_before_mutation(self):
        MemoryPath.rows[self.host]={'kind':'dir'};MemoryPath.rows[self.host+'/unowned']={'kind':'file','data':b'x'}
        with self.assertRaisesRegex(RuntimeError,'UNOWNED'):self.run_boot()
        self.assertEqual(self.calls,[])
        MemoryPath.rows={};self.existing(b'{"chunksize":2048,"target_chunks":4}')
        with self.assertRaisesRegex(RuntimeError,'NOT_2048_2'):self.run_boot()
        self.assertEqual(self.calls,[])
    def test_missing_or_linked_settings_are_refused(self):
        self.existing();p=self.host+'/modules/DaphileCamillaDSP/data/audio-buffer.json'
        del MemoryPath.rows[p]
        with self.assertRaisesRegex(RuntimeError,'NOT_VERIFIED'):self.run_boot()
        self.existing();MemoryPath.rows[p]['symlink']=True
        with self.assertRaisesRegex(RuntimeError,'NOT_VERIFIED'):self.run_boot()
        self.assertEqual(self.calls,[])


class ReleaseTests(unittest.TestCase):
    def test_pinned_archives_and_member_safety(self):
        for v in ('0.4.71','0.4.72'):
            path,sha,_=builder.VERSIONS[v]
            raw,entry,files=builder.validate_zip(path,sha,'DaphileCamillaDSP',v,51)
            self.assertEqual(entry['version'],v);self.assertEqual(len(files),51)
            with self.assertRaisesRegex(RuntimeError,'unexpected release archive'):
                builder.validate_zip(path,'0'*64,'DaphileCamillaDSP',v,51)
    def test_efi_replacement_is_bounded(self):
        self.assertEqual(builder.patch_efi(b'x fw2505251550 y','fw2611270720',1),b'x fw2611270720 y')
        with self.assertRaises(RuntimeError):builder.patch_efi(b'x','fw2611270720',1)
        iso=builder.ROOT/'tmp/t620-stock-20261121/image'
        for name,count in (('efi/boot/bootx64.efi',1),('efi/boot/bootia32.efi',1),('efi2505251550.img',2)):
            src=(iso/name).read_bytes();out=builder.patch_efi_options(src,count)
            self.assertEqual(len(src),len(out));self.assertEqual(out.count(b'raid=noautodetect'),count)
    def test_selected_and_unselected_stock_defaults(self):
        original=b'BUFFERTIME=100\nPERIODCOUNT=3\n';new=builder.routed_buffer_defaults(original).decode()
        with tempfile.TemporaryDirectory(dir=builder.WORK) as tmp:
            d=Path(tmp)
            # Probe behavior is separately exercised with .72's actual code.
            (d/'probe').write_text('#!/bin/sh\n[ "$1" = 3 ] && [ "$2" = 0 ]\n',newline='\n')
            script=new.replace('/mnt/hdd/system/daphile-audio-host/native/route.json','"$DARC_TEST_ROOT/route.json"')
            script=script.replace('/usr/bin/python3.13 -I -B /usr/local/libexec/daphile-route-default','sh "$DARC_TEST_ROOT/probe"')
            script+='\nprintf "%s:%s\\n" "$BUFFERTIME" "$PERIODCOUNT"\n'
            (d/'defaults.sh').write_text(script,newline='\n')
            def run(card):
                s='export PATH=/usr/bin:$PATH; export DARC_TEST_ROOT="$PWD"; devnum=0; cardnum='+str(card)+'; . ./defaults.sh'
                p=subprocess.run(['C:/msys64/usr/bin/bash.exe','-c',s],cwd=d,capture_output=True,text=True,timeout=15)
                self.assertEqual(p.returncode,0,p.stderr);return p.stdout.strip()
            self.assertEqual(run(3),'100:3')
            (d/'route.json').write_text('{"version":1,"alsa_id":"RME"}')
            self.assertEqual(run(3),'40:4');self.assertEqual(run(4),'100:3')
        path=builder.VERSIONS['0.4.72'][0]
        with zipfile.ZipFile(path) as z:tree=ast.parse(z.read('DaphileCamillaDSP/backend/player_router.py'))
        fun=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='routed_buffer_args')
        env={};exec(compile(ast.Module(body=[fun],type_ignores=[]),'player_router','exec'),env)
        f=env['routed_buffer_args'];self.assertEqual(f(['-a','40:4:32:0:0']),['-a','80:8:32:0:0'])
        self.assertEqual(f(['-a','100:3:32:0:0']),['-a','100:3:32:0:0'])

    def test_router_identity_is_the_real_release_implementation(self):
        with tempfile.TemporaryDirectory(dir=builder.WORK) as tmp:
            d=Path(tmp);base=d/'native';base.mkdir();proc=d/'proc';(proc/'card3').mkdir(parents=True)
            (proc/'card3/id').write_text('RME\n');sysroot=d/'sys';device=sysroot/'class/sound/card3/device';device.mkdir(parents=True)
            for name,value in {'idVendor':'1234','idProduct':'abcd','serial':'SYNTHETIC'}.items():(device/name).write_text(value+'\n')
            with zipfile.ZipFile(builder.VERSIONS['0.4.72'][0]) as z:router=z.read('DaphileCamillaDSP/backend/player_router.py')
            (base/'player-router').write_bytes(router);player=d/'squeezelite';os.link(base/'player-router',player)
            import hashlib
            owner={'tag':'DAPHILE_CAMILLA_NATIVE_ROUTER_V1','files':{'player-router':hashlib.sha256(router).hexdigest()}}
            (base/'owner.json').write_text(json.dumps(owner));route={'version':1,'alsa_id':'RME','usb':{'vendor':'1234','product':'abcd','serial':'SYNTHETIC'}}
            (base/'route.json').write_text(json.dumps(route))
            def regular_fixture(path,private=False):return path.stat()
            # Windows cannot report Linux uid/mode semantics; actual router identity,
            # pinned bytes, hardlink identity and synthetic sysfs are exercised.
            with mock.patch.object(probe.os,'geteuid',return_value=0,create=True), mock.patch.object(probe,'trusted_directory',return_value=True), mock.patch.object(probe,'regular',side_effect=regular_fixture):
                test=lambda:probe.selected('3','0',base,player,proc,sysroot)
                self.assertTrue(test());self.assertFalse(probe.selected('3','1',base,player,proc,sysroot))
                (device/'serial').write_text('OTHER\n');self.assertFalse(test());(device/'serial').write_text('SYNTHETIC\n')
                owner['files']['player-router']='0'*64;(base/'owner.json').write_text(json.dumps(owner));self.assertFalse(test())
                owner['files']['player-router']=hashlib.sha256(router).hexdigest();(base/'owner.json').write_text(json.dumps(owner))
                player.unlink();player.write_bytes(router);self.assertFalse(test())

    def test_stock_initrd_only_changes_live_boot_and_data(self):
        src=(builder.STOCK_ROOT.parent/'initrd').read_bytes()
        patched=live.protect(src);offset=(list(live.records(src))[-1][4]+511)&~511
        self.assertEqual(src[:offset],patched[:offset]);before=gzip.decompress(src[offset:]);after=gzip.decompress(patched[offset:])
        def extract(data):
            return {n:data[pos:pos+size] for n,_,pos,size,_ in live.records(data)}
        a=extract(before);b=extract(after);self.assertEqual(a.keys(),b.keys())
        for name in a:
            if name=='init':
                self.assertEqual(b[name],a[name].replace(live.ORIGINAL_BOOT,live.SAFE_BOOT).replace(live.ORIGINAL_DATA,live.SAFE_DATA))
            else:self.assertEqual(a[name],b[name],name)
        with self.assertRaisesRegex(RuntimeError,'digest'):live.protect(src+b'x')

    def test_physical_disk_autoactivation_is_blocked(self):
        pseudo=builder.Pseudo(builder.WORK/'stock.pseudo')
        for n in ('opt/daphile/bin/localmount','etc/init.d/lvm','etc/init.d/mdraid'):
            raw=pseudo.read(n);out=builder.protect_disk_automount(n,raw)
            self.assertIn(b'/etc/daphile-live-no-physical-mount',out)
            self.assertGreater(len(out),len(raw))

    def test_failure_logging_preserves_service_result(self):
        pseudo=builder.Pseudo(builder.WORK/'rc6-components.pseudo')
        for name in ('etc/init.d/daphile-modules','etc/init.d/daphile-audio-host'):
            source=pseudo.read(name);patched=builder.diagnostic_start(name,source)
            self.assertIn(b'local result=$?',patched);self.assertIn(b'eend $result',patched)
            with self.assertRaisesRegex(RuntimeError,'contract'):builder.diagnostic_start(name,source.replace(b'eend $?',b'eend 0'))


if __name__=='__main__':unittest.main()
