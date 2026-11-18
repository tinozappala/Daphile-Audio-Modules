"""GRUB-level unacknowledged-boot fallback plus bounded Linux health check.

The saved native daphile_opts always survives. A kernel hang cannot be detected
by this Python program: GRUB reverts on the next power cycle or reboot.
"""
import contextlib,hashlib,os,re,subprocess,time,uuid
from pathlib import Path
import engine
from detection import read,scan
GRUB=Path('/boot/grub/grub.cfg');ENV=Path('/boot/grub/grubenv')
MARKER='# DAPHILE AUTOTUNE GUARDED BOOT v1'
HOOK='''# DAPHILE AUTOTUNE GUARDED BOOT v1
insmod test
set dat_native_opts="${daphile_opts}"
# Recover only a prior unacknowledged primary boot.
if [ "${dat_boot_pending}" = "1" ]; then
  unset dat_profile_opts
  unset dat_boot_pending
  set dat_boot_recovered=1
  save_env dat_profile_opts dat_boot_pending dat_boot_recovered
fi
'''
PRIMARY_HOOK='''
  # Advanced options belong only to the first native firmware entry.
  if [ -n "${dat_profile_opts}" ]; then
    set dat_boot_pending=1
    if save_env dat_boot_pending; then
      set daphile_opts="${dat_native_opts} ${dat_profile_opts}"
    fi
  fi
'''

def env():
 p=subprocess.run(['grub-editenv',str(ENV),'list'],capture_output=True,text=True,check=True,timeout=5)
 return dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)

@contextlib.contextmanager
def writable_boot():
 mounts=(read('/proc/self/mountinfo') or '').splitlines();entries=[]
 for line in mounts:
  left,sep,right=line.partition(' - ');a=left.split();b=right.split()
  if len(a)>5 and a[4]=='/boot' and sep and len(b)>1:entries.append((a[2],a[5].split(','),b[0],b[1]))
 if len(entries)!=1 or entries[0][2]!='ext4' or not entries[0][3].startswith('/dev/'):raise RuntimeError('Writable installed native ext4 boot partition required')
 original=entries[0];was_ro='ro' in original[1]
 if was_ro:subprocess.run(['mount','-o','remount,rw','/boot'],check=True,capture_output=True,timeout=10)
 try:yield
 finally:
  os.sync()
  if was_ro:subprocess.run(['mount','-o','remount,ro','/boot'],check=True,capture_output=True,timeout=10)

def setenv(**values):
 if any(key not in ['dat_profile_opts','dat_boot_pending','dat_boot_recovered'] for key in values):raise ValueError('Unapproved GRUB variable')
 with writable_boot():
  for key,value in values.items():
   subprocess.run(['grub-editenv',str(ENV),'set',key+'='+value],check=True,capture_output=True,timeout=5)
   if env().get(key)!=value:raise RuntimeError('GRUB environment readback failed')

def config_guard():
 if GRUB.is_symlink() or ENV.is_symlink() or not GRUB.is_file() or not ENV.is_file():raise RuntimeError('Native GRUB files unavailable')
 text=GRUB.read_text()
 if text.count('load_env')!=1 or '${daphile_opts}' not in text or 'linux /boot/' not in text:raise RuntimeError('Unrecognized native Daphile boot layout')
 if GRUB.stat().st_uid!=0 or GRUB.stat().st_mode&0o022:raise RuntimeError('Boot configuration must be root controlled')
 return text

def replace_config(expected,text):
 if config_guard()!=expected:raise RuntimeError('Boot configuration changed concurrently')
 temp=GRUB.with_name('.dat-'+uuid.uuid4().hex)
 with writable_boot():
  try:
   with temp.open('x') as f:os.chmod(temp,0o644);f.write(text);f.flush();os.fsync(f.fileno())
   subprocess.run(['grub-script-check',str(temp)],check=True,capture_output=True,timeout=5)
   if config_guard()!=expected:raise RuntimeError('Concurrent firmware boot update')
   os.replace(temp,GRUB);os.sync()
  finally:temp.unlink(missing_ok=True)

