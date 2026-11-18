"""Bounded fixtures for policy metadata recovery; no player or hardware writes.

Run with the installed Linux Python to exercise real directory fsync. Controls,
transaction rollback and value comparisons are modeled, deliberately separate
from native engine/hardware tests. Windows substitutes only directory fsync.
"""
import argparse,copy,importlib.util,json,os,sys,tempfile,types
from pathlib import Path

BASE=Path(__file__).resolve().parent
SOURCE=Path(os.environ.get('AUTOTUNE_POLICY_SOURCE',str(BASE/'working/lib/autotune_v2/current_policy.py'))).resolve()
spec=importlib.util.spec_from_file_location('current_policy_fixture',SOURCE)
cp=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

if os.name=='nt':
    # The production Linux path still opens/fsyncs the directory itself.
    def fixture_open(path,flags,*args,**kwargs):
        if Path(path).is_dir():
            return os.open(str(Path(path)/'.fixture-dir-fsync'),os.O_CREAT|os.O_RDWR)
        return os.open(path,flags,*args,**kwargs)
    cp.os=types.SimpleNamespace(**{key:getattr(os,key) for key in dir(os)})
    cp.os.open=fixture_open

sys.modules['detection']=types.SimpleNamespace(numbers=lambda value:[int(x) for x in str(value).split(',')])
sys.modules['rollback_order']=types.SimpleNamespace(same_value=lambda change,a,b,numbers:a==b)


class Engine:
    def __init__(self):self.control=2;self.fail=None
    def load(self,path):return json.loads(Path(path).read_text())
    def atomic(self,path,value):
        if self.fail==Path(path).name:
            self.fail=None
            raise OSError('injected metadata write failure')
        Path(path).write_text(json.dumps(value,sort_keys=True))
    def start_ticks(self,pid):return '100'
    def boot_id(self):return 'boot'
    def guard(self,*args,**kwargs):pass
    def usb_devices(self):return []
    def setting_value(self,change):return self.control


def seed(root,engine):
    old=dict(id='old',profile='automatic',status='committed',boot_id='boot',
             changes=[dict(parameter='usb_power',path='/fixture/control',before=1,after=2,write_attempted=True)])
    saved=dict(id='policy',transaction_id='old',activity_id='previous',enabled=True)
    record=dict(id='run',status='configured')
    previous=dict(id='previous',status='completed')
    for name in cp.JOURNALS:engine.atomic(root/name,old)
    for name,value in [('v2-policy.json',saved),('automatic.json',record),('activity-session.json',previous)]:
        engine.atomic(root/name,value)
    cp.begin(root,saved,record,old,previous,engine)
    return old,saved,record,previous


def activate(root,engine):
    activity=dict(id='trial',kind='policy-revalidation-v2',restore_playback=False,owner=os.getpid(),
                  start='100',boot_id='boot',initial_transaction_id='old',status='running')
    engine.atomic(root/'activity-session.json',activity)
    cp.bind(root,activity,engine)
    return activity


def native_trial(root,engine,old):
    native=dict(id='new',profile='automatic-policy-reapply',status='committed',boot_id='boot',
                parent_pid=os.getpid(),parent_start='100',previous_profile=old,
                session_owner={'kind':'activity','id':'trial'},
                changes=[dict(parameter='usb_power',path='/fixture/control',before=2,after=3,write_attempted=True)])
    for name in cp.JOURNALS:engine.atomic(root/name,native)
    engine.control=3
    return native


def promote(root,engine,old,saved,activity):
    native=native_trial(root,engine,old)
    final=copy.deepcopy(native)
    final['profile']='automatic'
    final['changes'][0]['before']=1
    updated=dict(saved,transaction_id='new',activity_id='trial')
    cp.promotion(root,activity,native,final,updated,engine)
    return native,final,updated


def native_rollback(root,engine,old,transaction):
    # Independent oracle: native rollback must undo the immediate trial 2->3,
    # while a later explicit policy undo would restore original defaults 1.
    assert transaction['changes'][0]['before']==2,'merged original-before leaked into native rollback'
    engine.control=transaction['changes'][0]['before']
    transaction.update(status='rolled-back',parameter_restoration_complete=True,rollback_errors=[])
    engine.atomic(root/'transaction.json',transaction)
    engine.atomic(root/'current.json',old)


def rollback(root,engine,old,activity):
    cp.prepare_recovery(root,activity,engine)
    transaction=engine.load(root/'transaction.json')
    if transaction!=old:native_rollback(root,engine,old,transaction)
    cp.recover(root,activity,engine)


def assert_old(root,engine,old,saved):
    assert engine.control==2
    assert all(engine.load(root/name)==old for name in cp.JOURNALS)
    assert engine.load(root/'v2-policy.json')==saved
    assert not (root/cp.FILE).exists()


