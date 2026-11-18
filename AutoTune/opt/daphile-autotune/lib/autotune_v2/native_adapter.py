"""Native, reversible trial adapter for an already playing controlled fixture.

No persistent application or playlist replacement occurs here. The surrounding
workflow owns fixture selection, its independent recovery lease, and cleanup.
"""
import copy,hashlib,json,math,os,re,stat,types,uuid,wave
from pathlib import Path
try:from measurement_contract import BRACKET_PROTOCOL
except ImportError:from ..measurement_contract import BRACKET_PROTOCOL
try:from playback_contract import standalone,validate
except ImportError:from ..playback_contract import standalone,validate


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def independent_coordinates(inventory):
    """Do not let a placement regression hide a useful scheduling policy.

    The native change factory and every guard still validate each derived
    coordinate. Deduplication below uses actual controls, not candidate names.
    The coupled candidate remains available for later comparison.
    """
    result=[]
    for original in inventory['candidates']:
        spec=copy.deepcopy(original)
        if spec.get('stage')=='scheduling' and 'output' in spec and spec.get('policy') is not None:
            priority={k:v for k,v in spec.items() if k not in ('output','irq')}
            priority.update(name='priority-only-'+str(spec['policy'])+'-'+str(spec['priority']),coordinate='priority')
            placement={k:v for k,v in spec.items() if k not in ('policy','priority')}
            placement.update(name='placement-only-'+str(spec['output'])+'-'+str(spec['irq']),coordinate='placement')
            result.extend((priority,placement))
        result.append(spec)
    return result


def native_api():
    import engine,player,search,comparison,measure,policy_restore
    from detection import scan,read
    from native_coordination import Lease
    from quiet_playback import qualified_lms
    return types.SimpleNamespace(engine=engine,player=player,search=search,comparison=comparison,measure=measure,
                                 native_digest=policy_restore.native_digest,scan=scan,read=read,fixture_root=Path('/mnt/hdd/daphile-autotune-samples'),coordinate=Lease,verify_restore=qualified_lms)


