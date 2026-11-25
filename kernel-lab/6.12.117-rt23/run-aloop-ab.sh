#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
lab=$r/vm-lab
release=6.12.117-rt23-daphile-v6-hrtimer-lab
kernel=$r/artifacts/$release/bzImage
initrd=$lab/aloop-lab-initramfs.cpio.gz
mode=$1
if [[ "$mode" = preflight ]]; then
    bash -n "$0"
    gzip -t "$initrd"
    chroot "$lab/initramfs" /bin/busybox --list | grep -xE 'dd|timeout|poweroff|mount'
    set +e
    timeout 3 qemu-system-x86_64 -machine accel=kvm -cpu host -S -display none -nodefaults -m 128 -nic none >"$lab/kvm-preflight.log" 2>&1
    rc=$?
    set -e
    if [[ "$rc" = 124 ]]; then echo KVM_PREFLIGHT_PASS; else cat "$lab/kvm-preflight.log"; echo KVM_PREFLIGHT_FAILED; exit 1; fi
    echo VM_RUNNER_PREFLIGHT_PASS
    exit 0
fi
[[ "$mode" = run ]] || { echo 'usage: run-aloop-ab.sh preflight|run'; exit 2; }
grep -q '^COMPILED ' "$r/logs/full-hrtimer-build.status"
test -s "$kernel"
(cd "$r/artifacts/$release"; sha256sum -c SHA256SUMS)
test -s "$initrd"
dest=$(mktemp -d "$lab/ab-$(date +%Y%m%d-%H%M%S)-XXXXXX")
echo "RESULT_DIR=$dest"
for variant in A-jiffies B-hrtimer A2-jiffies; do
    img=$dest/$variant.raw
    truncate -s 4M "$img"
    arg=''
    if [[ "$variant" = B-hrtimer ]]; then arg='snd_aloop.timer_source=hrtimer'; fi
    echo "BOOT $variant"
    set +e
    timeout 180 qemu-system-x86_64 -machine accel=kvm -cpu host -smp 2 -m 1024 -display none -serial none -monitor none -nic none -no-reboot -kernel "$kernel" -initrd "$initrd" -append "rdinit=/init panic=10 $arg" -drive "file=$img,if=virtio,format=raw,cache=none" >"$dest/$variant.qemu.log" 2>&1
    rc=$?
    set -e
    echo "$rc" > "$dest/$variant.qemu.rc"
    python3 - "$img" "$dest/$variant.txt" <<'PY'
from pathlib import Path
import sys
b=Path(sys.argv[1]).read_bytes().split(b'\0',1)[0]
Path(sys.argv[2]).write_bytes(b)
PY
    if ! grep -q '^DAPHILE_ALOOP_VM_END ' "$dest/$variant.txt"; then echo "INCOMPLETE_GUEST $variant"; break; fi
    grep -E 'PCM_RESULT|DAPHILE_ALOOP_VM_END|START_STOP_ROUND' "$dest/$variant.txt"
done
python3 - "$dest" <<'PY'
from pathlib import Path
import sys,json,re
p=Path(sys.argv[1]);out={}
for v in ('A-jiffies','B-hrtimer','A2-jiffies'):
 f=p/(v+'.txt')
 if not f.exists():out[v]={'status':'NOT_RUN'};continue
 s=f.read_text(errors='replace')
 rows=[dict(re.findall(r'(\w+)=([^\s]+)',l)) for l in s.splitlines() if l.startswith('PCM_RESULT ')]
 out[v]={'complete':bool(re.search(r'^DAPHILE_ALOOP_VM_END failed=[01]$',s,re.M)),'guest_pass':bool(re.search(r'^DAPHILE_ALOOP_VM_END failed=0$',s,re.M)),'qemu_rc':(p/(v+'.qemu.rc')).read_text().strip(),'rows':rows,'expected_rows':19,'stop_rounds':len(re.findall(r'^START_STOP_ROUND=',s,re.M)),'actual_timer_readback':s.splitlines()[3] if len(s.splitlines())>3 else None}
(p/'summary.json').write_text(json.dumps(out,indent=2))
print(json.dumps({v:{k:w for k,w in x.items() if k!='rows'} for v,x in out.items()},indent=2))
PY
echo "EVIDENCE_DIR=$dest"
