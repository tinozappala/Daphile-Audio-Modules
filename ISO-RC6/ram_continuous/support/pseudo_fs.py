"""Squashfs-tools 4.6.1 pseudo metadata/data reader; no host inode emulation."""
from pathlib import Path
import hashlib,base64,mmap
MARKER=b'#\n# START OF DATA - DO NOT MODIFY\n#\n'
def name_and_rest(raw):
 name=bytearray();i=0
 while i<len(raw):
  c=raw[i]
  if c==92:
   i+=1;assert i<len(raw),'Dangling filename escape';name.append(raw[i]);i+=1
  elif c in [32,9]:break
  else:name.append(c);i+=1
 assert name
 return name.decode('utf-8','surrogateescape'),raw[i:].lstrip()
def read_header(path):
 path=Path(path)
 with path.open('rb') as f:
  prefix=f.read(8*1024*1024)
 at=prefix.find(MARKER);assert at>=0,'Pseudo data marker not found within bound'
 header=prefix[:at];start=at+len(MARKER);entries={}
 for raw in header.splitlines():
  if not raw or raw.startswith(b'#'):continue
  name,rest=name_and_rest(raw);assert name not in entries
  fields=rest.split();kind=fields[0].decode();assert kind in ['D','R','S','C','B','I'],'Unsupported pseudo type '+kind
  row={'kind':kind,'mtime':int(fields[1]),'mode':int(fields[2],8),'uid':int(fields[3]),'gid':int(fields[4]),'raw':raw}
  if kind=='R':
   assert len(fields)==8;row.update(bytes=int(fields[5]),offset=int(fields[6]),data_flags=int(fields[7]))
   assert 0<=row['offset'] and 0<=row['bytes'] and start+row['offset']+row['bytes']<=path.stat().st_size
  elif kind=='S':row['symlink_target_base64']=base64.b64encode(rest.split(None,5)[5]).decode()
  elif kind in ['B','C']:assert len(fields)==7;row['rdev']=[int(fields[5]),int(fields[6])]
  elif kind=='I':assert len(fields)==6;row['node_type']=fields[5].decode()
  else:assert len(fields)==5
  entries[name]=row
 return {'header':header,'start':start,'entries':entries,'data_bytes':path.stat().st_size-start}
def inventory(path):
 info=read_header(path);table={}
 with Path(path).open('rb') as f:
  with mmap.mmap(f.fileno(),0,access=mmap.ACCESS_READ) as mm:
   for name,row in info['entries'].items():
    item={k:v for k,v in row.items() if k not in ['raw','offset']}
    if row['kind']=='R':
     h=hashlib.sha256();start=info['start']+row['offset'];end=start+row['bytes']
     for at in range(start,end,1024*1024):h.update(mm[at:min(at+1024*1024,end)])
     item['sha256']=h.hexdigest()
    table[name]=item
 return table
