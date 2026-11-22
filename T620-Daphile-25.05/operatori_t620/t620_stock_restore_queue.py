from pathlib import Path
import http.client, json, os, sys, time, urllib.parse

private=Path('/mnt/hdd/system/stock-2505251550-private')
baseline=private/(private/'baseline').read_text().strip()
assert Path('/version').read_text().strip()=='fw2505251550'
assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'

def rpc(player,args):
    conn=http.client.HTTPConnection('127.0.0.1',9000,timeout=12)
    try:
        conn.request('POST','/jsonrpc.js',json.dumps({'id':1,'method':'slim.request','params':[player,args]}),{'Content-Type':'application/json'})
        response=conn.getresponse()
        assert response.status==200
        return json.load(response)['result']
    finally: conn.close()

def status(player): return rpc(player,['status',0,10000,'tags:u'])
def tracks(state): return [x['url'] for x in state.get('playlist_loop',[])]

server=rpc('', ['serverstatus',0,20])
assert any(x.get('connected') for x in server.get('players_loop',[]))
assert str(server.get('rescan',0)) in ('0','False','None'), 'LMS still indexing; leave queue writes until the native abort is confirmed'
for player,saved in json.loads((baseline/'queue.json').read_text()).items():
    current=status(player)
    wanted=tracks(saved)
    original=tracks(current)
    assert saved.get('mode')=='stop' and current.get('mode')=='stop','Queue is no longer stopped; preserve user activity'
    assert original==wanted[:len(original)],'The existing queue differs; preserve it instead of overwriting'
    for url in wanted[len(original):]:
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme=='file':
            path=Path(urllib.parse.unquote(parsed.path)); assert path.is_file()
            with path.open('rb') as f: assert f.read(8192)
    for index in range(len(original),len(wanted)):
        live=status(player)
        assert live.get('mode')=='stop' and tracks(live)==wanted[:index]
        url=wanted[index]
        parsed=urllib.parse.urlsplit(url)
        item=urllib.parse.unquote(parsed.path) if parsed.scheme=='file' else url
        try: rpc(player,['playlist','add',item])
        except (OSError,KeyError): pass  # An uncertain response is reconciled by reading the queue.
        end=time.monotonic()+12
        while time.monotonic()<end:
            observed=tracks(status(player))
            if observed==wanted[:index+1]: break
            assert observed==wanted[:index], 'Unexpected playlist change; preserve external activity'
            time.sleep(.2)
        else: raise RuntimeError('LMS did not confirm the requested track')
    now=status(player)
    if str(now.get('playlist_cur_index'))!=str(saved.get('playlist_cur_index')):
        assert str(saved.get('playlist_cur_index'))=='0','Active index switching could start audio'
    checks=('playlist_tracks','playlist_cur_index','mode','power','mixer volume','playlist shuffle','playlist repeat')
    assert all(str(now.get(x))==str(saved.get(x)) for x in checks), [x for x in checks if str(now.get(x))!=str(saved.get(x))]
    assert tracks(now)==wanted
    print('Queue restored and verified',len(wanted),'tracks, order, index, STOP, volume and repeat',flush=True)
os.umask(0o077)
(private/'queue-verified').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
