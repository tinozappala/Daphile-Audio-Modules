from copy import deepcopy
from http.client import HTTPConnection
import json

def api(path,payload=None):
    global cookie
    c=HTTPConnection('127.0.0.1',80,timeout=42)
    headers={'Content-Type':'application/json'}
    if cookie:headers['Cookie']=cookie
    try:
        c.request('POST' if payload is not None else 'GET','/cgi-bin/RMEPlugin/api/'+path,json.dumps(payload) if payload is not None else None,headers)
        r=c.getresponse()
        if r.getheader('Set-Cookie'):cookie=r.getheader('Set-Cookie').split(';')[0]
        v=json.loads(r.read())
        if r.status!=200:raise RuntimeError((path,r.status,v.get('error')))
        return v
    finally:c.close()

def setval(route,**patch):
    global s
    v=api('rme/'+route,{'confirm':True,'patch':dict(output=o,expected=s['revision'],**patch)})
    print(route,v['status'],v.get('reason'),flush=True)
    assert v.get('readback_verified') and v['status'] in ('applied-verified','no-change')
    s=v['state']
def snapshot():return api('rme/snapshot',{})
def equivalent(a,b):
    return all(a[k]==b[k] for k in ('outputs','current_eq','device'))
def undo():
    global s
    v=api('rme/control/restore',dict(confirm=True,output=o,expected=s['revision']))
    print('undo',v['status'],v.get('reason'),flush=True)
    assert v.get('readback_verified')
    s=v['state']

cookie=None
api('session');s=snapshot()
assert s['protected_eq_slots']==[4,5]
for slot in (4,5):
 b=api('rme/memory/read',{'slot':slot})
 try:
  api('rme/memory/store',{'confirm':True,'patch':{'output':'phones','expected':s['revision'],'slot':slot,'fingerprint':b['fingerprint'],'replace':True,'name':'Tentativo'}})
  raise AssertionError('Salvataggio protetto consentito')
 except RuntimeError as e:
  assert 'protetto' in str(e)
 print('SOVRASCRITTURA BLOCCATA',slot,flush=True)
assert not snapshot()['pending']
