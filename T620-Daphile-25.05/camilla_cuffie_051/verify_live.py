import json
from urllib.request import build_opener, HTTPCookieProcessor, Request
from urllib.error import HTTPError
client=build_opener(HTTPCookieProcessor())
base='http://127.0.0.1/cgi-bin/CamillaPlugin/api/'
def read(name):
 with client.open(base+name,timeout=15) as r:return json.load(r)
assert read('session')['authenticated']
before=read('presets')
assert len(before['protected_ids'])==2
for ident in before['protected_ids']:
 profile=next(x for x in before['presets'] if x['id']==ident)
 for op,body in [('save',{'profile':profile}),('delete',{'id':ident})]:
  body['revision']=before['revision']
  req=Request(base+'presets/'+op,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
  try:client.open(req,timeout=15)
  except HTTPError as error:
   message=json.load(error)['error']
   assert error.code==400 and ('Duplica' in message or 'protett' in message),(error.code,message)
   print(profile['name'],op,error.code,message)
  else:raise AssertionError(op)
assert before==read('presets')
print('Catalogo e revisione invariati')
