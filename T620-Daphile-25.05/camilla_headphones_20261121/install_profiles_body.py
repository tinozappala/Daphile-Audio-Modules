"""Add, inspect and test the two headphone presets through the live CGI."""
import copy,http.client,json
from pathlib import Path

prefix='/cgi-bin/CamillaPlugin/'
cookie=None
def request(path,body=None):
 global cookie
 c=http.client.HTTPConnection('127.0.0.1',80,timeout=25)
 headers={}
 if cookie:headers['Cookie']=cookie
 if body is not None:headers['Content-Type']='application/json'
 try:
  c.request('GET' if body is None else 'POST',prefix+path,None if body is None else json.dumps(body),headers)
  r=c.getresponse();raw=r.read(2*1024*1024)
  if r.getheader('Set-Cookie'):cookie=r.getheader('Set-Cookie').split(';',1)[0]
  try:data=json.loads(raw)
  except ValueError:data=raw
  return r.status,data
 finally:c.close()

def get(path):
 status,data=request(path)
 if status!=200:raise RuntimeError((path,status,data))
 return data

def mutate(path,body):
 status,data=request(path,body)
 if status!=200:raise RuntimeError((path,status,data))
 return data

assert get('api/session')['authenticated']
initial=get('api/presets')
original={p['id']:p for p in initial['presets']}
active=get('api/profile/state')
audio=get('api/status')['audio']
assert not audio['enabled'] and not audio['active']
assert all(p['id'] not in original for p in PROFILES)
snap=initial
for profile in PROFILES:
 snap=mutate('api/presets/save',{'revision':snap['revision'],'profile':profile})
 row=next(p for p in snap['presets'] if p['id']==profile['id'])
 assert row==profile and profile['id'] in snap['protected_ids']
 print('CREATED',profile['name'],'bands',len(profile['peq']['L']),'pre_gain',profile['levels']['pre_gain'],flush=True)

for profile in PROFILES:
 before=get('api/presets')
 status,reply=request('api/presets/delete',{'revision':before['revision'],'id':profile['id']})
 if status==200:raise RuntimeError('Cancellazione di preset protetto riuscita')
 assert 'protett' in str(reply).lower(),(status,reply)
 after=get('api/presets')
 assert after['revision']==before['revision'] and after['presets']==before['presets']
 updated=copy.deepcopy(profile)
 for channel in ('L','R'):updated['peq'][channel][0]['gain']+=.1
 changed=mutate('api/presets/save',{'revision':after['revision'],'profile':updated})
 assert next(p for p in changed['presets'] if p['id']==profile['id'])==updated
 restored=mutate('api/presets/save',{'revision':changed['revision'],'profile':profile})
 assert next(p for p in restored['presets'] if p['id']==profile['id'])==profile
 print('DELETE_BLOCKED_AND_EDIT_VERIFIED',profile['name'],flush=True)

final=get('api/presets');observed={p['id']:p for p in final['presets']}
assert all(observed.get(k)==p for k,p in original.items())
assert get('api/profile/state')==active
now=get('api/status')['audio']
assert not now['enabled'] and not now['active']
print(json.dumps({'status':'PASS','original_presets_preserved':True,'active_profile_unchanged':True,
                  'protected_ids':final['protected_ids'],'count':len(final['presets']),
                  'native_audio_mode':now['mode']}),flush=True)
