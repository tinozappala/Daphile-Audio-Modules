from pathlib import Path
import http.client, json, sys

host=Path('/mnt/hdd/system/daphile-audio-host'); module=host/'modules/DaphileRME'
info=json.loads((module/'current.json').read_text()); assert info['version']=='0.5.16'
release=module/'releases'/info['release']
sys.path.insert(0,str(release/'rme/lib'))
import rme_control as ctl, rme_peq as peq

def fetch(path,body=None,cookie=None):
    c=http.client.HTTPConnection('127.0.0.1',80,timeout=35)
    try:
        h={}
        if cookie:h['Cookie']=cookie
        if body is not None:h['Content-Type']='application/json'
        c.request('GET' if body is None else 'POST','/cgi-bin/RMEPlugin/'+path,None if body is None else json.dumps(body),h)
        res=c.getresponse(); data=json.load(res)
        if res.status!=200: raise RuntimeError((res.status,data.get('error')))
        return data,res.getheader('Set-Cookie')
    finally:c.close()

s,token=fetch('api/session'); assert s['authenticated']; cookie=token.split(';',1)[0]
before=peq.verified_read(); original=ctl.values(before)
assert original['4:28']==2 and original['4:2']==original['5:2']==1
snapshot,_=fetch('api/rme/snapshot',{},cookie)
cap=snapshot['capabilities']['outputs']['line']
assert cap['tone_enabled']['writable'] and not cap['bass_gain']['writable'] and not cap['treble_gain']['writable']
assert snapshot['revision']==peq.revision(before) and snapshot['pending'] is None
reply,_=fetch('api/rme/control/apply',{'confirm':True,'patch':{'output':'line','expected':snapshot['revision'],'values':{'tone_enabled':False}}},cookie)
print('COMMAND',reply.get('status'),reply.get('reason'),flush=True)
assert reply['status'] in ('applied-verified','no-change') and reply['readback_verified']
after=peq.verified_read(); ctl.check(before,after,{'4:20':0},allow_dependencies=False)
assert len(ctl.values(after))==220
preset,_=fetch('api/rme/preset/apply',{'confirm':True,'patch':{'output':'line','expected':peq.revision(after),'slot':1}},cookie)
assert preset['status']=='no-change' and preset['readback_verified']
assert preset['state']['outputs']['line']['tone_enabled'] is False and preset['state']['current_eq']['line']['eq_enabled']==1
print('Verified on the DAC: Line Tones OFF, EQ ON, preset 1 selected; other 219 registers identical.',flush=True)
