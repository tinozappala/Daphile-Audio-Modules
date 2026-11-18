"""Native RT receiver and publication ACK checks; private fixtures only.

python -B native_receiver_tests.py [--lib working/lib] [--policy native_working/daphile-audio-policy]
Perl receiver cases require root Linux with Perl. Portable ACK cases execute
the actual Python renew() using mocked environment/atomic/process primitives.
"""
import argparse,contextlib,copy,errno,importlib,json,os,shutil,subprocess,sys,tempfile,time,types
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode=True
BASE=Path(__file__).resolve().parent
BOOT='11111111-1111-4111-8111-111111111111'


def python_cases(lib):
    sys.path.insert(0,str(lib));module=importlib.import_module('native_coordination')
    common=types.ModuleType('autotune_v2.common_activity');common.require_exclusive=lambda:None
    atomic=types.ModuleType('autotune_v2.reference_state')
    def save(path,value):
        Path(path).write_text(json.dumps(value));os.chmod(path,0o600)
    atomic.atomic=save
    locks=types.ModuleType('fcntl');locks.LOCK_EX=2;last_fd=[None]
    locks.flock=lambda stream,mode:last_fd.__setitem__(0,stream.fileno())
    modules={'autotune_v2.common_activity':common,'autotune_v2.reference_state':atomic,'fcntl':locks}
    import autotune_v2
    passed=[]
    mode_shim=patch.object(module,'stat',types.SimpleNamespace(S_IMODE=lambda value:0o600)) if os.name!='posix' else contextlib.nullcontext()
    with tempfile.TemporaryDirectory(prefix='native-ack-',dir=BASE) as temporary,patch.dict(sys.modules,modules),mode_shim,\
            patch.object(autotune_v2,'common_activity',common,create=True):
        root=Path(temporary)
        def lease(name):
            path=root/name;path.mkdir();(path/'lock').touch()
            obj=module._USBLease.__new__(module._USBLease)
            obj.root=path;obj.path=path/'autotune-rt.json';obj.token='c'*32
            obj.kind='measurement';obj.record=None;obj.acknowledged=False
            env=dict(boot_id=BOOT,pid=123,start='987',audio_pid=456,audio_start='543',usb_path='1-2',
                     vendor='1234',product='5678',native_sha256='a'*64,player_preferences='/mnt/hdd/system/spac/ADI-2 DAC (57850059)/volume.conf',
                     player_sha256='b'*64,monotonic=1000.)
            obj.environment=lambda:copy.deepcopy(env)
            return obj,env
        def response(obj,mode='ok',env=None):
            def execute(argv,**kwargs):
                assert argv==[module.NATIVE_POLICY,'--json'] and kwargs['timeout']==8
                # An actually closed publication FD proves the subprocess is
                # outside that flock even on this portable fixture host.
                try:os.fstat(last_fd[0])
                except OSError as error:assert error.errno==errno.EBADF
                else:raise AssertionError('native CLI called while publication mutex is open')
                if mode=='timeout':raise subprocess.TimeoutExpired(argv,8)
                if mode=='bad-json':return types.SimpleNamespace(returncode=0,stdout=b'')
                ack=dict(rt_coordinator=obj.kind,rt_coordinator_token=obj.token,errors=[])
                if mode=='wrong-token':ack['rt_coordinator_token']='d'*32
                if mode=='no-token':ack.pop('rt_coordinator_token')
                if mode=='wrong-kind':ack['rt_coordinator']='verified-policy'
                if mode=='errors':ack['errors']=['native fixture rejected']
                if mode=='dead-context':env['audio_start']='new-process'
                if mode=='claim-replaced':
                    changed=dict(obj.record,token='e'*32);obj.path.write_text(json.dumps(changed))
                return types.SimpleNamespace(returncode=1 if mode=='exit-failure' else 0,stdout=json.dumps(ack).encode())
            return execute
        for mode in ('bad-json','wrong-token','no-token','wrong-kind','errors','timeout','exit-failure','dead-context','claim-replaced'):
            obj,env=lease(mode)
            with patch.object(module.subprocess,'run',response(obj,mode,env)),patch.object(module,'time',types.SimpleNamespace(monotonic=lambda:env['monotonic'])):
                try:obj.renew()
                except RuntimeError:pass
                else:raise AssertionError('unproved receiver acknowledgment accepted: '+mode)
                assert obj.acknowledged is False
            passed.append('refuses '+mode+' without coordinated result')
        obj,env=lease('active-reuse');calls=[]
        good=response(obj)
        def execute(*args,**kwargs):calls.append(args[0]);return good(*args,**kwargs)
        with patch.object(module.subprocess,'run',execute),patch.object(module,'time',types.SimpleNamespace(monotonic=lambda:env['monotonic'])):
            first=obj.renew();token=obj.token
            assert first['status']=='coordinated' and first['native_receiver_acknowledged'] is True
            env['monotonic']+=1;obj.renew();assert len(calls)==1 and obj.token==token
            env['monotonic']+=61;obj.renew();assert len(calls)==2 and obj.token!=token
            token=obj.token;env['audio_start']='544';obj.renew();assert len(calls)==3 and obj.token!=token
        passed.append('actual token ACK once; active renewal has no subprocess; expired/changed context rotates token and rechecks once')
    return passed


