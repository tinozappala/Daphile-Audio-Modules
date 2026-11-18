"""Fixture checks for truthful physical USB discovery and player readiness.

These checks do not qualify real hardware or a third-party DSP. Linux native
checks may pass the staged code directory as the sole command-line argument.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

stage=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/'working'
sys.path.insert(0,str(stage/'lib'))
import audio_context
import audio_setup
import detection
import player
import physical_audio
from autotune_v2 import native_target
from usb_target import identity

MAC='00:11:22:33:44:55'
USB=dict(path='1-2',idVendor='1234',idProduct='5678',serial='fixture-dac',product='Fixture USB DAC')
PROCS=[dict(pid=120,name='squeezelite',threads=[dict(tid=121)])]


def card(number,usb_path=None,state='RUNNING',owner=121):
    pcm=dict(device=0,subdevice=0,active=state!='CLOSED',
        status='state: '+state+'\nowner_pid : '+str(owner),
        hw=dict(rate='192000 (192000/1)',channels='2',format='S32_LE',buffer_size='19200',period_size='6400'))
    return dict(card=number,id='USB' if usb_path else 'Loopback',usb_path=usb_path,
        sysfs='/sys/devices/fixture'+str(number),pcms=[pcm])


def observed_usb():
    return dict(dac=detection._native_selected_dac([card(3,'1-2')],PROCS,[USB]))


def physical_card(number=1,bus='pci',driver='snd_hda_intel',alsa_id='PCH'):
    value=card(number)
    value.update(id=alsa_id,bus=bus,driver=driver,
        sysfs='/sys/devices/pci0000:00/0000:00:1f.3' if bus=='pci' else '/sys/devices/platform/soc/12340000.audio')
    return value


class Clock:
    def __init__(self):self.now=0
    def monotonic(self):return self.now
    def sleep(self,duration):self.now+=duration


class Discovery(unittest.TestCase):
    def test_virtual_feed_never_claims_verified_physical_dac(self):
        self.assertEqual(detection._native_selected_dac([card(0)],PROCS,[USB]),dict(status='unknown',candidates=0))

    def test_usb_running_native_owner_is_verified(self):
        result=observed_usb()['dac']
        self.assertEqual((result['status'],result['card'],result['owner_pid']),('verified-active',3,121))
        self.assertEqual(result['identity'],identity(USB))

    def test_prepared_usb_is_not_running(self):
        result=detection._native_selected_dac([card(3,'1-2','PREPARED')],PROCS,[USB])
        self.assertEqual(result['status'],'unknown')

    def test_closed_usb_with_running_loopback_stays_unknown(self):
        result=detection._native_selected_dac([card(0),card(3,'1-2','CLOSED')],PROCS,[USB])
        self.assertEqual(result['status'],'unknown')

    def test_usb_identity_must_be_unique_and_identified(self):
        for devices in ([USB,dict(USB)], [dict(path='1-2')], []):
            with self.subTest(devices=devices):
                result=detection._native_selected_dac([card(3,'1-2')],PROCS,devices)
                self.assertEqual(result['status'],'unknown')

    def test_optional_processor_contract_is_preserved(self):
        external=copy.deepcopy(observed_usb()['dac'])
        external.update(owner_pid=900,owner_contract=dict(role='pcm-processor',context_sha256='a'*64))
        with patch.object(audio_context,'select',return_value=external):
            self.assertEqual(detection.selected_dac([card(0)],PROCS,[USB]),external)

    def test_unobserved_physical_metadata_is_not_guessed(self):
        physical=card(1);physical.update(id='HDA',sysfs='/sys/devices/pci0000:00/0000:00:1b.0')
        with patch.object(audio_context,'kernel_owner') as observe:
            self.assertEqual(audio_context.select([physical],[])['status'],'unknown')
            observe.assert_not_called()

    def test_physical_pci_and_platform_outputs_are_discovered(self):
        for physical in (physical_card(),physical_card(bus='platform',driver='soc_i2s',alsa_id='SoCAudio')):
            with self.subTest(bus=physical['bus']):
                found=detection._native_selected_dac([physical],PROCS,[])
                self.assertEqual(found['status'],'verified-active')
                self.assertIsNone(found['usb'])
                self.assertEqual(found['identity'],physical_audio.identity(physical,[]))

    def test_physical_hdmi_device_coordinates_are_retained(self):
        physical=physical_card(alsa_id='HDMI');physical['pcms'][0]['device']=7
        found=detection._native_selected_dac([physical],PROCS,[])
        self.assertEqual((found['status'],found['device']),('verified-active',7))

    def test_software_only_driver_and_virtual_path_are_refused(self):
        for physical in (physical_card(driver='snd_dummy'),dict(physical_card(),sysfs='/sys/devices/virtual/sound/card0')):
            with self.subTest(card=physical):
                self.assertEqual(detection._native_selected_dac([physical],PROCS,[])['status'],'unknown')

    def test_multiple_physical_outputs_are_ambiguous(self):
        self.assertEqual(detection._native_selected_dac([physical_card(),card(3,'1-2')],PROCS,[USB])['status'],'ambiguous')

    def test_external_non_usb_physical_output_is_read_only(self):
        physical=physical_card();observation=dict(role='pcm-processor',read_only=True,context_sha256='a'*64)
        with (patch.object(audio_context,'owners',return_value={}),
              patch.object(audio_context,'kernel_owner',return_value=observation)):
            found=audio_context.select([physical],[])
        self.assertEqual((found['status'],found['transport']),('verified-active','alsa-physical'))
        self.assertIsNone(found['usb']);self.assertTrue(found['owner_contract']['read_only'])


class NativeCapabilities(unittest.TestCase):
    def rate(self,physical,rates,descriptor=None,key='PCM_RATES',settings='RESAMPLE=exception\nRATELIMIT=0'):
        physical=copy.deepcopy(physical)
        physical['declared_pcm_rates']=descriptor or []
        hardware=dict(alsa=dict(cards=[physical]),usb=[USB] if physical.get('usb_path') else [])
        target=dict(card=physical['card'],device=physical['pcms'][0]['device'],alsa_id=physical['id'],path='/fixture/volume.conf',
            identity=physical_audio.identity(physical,hardware['usb']))
        def read(path):
            if str(path).startswith('/run/spacd/info/'):return key+'='+rates
            return settings
        return native_target.rate(hardware,target,read)

    def test_non_usb_rates_use_daphile_capability_when_no_stream_descriptor(self):
        self.assertEqual(self.rate(physical_card(),'44100 48000 96000',key='RATES'),96000)

    def test_available_stream_descriptor_constrains_daphile_rates(self):
        self.assertEqual(self.rate(card(3,'1-2'),'44100 96000 192000',[44100,96000]),96000)

    def test_missing_rates_and_virtual_output_are_refused(self):
        with self.assertRaisesRegex(RuntimeError,'No controlled PCM rate'):self.rate(physical_card(),'')
        with self.assertRaisesRegex(RuntimeError,'Physical native output'):self.rate(card(0),'44100 48000')

    def test_active_non_usb_target_uses_native_card_mapping(self):
        physical=physical_card()
        active=detection._native_selected_dac([physical],PROCS,[])
        api=unittest.mock.Mock();api.target.return_value=dict(card=1,alsa_id='PCH',mac=MAC)
        self.assertEqual(native_target.resolve(dict(dac=active),player=api),api.target.return_value)
        api.target.assert_called_once()

    def test_rate_capability_uses_the_selected_hdmi_device(self):
        physical=physical_card(alsa_id='HDMI')
        physical['pcms'].append(dict(physical['pcms'][0],device=7))
        hardware=dict(alsa=dict(cards=[physical]),usb=[])
        target=dict(card=1,device=7,alsa_id='HDMI',path='/fixture/volume.conf',identity=physical_audio.identity(physical,[]))
        def read(path):
            if str(path).endswith('/1.0'):return 'PCM_RATES=44100 48000 192000'
            if str(path).endswith('/1.7'):return 'PCM_RATES=48000'
            return 'RESAMPLE=exception\nRATELIMIT=0'
        self.assertEqual(native_target.rate(hardware,target,read),48000)

    def test_native_conversion_is_refused_without_changing_preferences(self):
        for settings in ('RESAMPLE=always\nRATELIMIT=0','RESAMPLE=exception\nRATELIMIT=96000'):
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(RuntimeError,'ricampionamento nativo corrente'):
                    self.rate(physical_card(),'44100 48000 96000',key='RATES',settings=settings)

    def test_unobserved_native_resampling_settings_are_not_assumed(self):
        with self.assertRaisesRegex(RuntimeError,'ricampionamento nativo corrente'):
            self.rate(physical_card(),'44100 48000',settings='')


class SetupStages(unittest.TestCase):
    def report(self,external,upstream=None):
        hardware=observed_usb();hardware.update(cpu={},pci=[],irq=[],alsa=dict(cards=[]),squeezelite=[])
        if external:hardware['dac']['owner_contract']=dict(role='pcm-processor')
        with (patch.object(player,'target',return_value=dict(path='/fixture/volume.conf')),
              patch.object(audio_setup,'read',return_value='BUFFERTIME=40\nPERIODCOUNT=2'),
              patch.object(audio_setup,'_upstream_pcm',return_value=upstream),
              patch('t630.eligibility',return_value=dict(eligible=False))):
            return audio_setup.report(hardware)

    def test_external_output_buffers_are_not_compared_to_native_preferences(self):
        found=self.report(True)
        self.assertFalse(any('configured and effective' in issue for issue in found['issues']))
        self.assertEqual((found['effective']['buffer_ms'],found['configured']['BUFFERTIME']),(100, '40'))
        self.assertEqual(found['buffer_comparison_stage'],'unavailable')

    def test_external_native_feed_can_be_compared_when_observed(self):
        found=self.report(True,dict(effective=dict(buffer_ms=40,periods=2)))
        self.assertFalse(found['issues'])
        self.assertEqual(found['buffer_comparison_stage'],'native-player-feed')

    def test_direct_native_buffer_mismatch_is_still_reported(self):
        found=self.report(False)
        self.assertTrue(any('configured and effective' in issue for issue in found['issues']))

    def test_upstream_mapping_selects_only_the_configured_cards_native_pid(self):
        with tempfile.TemporaryDirectory(prefix='autotune-feed-') as directory:
            runtime=Path(directory);pid=runtime/'3'/'d'/'0'/'pid';pid.parent.mkdir(parents=True);pid.write_text('120')
            wanted=card(0);other=card(2,owner=222)
            hardware=dict(alsa=dict(cards=[wanted,other]),squeezelite=PROCS+[dict(pid=220,name='squeezelite',threads=[dict(tid=222)])])
            found=audio_setup._upstream_pcm(hardware,dict(card=3),runtime)
            self.assertEqual((found['card'],found['owner_pid']),(0,121))


class Readiness(unittest.TestCase):
    def run_wait(self,observations,mapped=MAC,expected_error=False):
        clock=Clock()
        sequence=iter(observations)
        current=[observations[-1]]
        def scan():
            current[0]=next(sequence,current[0]);return current[0]
        with (patch.object(player,'scan',side_effect=scan),
              patch.object(player,'target',return_value=dict(mac=mapped)) as target,
              patch.object(player,'rpc',return_value=dict(mode='play')) as rpc,
              patch.object(player.time,'monotonic',side_effect=clock.monotonic),
              patch.object(player.time,'sleep',side_effect=clock.sleep)):
            if expected_error:
                with self.assertRaisesRegex(RuntimeError,'did not resume'):
                    player.wait_audio(MAC,timeout=2,expected_rate=192000)
                result=None
            else:result=player.wait_audio(MAC,timeout=2,expected_rate=192000)
        return result,target,rpc,clock

    def test_legacy_virtual_verified_claim_does_not_satisfy_wait(self):
        legacy=dict(dac=dict(status='verified-active',pcm=card(0)['pcms'][0],usb=None))
        result,target,rpc,clock=self.run_wait([legacy],expected_error=True)
        target.assert_not_called();rpc.assert_not_called()

    def test_deferred_usb_waits_for_the_physical_pcm(self):
        idle=dict(dac=dict(status='unknown',candidates=0))
        physical=observed_usb()
        result,target,rpc,clock=self.run_wait([idle,idle,physical])
        self.assertIs(result,physical)
        self.assertEqual(clock.now,1)
        target.assert_called_once_with(physical)
        rpc.assert_called_once_with(MAC,['status','-','1'])

    def test_another_player_cannot_satisfy_readiness(self):
        result,target,rpc,clock=self.run_wait([observed_usb()],mapped='00:11:22:33:44:66',expected_error=True)
        self.assertTrue(target.called);rpc.assert_not_called()

    def test_matching_physical_usb_fixture_is_accepted(self):
        physical=observed_usb()
        result,target,rpc,clock=self.run_wait([physical],mapped=MAC.upper())
        self.assertIs(result,physical)
        self.assertEqual(clock.now,0)
        target.assert_called_once_with(physical)

    def test_matching_non_usb_physical_fixture_is_accepted(self):
        physical=dict(dac=detection._native_selected_dac([physical_card()],PROCS,[]))
        result,target,rpc,clock=self.run_wait([physical])
        self.assertIs(result,physical)
        target.assert_called_once_with(physical)

    def test_usb_parameters_do_not_override_unidentified_target(self):
        physical=observed_usb();physical['dac']['identity']=dict(serial='different-dac')
        clock=Clock()
        with (patch.object(player,'scan',return_value=physical),
              patch.object(player,'rpc') as rpc,
              patch.object(player.time,'monotonic',side_effect=clock.monotonic),
              patch.object(player.time,'sleep',side_effect=clock.sleep)):
            with self.assertRaisesRegex(RuntimeError,'did not resume'):
                player.wait_audio(MAC,timeout=1,expected_rate=192000)
            rpc.assert_not_called()


@unittest.skipUnless(os.name=='posix' and getattr(os,'geteuid',lambda:-1)()==0,
    'Root Linux filesystem/character-device fixtures required')
class KernelObservation(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='autotune-audio-context-')
        self.root=Path(self.temporary.name);self.proc=self.root/'proc';self.snd=self.root/'snd'
        self.config=self.root/'state'/'engine-config.json'
        self.write(self.config,json.dumps(dict(devices=dict(samplerate=192000))))
        self.write(self.config.parent/'active_profile.json','{"gain":0}')
        self.write(self.config.parent/'last_valid_config.json','{"gain":0}')
        self.write(self.config.parent/'peq-intent.json','{"enabled":true}')
        self.raw=b'/opt/fixture/audio-engine\0'+os.fsencode(self.config)+b'\0'
        self.write(self.proc/'900'/'cmdline',self.raw)
        self.write(self.proc/'900'/'stat',self.stat(900,'501'))
        self.write(self.proc/'900'/'task'/'901'/'stat',self.stat(901,'502'))
        self.write(self.proc/'901'/'status','Tgid:\t900\nUid:\t0\t0\t0\t0\n')
        self.physical=card(3,'1-2',owner=901);self.pcm=self.physical['pcms'][0]
        self.endpoint=self.proc/'asound'/'card3'/'pcm0p'/'sub0'
        self.write(self.endpoint/'status',self.pcm['status'])
        self.write(self.endpoint/'hw_params','\n'.join(k+': '+v for k,v in self.pcm['hw'].items()))
        self.fd=self.proc/'900'/'fd'/'5';self.fd.parent.mkdir(parents=True)
        self.fd.symlink_to('/dev/null')
        self.snd.mkdir();(self.snd/'pcmC3D0p').symlink_to('/dev/null')

    def tearDown(self):self.temporary.cleanup()

    @staticmethod
    def stat(pid,start):return str(pid)+' (fixture engine) '+' '.join(['S']+['0']*18+[start]+['0']*10)

    @staticmethod
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        if isinstance(value,bytes):path.write_bytes(value)
        else:path.write_text(value)
        path.chmod(0o600)

    def observe(self):return audio_context.kernel_owner(self.physical,self.pcm,901,proc=self.proc,snd=self.snd,usb=[USB])

    def test_matching_device_fd_and_live_process_are_read_only_observations(self):
        observed=self.observe()
        self.assertEqual((observed['process_pid'],observed['process_start'],observed['thread_start']),(900,'501','502'))
        self.assertTrue(observed['read_only'])
        self.assertEqual(observed['role'],'pcm-processor')
        self.assertRegex(observed['context_sha256'],'^[a-f0-9]{64}$')
        self.assertFalse((self.root/'owners.json').exists())

    def test_matching_physical_non_usb_device_fd_is_observed(self):
        self.physical=physical_card(3);self.physical['pcms']=[self.pcm]
        observed=self.observe()
        self.assertEqual(observed['observation'],'kernel-physical-pcm')
        self.assertTrue(observed['read_only'])

    def test_mismatched_device_fd_is_refused(self):
        self.fd.unlink();self.fd.symlink_to('/dev/zero')
        with self.assertRaisesRegex(RuntimeError,'does not hold'):self.observe()

    def test_nonroot_pcm_credentials_are_refused(self):
        self.write(self.proc/'901'/'status','Tgid:\t900\nUid:\t1000\t1000\t1000\t1000\n')
        with self.assertRaisesRegex(RuntimeError,'credentials'):self.observe()

    def test_changed_pcm_status_during_observation_is_refused(self):
        original=audio_context.configuration_context
        def change(arguments):
            result=original(arguments)
            self.write(self.endpoint/'status','state: PREPARED\nowner_pid : 901')
            return result
        with patch.object(audio_context,'configuration_context',side_effect=change):
            with self.assertRaisesRegex(RuntimeError,'not running'):self.observe()

    def test_changed_device_fd_during_observation_is_refused(self):
        original=audio_context.configuration_context
        def change(arguments):
            result=original(arguments)
            self.fd.unlink();self.fd.symlink_to('/dev/zero')
            return result
        with patch.object(audio_context,'configuration_context',side_effect=change):
            with self.assertRaisesRegex(RuntimeError,'device FD changed'):self.observe()

    def test_reused_process_during_observation_is_refused(self):
        original=audio_context.configuration_context
        def change(arguments):
            result=original(arguments)
            self.write(self.proc/'900'/'stat',self.stat(900,'999'))
            return result
        with patch.object(audio_context,'configuration_context',side_effect=change):
            with self.assertRaisesRegex(RuntimeError,'owner changed'):self.observe()

    def test_live_profile_and_enable_changes_change_the_binding(self):
        for name in ('active_profile.json','last_valid_config.json','peq-intent.json'):
            with self.subTest(name=name):
                before=self.observe()['context_sha256']
                self.write(self.config.parent/name,'{"changed":true}')
                self.assertNotEqual(self.observe()['context_sha256'],before)

    def test_audio_logs_are_not_hashed(self):
        before=self.observe()['context_sha256']
        self.write(self.config.parent/'audio-pipeline.log','unrelated log growth')
        self.assertEqual(self.observe()['context_sha256'],before)

    def test_configuration_mutation_while_read_is_refused(self):
        original=os.fstat;calls=[]
        def change(fd):
            result=original(fd);calls.append(fd)
            if len(calls)==1:self.write(self.config,'{"different":"size"}')
            return result
        with patch.object(audio_context.os,'fstat',side_effect=change):
            with self.assertRaisesRegex(RuntimeError,'changed while observed'):
                audio_context._file_observation(self.config,2*1024*1024)

    def test_configuration_count_is_bounded(self):
        for index in range(33):self.write(self.config.parent/(str(index)+'.json'),'{}')
        with self.assertRaisesRegex(RuntimeError,'count exceeds bound'):self.observe()

    def test_live_scan_skips_report_subprocesses_but_keeps_runtime_fields(self):
        with (patch.object(detection,'command',side_effect=AssertionError('Report subprocess invoked')),
              patch.object(detection,'executable_help',side_effect=AssertionError('Executable help invoked'))):
            found=detection.live_scan()
        self.assertIsInstance(found['alsa']['cards'],list)
        self.assertIn('configuration',found);self.assertIn('irq',found)
        self.assertIsNone(found['openrc'])
        for interface in found['network']:
            self.assertIsNone(interface['offload']);self.assertIsNone(interface['eee']);self.assertIsNone(interface['moderation'])


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    outcome=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