def recovery_entry(text):
 """Clone only the first native Daphile entry, retaining its UUIDs/images."""
 start=text.index('menuentry ');opening=text.index('{',start);depth=1;end=opening+1
 while end<len(text) and depth:
  depth+=1 if text[end]=='{' else -1 if text[end]=='}' else 0;end+=1
 if depth:raise RuntimeError('Unbalanced native GRUB entry')
 body=text[opening+1:end-1]
 body=re.sub(r'^\s*savedefault\s+\d+\s*$', '',body,flags=re.M)
 # Daphile's native bootcheck enumerates only literal daphile=fw lines.
 # A second literal for the same image produces two indices, making its
 # grub-set-default call fail and leaving the next-boot fallback selected.
 images=re.findall(r'\bdaphile=(fw[0-9]+)\b',body)
 if len(images)!=1:raise RuntimeError('One literal native firmware argument required')
 image=images[0]
 if '/boot/'+image+'/kernel' not in body or '/boot/'+image+'/initrd' not in body:raise RuntimeError('Recovery kernel/initrd identity mismatch')
 body=body.replace('daphile='+image,'daphile=${dat_recovery_firmware}',1)
 body=body.replace('${daphile_opts}','${dat_native_opts} dat_safe_boot=1')
 return "\nmenuentry 'Daphile AutoTune recovery - native options' {\n  set dat_recovery_firmware="+image+"\n  unset dat_profile_opts\n  unset dat_boot_pending\n  save_env dat_profile_opts dat_boot_pending\n"+body+'\n}\n'

def stage(selected=None,tickless=False,state=engine.STATE,confirmed=False):
 if os.geteuid()!=0 or not confirmed:raise RuntimeError('Root and explicit boot/experimental acknowledgement required')
 from power_policy import isolation
 h=scan();plan=isolation(h,selected,tickless)
 if not h['system'].get('product_uuid'):raise RuntimeError('Host identity unavailable')
 with engine.locked(state) as root:
  engine.ensure_idle_transaction(root);text=config_guard();variables=env()
  if variables.get('dat_profile_opts') or variables.get('dat_boot_pending')=='1':raise RuntimeError('An active boot profile already exists; restore it first')
  base=variables.get('daphile_opts','')
  if any(x+'=' in base for x in ['isolcpus','irqaffinity','nohz_full','rcu_nocbs']):raise RuntimeError('Native isolation already configured; not overwritten')
  if MARKER in text:raise RuntimeError('Existing guarded hook requires saved recovery record')
  token=uuid.uuid4().hex;options=' '.join(plan['options'])+' dat_boot_guard='+token
  record={'id':token,'status':'preparing','host_uuid':h['system']['product_uuid'],'kernel_release':h['kernel']['release'],'firmware':h['daphile_version'],'original_config':text,'original_config_sha256':hashlib.sha256(text.encode()).hexdigest(),'native_options':base,'profile_options':options,'plan':plan,'failed_boots':0,'created':time.time()}
  opening=text.index('{',text.index('menuentry '))
  primary=text[:opening+1]+PRIMARY_HOOK+text[opening+1:]
  hooked=primary.replace('load_env','load_env\n'+HOOK,1)+recovery_entry(text)
  hooked=re.sub(r'^set timeout=0$', 'set timeout=5',hooked,flags=re.M)
  record['hooked_config_sha256']=hashlib.sha256(hooked.encode()).hexdigest()
  engine.atomic(root/'boot-profile.json',record)
  try:
   fresh=scan()
   if fresh['system'].get('product_uuid')!=h['system']['product_uuid'] or isolation(fresh,selected,tickless)!=plan:raise RuntimeError('CPU topology changed before boot write')
   replace_config(text,hooked)
   if env().get('daphile_opts','')!=base:raise RuntimeError('Native boot options changed concurrently')
   setenv(dat_profile_opts=options,dat_boot_pending='0',dat_boot_recovered='0')
   record.update(status='staged-reboot-required',hooked_config_sha256=hashlib.sha256(hooked.encode()).hexdigest());engine.atomic(root/'boot-profile.json',record)
  except BaseException:
   try:setenv(dat_profile_opts='',dat_boot_pending='0');record['status']='stage-failed-disabled'
   except Exception as e:record.update(status='stage-failed-recovery-required',disable_error=str(e))
   engine.atomic(root/'boot-profile.json',record);raise
 return {k:v for k,v in record.items() if k not in ['original_config','native_options']}

def status(state=engine.STATE):
 path=Path(state)/'boot-profile.json'
 if not path.exists():return {'status':'inactive','first_boot_autotune':False}
 r=engine.load(path);return {k:v for k,v in r.items() if k not in ['original_config','native_options']}

