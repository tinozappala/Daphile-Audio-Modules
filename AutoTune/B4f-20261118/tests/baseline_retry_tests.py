"""Run the actual NativeAdapter observation/pair flow with fake primitives.

No audio device, RPC endpoint, service, timer or production state is touched.
Metric qualification is supplied explicitly; these tests exercise bounded
retry orchestration, guards and accounting rather than the metric validator.
"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

stage=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/'working'
sys.path.insert(0,str(stage/'lib'))
import calibration_budget
import measurement_contract
from autotune_v2 import native_adapter as module
from autotune_v2 import paired_search
from trace_feed import ACCOUNTING_VERSION


class Cancelled(BaseException):pass


def window(*issues,peak=30):
    return dict(test_issues=list(issues),max_cpu_percent=dict(cpu=20,cpu0=peak,cpu1=15),
        scheduler_trace=dict(accounting_version=ACCOUNTING_VERSION),
        telemetry=dict(temperatures=dict(sensor=dict(peak_c=50,headroom_c=35))))


def busy():return window(module.CPU_BUSY_ISSUE,peak=92)


class Clock:
    def __init__(self):self.now=0;self.sleeps=[];self.after_sleep=None
    def sleep(self,seconds):
        self.sleeps.append(seconds);self.now+=seconds
        if self.after_sleep:self.after_sleep()


class Deadline:
    def __init__(self,clock,limit=100):self.clock=clock;self.limit=limit;self.requirements=[]
    def require(self,seconds):
        self.requirements.append((self.clock.now,seconds))
        if self.clock.now+seconds>self.limit:raise calibration_budget.BudgetExpired('Original operational deadline')


class Engine:
    def __init__(self,fixture):
        self.fixture=fixture;self.pending=False;self.events=[];self.factory_calls=0;self.writes=0;self.idle_checks=0
    def atomic(self,path,value):Path(path).write_text(json.dumps(value))
    def load(self,path):return json.loads(Path(path).read_text())
    def log(self,root,value):self.events.append(copy.deepcopy(value))
    def ensure_idle_transaction(self,root):
        self.idle_checks+=1
        if self.pending:raise RuntimeError('Pending transaction retained')
    def trial(self,factory,profile,seconds,root,commit,*,observer,**kwargs):
        before=observer(seconds)
        self.factory_calls+=1;changes=factory(self.fixture.scan())
        if not changes:return dict(status='no-change',baseline=before)
        self.pending=True;self.writes+=1;self.fixture.policy='performance'
        try:after=observer(seconds)
        except BaseException:
            self.pending=False;self.fixture.policy='powersave'
            self.atomic(Path(root)/'transaction.json',dict(profile=profile,status='rolled-back',changes=[]))
            raise
        self.pending=False;self.fixture.policy='powersave'
        return dict(status='trial-passed-restored',restoration='rolled-back',benchmark=dict(before=before,after=after))


class Fixture:
    def __init__(self,root,observations,budget=True):
        self.root=Path(root);self.clock=Clock();self.deadline=Deadline(self.clock)
        self.observations=list(observations);self.measured=[];self.seeks=[];self.policy='powersave'
        self.queue_changed=False;self.config_changed=False;self.after_measure=None
        self.engine=Engine(self)
        self.adapter=module.NativeAdapter.__new__(module.NativeAdapter)
        self.adapter.root=self.root;self.adapter.target=dict(mac='fixture-player');self.adapter.workload=dict(rate=192000)
        self.adapter.identifier='fixture';self.adapter.coordination=None;self.adapter.controls=[]
        self.adapter.observation_budget=None;self.adapter.baseline_retry_count=0
        if budget:self.adapter.bind_observation_budget(self.deadline)
        self.adapter.api=types.SimpleNamespace(
            engine=self.engine,scan=self.scan,
            player=types.SimpleNamespace(rpc=self.rpc,wait_audio=lambda *a,**k:None,assert_library_idle=lambda h:None),
            comparison=types.SimpleNamespace(playback=lambda mac:dict(position=0)),
            measure=types.SimpleNamespace(observe=self.measure),
            search=types.SimpleNamespace(changes=lambda h,spec:[dict(parameter='governor_search',before='powersave',after='performance')]))
        self.adapter.verify_workload=self.verify_workload;self.adapter.binding=self.binding
    def scan(self):return dict(source='fresh-fixture-scan')
    def verify_workload(self,hardware):
        if self.queue_changed:raise RuntimeError('Controlled playlist changed')
    def binding(self):
        self.verify_workload(self.scan())
        return dict(settings='changed' if self.config_changed else self.policy)
    def rpc(self,mac,command):
        if command!=['time','0']:raise AssertionError('Unexpected fake command')
        self.seeks.append((self.clock.now,command))
    def measure(self,duration,**kwargs):
        value=self.observations.pop(0)
        if isinstance(value,BaseException):raise value
        value=copy.deepcopy(value)
        value.update(observation_monotonic_start=self.clock.now,
            observation_monotonic_end=self.clock.now+duration,duration=duration)
        self.clock.now+=duration;self.measured.append(value)
        if self.after_measure:self.after_measure(len(self.measured))
        return value
    def pair(self):return self.adapter.pair(dict(name='governor-performance',stage='energy',governor='performance'),10)


class BaselineRetry(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='autotune-baseline-retry-')
        self.root=Path(self.temporary.name)
        self.qualification=patch.object(measurement_contract,'optimization_issues',side_effect=lambda value,**kwargs:value['test_issues'])
        self.standalone=patch.object(module,'standalone',return_value={})
        self.qualification.start();self.standalone.start()
    def tearDown(self):self.standalone.stop();self.qualification.stop();self.temporary.cleanup()
    def run_fixture(self,fixture):
        with patch.object(module.time,'sleep',side_effect=fixture.clock.sleep):return fixture.pair()

    def test_one_busy_baseline_then_fresh_valid_windows(self):
        fixture=Fixture(self.root,[busy(),window(),window(),window()])
        result=self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),fixture.engine.factory_calls,fixture.engine.writes),(4,1,1))
        self.assertEqual(fixture.clock.sleeps,[2]);self.assertEqual(fixture.adapter.baseline_retry_count,1)
        self.assertEqual(fixture.deadline.requirements,[(10,12),(12,10)])
        before=result['before'];retry=before['baseline_retry']
        self.assertEqual((before['observation_monotonic_start'],before['observation_monotonic_end']),(12,22))
        self.assertEqual(retry['discarded_observations'][0]['observation_monotonic_end'],10)
        self.assertEqual(retry['accepted_attempt'],2)
        self.assertEqual(fixture.engine.events[0]['measurement']['max_cpu_percent']['cpu0'],92)
        self.assertEqual(fixture.engine.events[0]['event'],'baseline-busy-window-discarded')
        self.assertTrue(result['restoration_complete']);self.assertEqual(fixture.deadline.limit,100)

    def test_two_busy_baselines_stop_before_factory_or_write(self):
        fixture=Fixture(self.root,[busy(),busy()])
        with self.assertRaises(module.ObservationRejected):self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),fixture.engine.factory_calls,fixture.engine.writes),(2,0,0))
        self.assertEqual(fixture.adapter.baseline_retry_count,1);self.assertEqual(fixture.clock.sleeps,[2])

    def test_other_issues_and_invalid_cpu_values_are_not_retried(self):
        failures=[window('Scheduler integrity unavailable or invalid: lost_event_markers'),
            window(module.CPU_BUSY_ISSUE,'Comparable scheduler clock/wakeup observation required',peak=92),
            window(module.CPU_BUSY_ISSUE,peak=float('nan')),window(module.CPU_BUSY_ISSUE,peak=101),
            window('Selected PCM observation incomplete',peak=92)]
        for index,value in enumerate(failures):
            with self.subTest(index=index):
                fixture=Fixture(self.root,[value])
                with self.assertRaises(module.ObservationRejected):self.run_fixture(fixture)
                self.assertEqual((len(fixture.measured),len(fixture.seeks),fixture.engine.writes),(1,1,0))
                self.assertEqual(fixture.clock.sleeps,[]);self.assertEqual(fixture.adapter.baseline_retry_count,0)

    def test_queue_configuration_and_pending_transaction_changes_prevent_another_seek(self):
        for during_settle in (False,True):
            for change in ('queue_changed','config_changed','pending'):
                with self.subTest(change=change,during_settle=during_settle):
                    fixture=Fixture(self.root,[busy(),window()])
                    def alter():setattr(fixture.engine if change=='pending' else fixture,change,True)
                    if during_settle:fixture.clock.after_sleep=alter
                    else:fixture.after_measure=lambda count:alter() if count==1 else None
                    with self.assertRaises(RuntimeError):self.run_fixture(fixture)
                    self.assertEqual((len(fixture.seeks),fixture.engine.factory_calls,fixture.engine.writes),(1,0,0))
                    self.assertEqual(fixture.adapter.baseline_retry_count,0)

    def test_post_apply_busy_window_is_never_retried(self):
        for observations,expected in (([window(),busy()],0),([busy(),window(),busy()],1)):
            with self.subTest(expected=expected):
                fixture=Fixture(self.root,observations)
                with self.assertRaises(module.ObservationRejected):self.run_fixture(fixture)
                self.assertEqual(fixture.adapter.baseline_retry_count,expected)
                self.assertEqual((fixture.engine.factory_calls,fixture.engine.writes),(1,1))
                self.assertFalse(fixture.engine.pending)
                self.assertEqual(fixture.policy,'powersave')

    def test_restored_baseline_busy_window_is_never_retried(self):
        fixture=Fixture(self.root,[window(),window(),busy()])
        expected_binding=fixture.binding()
        result=self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),fixture.adapter.baseline_retry_count),(3,0))
        self.assertEqual(fixture.clock.sleeps,[])
        self.assertEqual(result['restored_baseline']['max_cpu_percent']['cpu0'],92)
        self.assertTrue(result['restoration_complete'])
        self.assertEqual(fixture.binding(),expected_binding)
        self.assertEqual(fixture.engine.events[-1]['event'],'restored-baseline-cpu-window-rejected')
        self.assertFalse(fixture.engine.events[-1]['retried'])

    def test_other_restored_errors_still_abort(self):
        for issues in (('Scheduler integrity unavailable or invalid: buffer_loss_count',),
                (module.CPU_BUSY_ISSUE,'Comparable scheduler clock/wakeup observation required')):
            with self.subTest(issues=issues):
                fixture=Fixture(self.root,[window(),window(),window(*issues,peak=92)])
                with self.assertRaises(module.ObservationRejected):self.run_fixture(fixture)
                self.assertEqual(fixture.clock.sleeps,[])

    def test_restored_busy_window_does_not_hide_configuration_or_queue_changes(self):
        for change in ('queue_changed','config_changed'):
            with self.subTest(change=change):
                fixture=Fixture(self.root,[window(),window(),busy()])
                fixture.after_measure=lambda count:setattr(fixture,change,True) if count==3 else None
                with self.assertRaises(RuntimeError):self.run_fixture(fixture)
                self.assertFalse(any(event['event']=='restored-baseline-cpu-window-rejected' for event in fixture.engine.events))

    def test_no_retry_without_the_original_active_budget(self):
        fixture=Fixture(self.root,[busy()],budget=False)
        with self.assertRaises(module.ObservationRejected):self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),fixture.engine.idle_checks,fixture.adapter.baseline_retry_count),(1,0,0))
        self.assertEqual(fixture.clock.sleeps,[])

    def test_existing_deadline_cannot_be_extended_by_retry(self):
        fixture=Fixture(self.root,[busy(),window()]);fixture.deadline.limit=21
        with self.assertRaises(calibration_budget.BudgetExpired):self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),fixture.clock.now,fixture.deadline.limit),(1,10,21))
        self.assertEqual(fixture.clock.sleeps,[])
        with self.assertRaisesRegex(RuntimeError,'cannot be replaced'):
            fixture.adapter.bind_observation_budget(Deadline(fixture.clock,limit=1000))
        self.assertIs(fixture.adapter.observation_budget,fixture.deadline)

    def test_deadline_is_rechecked_after_settling(self):
        fixture=Fixture(self.root,[busy(),window()]);fixture.deadline.limit=23
        fixture.clock.after_sleep=lambda:setattr(fixture.clock,'now',20)
        with self.assertRaises(calibration_budget.BudgetExpired):self.run_fixture(fixture)
        self.assertEqual((len(fixture.measured),len(fixture.seeks),fixture.adapter.baseline_retry_count),(1,1,0))
        self.assertEqual(fixture.deadline.limit,23)

    def test_cancellation_and_deadline_exceptions_are_not_retried(self):
        for error in (Cancelled('cancelled'),calibration_budget.BudgetExpired('expired')):
            with self.subTest(error=type(error).__name__):
                fixture=Fixture(self.root,[error])
                with self.assertRaises(type(error)):self.run_fixture(fixture)
                self.assertEqual((len(fixture.seeks),fixture.adapter.baseline_retry_count),(1,0))
                self.assertEqual(fixture.clock.sleeps,[])

    def test_final_commit_observer_does_not_retry_busy_baseline(self):
        fixture=Fixture(self.root,[busy()])
        with patch.object(module.time,'sleep',side_effect=fixture.clock.sleep):
            with self.assertRaises(module.ObservationRejected):
                fixture.adapter.commit(dict(name='combined',stage='combined',governor='performance'),10,expected_binding=fixture.binding())
        self.assertEqual((len(fixture.measured),fixture.adapter.baseline_retry_count),(1,0))
        self.assertEqual(fixture.clock.sleeps,[])

    def run_search(self,fixture,regression=False,progress=lambda value:None):
        binding=dict(boot_id='01234567-1234-1234-1234-123456789abc',hardware_sha256='a'*64,
            preferences_sha256='b'*64,workload_sha256='c'*64)
        fixture.adapter.api.search.changes=lambda h,spec:[] if spec['stage']=='reference' else [
            dict(parameter='governor_search',before='powersave',after='performance')]
        adapter=types.SimpleNamespace(binding=lambda:copy.deepcopy(binding),pair=fixture.adapter.pair)
        inventory=dict(candidates=[dict(name='current',stage='reference'),
            dict(name='governor-performance',stage='energy',governor='performance')])
        metrics=dict(cpu=20,context=100,interrupts=100,wakeups=10,latency=.1,thermal=50)
        with (patch.object(module.time,'sleep',side_effect=fixture.clock.sleep),
              patch.object(paired_search,'optimization_issues',side_effect=lambda value,**kwargs:value['test_issues']),
              patch.object(paired_search,'metric',side_effect=lambda value:dict(metrics)),
              patch.object(paired_search,'compare',return_value=['Measured scheduling latency regression'] if regression else [])):
            return paired_search.run(inventory,adapter,types.SimpleNamespace(remaining=lambda:10000),progress=progress)

    def test_search_excludes_restored_cpu_row_without_aborting_recovered_controls(self):
        fixture=Fixture(self.root,[window() for _ in range(11)]+[busy()])
        report=self.run_search(fixture)
        self.assertEqual((len(fixture.measured),fixture.engine.writes,report['complete_comparisons']),(12,1,0))
        self.assertEqual(fixture.policy,'powersave');self.assertFalse(fixture.engine.pending)
        self.assertEqual(fixture.clock.sleeps,[]);self.assertEqual(fixture.adapter.baseline_retry_count,0)
        row=report['screening'][0]
        self.assertIn('restored_baseline: '+module.CPU_BUSY_ISSUE,row['issues'])
        self.assertEqual(row['measurements'][0]['restored_baseline']['max_cpu_percent']['cpu0'],92)
        self.assertFalse(paired_search.screening_promising(row))
        self.assertEqual(report['qualification'],[]);self.assertIsNone(report['proposed_policy'])

    def test_reference_rejected_third_window_still_refuses_search(self):
        fixture=Fixture(self.root,[window(),window(),busy()]);progress=[]
        with self.assertRaisesRegex(paired_search.SearchRefused,'Reference incomplete'):
            self.run_search(fixture,progress=progress.append)
        self.assertEqual((len(fixture.measured),fixture.engine.writes),(3,0))
        self.assertEqual(progress[-1]['complete_comparisons'],0)
        self.assertEqual(progress[-1]['status'],'refused')

    def test_complete_comparison_is_counted_before_regression_rejection(self):
        fixture=Fixture(self.root,[window() for _ in range(12)])
        report=self.run_search(fixture,regression=True)
        self.assertEqual(report['complete_comparisons'],1)
        self.assertTrue(report['screening'][0]['issues'])
        self.assertIsNone(report['proposed_policy'])

    def test_valid_unchanged_alternative_counts_but_has_no_proposal(self):
        fixture=Fixture(self.root,[window() for _ in range(12)])
        report=self.run_search(fixture)
        self.assertEqual(report['complete_comparisons'],1)
        self.assertEqual(report['screening'][0]['issues'],[])
        self.assertIsNone(report['proposed_policy'])

    def test_invalid_accounting_never_counts_as_a_complete_comparison(self):
        observations=[window() for _ in range(12)]
        observations[-1]['scheduler_trace']['accounting_version']='unsupported-historical'
        fixture=Fixture(self.root,observations)
        report=self.run_search(fixture)
        self.assertEqual(report['complete_comparisons'],0)
        self.assertIn('restored_baseline: Scheduler accounting version is historical or unsupported',report['screening'][0]['issues'])


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
