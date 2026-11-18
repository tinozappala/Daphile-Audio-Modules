#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
release=$(cat "$base/kernelrelease")
: "${release:?missing kernelrelease}"
test "$release" = "$(kernel_make -s kernelrelease)"
raw=$base/module-install
stage=$base/stage
test ! -e "$raw/lib/modules/$release" && test ! -e "$stage/usr/lib/modules/$release"
export XZ_OPT=-T1
{ time kernel_make -j4 modules_install INSTALL_MOD_PATH="$raw" INSTALL_MOD_STRIP=1 DEPMOD="$host/sbin/depmod"; } > "$base/modules-install.log" 2>&1
modules=$raw/lib/modules/$release
for link in build source; do
   if [ -L "$modules/$link" ]; then rm -- "$modules/$link"; fi
done
{ time "$host/sbin/depmod" -a -e -F "$obj/System.map" -b "$raw" "$release"; } > "$base/depmod.log" 2>&1
if grep -Eiq 'unknown symbol|disagrees about version|WARNING:|ERROR:' "$base/depmod.log"; then
    cat "$base/depmod.log" >&2
    exit 1
fi
if grep -Fxq CONFIG_MODVERSIONS=y "$obj/.config"; then
    "$host/sbin/depmod" -a -e -E "$obj/Module.symvers" -b "$raw" "$release" > "$base/depmod-modversions.log" 2>&1
    test ! -s "$base/depmod-modversions.log"
fi
install -d -m 755 "$stage/boot" "$stage/usr/lib/modules" "$stage/config" "$stage/licenses"
mv "$modules" "$stage/usr/lib/modules/"
install -m 644 "$obj/arch/x86/boot/bzImage" "$stage/boot/kernel"
install -m 644 "$obj/.config" "$stage/boot/kernel.config"
install -m 644 "$obj/System.map" "$stage/boot/System.map"
install -m 644 "$base/kernelrelease" "$stage/kernelrelease"
install -m 644 "$base/KERNEL_CONFIG_RC5.txt" "$stage/config/RC5-original.config"
install -m 644 "$base/config.diff" "$stage/config/RC5-to-candidate.diff"
install -m 644 "$src/COPYING" "$stage/licenses/Linux-COPYING"
install -m 644 "$src/LICENSES/preferred/GPL-2.0" "$stage/licenses/GPL-2.0"
install -m 644 "$src/LICENSES/exceptions/Linux-syscall-note" "$stage/licenses/Linux-syscall-note"
"$src/scripts/extract-ikconfig" "$stage/boot/kernel" > "$base/extracted.config"
cmp "$stage/boot/kernel.config" "$base/extracted.config"
file "$stage/boot/kernel" > "$base/kernel-file.log"
find "$stage" -type d -exec chmod 755 {} +
find "$stage/usr/lib/modules" -type f -exec chmod 644 {} +
python3 "$base/audit-modules.py" "$base/KERNEL_BUILD_INPUT.json" "$stage" > "$base/module-audit.json"
