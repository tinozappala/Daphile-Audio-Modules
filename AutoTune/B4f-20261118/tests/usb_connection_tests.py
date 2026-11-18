"""Serial-less USB connection identity fixtures; never touches native devices.

Portable cases model only the USB observation primitive. Root Linux cases
also exercise that primitive against actual protected temporary directories.
"""
import ast,copy,importlib,json,os,sys,tempfile,types
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode=True
BOOT='12345678-1234-4234-8234-123456789abc'


def run(lib):
    sys.path.insert(0,str(lib))
    physical=importlib.import_module('physical_audio')
    usb_target=importlib.import_module('usb_target')
    passed=[];deferred=[]
    device=dict(path='1-2',sysfs='/sys/devices/pci0000:00/0000:00:14.0/usb1/1-2',
                idVendor='1234',idProduct='5678',serial=None,product='USB audio without serial',
                busnum='1',devnum='2',controller='0000:00:14.0',irq=17,power=dict(control='auto'))
    card=dict(card=3,id='UsbAudio',usb_path='1-2',sysfs=device['sysfs']+'/1-2:1.0')
    observed=dict(values={key:device.get(key) for key in ('idVendor','idProduct','serial','product','busnum','devnum')},
                  boot_id=BOOT,device_inode=dict(device=20,inode=500,ctime_ns=123456789))
    hardware=dict(hardware_id='fixture',system=dict(product_uuid='generic-host'),kernel=dict(release='generic-kernel'),
                  cpu=dict(cores=[],online='0',isolated=''),alsa=dict(cards=[card]),usb=[device])
    hardware['dac']=dict(status='verified-active',usb=device)
    with patch.object(physical,'_connection_observation',lambda path:copy.deepcopy(observed)):
        identity=physical.identity(card,[device]);hardware['dac']['identity']=identity
        assert physical.identified(identity) and physical.is_usb(identity)
        assert identity['kind']==physical.USB_CONNECTION and identity['serial'] is None
        assert identity['device_inode']==observed['device_inode'] and identity['boot_id']==BOOT
        assert len(physical.matches(identity,hardware))==1 and physical.usb_matches(identity,hardware)==[device]
        coordination=importlib.import_module('native_coordination')
        with patch.object(coordination,'_USBLease',lambda *args:'native-usb-coordination'):
            assert coordination.Lease(hardware,dict(identity=identity))=='native-usb-coordination'
        legacy=usb_target.identity(device)
        assert legacy is not None and not physical.identified(legacy) and physical.matches(legacy,hardware)==[]
        passed.append('serial-less identity binds observed VID/PID, ALSA ID, route, boot and device inode; old unanchored connections are historical only')

        serial=dict(device,serial='real-device-serial')
        assert physical.identity(card,[serial])==usb_target.identity(serial)
        passed.append('serial-numbered USB identity representation is unchanged')

        for key,value in (('inode',501),('ctime_ns',123456790),('device',21)):
            original=observed['device_inode'][key];observed['device_inode'][key]=value
            assert physical.matches(identity,hardware)==[] and physical.usb_matches(identity,hardware)==[]
            observed['device_inode'][key]=original
        observed['boot_id']='87654321-4321-4321-8321-cba987654321'
        assert physical.matches(identity,hardware)==[]
        newer=physical.identity(card,[device]);assert newer is not None and newer!=identity
        observed['boot_id']=BOOT
        changed=copy.deepcopy(hardware);changed['alsa']['cards'][0]['id']='DifferentAlsaId'
        assert physical.matches(identity,changed)==[]
        passed.append('hotplug inode/ctime/device changes, reboot and ALSA replacement all reject the former identity')

        for field in ('idVendor','idProduct','busnum','devnum','product'):
            saved=observed['values'][field];observed['values'][field]='different'
            assert physical.identity(card,[device]) is None
            observed['values'][field]=saved
        assert physical.identity(dict(card,sysfs='/sys/devices/other-device'),[device]) is None
        observed['boot_id']='unobserved';assert physical.identity(card,[device]) is None;observed['boot_id']=BOOT
        passed.append('missing/mismatched live attributes, unrelated ALSA route and invalid boot evidence refuse identity')

        # Execute the exact two bounded engine functions without importing its
        # unrelated Linux runtime or starting a native transaction.
        source=lib/'engine.py';tree=ast.parse(source.read_text(),str(source))
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in ('recommend','guard')]
        namespace=dict(PROFILES=['safe'],read=lambda path:'generic-host')
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),namespace)
        runtime=types.ModuleType('runtime_ext');runtime.PARAMETERS=set()
        with patch.dict(sys.modules,{'runtime_ext':runtime}):
            changes=namespace['recommend'](hardware,'safe')['changes']
            assert len(changes)==1 and changes[0]['identity']==identity
            change=changes[0]
            namespace['guard'](change,hardware,False);namespace['guard'](change,hardware,True)
            try:namespace['guard'](dict(change,identity=legacy),hardware,True)
            except RuntimeError:pass
            else:raise AssertionError('historical unanchored identity authorized rollback')
            observed['device_inode']['inode']+=1
            for restoring in (False,True):
                try:namespace['guard'](change,hardware,restoring)
                except RuntimeError:pass
                else:raise AssertionError('engine USB PM accepted a replacement')
            observed['device_inode']['inode']-=1
        passed.append('actual engine USB PM recommendation supports serial-less connections and guard rejects replacement during apply AND rollback')

        # The accepted metadata must retain its boot/inode witness; reducing
        # it to VID/PID/port would wrongly authorize reapply after reboot.
        persistence=importlib.import_module('autotune_v2.persistence')
        target=dict(identity=identity,alsa_id=card['id'],mac='12:34:56:78:9a:bc',device=0)
        bound=persistence.binding(hardware,target)
        assert 'usb' not in bound and bound['physical_route']['identity']==identity
        observed['boot_id']='87654321-4321-4321-8321-cba987654321'
        fresh=physical.identity(card,[device]);target['identity']=fresh
        assert persistence.binding(hardware,target)!=bound
        observed['boot_id']=BOOT
        passed.append('persistent policy retains the full connection witness and a reboot produces a different binding requiring recalibration')

    if sys.platform=='linux' and os.geteuid()==0:
        with tempfile.TemporaryDirectory(prefix='usb-connection-proof-',dir=Path(__file__).resolve().parent) as temporary:
            root=Path(temporary);node=root/'device';node.mkdir(mode=0o700)
            boot=root/'boot-id';boot.write_text(BOOT)
            for key,value in observed['values'].items():
                if value is not None:(node/key).write_text(value+'\n')
            with patch.object(physical,'BOOT_ID',boot):
                actual=physical._connection_observation(str(node))
                stat=node.stat()
                assert actual['device_inode']==dict(device=stat.st_dev,inode=stat.st_ino,ctime_ns=stat.st_ctime_ns)
                assert actual['values']==observed['values'] and actual['boot_id']==BOOT
                alias=root/'symlink';alias.symlink_to(node,target_is_directory=True)
                try:physical._connection_observation(str(alias))
                except OSError:pass
                else:raise AssertionError('symlink connection directory accepted')
                (node/'idVendor').chmod(0o666)
                try:physical._connection_observation(str(node))
                except RuntimeError:pass
                else:raise AssertionError('writable USB attribute accepted')
                (node/'idVendor').chmod(0o644)
                real_stat=physical.os.stat;calls=[0]
                def replaced_named(path,*args,**kwargs):
                    if str(path)==str(node):
                        calls[0]+=1
                        if calls[0]==2:
                            node.rename(root/'old-device');node.mkdir(mode=0o700)
                    return real_stat(path,*args,**kwargs)
                with patch.object(physical.os,'stat',replaced_named):
                    try:physical._connection_observation(str(node))
                    except RuntimeError:pass
                    else:raise AssertionError('replaced device directory accepted')
            passed.append('Linux actual OFD directory/attribute reads verify inode and reject symlink, writable attributes and replacement during observation')
    else:
        deferred.append('actual protected directory/OFD/attribute test requires root Linux; portable cases model the observation primitive')
    return dict(status='PASS',scope='USB connection fixtures only; no native audio or device writes',device_writes=0,cases=passed,deferred=deferred)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib',type=Path,default=Path(__file__).resolve().parent/'working/lib')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();raw=json.dumps(run(args.lib.resolve()),indent=2)+'\n'
    if args.output:args.output.write_text(raw)
    print(raw,end='')