class NativeAdapter:
    def __init__(self,state,workload,api=None):
        if not isinstance(workload,dict) or set(workload)!={'path','sha256','rate','bits','channels','scope'}:
            raise ValueError('Exact controlled workload definition required')
        if workload['scope']!='owned-native-pcm' or type(workload['rate']) is not int or workload['bits'] not in (24,32) or workload['channels']!=2:
            raise ValueError('Controlled native PCM scope required')
        self.root=Path(state);self.api=api or native_api();self.workload=copy.deepcopy(workload)
        self.controls=[];self.inventory={'candidates':[]};self.identifier=uuid.uuid4().hex
        self.h=self.api.scan();self.api.player.assert_library_idle(self.h);self.target=self.api.player.target(self.h)
        self.preferences=Path(self.target['path']).read_bytes()
        self.source=self.api.comparison.playback(self.target['mac'])
        self.fixture_playback=copy.deepcopy(validate(self.api.player.playback_snapshot(self.target['mac'])))
        self.fixture_pin=self.verify_fixture()
        self.verify_workload(self.h)
        self.inventory=self.safe_inventory(self.api.search.specs(self.h))
        self.coordination=self.api.coordinate(self.h,self.target) if hasattr(self.api,'coordinate') else None
        try:self.renew()
        except BaseException:
            self.close();raise

    def renew(self):
        if self.coordination:self.coordination.renew(self.api.scan())

    def close(self):
        if self.coordination:
            self.coordination.close();self.coordination=None

    def observe(self,seconds):
        # Every native observation is <=30s, while the native claim expires
        # after60s. No renewal thread can outlive failure or silently take
        # ownership after a changed setting. USB protection stays native.
        if seconds>30 and self.coordination:raise ValueError('Coordinated observation exceeds renewal interval')
        self.renew();standalone(self.target['mac'],self.api.player.rpc,getattr(self.api,'verify_restore',None))
        result=self.api.measure.observe(seconds)
        standalone(self.target['mac'],self.api.player.rpc,getattr(self.api,'verify_restore',None))
        self.renew();return result

    def controlled_observation(self,seconds):
        """Measure the same owned PCM segment after its running state is read."""
        self.verify_workload(self.api.scan())
        self.api.player.rpc(self.target['mac'],['time','0'])
        self.api.player.wait_audio(self.target['mac'],20,expected_rate=self.workload['rate'])
        position=self.api.comparison.playback(self.target['mac']).get('position')
        if type(position) not in (int,float) or not math.isfinite(position) or not 0<=position<=2:
            raise RuntimeError('Controlled observation start position not verified')
        return self.observe(seconds)

    @staticmethod
    def file_pin(path):
        value=path.stat()
        return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_uid,stat.S_IMODE(value.st_mode))

    def verify_fixture(self):
        path=Path(self.workload['path'])
        if path.parent!=self.api.fixture_root or path.is_symlink() or not path.is_file() or not re.fullmatch(r'[a-f0-9]{32}\.wav',path.name):
            raise RuntimeError('Root-owned generated fixture required')
        before=self.file_pin(path)
        if os.name=='posix' and (before[-2]!=os.geteuid() or before[-1]&0o022):raise RuntimeError('Fixture permissions are not private to its writer')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            while block:=stream.read(1024*1024):digest.update(block)
        with wave.open(str(path),'rb') as stream:
            valid=(stream.getframerate()==self.workload['rate'] and stream.getsampwidth()*8==self.workload['bits']
                   and stream.getnchannels()==2 and stream.getnframes()>=45*self.workload['rate'])
        if not valid or self.file_pin(path)!=before or digest.hexdigest()!=self.workload['sha256']:
            raise RuntimeError('Controlled fixture changed')
        return before

    def verify_workload(self,h):
        path=Path(self.workload['path'])
        # Hash the whole owned sample once, outside the observations. Inode,
        # size, mtime AND ctime protect subsequent checks without repeatedly
        # reading hundreds of MB and loading the server during a trial.
        if path.is_symlink() or self.file_pin(path)!=self.fixture_pin:raise RuntimeError('Controlled fixture changed')
        current=self.api.player.playback_snapshot(self.target['mac'])
        validate(current)
        expected=self.api.player.playlist_key(path.resolve().as_uri())
        keys=[self.api.player.playlist_key(u) for u in current['urls']]
        original_keys=[self.api.player.playlist_key(u) for u in self.fixture_playback['urls']]
        if (not keys or keys!=original_keys or keys[-1]!=expected or keys.count(expected)!=1
                or current['index']!=len(keys)-1 or current['mode']!='play'):
            raise RuntimeError('Controlled playlist changed')
        if any(current[k]!=self.fixture_playback[k] for k in ('volume','mute','power','repeat','shuffle')) or any(current[k]!=0 for k in ('repeat','shuffle')) or current['power']!=1:
            raise RuntimeError('Controlled player gain, mute, power or playback flags changed')
        observed=self.api.comparison.playback(self.target['mac'])
        if any(observed[k]!=self.source[k] for k in ('sample_hash','volume')):raise RuntimeError('Controlled source or volume changed')
        hw=h.get('dac',{}).get('pcm',{}).get('hw',{})
        try:valid=int(str(hw['rate']).split()[0])==self.workload['rate'] and int(hw['channels'])==2
        except (KeyError,ValueError):valid=False
        if not valid or hw.get('format') not in ('S24_LE','S24_3LE','S32_LE'):raise RuntimeError('Controlled PCM format not verified')
        standalone(self.target['mac'],self.api.player.rpc,getattr(self.api,'verify_restore',None))

    @staticmethod
    def control_key(change):
        return {k:change[k] for k in ('parameter','path','policy','tid','service','irq') if k in change}

    def safe_inventory(self,inventory):
        candidates=[];deferred=[];seen=set()
        for original in independent_coordinates(inventory):
            spec=copy.deepcopy(original)
            if spec.get('stage')=='reference':candidates.append(spec);continue
            if self.h.get('dac',{}).get('owner_contract') and spec.get('stage')=='scheduling':
                deferred.append(dict(name=spec['name'],reason='external-pcm-thread-policy-owned-by-provider'));continue
            if spec.get('stage')=='network':
                deferred.append(dict(name=spec['name'],reason='dedicated-comparable-network-workload-required'));continue
            try:
                changes=self.api.search.changes(self.h,spec)
                if not changes:
                    deferred.append(dict(name=spec['name'],reason='already-current'));continue
                signature=fingerprint([{**self.control_key(c),'after':c['after']} for c in changes])
                if signature in seen:
                    deferred.append(dict(name=spec['name'],reason='same-native-controls-already-planned'));continue
                for c in changes:self.api.engine.guard(c,self.h)
            except (RuntimeError,ValueError,OSError):
                # Keep the output thread scheduler even if its shared USB IRQ
                # makes an affinity coordinate inadmissible. Recheck every
                # derived coordinate through the native change factory.
                if spec.get('stage')=='scheduling' and 'output' in spec and spec.get('policy') is not None:
                    reduced={k:v for k,v in spec.items() if k not in ('output','irq')}
                    reduced['name']='scheduler-only-'+str(spec['policy'])+'-'+str(spec['priority'])
                    try:
                        changes=self.api.search.changes(self.h,reduced)
                        if not changes:raise ValueError('Already current')
                        for c in changes:self.api.engine.guard(c,self.h)
                        signature=fingerprint([{**self.control_key(c),'after':c['after']} for c in changes])
                        if signature in seen:raise ValueError('Duplicate')
                        spec=reduced
                    except (RuntimeError,ValueError,OSError):
                        deferred.append(dict(name=original['name'],reason='native-control-guard-refused'));continue
                else:
                    deferred.append(dict(name=spec['name'],reason='native-control-guard-refused'));continue
            seen.add(signature);candidates.append(spec)
            for change in changes:
                key=self.control_key(change)
                if not any(self.control_key(old)==key for old in self.controls):self.controls.append(copy.deepcopy(change))
        return dict(candidates=candidates,deferred_candidates=deferred,coverage='Native audio controls; network requires its own observed workload',sound_quality_claim=False)

    def binding(self):
        h=self.api.scan();self.verify_workload(h)
        if h.get('dac',{}).get('owner_contract')!=self.h.get('dac',{}).get('owner_contract'):
            raise RuntimeError('Optional PCM process/thread ownership changed')
        fresh=self.api.player.target(h)
        if fresh!=self.target or Path(fresh['path']).read_bytes()!=self.preferences:raise RuntimeError('Native preferences or target changed')
        native=self.api.native_digest()
        if not isinstance(native,str) or not re.fullmatch('[a-f0-9]{64}',native):raise RuntimeError('Native Settings fingerprint unavailable')
        values=[]
        for c in self.controls:
            self.api.engine.guard(c,h,restoring=True)
            value=self.api.engine.setting_value(c)
            if c['parameter']=='usb_irq_affinity':
                effective=self.api.read('/proc/irq/'+str(c['irq'])+'/effective_affinity_list')
                if not isinstance(effective,str) or not re.fullmatch(r'[0-9]+(?:[-,][0-9]+)*',effective):raise RuntimeError('Effective IRQ placement unavailable')
                value=dict(requested=value,effective=effective)
            values.append(dict(control=self.control_key(c),value=value))
        return dict(boot_id=self.api.engine.boot_id(),hardware_sha256=fingerprint(dict(hardware_id=h['hardware_id'],target=self.target)),
                    preferences_sha256=fingerprint(dict(native_settings=native,player=hashlib.sha256(self.preferences).hexdigest(),runtime=values)),
                    workload_sha256=fingerprint({k:v for k,v in self.workload.items() if k!='path'}))

    def pair(self,spec,seconds):
        self.renew()
        expected_binding=self.binding()
        clean={k:v for k,v in spec.items() if k not in ('screen_family','applicable')}
        label='v2-'+self.identifier+'-'+uuid.uuid4().hex
        try:
            result=self.api.engine.trial(lambda h:self.api.search.changes(h,clean),label,seconds,self.root,False,
                observer=self.controlled_observation,calibration=True,restore_position=False)
        except RuntimeError:
            path=self.root/'transaction.json'
            record=json.loads(path.read_text()) if path.exists() else {}
            # A rejected metric is usable as a rejected observation only after
            # the same owned journal reports and proves full settings recovery.
            if record.get('profile')!=label or record.get('status')!='rolled-back' or not isinstance(record.get('benchmark'),dict):raise
            for c in record['changes']:
                self.api.engine.guard(c,self.api.scan(),restoring=True)
                if self.api.engine.setting_value(c)!=c['before']:raise RuntimeError('Rejected trial restoration not verified')
            result=dict(status='rejected-restored',benchmark=record['benchmark'],restoration='rolled-back')
        self.verify_workload(self.api.scan())
        if result.get('status')=='no-change':
            benchmark=dict(before=result['baseline'],after=self.controlled_observation(seconds))
            changed=False
        else:
            if result.get('status') not in ('trial-passed-restored','rejected-restored') or result.get('restoration')!='rolled-back':
                raise RuntimeError('Native trial did not restore its settings')
            benchmark=result['benchmark'];changed=True
        # A restored value is not a restored measurement. Observe the original
        # configuration again to avoid attributing a falling background load
        # to the candidate. Both bindings bracket this third fresh window.
        if self.binding()!=expected_binding:
            raise RuntimeError('Original native binding not restored before follow-up observation')
        restored=self.controlled_observation(seconds)
        if self.binding()!=expected_binding:
            raise RuntimeError('Native binding changed during restored baseline observation')
        return dict(**benchmark,restored_baseline=restored,comparison_protocol=BRACKET_PROTOCOL,
                    restoration_complete=True,application_observed=changed)

    def commit(self,spec,seconds=30,expected_binding=None):
        """Commit only this measured coordinate after a NEW guarded comparison.

        The outer activity lease remains armed through playlist restoration and
        result publication. This transaction never stores a playback bookmark,
        so recovery cannot replay a now obsolete controlled position.
        """
        from .accounting import issues as accounting_issues
        try:
            from measurement_contract import confirmation_issues,optimization_issues
        except ImportError:
            from ..measurement_contract import confirmation_issues,optimization_issues
        if not isinstance(spec,dict) or spec.get('name')!='combined' or spec.get('stage')!='combined' or len(spec)<=2:
            raise ValueError('Nonempty measured combined coordinate required')
        if not isinstance(expected_binding,dict) or self.binding()!=expected_binding:
            raise RuntimeError('Final application differs from qualified baseline')
        observations=[]
        def observer(duration):
            observation=self.controlled_observation(duration)
            if optimization_issues(observation)+accounting_issues(observation):
                raise RuntimeError('Final application measurement incomplete or unversioned')
            observations.append(copy.deepcopy(observation))
            if len(observations)==2:
                if observations[0]['observation_monotonic_end']>observations[1]['observation_monotonic_start']:
                    raise RuntimeError('Final observations overlap or were reused')
                issues=confirmation_issues(*observations)
                if issues:
                    raise RuntimeError('Final application did not independently confirm improvement: '+'; '.join(issues))
            if len(observations)>2:raise RuntimeError('Unexpected final observation sequence')
            return observation
        def changes(h):
            self.verify_workload(h)
            if self.binding()!=expected_binding:raise RuntimeError('Native runtime changed during final baseline')
            if self.api.player.target(h)!=self.target or Path(self.target['path']).read_bytes()!=self.preferences:
                raise RuntimeError('Final application context changed')
            rows=self.api.search.changes(h,copy.deepcopy(spec))
            if not rows:raise RuntimeError('Measured proposal no longer changes any control')
            permitted={fingerprint(self.control_key(c)) for c in self.controls}
            if any(fingerprint(self.control_key(c)) not in permitted for c in rows):
                raise RuntimeError('Final application introduced an unmeasured control')
            for c in rows:self.api.engine.guard(c,h)
            return rows
        result=self.api.engine.trial(changes,'automatic',seconds,self.root,True,
                                     observer=observer,calibration=True,restore_position=False)
        if result.get('status')!='committed' or len(observations)!=2:
            raise RuntimeError('Final guarded application did not complete')
        return result

__release_signature__ = 'Tino Zappala'
