"""Atomic preset store with revision conflicts, bounded history and validated recovery."""
import copy,json,os,pathlib,threading,uuid
from .model import flat,validate,identifier,ValidationError
class ConflictError(ValidationError): pass

with pathlib.Path(__file__).with_name('headphones.json').open(encoding='utf8') as source:
 DEFAULT_HEADPHONES = {p['id']:validate(p) for p in json.load(source)}
PROTECTED_PRESETS = frozenset(DEFAULT_HEADPHONES)
class Store:
 def __init__(self,path):
  self.path=pathlib.Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock(); self.recovery=None
  self.data={'revision':0,'presets':{},'previous':None}
  if self.path.exists():
   try: self.data=self._read(self.path)
   except (ValueError,OSError,TypeError,KeyError):
    backup=self.path.with_suffix('.previous.json')
    if not backup.exists(): raise ValidationError('Corrupt state; no valid backup; refusing startup')
    self.data=self._read(backup); self.recovery='Recovered previous valid state'
  self._preserve_headphone_edits()
 def _preserve_headphone_edits(self):
  updated=copy.deepcopy(self.data)
  presets=updated['presets']
  for key,default in DEFAULT_HEADPHONES.items():
   old=presets.get(key)
   if old is None or old==default:continue
   comparable=copy.deepcopy(old)
   for channel in ('L','R'):
    for band,reference in zip(comparable['peq'][channel],default['peq'][channel]):
     band['id']=reference['id']
   if comparable!=default:
    personal=copy.deepcopy(old)
    personal['id']=uuid.uuid4().hex
    personal['name']=old['name'][:60]+' · personale'
    presets[personal['id']]=validate(personal)
   presets[key]=copy.deepcopy(default)
  if updated!=self.data:
   updated['revision']+=1
   self._atomic(self.path.with_suffix('.previous.json'),self.data)
   self._atomic(self.path,updated)
   self.data=updated
 def _read(self,path):
  d=json.loads(path.read_text(encoding='utf8'))
  if not isinstance(d,dict) or set(d)!={'revision','presets','previous'} or type(d['revision']) is not int or d['revision']<0 or not isinstance(d['presets'],dict): raise ValidationError('Corrupt store')
  for key,p in d['presets'].items():
   if key=='flat' or key!=p['id']: raise ValidationError('Invalid stored identifier')
   validate(p)
  if d['previous'] is not None:
   if not isinstance(d['previous'],dict): raise ValidationError('Invalid history')
   for key,p in d['previous'].items():
    if key=='flat' or key!=p['id']: raise ValidationError('Invalid history identifier')
    validate(p)
  return d
 def _atomic(self,path,data):
  temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
  try:
   with temp.open('x',encoding='utf8') as f:
    json.dump(data,f,allow_nan=False); f.flush(); os.fsync(f.fileno())
   os.replace(temp,path)
   if os.name=='posix':
    fd=os.open(path.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
  finally:
   if temp.exists(): temp.unlink()
 def snapshot(self):
  with self.lock:
   catalog = dict(self.data['presets'])
   catalog.update(DEFAULT_HEADPHONES)
   return {'revision':self.data['revision'],'presets':[flat()]+copy.deepcopy(list(catalog.values())),'recovery':self.recovery,'protected_ids':sorted(PROTECTED_PRESETS)}
 def import_slots_once(self,slots):
  """Merge the old four slots into the one catalog; retain the old file."""
  marker=self.path.with_name('preset-slots-imported.json')
  with self.lock:
   if marker.exists():return
   rows=slots.snapshot()['slots'];next_data=copy.deepcopy(self.data);added=[]
   for number,row in enumerate(rows,1):
    if row is None:continue
    p=copy.deepcopy(row['profile']);p['name']=row['name']
    # Stable across a crash between committing the catalog and its marker.
    p['id']=uuid.uuid5(uuid.NAMESPACE_URL,'daphile:legacy-slot:'+str(number)+':'+json.dumps(p,sort_keys=True)).hex
    p=validate(p)
    def content(value):return {k:v for k,v in value.items() if k!='id'}
    if p['id'] in next_data['presets'] or any(content(v)==content(p) for v in next_data['presets'].values()):continue
    if len(next_data['presets'])>=128:raise ValidationError('Archivio pieno: impossibile recuperare i vecchi preset')
    next_data['presets'][p['id']]=p;added.append(p['id'])
   if added:
    next_data['previous']=copy.deepcopy(self.data['presets']);next_data['revision']+=1
    self._atomic(self.path.with_suffix('.previous.json'),self.data)
    self._atomic(self.path,next_data);self.data=next_data
   self._atomic(marker,{'version':1,'imported':added})
 def mutate(self,revision,operation,profile=None,preset_id=None):
  with self.lock:
   if type(revision) is not int or revision!=self.data['revision']: raise ConflictError('State changed; reload before saving')
   next_data=copy.deepcopy(self.data); presets=next_data['presets']; before=copy.deepcopy(presets)
   if operation=='save':
    p=validate(profile)
    if p['id'] in PROTECTED_PRESETS:
     raise ValidationError('Preset cuffie predefinito. Usa Duplica per salvare le tue modifiche.')
    if p['id']=='flat': raise ValidationError('Flat is immutable; duplicate first')
    if p['id'] not in presets and len(presets)>=128: raise ValidationError('Preset limit reached')
    presets[p['id']]=p
   elif operation=='delete':
    identifier(preset_id)
    if preset_id in PROTECTED_PRESETS:
     raise ValidationError('Questo preset cuffie è protetto dalla cancellazione.')
    if preset_id=='flat' or preset_id not in presets: raise ValidationError('Preset is immutable or absent')
    del presets[preset_id]
   elif operation=='restore':
    if self.data['previous'] is None: raise ValidationError('No previous state')
    next_data['presets']=copy.deepcopy(self.data['previous'])
    # Undoing an unrelated operation cannot remove a protected profile or
    # silently revert edits made to it after the history snapshot.
    for key in PROTECTED_PRESETS:
     if key in before: next_data['presets'][key]=copy.deepcopy(before[key])
   else: raise ValidationError('Unknown operation')
   next_data['previous']=before; next_data['revision']+=1
   self._atomic(self.path.with_suffix('.previous.json'),self.data)
   self._atomic(self.path,next_data); self.data=next_data
   return self.snapshot()