def run():
    passed=[]
    with tempfile.TemporaryDirectory(prefix='policy-fixture-',dir=BASE) as temporary:
        base=Path(temporary)
        for count in range(5):
            root=base/('crash'+str(count));root.mkdir();engine=Engine()
            old,saved,*_=seed(root,engine);activity=activate(root,engine)
            native,final,updated=promote(root,engine,old,saved,activity)
            writes=[(name,final) for name in cp.JOURNALS]+[('v2-policy.json',updated)]
            for name,value in writes[:count]:engine.atomic(root/name,value)
            rollback(root,engine,old,activity)
            assert_old(root,engine,old,saved)
        passed.append('five partial-promotion crash points restore native before=2 and accepted metadata')

        root=base/'success';root.mkdir();engine=Engine()
        old,saved,*_=seed(root,engine);activity=activate(root,engine)
        native,final,updated=promote(root,engine,old,saved,activity)
        for name in cp.JOURNALS:engine.atomic(root/name,final)
        engine.atomic(root/'v2-policy.json',updated)
        activity['status']='completed';engine.atomic(root/'activity-session.json',activity)
        cp.reconcile(root,engine)
        assert engine.control==3 and all(engine.load(root/name)==final for name in cp.JOURNALS)
        assert not (root/cp.FILE).exists()
        passed.append('completed native trial preserves committed policy and clears snapshot')

        for phase in ('before-lease','before-trial'):
            root=base/phase;root.mkdir();engine=Engine();old,saved,*_=seed(root,engine)
            if phase=='before-lease':cp.reconcile(root,engine)
            else:
                activity=activate(root,engine);rollback(root,engine,old,activity)
            assert_old(root,engine,old,saved)
        passed.append('death before Lease and before trial preserves accepted policy')

        for name in ('v2-policy.json','automatic.json','current.json'):
            root=base/('cas-'+name);root.mkdir();engine=Engine()
            old,saved,*_=seed(root,engine);activity=activate(root,engine)
            promote(root,engine,old,saved,activity)
            foreign=engine.load(root/name);foreign['id']='newer-intent';engine.atomic(root/name,foreign)
            before={key:(root/key).read_bytes() for key in cp.JOURNALS+('v2-policy.json','automatic.json')}
            try:cp.prepare_recovery(root,activity,engine)
            except RuntimeError:pass
            else:raise AssertionError('newer intent accepted: '+name)
            assert all((root/key).read_bytes()==value for key,value in before.items())
        passed.append('CAS preserves newer policy, automatic result and journal byte-for-byte')

        root=base/'metadata-retry';root.mkdir();engine=Engine()
        old,saved,*_=seed(root,engine);activity=activate(root,engine)
        native,final,updated=promote(root,engine,old,saved,activity)
        for name in cp.JOURNALS:engine.atomic(root/name,final)
        engine.atomic(root/'v2-policy.json',updated)
        cp.prepare_recovery(root,activity,engine)
        native_rollback(root,engine,old,engine.load(root/'transaction.json'))
        engine.fail='last-known-good.json'
        try:cp.recover(root,activity,engine)
        except OSError:pass
        else:raise AssertionError('injected failure missing')
        cp.prepare_recovery(root,activity,engine);cp.recover(root,activity,engine)
        assert_old(root,engine,old,saved)
        passed.append('interrupted metadata reconciliation resumes from durable real rollback receipt')

        root=base/'native-already-rolled-back';root.mkdir();engine=Engine()
        old,saved,*_=seed(root,engine);activity=activate(root,engine)
        native=native_trial(root,engine,old)
        native_rollback(root,engine,old,native)
        # The native engine keeps last-known-good committed after rollback.
        assert engine.load(root/'last-known-good.json')['status']=='committed'
        cp.prepare_recovery(root,activity,engine);cp.recover(root,activity,engine)
        assert_old(root,engine,old,saved)
        passed.append('native rollback before snapshot preserves its real committed last-known-good receipt')

        root=base/'incomplete-rollback';root.mkdir();engine=Engine()
        old,saved,*_=seed(root,engine);activity=activate(root,engine)
        promote(root,engine,old,saved,activity)
        cp.prepare_recovery(root,activity,engine)
        transaction=engine.load(root/'transaction.json')
        transaction.update(status='rollback-incomplete',parameter_restoration_complete=False,rollback_errors=['fixture incomplete'])
        engine.atomic(root/'transaction.json',transaction)
        before={key:(root/key).read_bytes() for key in cp.JOURNALS+('v2-policy.json','automatic.json')}
        try:cp.recover(root,activity,engine)
        except RuntimeError:pass
        else:raise AssertionError('incomplete native recovery accepted')
        assert all((root/key).read_bytes()==value for key,value in before.items())
        passed.append('incomplete native rollback never relabels accepted metadata')

    return dict(status='PASS',scope='metadata fixture; control/rollback primitives modeled',
                real_posix_directory_fsync=os.name!='nt',source=str(SOURCE),cases=passed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=run()
    raw=json.dumps(result,indent=2)+'\n'
    if args.output:args.output.write_text(raw)
    print(raw,end='')
