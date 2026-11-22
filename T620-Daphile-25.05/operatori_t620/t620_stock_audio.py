from pathlib import Path
import http.client, json, os, re, sqlite3, stat, tempfile, time, urllib.parse

private=Path('/mnt/hdd/system/stock-2505251550-private')
baseline=private/(private/'baseline').read_text().strip()
assert Path('/version').read_text().strip()=='fw2505251550'
assert (private/'queue-verified').read_text()==Path('/proc/sys/kernel/random/boot_id').read_text()
state=private.parent/'daphile-audio-host/modules/DaphileCamillaDSP/data'

def request(port,path,data=None,cookie=None,timeout=30):
    client=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        headers={'Content-Type':'application/json'}
        if cookie: headers['Cookie']=cookie
        client.request('GET' if data is None else 'POST',path,None if data is None else json.dumps(data),headers)
        response=client.getresponse()
        return response.status,json.load(response),response.getheader('Set-Cookie')
    finally: client.close()

saved_queues=json.loads((baseline/'queue.json').read_text())
assert len(saved_queues)==1
player,saved=next(iter(saved_queues.items()))
def lms(*args):
    code,data,_=request(9000,'/jsonrpc.js',{'id':1,'method':'slim.request','params':[player,list(args)]})
    assert code==200
    return data['result']

prefix='/cgi-bin/CamillaPlugin/'
code,data,token=request(80,prefix+'api/session')
assert code==200 and data['authenticated'] and token
cookie=token.split(';',1)[0]
def cam(path,data=None):
    code,reply,_=request(80,prefix+'api/'+path,data,cookie)
    if code!=200: raise RuntimeError('Camilla '+path+' HTTP '+str(code)+': '+str(reply.get('error',''))[:160])
    return reply

def queue(): return lms('status',0,10000,'tags:u')
def signature(q):
    return ([x['url'] for x in q['playlist_loop']],*[str(q.get(k)) for k in ('playlist_cur_index','mixer volume','power','playlist shuffle','playlist repeat')])

current=queue()
assert signature(current)==signature(saved) and current.get('mode')=='stop'
original_signature=signature(saved)
catalog=private.parent/'mediaserver/cache/library.db'
with sqlite3.connect(catalog.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    rows=db.execute("SELECT url FROM tracks WHERE samplerate=44100 AND lower(url) LIKE '%.flac' AND url LIKE 'file:%Network%' LIMIT 120").fetchall()
    pcm_url=next((u for u, in rows if u not in original_signature[0] and Path(urllib.parse.unquote(urllib.parse.urlsplit(u).path)).is_file()),None)
assert pcm_url, 'No 44.1 kHz FLAC available from the NAS index'
candidate=urllib.parse.unquote(urllib.parse.urlsplit(pcm_url).path)
test_track=len(original_signature[0])
with Path(candidate).open('rb') as f: assert f.read(4)==b'fLaC'
original=cam('status')
assert not original['audio']['enabled'] and original['audio']['mode']=='native-bypass'
profile=cam('profile')
restore={}
for name in ('active_profile.json','peq-intent.json','presets.json','preset-slots.json'):
    p=state/name
    if p.is_file(): restore[name]=(p.read_bytes(),stat.S_IMODE(p.stat().st_mode))
before_presets=cam('presets')

cards=[p.parent for p in Path('/proc/asound').glob('card[0-9]*/id') if p.read_text().strip().startswith('DAC')]
assert len(cards)==1
pcm=cards[0]/'pcm0p/sub0'
assert (pcm/'status').is_file()

def audio_observation():
    status=(pcm/'status').read_text()
    params=(pcm/'hw_params').read_text()
    running='RUNNING' in status
    pointer=re.search(r'hw_ptr\s*:\s*(\d+)',status)
    return running,int(pointer[1]) if pointer else None,{m[1]:m[2] for m in re.finditer(r'(?m)^(format|rate|channels): (.+)$',params)}

def wait_pcm(expected,require_dsp):
    deadline=time.monotonic()+48
    previous=None
    last_mode=None
    while time.monotonic()<deadline:
        info=cam('status') if require_dsp else None
        if info: last_mode=info.get('audio',{}).get('mode')
        playing=queue()
        running,pointer,params=audio_observation()
        assert signature(playing)==expected, 'External playlist, output volume, or player settings changed'
        if running and pointer is not None and previous is not None and pointer!=previous and playing.get('mode')=='play':
            if not require_dsp or (info.get('connected') and info.get('audio',{}).get('active')):
                return params,info.get('sample_rate') if info else None
        previous=pointer if running else None
        time.sleep(.75)
    raise RuntimeError('Physical PCM or Camilla route did not converge: '+str(last_mode))

success=False
error=None
owned=False
try:
    lms('playlist','add',candidate)
    owned_signature=(original_signature[0]+[pcm_url],*original_signature[1:])
    observed=signature(queue())
    assert observed==owned_signature, 'NAS test track addition was not confirmed'
    owned=True
    lms('playlist','index',test_track,0,1)
    active_signature=(owned_signature[0],str(test_track),*owned_signature[2:])
    assert signature(queue())==active_signature
    reply=cam('audio/enable',{'enabled':True,'profile':profile})
    assert reply.get('enabled')
    lms('play')
    pcm_state,rate=wait_pcm(active_signature,True)
    print('CAMILLA ACTIVE THROUGH RME',pcm_state,'engine rate',rate,flush=True)
    success=True
except (RuntimeError,AssertionError,KeyError,OSError) as exc:
    error=type(exc).__name__+': '+str(exc)
finally:
    latest=queue()
    latest_signature=signature(latest)
    assert latest_signature[2:]==original_signature[2:]
    assert latest_signature[0] in (original_signature[0],original_signature[0]+[pcm_url]),'External changes detected during test; keep their state'
    if latest.get('mode')=='play': lms('stop')
    refreshed=cam('status')
    if refreshed.get('audio',{}).get('enabled'):
        cam('audio/enable',{'enabled':False})
    if latest_signature[0]==original_signature[0]+[pcm_url]:
        lms('playlist','index',int(original_signature[1]),0,1)
        lms('playlist','delete',test_track)
    final=queue()
    assert final['mode']=='stop' and signature(final)==signature(saved)
    assert cam('presets')==before_presets
    for name in ('active_profile.json','peq-intent.json'):
        p=state/name
        if name in restore and p.read_bytes()!=restore[name][0]:
            fd,tmp=tempfile.mkstemp(dir=state,prefix='.t620-stock-test-')
            with os.fdopen(fd,'wb') as f:
                f.write(restore[name][0]);f.flush();os.fchmod(f.fileno(),restore[name][1]);os.fsync(f.fileno())
            os.replace(tmp,p)
        elif name not in restore and p.is_file(): p.unlink()
    assert cam('profile')==profile
    assert cam('status')['audio']['mode']=='native-bypass'
    print('PLAYER RESTORED',len(final['playlist_loop']),'tracks, STOP, same volume, same presets, Camilla bypass',flush=True)
if not success: raise RuntimeError(error)
os.umask(0o077)
(private/'audio-verified').write_text(Path('/proc/sys/kernel/random/boot_id').read_text())
