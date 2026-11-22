from pathlib import Path
import hashlib, json, os, re, shutil, stat, subprocess, tempfile

boot = Path('/boot')
data = Path('/mnt/hdd/system')
private = data/'stock-2505251550-private'
vendor = private/'vendor'
before = private/(private/'baseline').read_text().strip()
firmware = 'fw2505251550'
previous = 'fw261118b7rc6'
slot = boot/firmware
upper = data/('root-rw-'+firmware)
tree = upper/'upper'
os.umask(0o022)

def command(*args, **kwargs):
    return subprocess.run(args, check=True, timeout=kwargs.pop('timeout',20), **kwargs)

def digest(p, kind='sha256'):
    with p.open('rb') as stream:
        return hashlib.file_digest(stream,kind).hexdigest()

def copy(src,dest):
    assert src.is_file() and not src.is_symlink() and not os.path.lexists(dest)
    mode=stat.S_IMODE(src.stat().st_mode)
    dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('xb') as out, src.open('rb') as incoming:
        shutil.copyfileobj(incoming,out)
        os.fchmod(out.fileno(),mode)
        out.flush()
        os.fsync(out.fileno())
    assert digest(src)==digest(dest)

assert Path('/sys/class/dmi/id/product_name').read_text().strip()=='HP t620 Quad Core TC'
assert 'daphile='+previous in Path('/proc/cmdline').read_text().split()
assert digest(data/'conf/config.sys')==digest(before/'conf/config.sys')
assert not os.path.lexists(slot) and not os.path.lexists(upper)
assert (boot/previous/'ok').is_file() and (boot/previous/'rootfs').is_file()
assert (boot/'fw261116a6rc4'/'ok').is_file() and (boot/'fw261116a6rc4'/'rootfs').is_file()
for line in (vendor/'md5sum.txt').read_text().splitlines():
    expected,name=line.split(); assert name.startswith(firmware+'/')
    assert digest(vendor/Path(name).name,'md5')==expected

cfg=boot/'grub/grub.cfg'
old=cfg.read_text()
assert old==(before/'grub.cfg').read_text() and 'daphile='+firmware+' ' not in old
entries=re.findall(r'(?ms)^menuentry [^\n]+\n.*?^}\s*',old)
ref=[x for x in entries if 'daphile='+previous+' ' in x]
assert len(ref)==1
index=len(entries)
entry=ref[0].replace(previous,firmware).replace(' ${daphile_opts}','')
entry=re.sub(r"^menuentry '[^']+'", "menuentry 'Daphile 25.05-b251550 RT (originale)'",entry)
entry=re.sub(r'(?m)^(\s*savedefault\s+)9$',r'\g<1>'+str(index),entry)
anchor='    fi\n  fi\nfi\nset daphile_next_entry='
assert old.count(anchor)==1
branch='    elif [ "${daphile_next_entry}" = "'+str(index)+'" ]; then\n      set default='+str(index)+'\n      set boot_once=true\n'
new=old.replace(anchor,branch+anchor).rstrip()+'\n\n'+entry.strip()+'\n'
candidate=before/'grub.candidate'
candidate.write_text(new)
command('grub-script-check',str(candidate))

