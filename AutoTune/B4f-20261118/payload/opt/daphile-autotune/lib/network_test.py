"""Bounded read-only NAS checks; no remount, writes, or cache eviction."""
import json,os,re,socket,subprocess,sys,time
from pathlib import Path
from detection import read,scan,command

def mounts():
 result=[]
 for line in (read('/proc/self/mountinfo') or '').splitlines():
  a,_,b=line.partition(' - ');left=a.split();right=b.split()
  if len(left)>4 and len(right)>1 and right[0] in ['cifs','nfs','nfs4']:
   decode=lambda s:re.sub(r'\\([0-7]{3})',lambda m:chr(int(m[1],8)),s)
   result.append(dict(path=decode(left[4]),fs=right[0],source=decode(right[1])))
 return result

def nas_probe(path):
 item=next((x for x in mounts() if x['path']==path),None)
 if not item:raise RuntimeError('Not a current network mount')
 source=item['source'];host=source[2:].split('/')[0] if source.startswith('//') else source.split(':')[0]
 if '@' in host:host=host.rsplit('@',1)[1]
 socket.create_connection((host,445 if item['fs']=='cifs' else 2049),timeout=3).close()
 start=time.monotonic();st=os.statvfs(path);chosen=None;queue=[(Path(path),0)];entries=0
 try:
  from player import rpc
  from urllib.parse import urlparse,unquote
  players=rpc('',['serverstatus','0','20']).get('players_loop',[])
  for player in players:
   tracks=rpc(player['playerid'],['status','-','1','tags:u']).get('playlist_loop',[])
   for track in tracks:
    parsed=urlparse(track.get('url',''))
    if parsed.scheme=='file':
     candidate=Path(unquote(parsed.path)).resolve()
     if str(candidate).startswith(path.rstrip('/')+'/') and candidate.suffix.lower() in ['.flac','.wav','.dsf','.dff','.aiff','.mp3']:chosen=candidate;break
   if chosen:break
 except Exception:pass
 while not chosen and queue and entries<2048 and time.monotonic()-start<8:
  root,depth=queue.pop()
  with os.scandir(root) as listing:
   for entry in listing:
    entries+=1
    if entries>2048:break
    if entry.is_symlink() or entry.name.startswith('.'):continue
    if entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in ['.flac','.wav','.dsf','.dff','.aiff','.mp3']:
     chosen=Path(entry.path);break
    if depth<8 and entry.is_dir(follow_symlinks=False):queue.append((Path(entry.path),depth+1))
  if chosen:break
 if not chosen:return dict(status='inconclusive',tcp_reachable=True,metadata_read=True,reason='No media file within bounded search',sample_read=False)
 t=time.monotonic();total=0
 fd=os.open(chosen,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
 try:
  while total<16*1024*1024:
   data=os.read(fd,min(1024*1024,16*1024*1024-total))
   if not data:break
   total+=len(data)
 finally:os.close(fd)
 elapsed=time.monotonic()-t
 return dict(status='pass' if total else 'inconclusive',tcp_reachable=True,metadata_read=True,sample_read=total>0,bytes_read=total,seconds=elapsed,observed_MB_s=total/1024/1024/max(elapsed,.000001),cache_possible=True,free_bytes=st.f_bavail*st.f_frsize)

def audit():
 results=[]
 for index,m in enumerate(mounts()):
  # Path stays in local child only; passwords and server/share names are not exported.
  args=[sys.executable,str(Path(__file__).resolve()),'probe',m['path']]
  child=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
  try:
   out,err=child.communicate(timeout=12)
   result=json.loads(out) if child.returncode==0 else dict(status='failed',reason='NAS TCP/metadata/media read failed')
  except subprocess.TimeoutExpired:
   child.kill()
   try:child.communicate(timeout=1);blocked=False
   except subprocess.TimeoutExpired:blocked=True
   result=dict(status='inconclusive',reason='NAS probe timed out',uninterruptible_child_possible=blocked)
  except (ValueError,OSError):result=dict(status='failed',reason='Malformed probe response')
  results.append(dict(mount_index=index,filesystem=m['fs'],**result))
 h=scan();interfaces=[dict(name=x['name'],carrier=x['carrier'],speed=x['speed'],duplex=x['duplex'],statistics=x['statistics'],eee=x['eee'],offload=x['offload'],moderation=x['moderation']) for x in h['network'] if x['name']!='lo']
 return dict(network=interfaces,route=command(['ip','route']),nas=results,status='pass' if results and all(x['status']=='pass' for x in results) else 'inconclusive',limits=['Cached reads are not disk throughput','NFSv3 portmapper/dynamic ports not certified','No DHCP/static-IP reconfiguration','No NAS contents changed'])

if __name__=='__main__':
 try:print(json.dumps(nas_probe(sys.argv[2])))
 except Exception:sys.exit(1)
