"""Offline checks for the candidate initrd's fail-closed branch selection."""
import gzip
from pathlib import Path
import subprocess
import unittest

import initrd_dualmode as candidate
from protect_live_initrd import records


def shell(script,cmdline=''):
    prefix="""cat() {
      if [ "$1" = /proc/cmdline ]; then printf '%s\\n' "$FAKE_CMDLINE"; else command cat "$@"; fi
    }
    """
    p=subprocess.run(['/usr/bin/busybox','ash','-c',prefix+candidate.NEW_BOOT.decode()+
                      '\n'+candidate.NEW_DATA.decode()+'\n'+script],
                     env={'FAKE_CMDLINE':cmdline,'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
    if p.returncode:raise AssertionError((p.returncode,p.stdout,p.stderr))
    return p.stdout


class DualMode(unittest.TestCase):
    def test_requires_exact_positive_marker_and_live_takes_precedence(self):
        cases=[('',False),('quiet',False),('live',False),('liveish',False),
               ('boot_uuid=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa data_uuid=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',False),
               ('daphile_installed=1x',False),('daphile_installed=0',False),
               ('daphile_installed=1',True),('daphile_installed=1 live',False)]
        for args,installed in cases:
            with self.subTest(args=args):
                out=shell('if is_installed_boot; then printf installed; else printf live; fi',args)
                self.assertEqual(out,'installed' if installed else 'live')

    def test_missing_or_invalid_uuid_never_reaches_mount_or_lookup(self):
        for boot,data in [('', ''),('bad','bad'),('a'*36,'a'*36),('z'*36,'a'*36)]:
            with self.subTest(boot=boot,data=data):
                script=("is_installed_boot() { return 0; }; "
                        "unique_uuid_partition() { printf UNSAFE_LOOKUP; return 1; }; "
                        "mount() { printf UNSAFE_MOUNT; return 1; }; "
                        f"boot_uuid='{boot}'; data_uuid='{data}'; "
                        "if mount_boot; then printf BAD_SUCCESS; else printf REJECTED; fi")
                self.assertEqual(shell(script),'REJECTED')

    def test_data_defaults_to_tmpfs_without_validated_device(self):
        stub="mount_dev() { printf '%s>%s' \"$1\" \"$2\"; }; rescue_shell() { printf ERROR; }; "
        self.assertEqual(shell(stub+"INSTALLED_DATA=''; mount_data /mnt/hdd"),'none>/mnt/hdd')
        self.assertEqual(shell(stub+"INSTALLED_DATA='/dev/sda2'; mount_data /mnt/hdd"),'/dev/sda2>/mnt/hdd')

    def test_boot_and_data_must_be_partitions_of_same_disk(self):
        script=("if same_parent_device '$BOOT' '$DATA'; then printf SAME; else printf REJECTED; fi")
        a='/sys/devices/pci/block/sda/sda1';b='/sys/devices/pci/block/sda/sda2'
        c='/sys/devices/pci/block/sdb/sdb1'
        for boot,data,expected in [(a,b,'SAME'),(a,c,'REJECTED'),('',b,'REJECTED')]:
            with self.subTest(boot=boot,data=data):
                self.assertEqual(shell(script.replace('$BOOT',boot).replace('$DATA',data)),expected)

    def test_cpio_rewrite_only_changes_init(self):
        sample=Path('/mnt/c/Build/Daphile-Kernel/iso_candidate_075_scripts/source-initrd')
        if not sample.is_file():self.skipTest('source initrd not yet extracted')
        old=sample.read_bytes();new=candidate.patch_initrd(old)
        def payloads(raw):
            offset=(list(records(raw))[-1][4]+511)&~511
            data=gzip.decompress(raw[offset:])
            return {name:data[pos:pos+size] for name,_,pos,size,_ in records(data)}
        a=payloads(old);b=payloads(new)
        self.assertEqual(a.keys(),b.keys())
        self.assertTrue(all(a[k]==b[k] for k in a if k!='init'))
        self.assertIn(b'daphile_installed=1',b['init'])


if __name__=='__main__':unittest.main(verbosity=2)
