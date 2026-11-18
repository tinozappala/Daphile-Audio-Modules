"""Read-only shadow-mount comparisons in a private mount namespace.

Production mounts and library files are never remounted or written. CIFS probes
need an existing root-private credentials file; unavailable credentials are an
explicit unsupported result, never synthesized or exported.
"""
import hashlib,itertools,json,mmap,os,re,statistics,subprocess,sys,time,uuid
from pathlib import Path
from detection import read
import engine,network_test

def mount_options(path):
 for line in (read('/proc/mounts') or '').splitlines():
  fields=line.split()
  if len(fields)<4:continue
  decoded=re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),fields[1])
  if decoded==path:return dict(item.split('=',1) if '=' in item else (item,'') for item in fields[3].split(','))
 return {}

def candidates(item):
 options=mount_options(item['path']);base=['ro','nosuid','nodev','noexec']
 if item['fs']=='cifs':
  cred=options.get('credentials') or options.get('cred')
  if not cred:return {'status':'unsupported','reason':'Existing private CIFS credential-file not available; production mount preserved'}
  p=Path(cred)
  if not p.is_absolute() or p.is_symlink() or not p.is_file() or p.stat().st_uid!=0 or p.stat().st_mode&0o077:return {'status':'unsupported','reason':'CIFS credential-file is not root-private'}
  base+=['credentials='+cred,'cache=strict','nosharesock']
  for key in ['vers','sec','domain','iocharset']:
   if options.get(key) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',options[key]):base.append(key+'='+options[key])
  variants=[base+['rsize='+str(size),'wsize='+str(size),'actimeo='+str(age)] for size,age in itertools.product([65536,262144,1048576],[0,1,30])]
 else:
  base+=['proto=tcp','hard','nosharecache']
  for key in ['vers','minorversion','sec']:
   if options.get(key) and re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',options[key]):base.append(key+'='+options[key])
  variants=[base+['rsize='+str(size),'wsize='+str(size),'actimeo='+str(age)] for size,age in itertools.product([65536,262144,1048576],[0,1,30])]
 return {'status':'available','variants':variants,'options':options}

def source_file(item):
 from player import rpc
 from urllib.parse import unquote,urlparse
 for p in rpc('',['serverstatus','0','20']).get('players_loop',[]):
  for track in rpc(p['playerid'],['status','-','1','tags:u']).get('playlist_loop',[]):
   u=urlparse(track.get('url',''));f=Path(unquote(u.path))
   if u.scheme=='file' and f.is_file():
    try:return str(f.resolve().relative_to(Path(item['path']).resolve()))
    except ValueError:pass
 raise RuntimeError('No current material on this NAS mount')

def probe(spec):
 subprocess.run(['mount','--make-rprivate','/'],check=True,capture_output=True,timeout=5)
 directory=Path(spec['directory']);directory.mkdir(mode=0o700)
 mounted=False
 try:
  subprocess.run(['mount','-t',spec['filesystem'],'-o',','.join(spec['options']),spec['source'],str(directory)],capture_output=True,check=True,timeout=20);mounted=True
  file=directory/spec['relative'];resolved=file.resolve()
  if directory.resolve() not in resolved.parents or file.is_symlink():raise RuntimeError('Probe file escaped shadow mount')
  if not hasattr(os,'O_DIRECT'):raise RuntimeError('Direct I/O unavailable: cached throughput is not a tuning decision')
  size=file.stat().st_size//4096*4096;size=min(size,16*1024*1024)
  if size<1024*1024:raise RuntimeError('Sample too small for comparable direct reads')
  measurements=[];digests=[]
  for _ in range(3):
   fd=os.open(file,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_DIRECT);buffer=mmap.mmap(-1,1024*1024);view=memoryview(buffer);started=time.monotonic();total=0;digest=hashlib.sha256()
   try:
    while total<size:
     n=os.readv(fd,[view[:min(len(view),size-total)]])
     if n<=0:break
     digest.update(view[:n]);total+=n
    elapsed=time.monotonic()-started
    if total!=size:raise RuntimeError('Incomplete direct read')
    measurements.append({'bytes':total,'seconds':elapsed,'MB_s':total/1048576/elapsed});digests.append(digest.hexdigest())
   finally:view.release();buffer.close();os.close(fd)
  return {'status':'measured','reads':measurements,'hashes':digests,'median_MB_s':statistics.median(x['MB_s'] for x in measurements),'direct_io':True,'production_mount_changed':False}
 finally:
  if mounted:subprocess.run(['umount',str(directory)],capture_output=True,timeout=10,check=True)
  directory.rmdir()

def run(state=engine.STATE):
 root=Path(state);report={'status':'running','mounts':[],'production_mounts_changed':False,'sound_quality_claim':False}
 for index,item in enumerate(network_test.mounts()):
  plan=candidates(item);row={'mount_index':index,'filesystem':item['fs'],'status':plan['status'],'trials':[]}
  if plan['status']!='available':row['reason']=plan['reason'];report['mounts'].append(row);continue
  try:relative=source_file(item)
  except Exception as e:row.update(status='inconclusive',reason=str(e));report['mounts'].append(row);continue
  for number,options in enumerate(plan['variants']):
   spec={'filesystem':item['fs'],'source':item['source'],'relative':relative,'options':options,'directory':str(root/('nas-shadow-'+uuid.uuid4().hex))}
   # Private stdin carries mount credentials-path and sample path. Command and
   # exported reports contain neither source names nor authentication data.
   p=subprocess.Popen(['unshare','--mount',sys.executable,str(Path(__file__).resolve()),'probe'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,start_new_session=True)
   try:
    out,_=p.communicate(json.dumps(spec),timeout=60);result=json.loads(out) if p.returncode==0 else {'status':'unsupported-or-failed','reason':'Read-only mount or direct I/O rejected'}
   except subprocess.TimeoutExpired:
    p.kill()
    try:p.communicate(timeout=2)
    except subprocess.TimeoutExpired:pass
    result={'status':'inconclusive','reason':'Private probe deadline exceeded'}
   result.update(candidate=number,rsize=next(x.split('=')[1] for x in options if x.startswith('rsize=')),actimeo=next(x.split('=')[1] for x in options if x.startswith('actimeo=')));row['trials'].append(result)
   engine.atomic(root/'nas-tuning.json',dict(report,current=row))
  valid=[x for x in row['trials'] if x['status']=='measured']
  hashes={d for x in valid for d in x['hashes']}
  row['status']='compared' if valid and len(hashes)==1 else 'inconclusive'
  best=max(valid,key=lambda x:x['median_MB_s']) if row['status']=='compared' else None
  others=[x for x in valid if x is not best]
  separated=bool(best and len(valid)==len(plan['variants']) and others and min(x['MB_s'] for x in best['reads'])>1.1*max(x['MB_s'] for r in others for x in r['reads']))
  row['recommended_read_candidate']=best['candidate'] if separated else None
  row['decision']='separated-repeated-read-winner' if separated else 'inconclusive-retain-production-mount'
  row['application']='Read-only comparison recommendation; production SMB/NFS mount and write semantics preserved'
  report['mounts'].append(row)
 report['status']='finished' if report['mounts'] else 'not-applicable';engine.atomic(root/'nas-tuning.json',report);return report

if __name__=='__main__':
 try:print(json.dumps(probe(json.load(sys.stdin))))
 except Exception:raise SystemExit(1)
