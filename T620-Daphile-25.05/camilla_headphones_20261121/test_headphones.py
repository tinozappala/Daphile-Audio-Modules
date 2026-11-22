from pathlib import Path
import copy,json,sys,tempfile
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'DaphileCamillaDSP/backend'))
from peq.store import Store
from peq.model import ValidationError,compile_config
profiles=json.loads((root/'HEADPHONE_PROFILES.json').read_text('utf8'))
with tempfile.TemporaryDirectory(dir=root,prefix='test-') as d:
 store=Store(Path(d)/'presets.json')
 initial=store.snapshot()
 for p in profiles:
  snap=store.mutate(initial['revision'],'save',profile=p)
  initial=snap
 assert set(initial['protected_ids'])=={p['id'] for p in profiles}
 for p in profiles:
  try:store.mutate(store.snapshot()['revision'],'delete',preset_id=p['id'])
  except ValidationError:pass
  else:raise AssertionError('Protected preset was deletable')
  edited=copy.deepcopy(p);edited['peq']['L'][0]['gain']+=.5;edited['peq']['R'][0]['gain']+=.5
  snap=store.mutate(store.snapshot()['revision'],'save',profile=edited)
  saved=next(x for x in snap['presets'] if x['id']==p['id'])
  assert saved==edited
 snap=store.mutate(store.snapshot()['revision'],'restore')
 assert all(next(x for x in snap['presets'] if x['id']==p['id'])['peq']['L'][0]['gain']==p['peq']['L'][0]['gain']+.5 for p in profiles)
 restored=Store(Path(d)/'presets.json').snapshot()
 assert set(restored['protected_ids'])=={p['id'] for p in profiles}
for p in profiles:
 for rate in (44100,48000,192000,384000):
  cfg=compile_config(p,rate,512,{'type':'Stdin','channels':2,'format':'S32LE'},{'type':'Stdout','channels':2,'format':'S32LE'})
  assert len(cfg['pipeline'])==2 and all(len(row['names'])==11 for row in cfg['pipeline'])
print('PASS: delete blocked, editing persisted, restore preserved protected edits, profile compilation at four sample rates.')
