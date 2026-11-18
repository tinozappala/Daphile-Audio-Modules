"""Migrate only the byte-identified legacy T630 companion, with private rollback."""
import contextlib,hashlib,os,signal,subprocess,time,uuid
from pathlib import Path
import engine
from detection import read
ADDONS=Path('/mnt/hdd/add-ons');RUN=Path('/run/daphile-t630-affinity');NAME='95_hp_t630_audio_affinity'
KNOWN={'py':'84f32b9bc5360af7985bb4d59048afe60c02f00f746af9cb39a255a7482430ec','start':'3c2889b733ff7fd4a0e7223c2548deafd740b6cdb6cf4b63e1f4ec02d951f990','stop':'54bfc5d3134e1c70f535e46db83cc90040b179840443580faa176eec50860794'}
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def process():
 try:
  pid=int((RUN/'pid').read_text());start=engine.start_ticks(pid)
  argv=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
  if start and str(ADDONS/(NAME+'.py')).encode() in argv:return {'pid':pid,'start':start,'boot_id':engine.boot_id()}
 except (OSError,ValueError):pass
 return None
def stop_owned(r):
 if not r:return
 if engine.start_ticks(r['pid'])!=r['start']:return
 __import__('process_guard').signal_record(r,signal.SIGTERM);end=time.monotonic()+15
 while engine.start_ticks(r['pid'])==r['start'] and time.monotonic()<end:time.sleep(.1)
 if engine.start_ticks(r['pid'])==r['start']:raise RuntimeError('Companion did not gracefully restore and stop; migration refused')
def start_owned():
 subprocess.run(['/bin/sh',str(ADDONS/(NAME+'.start'))],check=True,timeout=10,capture_output=True)
 end=time.monotonic()+20
 while time.monotonic()<end:
  r=process()
  if r:return r
  time.sleep(.1)
 raise RuntimeError('Companion restart did not become observable')
def replace(path,data,mode):
 temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  os.replace(temp,path)
 finally:temp.unlink(missing_ok=True)
def installed_source():return str(Path(__file__).resolve()).startswith('/opt/daphile-autotune/lib/')
def migrate(state=engine.STATE):
 if not installed_source() or Path(state).resolve()!=engine.STATE.resolve():return {'status':'staged-or-test-source-no-migration'}
 if read('/sys/class/dmi/id/product_name')!='HP t630 Thin Client':return {'status':'not-applicable-other-hardware'}
 # A native firmware update temporarily boots without the requested isolation.
 # Preserve the running legacy daemon until the next qualified isolated boot.
 if read('/sys/devices/system/cpu/isolated') not in ['2-3','2,3']:return {'status':'deferred-until-requested-isolation','legacy_companion_preserved':True}
 target=ADDONS/(NAME+'.py');template=Path(__file__).parent/'native_companion.py'
 if not target.exists():return {'status':'not-installed-no-profile-created'}
 if target.is_symlink() or not target.is_file() or target.stat().st_uid!=0 or target.stat().st_mode&0o022:raise RuntimeError('Companion file is not root-owned and protected')
 wanted=digest(template)
 if digest(target)==wanted:return {'status':'already-current','sha256':wanted}
 for suffix,expected in KNOWN.items():
  path=ADDONS/(NAME+'.'+suffix)
  if path.is_symlink() or not path.is_file() or digest(path)!=expected:return {'status':'unrecognized-legacy-preserved','component':suffix}
 with engine.locked(state) as root:
  engine.ensure_session_owner(root);backup=root/'companion-original.py'
  if backup.exists() and digest(backup)!=KNOWN['py']:raise RuntimeError('Existing companion backup differs; no overwrite')
  if not backup.exists():replace(backup,target.read_bytes(),0o600)
  old=process();r={'status':'pending','before_sha256':KNOWN['py'],'after_sha256':wanted,'was_running':bool(old),'boot_id':engine.boot_id()}
  engine.atomic(root/'companion-migration.json',r)
  try:
   stop_owned(old)
   if digest(target)!=KNOWN['py']:raise RuntimeError('Legacy companion changed during migration')
   replace(target,template.read_bytes(),0o600)
   if old:start_owned()
   r['status']='updated-verified';r['current_sha256']=digest(target)
  except BaseException as e:
   try:
    # Restore only the file version written by this transaction.
    stop_owned(process())
    if digest(target) not in [KNOWN['py'],wanted]:raise RuntimeError('External companion edit preserved; manual recovery required')
    replace(target,backup.read_bytes(),0o600)
    if old:start_owned()
    r.update(status='failed-restored',error=type(e).__name__+': '+str(e))
   except Exception as recovery:r.update(status='recovery-required',error=type(e).__name__+': '+str(e),recovery_error=str(recovery))
   engine.atomic(root/'companion-migration.json',r);raise
  engine.atomic(root/'companion-migration.json',r);return r

__release_signature__ = 'Tino Zappala'
