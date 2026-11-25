# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
src=$r/sources/linux-6.12.117
base=$r/artifacts/6.12.117-rt23-daphile-v6-hrtimer-lab
obj=$r/build/daphile-hrtimer-hz300
grep -q '^COMPILED ' "$r/logs/full-hrtimer-build.status"
(cd "$base"; sha256sum -c SHA256SUMS)
test "$(sha256sum "$src/sound/drivers/aloop.c" | cut -d' ' -f1)" = 2e8be92ff2ec330df6abdcd65c4ad62c10acbf99a1241a6b9f429fe951b2ffc8
test ! -e "$obj" || { echo 'HZ300 directory exists; inspect before changing'; exit 1; }
mkdir -p "$obj"
cp "$base/.config" "$obj/.config"
cd "$src"
scripts/config --file "$obj/.config" -d HZ_1000 -d HZ_250 -d HZ_100 -e HZ_300 --set-str LOCALVERSION '-daphile-v6-hrtimer-hz300-lab'
make O="$obj" CC=gcc-12 olddefconfig >"$r/logs/hz300-config.log" 2>&1
python3 - "$base/.config" "$obj/.config" "$r/logs/hz300-config-diff.json" <<'PY'
import sys,re,json
from pathlib import Path
def vals(p):
 v={}
 for l in Path(p).read_text().splitlines():
  m=re.fullmatch(r'CONFIG_(\w+)=(.*)',l)
  if m:v[m[1]]=m[2]
  m=re.fullmatch(r'# CONFIG_(\w+) is not set',l)
  if m:v[m[1]]='n'
 return v
a,b=map(vals,sys.argv[1:3]);d=[{'symbol':k,'hz1000':a.get(k),'hz300':b.get(k)} for k in sorted(set(a)|set(b)) if a.get(k)!=b.get(k)]
Path(sys.argv[3]).write_text(json.dumps(d,indent=2))
print(json.dumps(d,indent=2))
for k,v in {'HZ':'300','HZ_300':'y','PREEMPT_RT':'y','SND_ALOOP':'y','HIGH_RES_TIMERS':'y','SND_HRTIMER':'y'}.items():assert b.get(k)==v,(k,b.get(k))
print('HZ300_CONFIG_CHECK_PASS')
PY
make O="$obj" CC=gcc-12 -s prepare >>"$r/logs/hz300-config.log" 2>&1
cat "$obj/include/config/kernel.release"
cp "$obj/.config" "$r/configs/rc6-hrtimer-hz300-lab.config"
df -h "$r"
free -h
