#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
mkdir -p "$obj"
(cd "$src"; patch --batch --forward --dry-run --fuzz=0 -p1 < "$base/patch-6.12.117-rt23.patch"; patch --batch --forward --fuzz=0 -p1 < "$base/patch-6.12.117-rt23.patch") > "$base/patch.log" 2>&1
cp "$base/KERNEL_CONFIG_RC5.txt" "$obj/.config"
{ time kernel_make olddefconfig; } > "$base/configure.log" 2>&1
python3 "$src/scripts/diffconfig" "$base/KERNEL_CONFIG_RC5.txt" "$obj/.config" > "$base/config.diff"
kernel_make -s kernelrelease > "$base/kernelrelease"
test "$(cat "$base/kernelrelease")" = 6.12.117-rt23-daphile-v6
for expected in CONFIG_PREEMPT_RT=y CONFIG_HZ=1000 CONFIG_GENERIC_CPU=y CONFIG_MODULE_COMPRESS_XZ=y CONFIG_MODULE_COMPRESS_ALL=y CONFIG_IKCONFIG=y 'CONFIG_LOCALVERSION="-daphile-v6"'; do
    grep -Fxq "$expected" "$obj/.config"
done
{ time kernel_make -j4 prepare scripts; } > "$base/prepare.log" 2>&1
cp "$obj/.config" "$base/pre-build.config"
bash "$base/compile.sh"