host=data/'daphile-audio-host'
service=Path('/etc/init.d/daphile-audio-host').read_text()
assert service.count('need daphile-modules; after mediaserver;')==1
with tempfile.TemporaryDirectory(prefix='vendor-mount-',dir=private) as m:
    command('mount','-t','squashfs','-o','loop,ro',str(vendor/'rootfs'),m)
    try:
        root=Path(m)
        assert (root/'version').read_text().strip()==firmware
        assert (root/'etc/runlevels/default/sshd').is_symlink()
        assert 'PermitRootLogin yes' in (root/'etc/ssh/sshd_config').read_text()
        command('chroot',m,'/usr/bin/python3','-B','-c','import os, signal, ssl, sqlite3, yaml; assert hasattr(os,"pidfd_open"); assert hasattr(os,"unshare"); assert hasattr(os,"setns"); assert hasattr(signal,"pidfd_send_signal")')
        upper.mkdir()
        (upper/'arch').write_text('x86_64\n')
        tree.mkdir()
        for key in ('ssh_host_ed25519_key','ssh_host_ed25519_key.pub'):
            copy(Path('/etc/ssh')/key,tree/'etc/ssh'/key)
        for name in ('machine-id','timezone','localtime'):
            src=Path('/etc')/name
            dst=tree/'etc'/name
            if src.is_symlink(): dst.symlink_to(src.readlink())
            elif src.is_file(): copy(src,dst)
        ssh_override=tree/'etc/ssh/sshd_config'
        ssh_override.write_text((root/'etc/ssh/sshd_config').read_text()+'\nHostKey /etc/ssh/ssh_host_ed25519_key\n')
        target=tree/'etc/init.d/daphile-audio-host'
        target.parent.mkdir()
        target.write_text(service.replace('need daphile-modules; after mediaserver;','need diskmount; after mediaserver;'))
        target.chmod(0o755)
        runlevel=tree/'etc/runlevels/default'
        runlevel.mkdir(parents=True)
        (runlevel/target.name).symlink_to('../../init.d/'+target.name)
        local=tree/'etc/local.d'
        local.mkdir()
        guard=local/'01_t620_stock_trial.start'
        guard.write_text('''#!/bin/sh
private=/mnt/hdd/system/stock-2505251550-private
[ -f "$private/armed" ] || exit 0
boot_id=$(cat /proc/sys/kernel/random/boot_id) || exit 0
(
    sleep 300
    if [ -f "$private/armed" ] && [ "$(cat "$private/ack" 2>/dev/null)" != "$boot_id" ]; then
        logger -t t620-stock-trial 'SSH trial was not acknowledged. Restarting into the previous default.'
        /usr/bin/openrc-shutdown -r now
    fi
) </dev/null >/dev/null 2>&1 &
exit 0
''')
        guard.chmod(0o755)
    finally:
        command('umount',m)

old_root=boot/'fw261116a6rc/rootfs'
stored=Path('/mnt/hdd/.daphile-fw-archive/fw261116a6rc/rootfs')
required=sum((vendor/name).stat().st_size for name in ('kernel','initrd','rootfs','menu.item','md5sum.txt'))+16*1048576
if shutil.disk_usage(boot).free<required:
    assert old_root.is_file() and not old_root.is_symlink()
    assert old_root.stat().st_size==393641984
    assert shutil.disk_usage(boot).free+old_root.stat().st_size>required
    if not stored.exists(): copy(old_root,stored)
    assert digest(old_root)==digest(stored)
else:
    old_root=None

command('mount','-o','remount,rw','/boot')
try:
    if old_root is not None:
        link=old_root.with_name('.rootfs.t620-stock-link')
        link.symlink_to(stored)
        os.replace(link,old_root)
        assert old_root.stat().st_size==393641984 and old_root.resolve()==stored
    assert shutil.disk_usage(boot).free>required
    slot.mkdir()
    for name in ('kernel','initrd','rootfs','menu.item','md5sum.txt'):
        copy(vendor/name,slot/name)
    command('md5sum','-c',str(slot/'md5sum.txt'),cwd=boot)
    (slot/'ok').touch(exist_ok=False)
    temporary=cfg.with_name('.grub.cfg.t620-stock')
    copy(candidate,temporary)
    os.replace(temporary,cfg)
    os.sync()
finally:
    command('mount','-o','remount,ro','/boot')

assert 'daphile='+previous in Path('/proc/cmdline').read_text().split()
record=dict(status='staged',stock=firmware,previous=previous,trial_entry=index,boot_free=shutil.disk_usage(boot).free,
            stock_rootfs=digest(slot/'rootfs'),nas_configuration_preserved=True)
(private/'stage.json').write_text(json.dumps(record)+'\n')
print(json.dumps(record))
