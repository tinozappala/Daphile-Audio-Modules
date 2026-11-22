from pathlib import Path
import hashlib, http.client, json, time

host=Path('/mnt/hdd/system/daphile-audio-host')
assert Path('/version').read_text().strip()=='fw2505251550'
assert hashlib.sha256((host/'code/broker.py').read_bytes()).hexdigest()==EXPECTED_SHA
def rpc(player,args):
    c=http.client.HTTPConnection('127.0.0.1',9000,timeout=6)
    try:
        c.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,args]}),{'Content-Type':'application/json'})
        r=c.getresponse(); assert r.status==200; return json.load(r)['result']
    finally:c.close()
players=[x['playerid'] for x in rpc('', ['serverstatus',0,10])['players_loop'] if x.get('connected')]
assert len(players)==1
player=players[0]
def status():return rpc(player,['status',0,10000,'tags:u'])
def identity(q):return ([x['url'] for x in q['playlist_loop']],str(q.get('playlist_cur_index')),
                         *[str(q.get(k)) for k in ('mixer volume','power','playlist shuffle','playlist repeat')])
now=status(); saved=None
for prior in sorted((host/'recovery').glob('broker-camilla-status-*/player-before.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True):
    old=json.loads(prior.read_text())
    if old['mode']=='play' and identity(now)==identity(old) and time.time()-prior.stat().st_mtime<1800:
        saved=json.loads(prior.with_name('player-paused.json').read_text()); break
assert saved is not None and identity(saved)==identity(now),'Unexpected playback changes left alone'
cursor=float(saved.get('time',0))
if now['mode']=='stop':
    rpc(player,['play'])
    if cursor>0:rpc(player,['time',cursor])
elif now['mode']=='pause':
    rpc(player,['play'])
elif now['mode']!='play':raise RuntimeError('Player mode changed')
deadline=time.monotonic()+55
while time.monotonic()<deadline:
    current=status()
    modules=json.loads((host/'status.json').read_text())['modules']
    rme,cam=modules['DaphileRME'],modules['DaphileCamillaDSP']
    marker=cam.get('native_recovery') or {}
    if (identity(current)==identity(saved) and current['mode']=='play' and rme['running'] and not rme['recovery_required']
        and cam['running'] and not cam['recovery_required'] and marker.get('state')=='operational' and marker['audio']['processing'] is True):
        elapsed=float(current.get('time',0))-cursor
        assert elapsed>=-2 and elapsed<=max(60,time.monotonic()-(deadline-55)+5),elapsed
        print('Same track and queue in play; position delta:',round(elapsed,3),'s; Camilla operational at',marker['audio']['rate'],'Hz')
        break
    time.sleep(.5)
else: raise RuntimeError('Playback or processing after resume did not confirm')
