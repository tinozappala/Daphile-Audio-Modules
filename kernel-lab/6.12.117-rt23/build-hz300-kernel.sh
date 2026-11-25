# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
src=$r/sources/linux-6.12.117
obj=$r/build/daphile-hrtimer-hz300
release=6.12.117-rt23-daphile-v6-hrtimer-hz300-lab
out=$r/artifacts/$release
status=$r/logs/hz300-build.status
log=$r/logs/hz300-build.log
cd "$src"
test "$(cat "$obj/include/config/kernel.release")" = "$release"
test "$(sha256sum sound/drivers/aloop.c | cut -d' ' -f1)" = 2e8be92ff2ec330df6abdcd65c4ad62c10acbf99a1241a6b9f429fe951b2ffc8
test ! -e "$status"
printf 'RUNNING %s\n' "$(date -Is)" > "$status"
trap 'rc=$?; if test "$rc" -ne 0; then printf "FAILED rc=%s %s\n" "$rc" "$(date -Is)" > "$status"; fi' EXIT
echo "HZ300_BUILD_STARTED $(date -Is)"
make O="$obj" CC=gcc-12 -j4 bzImage modules > "$log" 2>&1
test -s "$obj/arch/x86/boot/bzImage"
test -s "$obj/Module.symvers"
mkdir -p "$out"
cp "$obj/arch/x86/boot/bzImage" "$out/"
cp "$obj/System.map" "$obj/Module.symvers" "$obj/.config" "$out/"
make O="$obj" CC=gcc-12 INSTALL_MOD_PATH="$out/staged-root" modules_install >> "$log" 2>&1
(cd "$out"; sha256sum bzImage System.map Module.symvers .config > SHA256SUMS; sha256sum -c SHA256SUMS)
printf 'COMPILED %s\n' "$(date -Is)" > "$status"
cat "$status"
