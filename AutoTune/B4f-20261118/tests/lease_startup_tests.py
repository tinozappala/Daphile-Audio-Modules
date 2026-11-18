"""Session startup interruption fixtures; actual code, no audio/network/processes.

python -B lease_startup_tests.py [--lib PATH_TO_WORKING_LIB] [--output REPORT]
Only engine/child/process/native-player primitives are modeled. The actual
Lease constructor, session.recover, workload capture and recovery run intact.
"""
import argparse,contextlib,copy,importlib,importlib.util,json,os,sys,tempfile,time,types
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode=True


class Engine(types.ModuleType):
    def __init__(self):
        super().__init__('engine');self.lock_depth=0;self.rollback_calls=0
    @contextlib.contextmanager
    def locked(self,root):
        self.lock_depth+=1
        try:yield Path(root)
        finally:self.lock_depth-=1
    def load(self,path):
        if Path(path).name=='controlled-workload.json':assert self.lock_depth>0
        return json.loads(Path(path).read_text())
    def atomic(self,path,value):Path(path).write_text(json.dumps(value,sort_keys=True))
    def ensure_session_owner(self,root):pass
    def session_binding(self,root):return dict(kind='automatic',id='fixture-run')
    def start_ticks(self,pid):return '314159'
    def boot_id(self):return 'fixture-boot'
    def recover_owned_transaction(self,*args,**kwargs):
        self.rollback_calls+=1;return dict(status='no-change')


class Player(types.ModuleType):
    def __init__(self,state):
        super().__init__('player');self.state=copy.deepcopy(state);self.reads=[];self.writes=[]
    def playback_snapshot(self,mac):
        assert mac==self.state['mac'];return copy.deepcopy(self.state)
    def playlist_key(self,url):return url
    def rpc(self,mac,command):
        self.reads.append(command)
        if command and command[0]=='can':return dict(_can=1)
        if command==['serverstatus','0','1']:return dict(version='9.1.1')
        if command==['sync','?']:return dict(_sync='-')
        if command==['playerpref','syncgroupid','?']:return dict(_p2=None)
        if command==['mixer','volume','?']:return dict(_volume=self.state['volume'])
        if command==['mixer','muting','?']:return dict(_muting=self.state['mute'])
        self.writes.append(command);raise AssertionError('Unexpected native mutator: '+repr(command))


class Child:
    pid=43210
    def __init__(self):self.terminated=False;self.killed=False;self.code=None
    def poll(self):return self.code
    def terminate(self):self.terminated=True;self.code=-15
    def kill(self):self.killed=True;self.code=-9
    def wait(self,timeout=None):return self.code


class Clock:
    def __init__(self):self.value=0
    def time(self):return time.time()
    def monotonic(self):return self.value
    def sleep(self,seconds):self.value+=11