def restore(state=engine.STATE):
 with engine.locked(state) as root:
  r=engine.load(root/'boot-profile.json')
  if hashlib.sha256(r['original_config'].encode()).hexdigest()!=r['original_config_sha256']:raise RuntimeError('Saved native boot configuration integrity failure')
  if r['host_uuid']!=read('/sys/class/dmi/id/product_uuid'):raise RuntimeError('Boot profile belongs to another host')
  setenv(dat_profile_opts='',dat_boot_pending='0',dat_boot_recovered='0')
  text=config_guard()
  if hashlib.sha256(text.encode()).hexdigest()==r.get('hooked_config_sha256'):
   replace_config(text,r['original_config'])
  elif text!=r['original_config']:raise RuntimeError('Firmware changed GRUB config; profile disabled but no stale config restored')
  r['status']='disabled-reboot-required';engine.atomic(root/'boot-profile.json',r)
 return {'status':r['status'],'runtime_isolation_unchanged_until_reboot':True}

def acknowledge(r,checks,root):
 if set(checks)!={'web','lms'} or not all(checks.values()):return False
 if read('/sys/class/dmi/id/product_uuid')!=r['host_uuid'] or read('/version')!=r['firmware']:return False
 options=(read('/proc/cmdline') or '').split()
 if not all(token in options for token in r['profile_options'].split()):return False
 from detection import numbers
 if numbers(read('/sys/devices/system/cpu/isolated'))!=r['plan']['isolated_cpus']:return False
 setenv(dat_boot_pending='0');r.update(status='healthy-boot',health=checks,boot_id=engine.boot_id(),healthy_at=time.time());engine.atomic(root/'boot-profile.json',r);engine.atomic(root/'boot-last-known-good.json',r);return True

def health_check(state=engine.STATE,timeout=300):
 if not 1<=timeout<=600:raise ValueError('Health check timeout1..600 seconds')
 path=Path(state)/'boot-profile.json'
 if not path.exists():return {'status':'inactive','autotune_applied':False}
 r=engine.load(path);current=env()
 if current.get('dat_boot_recovered')=='1':
  with engine.locked(state) as root:
   if r['status']!='health-timeout-safe-next-boot':r['failed_boots']+=1
   r['status']='automatic-safe-boot-recovery';engine.atomic(root/'boot-profile.json',r)
   setenv(dat_boot_recovered='0')
  return {'status':r['status'],'failed_boots':r['failed_boots'],'native_boot_options_restored':True}
 if not current.get('dat_profile_opts'):return {'status':'inactive','autotune_applied':False}
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  import urllib.request
  checks={};opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  for name,url in [('web','http://127.0.0.1/'),('lms','http://127.0.0.1:9000/')]:
   try:
    with opener.open(url,timeout=2) as response:checks[name]=response.status==200
   except OSError:checks[name]=False
  if all(checks.values()):
   with engine.locked(state) as root:
    if acknowledge(r,checks,root):return status(state)
  time.sleep(2)
 with engine.locked(state) as root:
  # Leave pending armed: GRUB will use native options at the next restart.
  r.update(status='health-timeout-safe-next-boot',failed_boots=r['failed_boots']+1);engine.atomic(root/'boot-profile.json',r)
 return {'status':r['status'],'safe_next_boot':True,'reboot_performed':False}

def startup(state=engine.STATE):
 """Recovery only at boot; requested automations remain opt-in."""
 root=Path(state);result={'first_boot_autotune':False,'automatic_profile_applied':False}
 pending=root/'transaction.json'
 if pending.exists():
  r=engine.load(pending)
  if r.get('status') in ['pending','rollback-incomplete'] and r.get('boot_id')!=engine.boot_id():result['interrupted_runtime']=engine.restore('pending',root)['status']
 session=root/'ab-session.json'
 if session.exists():
  r=engine.load(session)
  if r.get('status') in ['running','recovering'] and r.get('boot_id')!=engine.boot_id():
   import ab_session
   result['interrupted_AB']=ab_session.public(ab_session.recover(root,r))
 lease=root/'activity-session.json'
 if lease.exists():
  record=engine.load(lease)
  if record.get('status') in ['running','recovering','recovery-required'] and record.get('boot_id')!=engine.boot_id():
   import session
   session.recover(root,record);result['interrupted_activity']='recovered'
 import companion_migration
 result['companion']=companion_migration.migrate(root)
 result['boot']=health_check(root)
 import automation
 result['automation']=automation.start(root)
 import continuous
 result['continuous']=continuous.start(root)
 import automatic
 result['automatic']=automatic.startup(root)
 return result
