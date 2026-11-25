# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
src=$r/sources/linux-6.12.117-ns-lab
ref=$r/artifacts/6.12.117-rt23-daphile-v6-hrtimer-ns-hz300-lab
lab=$r/optimization-lab
mkdir -p "$lab"
(cd "$ref";sha256sum -c SHA256SUMS)
# Wait for the already running matrix, to avoid disturbing CPU results.
matrix=$r/vm-lab/hz-comparison/ns-matrix-20261125-213937/summary.json
echo WAIT_FOR_MATRIX
for i in $(seq 1 180);do test ! -s "$matrix" || break;sleep 5;done
test -s "$matrix"
cd "$src"
for kind in isolation diagnostics psi;do
 obj=$lab/$kind
 test ! -e "$obj"
 mkdir "$obj"
 cp "$ref/.config" "$obj/.config"
 scripts/config --file "$obj/.config" --set-str LOCALVERSION "-daphile-v6-ns-hz300-$kind-lab"
 case "$kind" in
 isolation) scripts/config --file "$obj/.config" -d NO_HZ_IDLE -e NO_HZ_FULL -e RCU_NOCB_CPU;;
 diagnostics) scripts/config --file "$obj/.config" -e LOCK_STAT -e PROVE_LOCKING -e SCHEDSTATS -e FTRACE;;
 psi) scripts/config --file "$obj/.config" -e PSI -e PSI_DEFAULT_DISABLED;;
 esac
 make O="$obj" CC=gcc-12 olddefconfig >"$obj/config.log" 2>&1
 python3 - "$ref/.config" "$obj/.config" "$obj/config-diff.json" "$kind" <<'PY'
import sys,json,re
from pathlib import Path
def vals(p):
 out={}
 for l in Path(p).read_text().splitlines():
  m=re.fullmatch(r'CONFIG_(\w+)=(.*)',l)
  if m:out[m[1]]=m[2]
  m=re.fullmatch(r'# CONFIG_(\w+) is not set',l)
  if m:out[m[1]]='n'
 return out
a,b=map(vals,sys.argv[1:3])
d=[{'symbol':k,'baseline':a.get(k),'candidate':b.get(k)} for k in sorted(a.keys()|b.keys()) if a.get(k)!=b.get(k)]
target={'isolation':{'NO_HZ_FULL':'y','RCU_NOCB_CPU':'y'},'diagnostics':{'LOCK_STAT':'y','PROVE_LOCKING':'y'},'psi':{'PSI':'y','PSI_DEFAULT_DISABLED':'y'}}[sys.argv[4]]
checks={k:b.get(k)==v for k,v in target.items()}
invariants={k:b.get(k)==a.get(k) for k in ['HZ','PREEMPT_RT','SND_ALOOP','HIGH_RES_TIMERS','SND_HRTIMER','CPU_MITIGATIONS']}
result={'variant':sys.argv[4],'differences':d,'requested_checks':checks,'invariants':invariants,'status':'CONFIG_PASS' if all(checks.values()) and all(invariants.values()) else 'REVIEW_REQUIRED'}
Path(sys.argv[3]).write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
PY
done
# Compiler target feasibility only; never use -march=native on Intel build host.
printf 'unsigned long f(unsigned long x){return x*17+3;}\n' > "$lab/mtune-probe.c"
gcc-12 -O2 -mno-sse -mno-mmx -mno-sse2 -mno-avx -mtune=bdver4 -Werror -c "$lab/mtune-probe.c" -o "$lab/mtune-probe.o"
objdump -d "$lab/mtune-probe.o"
echo AMD_MTUNE_COMPILER_PROBE_PASS
echo OPTIMIZATION_CONFIG_PREPARATION_COMPLETE
