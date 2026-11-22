from pathlib import Path
import hashlib, json, os, re, shutil, subprocess

boot=Path('/boot')
private=Path('/mnt/hdd/system/stock-2505251550-private')
record=json.loads((private/'stage.json').read_text())
firmware=record['stock']; previous=record['previous']
cfg=boot/'grub/grub.cfg'; env=boot/'grub/grubenv'
assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
assert Path('/version').read_text().strip()==firmware
bootid=Path('/proc/sys/kernel/random/boot_id').read_text()
assert (private/'ack').read_text()==bootid
assert (private/'queue-verified').read_text()==bootid
assert (private/'audio-verified').read_text()==bootid
assert any(row.split()[2] in ('cifs','nfs','nfs4') for row in Path('/proc/mounts').read_text().splitlines())

def run(*args,**kwargs):
    return subprocess.run(args,check=True,timeout=kwargs.pop('timeout',15),**kwargs)

old=cfg.read_text()
entries=re.findall(r'(?ms)^menuentry [^\n]+\n.*?^}\s*',old)
new_entries=[]
for index,(slot,title) in enumerate(((firmware,'Daphile 25.05-b251550 RT (originale)'),(previous,'Daphile RC6 (ripristino)'))):
    selected=[x for x in entries if 'daphile='+slot+' ' in x]
    assert len(selected)==1 and (boot/slot/'ok').is_file()
    candidate=re.sub(r"^menuentry '[^']+'","menuentry '"+title+"'",selected[0])
    candidate=re.sub(r'(?m)^(\s*savedefault\s+)\d+$',r'\g<1>'+str(index),candidate)
    new_entries.append(candidate.strip())
    run('md5sum','-c',str(boot/slot/'md5sum.txt'),cwd=boot,timeout=45)
header=old.split('menuentry',1)[0]
one_shot='''set default=0
set fallback=1
set boot_once=
if [ -n "${next_entry}" ]; then
  set daphile_next_entry="${next_entry}"
  set next_entry=
  if save_env next_entry; then
    if [ "${daphile_next_entry}" = "0" ]; then
      set default=0
      set boot_once=true
    elif [ "${daphile_next_entry}" = "1" ]; then
      set default=1
      set boot_once=true
    fi
  fi
fi
set daphile_next_entry=
set timeout=6
'''
assert header.count('set default=9\nset fallback=6\n')==1
new=header.split('set default=9\n',1)[0]+one_shot+'\n'+'\n\n'.join(new_entries)+'\n'
os.umask(0o077)
(private/'grub.before-default').write_bytes(cfg.read_bytes())
(private/'grubenv.before-default').write_bytes(env.read_bytes())
proposal=private/'grub.default-candidate'
proposal.write_text(new)
run('grub-script-check',str(proposal))
run('mount','-o','remount,rw','/boot')
try:
    tmp=cfg.with_name('.grub.t620-stock-default')
    with tmp.open('x') as f:
        f.write(new); f.flush(); os.fchmod(f.fileno(),0o644); os.fsync(f.fileno())
    os.replace(tmp,cfg)
    run('grub-editenv',str(env),'set','saved_entry=0','next_entry=')
    assert cfg.read_text()==new
    os.sync()
finally:
    run('mount','-o','remount,ro','/boot')
(private/'armed').unlink()
trial=Path('/etc/local.d/01_t620_stock_trial.start')
if trial.is_file(): trial.unlink()
print('Default stock and one RC6 recovery menu; six-second timeout; no reboot requested')
