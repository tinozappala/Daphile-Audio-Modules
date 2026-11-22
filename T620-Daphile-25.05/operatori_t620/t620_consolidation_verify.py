from pathlib import Path
import hashlib, http.client, json, shutil, subprocess, time, urllib.parse

assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
assert Path('/version').read_text().strip()=='fw2505251550'
host=Path('/mnt/hdd/system/daphile-audio-host'); system=host.parent
setup=json.loads((host/'config.json').read_text()); state=Path(setup['rme_state'])
backup=system/'stock-2505251550-private'; baseline=backup/(backup/'baseline').read_text().strip()

def digest(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def get(path,port=80,body=None,token=None,timeout=25):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        headers={}
        if token:headers['Cookie']=token
        if body is not None:headers['Content-Type']='application/json'
        conn.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
        response=conn.getresponse(); raw=response.read(4*1024*1024)
        assert response.status==200,(path,response.status)
        return raw,response.getheader('Set-Cookie')
    finally:conn.close()

def api(path,**kw):
    raw,token=get(path,**kw)
    return json.loads(raw),token

def lms(player,args):
    data,_=api('/jsonrpc.js',port=9000,body={'id':1,'method':'slim.request','params':[player,args]})
    return data['result']

result={'firmware':'25.05-b251550 RT','kernel':Path('/proc/sys/kernel/osrelease').read_text().strip()}
grub=Path('/boot/grub/grub.cfg').read_text()
assert 'set default=0\nset fallback=1\n' in grub
for slot in ('fw2505251550','fw261118b7rc6'):
    assert ('daphile='+slot+' ') in grub and (Path('/boot')/slot/'ok').is_file()
    subprocess.run(['md5sum','-c',slot+'/md5sum.txt'],cwd='/boot',capture_output=True,check=True,timeout=50)
result['boot_default']='fw2505251550';result['boot_recovery']='fw261118b7rc6';result['boot_files_md5_verified']=True

approvals=json.loads((host/'approvals.json').read_text())
statuses=json.loads((host/'status.json').read_text())['modules']
result['audio_host']={'broker_sha256':digest(host/'code/broker.py')}
result['modules']={}
for name,version in (('DaphileRME','0.5.16'),('DaphileCamillaDSP','0.4.49')):
    current=statuses[name]
    assert current['running'] and current['current']['version']==version and not current['recovery_required'],(name,current.get('error'))
    d=current['current']['release']; entry=next(x for x in approvals[name] if x['sha256']==d)
    assert digest(host/'bundles'/entry['archive'])==d
    release=host/'modules'/name/'releases'/d
    for path,wanted in entry['files'].items():
        assert digest(Path(setup['plugins'])/name/path)==wanted,(name,path,'LMS')
        if path.startswith('backend/'):
            assert digest(release/path.removeprefix('backend/'))==wanted,(name,path,'worker')
    result['modules'][name]={'version':version,'sha256':d,'package_files_verified':len(entry['files'])}
    if name=='DaphileCamillaDSP':
        result['modules'][name]['engine']=subprocess.check_output([str(release/'bin/camilladsp'),'--version'],text=True,timeout=8).strip()

for app in ('RMEPlugin','CamillaPlugin'):
    prefix='/cgi-bin/'+app+'/'
    page,_=get(prefix)
    sess,token=api(prefix+'api/session'); assert sess['authenticated'] and token
    token=token.split(';',1)[0]
    if app=='RMEPlugin':
        assert b'id="recover-main"' in page
        started=time.monotonic(); r,_=api(prefix+'api/rme/snapshot',body={},token=token)
        assert r['pending'] is None and r['identity']
        assert r['capabilities']['outputs']['line']['tone_enabled']['writable']
        mem,_=api(prefix+'api/rme/memory/read',body={'slot':20},token=token)
        assert mem['read_only'] and not mem['empty']
        result['rme_panel']={'snapshot_seconds':round(time.monotonic()-started,3),'hardware_memory_20_read':True,'pending':False,
                             'line_tones_enabled':r['outputs']['line']['tone_enabled'],'line_eq_enabled':bool(r['current_eq']['line']['eq_enabled']),
                             'line_selector':r['outputs']['line']['eq_preset']}
    else:
        r,_=api(prefix+'api/status',token=token)
        assert r['module_version']=='0.4.49'
        result['camilla_panel']={'mode':r.get('audio',{}).get('mode'),'processing':r.get('audio',{}).get('processing'),'sample_rate':r.get('sample_rate'),'native_observation':statuses['DaphileCamillaDSP'].get('native_recovery',{}).get('state')}

page,_=get('/cgi-bin/AudioPlayer'); navy,_=api('/cgi-bin/AudioPlugins/navigation.json')
assert page.count(b'data-daphile-audio-sidebar')==1
assert {'DaphileRME','DaphileCamillaDSP'}<=set(navy['modules'])
result['navigation_modules']=navy['modules']
srv=lms('', ['serverstatus',0,20]); assert srv['version']=='9.0.3'
players=[p for p in srv['players_loop'] if p.get('connected')]; assert players
result['lms']={'version':srv['version'],'connected_players':len(players),'indexed_tracks':srv.get('info total songs'),'scanning':bool(srv.get('rescan'))}
mounts=[x.split()[:3] for x in Path('/proc/mounts').read_text().splitlines() if x.split()[2]=='cifs']; assert mounts
result['nas_mount_count']=len(mounts)
nas=[Path(x[1].replace('\\040',' ')).resolve() for x in mounts]
for player in players:
    q=lms(player['playerid'],['status',0,10000,'tags:u'])
    result['lms'].update(queue_tracks=q['playlist_tracks'],mode=q['mode'])
    for song in q.get('playlist_loop',[]):
        parsed=urllib.parse.urlsplit(song['url'])
        p=Path(urllib.parse.unquote(parsed.path)).resolve()
        if parsed.scheme=='file' and any(p.is_relative_to(root) for root in nas):
            with p.open('rb') as f:assert f.read(4096)
            result['lms']['nas_queued_media_read']=True
            break
    if result['lms'].get('nas_queued_media_read'):break
if not result['lms'].get('nas_queued_media_read'):
    saved=json.loads((baseline/'queue.json').read_text())
    for q in saved.values():
        for song in q.get('playlist_loop',[]):
            parts=urllib.parse.urlsplit(song['url']);p=Path(urllib.parse.unquote(parts.path)).resolve()
            if parts.scheme=='file' and p.is_file() and any(p.is_relative_to(mount) for mount in nas):
                with p.open('rb') as f:assert f.read(4096)
                result['lms']['nas_saved_media_read']=True
                break
assert any(result['lms'].get(k) for k in ('nas_queued_media_read','nas_saved_media_read'))
result['disk_free_bytes']={'boot':shutil.disk_usage('/boot').free,'data':shutil.disk_usage('/mnt/hdd').free}
print(json.dumps(result,indent=2))
