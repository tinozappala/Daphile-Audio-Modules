from pathlib import Path
import hashlib, http.client, importlib.util, json, os, subprocess, sys, tempfile, time

host=Path('/mnt/hdd/system/daphile-audio-host'); code=host/'code'; target=code/'broker.py'
assert Path('/version').read_text().strip()=='fw2505251550'
assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
original=target.read_bytes(); assert hashlib.sha256(original).hexdigest()=='fe5116dd1449ba442ee6bd81672d201f32f338af6c78676c88c0d59de72700eb'
candidate=sys.stdin.buffer.read(1048576); assert hashlib.sha256(candidate).hexdigest()==EXPECTED_SHA
compile(candidate,str(target),'exec')
camilla=host/'modules/DaphileCamillaDSP'; intent=camilla/'data/peq-intent.json'
initial_intent=json.loads(intent.read_text()); assert initial_intent['enabled'] is True
cfg=json.loads((host/'config.json').read_text()); current=json.loads((camilla/'current.json').read_text())
backup=Path(tempfile.mkdtemp(prefix='broker-camilla-status-',dir=host/'recovery'))
(backup/'broker.before.py').write_bytes(original)
(backup/'broker.candidate.py').write_bytes(candidate)
sys.path.insert(0,str(code)); spec=importlib.util.spec_from_file_location('check_broker',backup/'broker.candidate.py')
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
camilla_status=json.loads((host/'status.json').read_text())['modules']['DaphileCamillaDSP']
if camilla_status['running']:
    initial_probe=probe.native_recovery_status(camilla,True,current,cfg)
    assert initial_probe['state']=='operational' and initial_probe['audio']['processing'] is True
    print('Candidate reports active Camilla route operational before interruption',flush=True)
else:
    assert camilla_status['native_recovery']['state']=='pending'
    assert (camilla/'data/native-router.json').is_file() and (camilla/'data/native-audio-session.json').is_file()

def rpc(player,args):
    c=http.client.HTTPConnection('127.0.0.1',9000,timeout=5)
    try:
        c.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,args]}),{'Content-Type':'application/json'})
        r=c.getresponse(); assert r.status==200; return json.load(r)['result']
    finally:c.close()

players=[x['playerid'] for x in rpc('', ['serverstatus',0,10])['players_loop'] if x.get('connected')]
assert len(players)==1
player=players[0]
def status():return rpc(player,['status',0,10000,'tags:u'])
def identity(q):return ([x['url'] for x in q.get('playlist_loop',[])],str(q.get('playlist_cur_index')),
                         *[str(q.get(k)) for k in ('mixer volume','power','playlist shuffle','playlist repeat')])
def wait(check,limit=24):
    end=time.monotonic()+limit
    while time.monotonic()<end:
        observed=check()
        if observed:return observed
        time.sleep(.35)
    raise RuntimeError('Playback or controller did not confirm the transition')

initial=status(); assert initial['mode'] in ('play','pause','stop') and initial['playlist_loop']
was_playing=initial['mode']=='play'
(backup/'player-before.json').write_text(json.dumps(initial))
def swap(blob):
    p=target.with_name('.broker.py.camilla-status-new')
    with p.open('xb') as f:
        f.write(blob);os.fchmod(f.fileno(),target.stat().st_mode&0o777);f.flush();os.fsync(f.fileno())
    os.replace(p,target)
def rc(action):
    proc=subprocess.run(['rc-service','daphile-audio-host',action],capture_output=True,text=True,timeout=75)
    if proc.returncode: raise RuntimeError('Audio Host '+action+' failed: '+proc.stdout[-400:])

def broker_ready():
    try:
        m=json.loads((host/'status.json').read_text())['modules']
        return all(m[k]['running'] and not m[k]['recovery_required'] for k in ('DaphileCamillaDSP','DaphileRME'))
    except (OSError,ValueError,KeyError):return False

def restore_playback(paused):
    current=status()
    assert identity(current)==identity(paused),'Queue or playback preferences changed externally'
    if not was_playing:return
    if current['mode']=='play':return
    assert current['mode'] in ('pause','stop')
    rpc(player,['play'])
    if current['mode']=='stop' and float(paused.get('time',0))>1:
        rpc(player,['time',float(paused['time'])])
    wait(lambda: (q if q['mode']=='play' and identity(q)==identity(paused) else None) if (q:=status()) else None)

paused=initial
if not camilla_status['running']:
    previous=sorted(backup.parent.glob('broker-camilla-status-*/player-paused.json'),key=lambda p:p.stat().st_mtime_ns)
    assert previous
    saved=json.loads(previous[-1].read_bytes())
    if identity(saved)==identity(initial):paused=saved
assert identity(paused)==identity(initial)
(backup/'player-paused.json').write_text(json.dumps(paused))
rpc(player,['stop'])
wait(lambda: q if (q:=status())['mode']=='stop' and identity(q)==identity(initial) and float(q.get('time',0))==0 else None,limit=10)
try:
    assert target.read_bytes()==original
    swap(candidate)
    rc('restart')
    wait(broker_ready)
except BaseException:
    if target.read_bytes()==candidate:
        swap(original)
        rc('restart')
    restore_playback(paused)
    raise

assert json.loads(intent.read_text())['enabled'] is True
restore_playback(paused)
def operational():
    m=json.loads((host/'status.json').read_text())['modules']['DaphileCamillaDSP']
    return m['native_recovery'] if m['running'] and not m['recovery_required'] and m.get('native_recovery',{}).get('state')=='operational' else None
if was_playing:
    final=wait(operational,limit=60)
    assert final['audio']['processing'] and final['audio']['rate']>0
    print('Camilla operational with processing at',final['audio']['rate'],'Hz after resume',flush=True)
last=status(); assert identity(last)==identity(paused) and last['mode']==initial['mode']
print('Verified new broker, both modules, same queue and playback mode:',last['mode'],len(last['playlist_loop']),'position delta s',round(float(last.get('time',0))-float(paused.get('time',0)),2),flush=True)
