#!/usr/bin/python3
"""Install the B4f release delta on a compatible Daphile release; preserve DATA."""
from pathlib import Path
import argparse,base64,contextlib,fcntl,hashlib,json,os,socket,stat,subprocess,time,uuid

HERE=Path(__file__).resolve().parent
STATE=Path('/mnt/hdd/daphile-autotune')
SOCKET='/run/daphile-autotune-gui/control.sock'
digest=lambda raw:hashlib.sha256(raw).hexdigest()

def status():
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as s:
  s.settimeout(8);s.connect(SOCKET);s.sendall(b'{"action":"status"}\n');raw=b''
  while b'\n' not in raw:
   chunk=s.recv(65536)
   if not chunk:raise RuntimeError('AutoTune broker response incomplete')
   raw+=chunk
   if len(raw)>4*1048576:raise RuntimeError('AutoTune status exceeds bound')
 return json.loads(raw.split(b'\n')[0])

def command(action):
 p=subprocess.run(['/usr/local/bin/dat','--state',str(STATE),'gui',action],capture_output=True,timeout=15)
 if p.returncode:raise RuntimeError('AutoTune GUI '+action+' failed')

def target(name):
 p=Path('/'+name)
 if ('..' in p.parts or not (name.startswith('opt/daphile-autotune/') or name in ('var/www/localhost/htdocs/js/dat-core.js','usr/local/sbin/daphile-audio-policy'))):
  raise RuntimeError('Unknown release destination')
 if p.is_symlink():raise RuntimeError('Indirect destination refused: '+str(p))
 return p

def write(p,raw,mode):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_name('.'+p.name+'.b4f-'+uuid.uuid4().hex)
 try:
  with tmp.open('xb') as stream:
   os.fchmod(stream.fileno(),mode);os.fchown(stream.fileno(),0,0);stream.write(raw);stream.flush();os.fsync(stream.fileno())
  os.replace(tmp,p)
 finally:tmp.unlink(missing_ok=True)

@contextlib.contextmanager
def exclusion():
 cfg=json.loads(Path('/mnt/hdd/system/daphile-ram/config.json').read_text());root=Path(cfg['state_root'])
 if not root.is_absolute() or root.is_symlink() or root.stat().st_uid!=0:raise RuntimeError('Invalid native RAM state root')
 fds=[]
 try:
  for name,mode in [('autotune.lock',fcntl.LOCK_EX),('queue.lock',fcntl.LOCK_SH)]:
   fd=os.open(root/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);fds.append(fd);st=os.fstat(fd)
   if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise RuntimeError('Unprotected native lock')
   end=time.monotonic()+2
   while True:
    try:fcntl.flock(fd,mode|fcntl.LOCK_NB);break
    except BlockingIOError:
     if time.monotonic()>=end:raise RuntimeError('Audio/RAM operation in progress; retry after completion')
     time.sleep(.05)
  current=status()
  if any(j.get('running') or j.get('recovering') for j in current.get('jobs',[])):raise RuntimeError('Wait for the current AutoTune job to finish')
  for name in ('activity-session.json','transaction.json'):
   p=STATE/name
   if p.exists() and json.loads(p.read_text()).get('status') in ('running','recovering','recovery-required','pending','rollback-incomplete'):
    raise RuntimeError('Complete AutoTune recovery before installation')
  if (STATE/'v2-policy-monitor.json').exists():raise RuntimeError('Stop the AutoTune policy monitor before installation')
  yield
 finally:
  for fd in reversed(fds):os.close(fd)

def main():
 p=argparse.ArgumentParser(description=__doc__);g=p.add_mutually_exclusive_group(required=True)
 g.add_argument('--verify',action='store_true');g.add_argument('--install',action='store_true');g.add_argument('--rollback',type=Path)
 args=p.parse_args()
 if os.geteuid()!=0:raise RuntimeError('Root required')
 manifest=json.loads((HERE/'INSTALL_MANIFEST.json').read_text())
 if args.verify:
  bad=[r['path'] for r in manifest if not target(r['path']).is_file() or digest(target(r['path']).read_bytes())!=r['sha256']]
  if bad:raise RuntimeError('Installed release differs: '+', '.join(bad))
  print(json.dumps(dict(status='PASS',release='B4f',verified_files=len(manifest))));return
 with exclusion():
  if args.rollback:
   backup=args.rollback.resolve(strict=True)
   if backup.parent!=Path('/mnt/hdd/system/autotune-updates') or not backup.name.startswith('b4f-') or backup.stat().st_uid!=0 or backup.stat().st_mode&0o077:
    raise RuntimeError('Private native installation backup required')
   record=json.loads((backup/'UNDO.private.json').read_text());updates=[]
   for row in record:
    dest=target(row['path'])
    if not dest.is_file() or digest(dest.read_bytes())!=row['installed_sha256']:raise RuntimeError('Newer source preserved: '+row['path'])
    updates.append((dest,None if row['before'] is None else base64.b64decode(row['before']),row['mode']))
  else:
   updates=[];record=[]
   for row in manifest:
    dest=target(row['path']);raw=(HERE/'payload'/row['path']).read_bytes()
    if digest(raw)!=row['sha256']:raise RuntimeError('Package integrity failed: '+row['path'])
    before=dest.read_bytes() if dest.exists() else None
    if before is not None and digest(before)==row['sha256']:continue
    if (digest(before) if before is not None else None) not in row['accepted_before_sha256']:raise RuntimeError('Modified source preserved: '+row['path'])
    if dest.suffix=='.py':compile(raw,str(dest),'exec')
    if row['path']=='usr/local/sbin/daphile-audio-policy':
     checked=subprocess.run(['/usr/bin/perl','-c',str(HERE/'payload'/row['path'])],capture_output=True,timeout=10)
     if checked.returncode:raise RuntimeError('Native audio policy syntax check failed')
    mode=stat.S_IMODE(dest.stat().st_mode) if dest.exists() else row['mode']
    updates.append((dest,raw,mode));record.append(dict(path=row['path'],before=base64.b64encode(before).decode() if before is not None else None,mode=mode,installed_sha256=row['sha256']))
   if not updates:print(json.dumps(dict(status='already-installed',release='B4f')));return
   backup=Path('/mnt/hdd/system/autotune-updates')/('b4f-'+uuid.uuid4().hex);backup.mkdir(mode=0o700,parents=True)
   write(backup/'UNDO.private.json',(json.dumps(record)+'\n').encode(),0o600)
  command('stop');applied=[]
  try:
   for dest,raw,mode in updates:
    before=dest.read_bytes() if dest.exists() else None;applied.append((dest,before,mode))
    if raw is None:dest.unlink()
    else:write(dest,raw,mode)
   command('start');end=time.monotonic()+10
   while True:
    try:ready=status();break
    except OSError:
     if time.monotonic()>=end:raise RuntimeError('AutoTune GUI did not become ready')
     time.sleep(.1)
   if not ready.get('enabled'):raise RuntimeError('AutoTune is disabled; verify native POWER settings')
  except BaseException:
   for dest,raw,mode in reversed(applied):
    if raw is None:dest.unlink(missing_ok=True)
    else:write(dest,raw,mode)
   command('start');raise
  print(json.dumps(dict(status='rolled-back' if args.rollback else 'installed',release='B4f',changed_files=len(updates),backup=str(backup),audio_services_restarted=False,reboot=False)))

if __name__=='__main__':main()
