"""Four persistent preset slots. Saving never contacts an audio device."""
import copy,json,os,pathlib,threading,uuid

class Slots:
 def __init__(self,path,validate):
  self.path=pathlib.Path(path);self.validate=validate;self.lock=threading.RLock()
 def snapshot(self):
  with self.lock:
   if not self.path.exists():return {'revision':0,'slots':[None]*4}
   if self.path.is_symlink() or self.path.stat().st_size>131072:raise ValueError('Invalid preset slots file')
   data=json.loads(self.path.read_text(encoding='utf8'))
   if not isinstance(data,dict) or set(data)!={'revision','slots'} or type(data['revision']) is not int or data['revision']<0 or not isinstance(data['slots'],list) or len(data['slots'])!=4:raise ValueError('Invalid preset slots')
   for row in data['slots']:
    if row is not None:
     if not isinstance(row,dict) or set(row)!={'name','profile'} or not isinstance(row['name'],str) or not 1<=len(row['name'].strip())<=48:raise ValueError('Invalid preset slot')
     self.validate(row['profile'])
   return copy.deepcopy(data)
 def mutate(self,data,delete=False):
  required={'revision','slot'} if delete else {'revision','slot','name','profile'}
  if not isinstance(data,dict) or set(data)!=required or type(data['slot']) is not int or not 1<=data['slot']<=4:raise ValueError('Select one of four preset slots')
  with self.lock:
   current=self.snapshot()
   if type(data['revision']) is not int or data['revision']!=current['revision']:raise ValueError('Preset slots changed; reload before saving')
   if delete:row=None
   else:
    name=data['name']
    if not isinstance(name,str) or not 1<=len(name.strip())<=48:raise ValueError('Preset name must contain 1..48 characters')
    row={'name':name.strip(),'profile':self.validate(data['profile'])}
   current['slots'][data['slot']-1]=row;current['revision']+=1
   payload=json.dumps(current,allow_nan=False).encode('utf8')
   if len(payload)>131072:raise ValueError('Preset slots exceed 128 KiB')
   self.path.parent.mkdir(parents=True,exist_ok=True)
   temp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
   try:
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
     stream.write(payload);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,self.path)
    if os.name=='posix':
     fd=os.open(self.path.parent,os.O_RDONLY)
     try:os.fsync(fd)
     finally:os.close(fd)
   finally:
    if temp.exists():temp.unlink()
   return copy.deepcopy(current)
