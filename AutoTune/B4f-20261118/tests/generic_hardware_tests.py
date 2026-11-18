"""Generic physical ALSA/control fixtures; no native services or device writes.

python -B generic_hardware_tests.py --lib working/lib --output REPORT.json
Models discovered hardware only. Actual identity, planning, matching, guard,
coordination selection and persistence binding code execute unchanged.
"""
import argparse,copy,importlib,json,os,sys,tempfile,types
from pathlib import Path,PurePosixPath
from unittest.mock import patch

sys.dont_write_bytecode=True


class FixtureOS:
    def __getattr__(self,name):
        defaults=dict(SCHED_OTHER=0,SCHED_FIFO=1,SCHED_RR=2,SCHED_BATCH=3,SCHED_IDLE=5)
        if name in defaults:return getattr(os,name,defaults[name])
        return getattr(os,name)


def run(lib):
    sys.path.insert(0,str(lib))
    sys.path.insert(1,str(Path(__file__).resolve().parent))
    engine=types.ModuleType('engine');engine.STATE=Path('/unused-fixture-state');engine.start_ticks=lambda pid:'birth'
    stubs={name:types.ModuleType(name) for name in ('player','comparison','measure','session')}
    stubs['engine']=engine
    common=types.ModuleType('autotune_v2.common_activity');common.require_exclusive=lambda:None
    stubs['autotune_v2.common_activity']=common
    with patch.dict(sys.modules,stubs):
        physical=importlib.import_module('physical_audio')
        advanced=importlib.import_module('advanced');runtime=importlib.import_module('runtime_ext')
        power=importlib.import_module('power_policy');search=importlib.import_module('search')
        coordination=importlib.import_module('native_coordination')
        persistence=importlib.import_module('autotune_v2.persistence')
        adapter=importlib.import_module('autotune_v2.native_adapter')
        import autotune_v2
        usb=dict(idVendor='1234',idProduct='5678',serial='fixture-serial',product='Observed USB audio',path='1-2',
                 sysfs='/sys/devices/pci0000:00/0000:00:14.0/usb1/1-2',controller='0000:00:14.0',irq=17)
        cards={
            'usb':dict(card=1,id='UsbAudio',usb_path='1-2',sysfs=usb['sysfs']),
            'pci-hda':dict(card=2,id='HdaAudio',usb_path=None,sysfs='/sys/devices/pci0000:00/0000:00:1f.3',bus='pci',driver='snd_hda_intel'),
            'hdmi':dict(card=3,id='HdmiAudio',usb_path=None,sysfs='/sys/devices/pci0000:00/0000:01:00.1',bus='pci',driver='snd_hda_intel'),
            'platform-soc':dict(card=4,id='SocAudio',usb_path=None,sysfs='/sys/devices/platform/soc/12340000.audio',bus='platform',driver='asoc-simple-card')}
        passed=[]
        def hardware(kind,count=4):
            card=copy.deepcopy(cards[kind]);ident=physical.identity(card,[usb])
            cores=[dict(cpu=i,topology=dict(physical_package_id='0',core_id=str(i)),
                        cpufreq=dict(scaling_governor='powersave',scaling_available_governors='powersave performance')) for i in range(count)]
            threads=[dict(tid=1000,name='output',policy=1,priority=60,affinity=[count-1]),
                     dict(tid=2000,name='irq/17-xhci_hcd',policy=1,priority=50,affinity=[0])]
            return dict(system=dict(product_uuid='12345678-1234-1234-1234-123456789abc'),kernel=dict(release='generic-kernel',realtime='1'),
                        dac=dict(status='verified-active',identity=ident,usb=copy.deepcopy(usb) if kind=='usb' else None,
                                 card=card['card'],alsa_id=card['id'],owner_pid=1000,
                                 pcm=dict(hw=dict(format='S16_LE',rate='48000',channels='2'),status='state: RUNNING')),
                        alsa=dict(cards=[card]),usb=[copy.deepcopy(usb)] if kind=='usb' else [],pci=[],
                        cpu=dict(cores=cores,online=','.join(map(str,range(count))),isolated=''),
                        irq=[dict(irq=17,description='IR-PCI-MSI fasteoi xhci_hcd:usb1',affinity='0')],
                        squeezelite=[dict(name='squeezelite',pid=1000,threads=threads)])

        assert physical.identity(cards['usb'],[usb])==importlib.import_module('usb_target').identity(usb)
        for kind in cards:
            h=hardware(kind);ident=h['dac']['identity']
            assert physical.identified(ident) and len(physical.matches(ident,h))==1
            idle=copy.deepcopy(h);idle['dac']=dict(status='unknown')
            assert len(physical.matches(ident,idle))==1
            changed=copy.deepcopy(h);changed['alsa']['cards'][0]['sysfs']+='/replaced'
            if kind=='usb':changed['usb'][0]['serial']='replaced-serial'
            assert physical.matches(ident,changed)==[]
        passed.append('USB representation preserved; USB/PCI-HDA/HDMI/platform physical identities match without active PCM and reject replacement')

        for bad in (
            dict(cards['platform-soc'],id='Loopback'),dict(cards['platform-soc'],driver='snd_dummy'),
            dict(cards['platform-soc'],sysfs='/sys/devices/virtual/sound/card0'),
            dict(cards['platform-soc'],driver=None,bus=None,sysfs='/sys/devices/nonexistent-fixture-node'),
            dict(cards['usb'],usb_path='missing-usb')):
            assert physical.identity(bad,[usb]) is None
        passed.append('Loopback/dummy/virtual/unobserved drivers and missing USB routes never become physical outputs')

        def observed(path):
            text=str(path).replace('\\','/')
            if text=='/sys/class/dmi/id/product_uuid':return '12345678-1234-1234-1234-123456789abc'
            if text.endswith('scaling_available_governors'):return 'powersave performance'
            if text.endswith('scaling_driver'):return 'observed-cpufreq-driver'
            if text.endswith('/online'):return '0-63'
            if text.endswith('available_clocksource'):return 'observed_clock alternate_clock'
            return None

        with patch.object(advanced,'read',observed),patch.object(runtime,'read',observed),patch.object(power,'read',observed):
            for kind in cards:
                h=hardware(kind);ident=h['dac']['identity']
                governor=dict(host_uuid='12345678-1234-1234-1234-123456789abc',identity=ident,parameter='cpu_governor',
                              path='/sys/devices/system/cpu/cpufreq/policy0/scaling_governor',before='powersave',after='performance')
                advanced.guard(governor,h)
                extended=dict(host_uuid='12345678-1234-1234-1234-123456789abc',identity=ident,parameter='governor_search',
                              policy='/sys/devices/system/cpu/cpufreq/policy0',driver='observed-cpufreq-driver',before='powersave',after='performance')
                with patch.object(runtime,'Path',PurePosixPath):runtime.guard(extended,h)
                clock=dict(host_uuid='12345678-1234-1234-1234-123456789abc',identity=ident,parameter='clocksource',
                           path=str(power.CLOCK/'current_clocksource'),before='observed_clock',after='alternate_clock')
                power.guard(clock,h)
                replaced=copy.deepcopy(h);replaced['alsa']['cards']=[]
                for guard,change in ((advanced.guard,governor),(runtime.guard,extended),(power.guard,clock)):
                    try:guard(change,replaced)
                    except RuntimeError:pass
                    else:raise AssertionError('replaced physical device accepted by '+guard.__module__)
                if kind!='usb':
                    usb_change=dict(host_uuid='12345678-1234-1234-1234-123456789abc',identity=ident,parameter='usb_irq_affinity')
                    try:advanced.guard(usb_change,h)
                    except RuntimeError:pass
                    else:raise AssertionError('USB action accepted on '+kind)
                    try:search.changes(h,dict(output=[1],irq=[2]))
                    except RuntimeError:pass
                    else:raise AssertionError('USB placement accepted on '+kind)
            passed.append('governor/clock guards admit every physical transport; replacements and non-USB IRQ coordinates refuse before writes')

        with patch.object(search,'os',FixtureOS()),patch.object(search,'read',lambda path:None),patch.object(advanced,'os',FixtureOS()),\
             patch.object(power,'inventory',lambda:dict(cstates=[],available_clocksources=[],clocksource=None)),\
             patch.object(runtime,'network_inventory',lambda:dict(status='unsupported')):
            for kind in cards:
                for count in (1,2,4,64):
                    h=hardware(kind,count);inventory=search.specs(h)
                    scheduled=[row for row in inventory['candidates'] if row['stage']=='scheduling']
                    assert 1<=len(scheduled)<=22
                    assert any(row.get('policy') is not None for row in scheduled)
                    assert len(inventory['placement_coverage']['representative_groups'])<=4
                    if kind!='usb' or count<3:assert not any('irq' in row or 'output' in row for row in scheduled)
                    assert inventory['placement_coverage']['exhaustive'] is False
            h=hardware('platform-soc',1);h['kernel']['realtime']='0';h['cpu']['cores'][0]['cpufreq']={}
            inventory=search.specs(h)
            assert inventory['candidates']==[dict(name='current',stage='reference',coordinate='reference')]
        passed.append('1/2/4/64 CPU matrices keep bounded <=22 scheduling candidates; missing capabilities defer coordinates and retain reference')

        with patch.object(autotune_v2,'common_activity',common,create=True):
            for kind in ('pci-hda','hdmi','platform-soc'):
                h=hardware(kind);target=dict(identity=h['dac']['identity'])
                lease=coordination.Lease(h,target)
                assert lease.renew(h)==dict(status='not-required',reason='native-rt-policy-is-usb-only',identity_verified=True,policy_writes=0)
                replaced=copy.deepcopy(h);replaced['alsa']['cards']=[]
                try:lease.renew(replaced)
                except RuntimeError:pass
                else:raise AssertionError('non-USB policy coordinator accepted replacement')
                lease.close()
            with patch.object(coordination,'_USBLease',lambda *args:'existing-usb-policy'):
                assert coordination.Lease(hardware('usb'),dict(identity=hardware('usb')['dac']['identity']))=='existing-usb-policy'
        passed.append('non-USB coordination writes no native USB policy and still rejects lost devices; USB keeps its existing coordinator')

        for kind in cards:
            h=hardware(kind);target=dict(identity=h['dac']['identity'],alsa_id=h['dac']['alsa_id'],mac='12:34:56:78:9a:bc',device=3)
            bound=persistence.binding(h,target)
            if kind=='usb':assert bound['usb']==dict(controller='0000:00:14.0',port='2',vendor='1234',product='5678',serial='fixture-serial')
            else:assert bound['physical_route']==dict(transport='alsa-physical',identity=target['identity'],device=3)
        passed.append('persistent policy keeps USB route shape and binds non-USB physical route plus actual playback endpoint')

        fixture=Path('/fixture-owned.wav')
        for kind in cards:
            h=hardware(kind);obj=adapter.NativeAdapter.__new__(adapter.NativeAdapter)
            obj.workload=dict(path=str(fixture),rate=48000);obj.fixture_pin=('fixture-pin',)
            obj.target=dict(identity=h['dac']['identity'],mac='12:34:56:78:9a:bc')
            state=dict(mac=obj.target['mac'],urls=[fixture.resolve().as_uri()],index=0,mode='play',time=0,
                       repeat=1,shuffle=0,volume=37,mute=0,power=1)
            obj.fixture_playback=copy.deepcopy(state);obj.source=dict(sample_hash='fixture',volume=37)
            obj.api=types.SimpleNamespace(player=types.SimpleNamespace(playback_snapshot=lambda mac:copy.deepcopy(state),playlist_key=lambda url:url,rpc=lambda *args:None),
                                          comparison=types.SimpleNamespace(playback=lambda mac:dict(sample_hash='fixture',volume=37)))
            with patch.object(adapter.NativeAdapter,'file_pin',staticmethod(lambda path:('fixture-pin',))),patch.object(adapter,'standalone',lambda *args:None):
                obj.verify_workload(h)
                bad=copy.deepcopy(h);bad['dac']['pcm']['hw']['format']='DSD_U32_LE'
                try:obj.verify_workload(bad)
                except RuntimeError:pass
                else:raise AssertionError('DSD was accepted as controlled PCM')
        passed.append('actual S16 PCM containers qualify across transports; DSD containers remain excluded')

    from usb_connection_tests import run as connection_tests
    connections=connection_tests(lib)
    return dict(status='PASS',scope='generic discovered-hardware fixtures; no audio or hardware qualification',
                hardware_transports=list(cards)+['usb-without-serial'],cpu_counts=[1,2,4,64],device_writes=0,cases=passed,
                usb_connections=connections)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib',type=Path,default=Path(__file__).resolve().parent/'working/lib')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();raw=json.dumps(run(args.lib.resolve()),indent=2)+'\n'
    if args.output:args.output.write_text(raw)
    print(raw,end='')