def perl_cases(policy):
    if sys.platform!='linux' or os.geteuid()!=0 or not shutil.which('perl'):
        return [],['actual Perl receiver fixtures require root Linux with Perl']
    checked=subprocess.run(['perl','-c',str(policy)],capture_output=True,timeout=8)
    assert checked.returncode==0,checked.stderr.decode(errors='replace')
    passed=[]
    with tempfile.TemporaryDirectory(prefix='native-receiver-',dir=BASE) as temporary:
        root=Path(temporary);fixture=root/'fixture';fixture.mkdir(mode=0o700)
        def write(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
        usb=fixture/'sys/devices/pci0000:00/0000:00:12.0/usb1/1-1.3'
        for name,value in dict(idVendor='31c1',idProduct='0002',product='Controlled fixture',speed='480').items():write(usb/name,value)
        write(usb/'power/control','auto')
        card=fixture/'sys/class/sound/card3';card.mkdir(parents=True);(card/'device').symlink_to(usb,target_is_directory=True)
        (fixture/'sys/class/sound/pcmC3D0p').mkdir()
        write(fixture/'proc/sys/kernel/random/boot_id',BOOT)
        for pid,start in ((123,'987'),(456,'543')):
            fields=['S']+['0']*42;fields[19]=start;write(fixture/'proc'/str(pid)/'stat',str(pid)+' (owned fixture) '+' '.join(fields))
        native=fixture/'var/daphile/conf/config.sys';write(native,'hw_audio_rt=on\n')
        run=fixture/'run/daphile-audio-policy';run.mkdir(parents=True,mode=0o700);claim=run/'autotune-rt.json'
        import hashlib
        content='BUFFERTIME=40\nPERIODCOUNT=4\n'
        now=time.monotonic()
        base=dict(schema=1,token='c'*32,kind='measurement',boot_id=BOOT,pid=123,start='987',audio_pid=456,audio_start='543',usb_path='1-1.3',vendor='31c1',product='0002',
                  native_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),player_sha256=hashlib.sha256(content.encode()).hexdigest(),issued=now,expires=now+60)
        def execute(name,path,expected,kind='measurement',create=True):
            if create:write(fixture/path.lstrip('/'),content)
            value=dict(base,player_preferences=path,kind=kind);claim.write_text(json.dumps(value));claim.chmod(0o600)
            write(usb/'power/control','auto')
            result=subprocess.run(['perl',str(policy),'--test-root='+str(fixture),'--json'],capture_output=True,timeout=8)
            assert result.returncode==0,(name,result.stderr.decode(errors='replace'))
            actual=json.loads(result.stdout)
            assert actual['rt_coordinator']==expected,(name,actual)
            assert actual['rt_coordinator_token']==('c'*32 if expected else ''),(name,actual)
            assert (usb/'power/control').read_text().strip()=='on',name
            assert actual['errors']==[],(name,actual)
            passed.append(name+': exact receiver token and USB protection verified')
        for name in ('a384','ADI-2 DAC (57850059)','DAC 音声 Ω','outer/nested device'):
            for kind in ('measurement','verified-policy'):
                execute(name+' '+kind,'/mnt/hdd/system/spac/'+name+'/volume.conf',kind,kind)
        # These references resolve to valid fixture bytes, so a refusal tests
        # the path gate instead of merely missing-file/digest failure.
        for name,path in (
                ('dot segment','/mnt/hdd/system/spac/a384/./volume.conf'),
                ('parent segment','/mnt/hdd/system/spac/a384/../a384/volume.conf'),
                ('empty segment','/mnt/hdd/system/spac//a384/volume.conf'),
                ('newline','/mnt/hdd/system/spac/DAC\nline/volume.conf'),
                ('control byte','/mnt/hdd/system/spac/DAC\x01name/volume.conf'),
                ('wrong root','/mnt/hdd/system/spac2/a384/volume.conf')):
            execute(name,path,'')
        execute('NUL byte','/mnt/hdd/system/spac/DAC\x00name/volume.conf','',create=False)
        assert 'return $fallback if $test;' in policy.read_text()
        assert 'return $fallback if $test || $json;' not in policy.read_text()
        passed.append('JSON output mode preserves native preference/USB/RT branches')
    return passed,[]


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib',type=Path,default=BASE/'working/lib')
    parser.add_argument('--policy',type=Path,default=BASE/'native_working/daphile-audio-policy')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();python=python_cases(args.lib.resolve());perl,deferred=perl_cases(args.policy.resolve())
    result=dict(status='PASS' if not deferred else 'PASS_PORTABLE_PERL_PENDING',python_cases=python,perl_cases=perl,deferred=deferred,
                production_updated=False,actual_hardware_controls_written=0)
    raw=json.dumps(result,indent=2,ensure_ascii=False)+'\n'
    if args.output:args.output.write_text(raw,encoding='utf8')
    print(raw,end='')