def run(lib):
    sys.path.insert(0,str(lib))
    controlled=importlib.import_module('autotune_v2.controlled_workload')
    original=dict(mac='12:34:56:78:9a:bc',urls=['file:///music/one.flac','file:///music/one.flac'],
                  index=1,mode='stop',time=.1,volume=37,mute=1,power=1,repeat=0,shuffle=0)
    engine=Engine();player=Player(original)
    common=types.ModuleType('autotune_v2.common_activity')
    common.require_exclusive=lambda:object()
    common.child_contract=lambda:dict(close_fds=True)
    common.register_guard=lambda child:None
    budget=types.ModuleType('autotune_v2.budget_guard')
    budget.bind_activity_deadline=lambda *args:None
    detection=types.ModuleType('detection')
    detection.read=lambda path:'12345678-1234-1234-1234-123456789abc'
    modules=dict(engine=engine,player=player,detection=detection,
                 **{'autotune_v2.common_activity':common,'autotune_v2.budget_guard':budget})
    import autotune_v2
    passed=[]
    with patch.dict(sys.modules,modules),patch.object(autotune_v2,'common_activity',common,create=True):
        spec=importlib.util.spec_from_file_location('lease_session_under_test',lib/'session.py')
        session=importlib.util.module_from_spec(spec);spec.loader.exec_module(session)
        with tempfile.TemporaryDirectory(prefix='lease-startup-',dir=Path(__file__).resolve().parent) as temporary:
            base=Path(temporary)

            def fresh(name,previous=True):
                root=base/name;root.mkdir();player.state=copy.deepcopy(original)
                if previous:
                    old=dict(marker=controlled.MARKER,owner_id='older-completed-activity',boot_id='earlier-boot',
                             phase='restored',workload=dict(path=str(root/'old-absent.wav')),
                             original_playback=copy.deepcopy(original))
                    engine.atomic(root/'controlled-workload.json',old)
                return root

            def create(root,timeout=False,spawn_error=False):
                child=Child()
                def spawn(argv,**kwargs):
                    if spawn_error:raise OSError('fixture failed to spawn guard')
                    if not timeout:engine.atomic(root/('activity-ready-'+argv[-1]+'.json'),dict(id=argv[-1],watchdog=child.pid))
                    return child
                with patch.object(session.subprocess,'Popen',spawn),patch.object(session,'time',Clock()):
                    if timeout or spawn_error:
                        try:session.Lease(root,copy.deepcopy(original),1000,'automatic-v2')
                        except (OSError,RuntimeError):pass
                        else:raise AssertionError('guard startup failure was not raised')
                        return engine.load(root/'activity-session.json'),child
                    return session.Lease(root,copy.deepcopy(original),1000,'automatic-v2'),child

            for previous in (False,True):
                root=fresh('timeout-'+str(previous),previous)
                activity,child=create(root,timeout=True)
                assert child.terminated and activity['status']=='completed',activity
                assert activity['playback_recovery']==dict(status='no-controlled-workload-created',restored=True,queue_restored=True,player_commands=0)
                assert activity['initial_controlled_workload']['present'] is previous
            passed.append('guard readiness timeout records verified untouched recovery with or without an older restored workload')

            root=fresh('spawn-error');activity,child=create(root,spawn_error=True)
            assert activity['status']=='completed' and activity['playback_recovery']['restored'] is True
            passed.append('guard spawn failure records verified untouched recovery')

            root=fresh('death-before-create');lease,child=create(root)
            before=(root/'controlled-workload.json').read_bytes()
            session.recover(root,lease.record)
            activity=engine.load(root/'activity-session.json')
            assert activity['status']=='recovered' and activity['playback_recovery']['restored'] is True
            assert (root/'controlled-workload.json').read_bytes()==before
            passed.append('death immediately after Lease before create recovers without modifying the older completed workload')

            for mutation in ('newer-workload','missing-workload','file-reappeared','newer-queue','newer-gains'):
                root=fresh(mutation);lease,child=create(root)
                if mutation=='newer-workload':
                    record=json.loads((root/'controlled-workload.json').read_text());record['owner_id']='newer-owner';engine.atomic(root/'controlled-workload.json',record)
                elif mutation=='missing-workload':(root/'controlled-workload.json').unlink()
                elif mutation=='file-reappeared':(root/'old-absent.wav').write_bytes(b'new data')
                elif mutation=='newer-queue':player.state['urls']=['file:///music/new-user-choice.flac'];player.state['index']=0
                else:player.state['volume']=19
                files={path.name:path.read_bytes() for path in root.iterdir() if path.is_file() and path.name!='activity-session.json'}
                try:session.recover(root,lease.record)
                except RuntimeError:pass
                else:raise AssertionError('changed intent incorrectly accepted: '+mutation)
                activity=engine.load(root/'activity-session.json')
                assert activity['status']=='recovery-required'
                assert not activity.get('playback_recovery',{}).get('restored')
                assert all((root/name).read_bytes()==value for name,value in files.items())
            passed.append('newer workload, removed metadata, reappeared file, queue and gains all refuse untouched recovery without overwrites')

            root=fresh('timeout-newer-queue')
            child=Child()
            def changed_spawn(argv,**kwargs):
                player.state['urls']=['file:///music/new-user-intent.flac'];player.state['index']=0
                return child
            with patch.object(session.subprocess,'Popen',changed_spawn),patch.object(session,'time',Clock()):
                try:session.Lease(root,copy.deepcopy(original),1000,'automatic-v2')
                except RuntimeError:pass
                else:raise AssertionError('timeout expected')
            activity=engine.load(root/'activity-session.json')
            assert activity['status']=='recovery-required' and not activity.get('playback_recovery')
            passed.append('guard startup failure with changed player state remains truthfully recovery-required')

    assert not player.writes
    return dict(status='PASS',scope='actual startup/recovery code with modeled native primitives; no hardware qualification',
                source=str(lib),player_mutators=player.writes,cases=passed)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib',type=Path,default=Path(__file__).resolve().parent/'working/lib')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=run(args.lib.resolve());raw=json.dumps(result,indent=2)+'\n'
    if args.output:args.output.write_text(raw)
    print(raw,end='')
