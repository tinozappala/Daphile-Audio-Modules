# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
lab=$r/vm-lab
mkdir -p "$lab/hz-comparison"
dest=$lab/hz-comparison
cp -a "$lab/initramfs" "$dest/initramfs"
python3 - "$dest/initramfs/init" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]);s=p.read_text()
a='failed=0\n';assert s.count(a)==1
s=s.replace(a,'echo GUEST_COUNTERS_BEFORE\ncat /proc/stat\ncat /proc/interrupts\ncat /proc/softirqs\necho GUEST_COUNTERS_BEFORE_END\nfailed=0\n')
a='dmesg | tail -80\n';assert s.count(a)==1
s=s.replace(a,'echo GUEST_COUNTERS_AFTER\ncat /proc/stat\ncat /proc/interrupts\ncat /proc/softirqs\necho GUEST_COUNTERS_AFTER_END\n'+a)
p.write_text(s)
PY
chmod +x "$dest/initramfs/init"
(cd "$dest/initramfs";find . -print0 | cpio --null -o --format=newc 2>"$dest/cpio.log" | gzip -1 > "$dest/aloop-hz-comparison.cpio.gz")
gzip -t "$dest/aloop-hz-comparison.cpio.gz"
sha256sum "$dest/aloop-hz-comparison.cpio.gz"
echo HZ_COMPARISON_INITRAMFS_PASS
