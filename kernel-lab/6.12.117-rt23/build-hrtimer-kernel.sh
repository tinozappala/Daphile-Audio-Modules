# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
root=/opt/daphile-kernel
src=$root/sources/linux-6.12.117
obj=$root/build/daphile-hrtimer-lab
log=$root/logs/full-hrtimer-build.log
status=$root/logs/full-hrtimer-build.status
cd "$src"
test "$(cat "$obj/include/config/kernel.release")" = '6.12.117-rt23-daphile-v6-hrtimer-lab'
test "$(sha256sum sound/drivers/aloop.c | cut -d' ' -f1)" = '2e8be92ff2ec330df6abdcd65c4ad62c10acbf99a1241a6b9f429fe951b2ffc8'
if test -e "$status"; then cat "$status"; exit 1; fi
printf 'RUNNING %s\n' "$(date -Is)" > "$status"
trap 'rc=$?; if test "$rc" -ne 0; then printf "FAILED rc=%s %s\n" "$rc" "$(date -Is)" > "$status"; fi' EXIT
printf 'BUILD_STARTED %s\n' "$(date -Is)"
make O="$obj" CC=gcc-12 -j4 bzImage modules > "$log" 2>&1
test -s "$obj/arch/x86/boot/bzImage"; test -s "$obj/Module.symvers"
mkdir -p "$root/artifacts/6.12.117-rt23-daphile-v6-hrtimer-lab"
out=$root/artifacts/6.12.117-rt23-daphile-v6-hrtimer-lab
cp "$obj/arch/x86/boot/bzImage" "$out/"; cp "$obj/System.map" "$obj/Module.symvers" "$obj/.config" "$out/"
make O="$obj" CC=gcc-12 INSTALL_MOD_PATH="$out/staged-root" modules_install >> "$log" 2>&1
(cd "$out"; sha256sum bzImage System.map Module.symvers .config > SHA256SUMS)
printf 'COMPILED %s\n' "$(date -Is)" > "$status"
cat "$status"
