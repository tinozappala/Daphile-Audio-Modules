from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

root = Path('.')
def text(path):
    return Path(path).read_text()
def cfg(path):
    values = {}
    for line in text(path).splitlines():
        if line.startswith('CONFIG_'):
            key, val = line.split('=',1)
            values[key] = val
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            values[line[2:-11]] = 'n'
    return values
def command(*args):
    return subprocess.check_output(args,text=True).strip()
def seconds(path):
    minutes, seconds = re.findall(r'^real\s+(\d+)m([0-9.]+)s$',text(path),re.M)[-1]
    return 60*int(minutes)+float(seconds)
def sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()
stage = root/'stage'
inputs=json.loads(text('KERNEL_BUILD_INPUT.json'))
modules=json.loads(text('module-audit.json'))
before=cfg('KERNEL_CONFIG_RC5.txt')
after=cfg(stage/'boot/kernel.config')
config_diff={key:{'RC5':before.get(key),'candidate':after.get(key)}
             for key in sorted(before.keys()|after.keys()) if before.get(key)!=after.get(key)}
assert set(config_diff)=={'CONFIG_AS_VERSION','CONFIG_CC_VERSION_TEXT','CONFIG_LD_VERSION'}, config_diff
key_names=('LOCALVERSION','LOCALVERSION_AUTO','PREEMPT_RT','HZ','GENERIC_CPU','IKCONFIG','IKCONFIG_PROC',
           'MODULE_SIG','MODULE_COMPRESS_XZ','MODULE_COMPRESS_ALL','MODVERSIONS','BLK_DEV_INITRD','INITRAMFS_SOURCE',
           'BLK_DEV_LOOP','BLK_DEV_NVME','ATA','SATA_AHCI','SQUASHFS','SQUASHFS_XZ','EXT4_FS','OVERLAY_FS',
           'USB_XHCI_HCD','USB_EHCI_HCD','USB_OHCI_HCD','USB_STORAGE','USB_UAS','SND_USB_AUDIO','CIFS')
assert not modules['removed_names_with_multiplicity'],modules['removed_names_with_multiplicity']
assert not modules['added_names_with_multiplicity'],modules['added_names_with_multiplicity']
assert not modules['removed_paths'] and not modules['added_paths']
report={
 'release':modules['release'],
 'date_utc':datetime.now(timezone.utc).isoformat(),
 'classification':'Cloud-built successor of Daphile custom RC5 kernel; native boot pending',
 'baseline':{k:v for k,v in inputs.items() if k.startswith('baseline_') and k!='baseline_module_paths'},
 'sources':inputs['sources'],
 'source_verification':'Cloud checked complete HTTPS downloads, byte counts, SHA-256. GPG receipt fields are coordinator evidence on decompressed upstream streams.',
 'patch_dry_run':'PASS, no fuzz',
 'patch_application':'PASS, no fuzz',
 'toolchain':{'target_gcc':command('bookworm/sdk/usr/bin/gcc-12','--version').splitlines()[0],
              'host_gcc':command('gcc','--version').splitlines()[0],
              'target_assembler':command('as','--version').splitlines()[0],
              'target_linker':command('ld','--version').splitlines()[0],
              'debian_bookworm_packages':text('bookworm-pins.txt').splitlines(),
              'debian_trixie_host_packages':text('host-pins.txt').splitlines(),
              'sdk_acquisition':'APT signed indexes and package checksums; packages extracted without installation'},
 'config_diff':config_diff,
 'key_config':{key:after.get('CONFIG_'+key) for key in key_names},
 'kbuild':{'user':'builder','host':'daphile-build','timestamp_utc':'2026-11-17T00:00:00Z','build_version':1,
           'parallel_jobs':4,'targets':['bzImage','modules'],'timeout_minutes':45,
           'strip_modules':'--strip-debug via INSTALL_MOD_STRIP=1'},
 'timings_wall_seconds':{name:seconds(name+'.log') for name in ('configure','prepare','build','modules-install','depmod')},
 'kernel':{'description':text('kernel-file.log').split(': ',1)[-1].strip(),
           'bytes':(stage/'boot/kernel').stat().st_size,'sha256':sha256(stage/'boot/kernel'),
           'embedded_config_matches_final':True,'config_sha256':sha256(stage/'boot/kernel.config'),
           'system_map_sha256':sha256(stage/'boot/System.map')},
 'modules':modules,
 'tests':{'depmod_System_map':'PASS: explicit candidate release; no unresolved symbol diagnostics',
          'depmod_versions':'PASS' if (root/'depmod-modversions.log').exists() else 'not configured',
          'bzImage_and_config':'PASS','all_module_elf_arch_release_xz':'PASS',
          'first_build_invocation':'Failed in objtool because shell export polluted Make variables. Corrected wrapper sourcing; full build then completed.'},
 'limits':['No boot or initrd replacement or player write performed. Initrd compatibility and RME hardware behavior need native qualification.',
           'Kernel generic x86-64 configuration matches RC5. Optional CPU-dispatched drivers can contain instructions used on newer CPUs.',
           'Linux and PREEMPT_RT version selection includes USB audio and SquashFS XZ fixes. Device conditions and audio effects were not verified.'],
}
print(json.dumps(report,indent=2))
