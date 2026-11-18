#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$base"
python3 report.py > stage/report.json
install -m 644 README.md stage/
install -d -m 755 stage/recipe
for script in setup.sh build.sh compile.sh stage.sh assemble.sh; do install -m 755 "$script" stage/recipe/; done
for input in apt-local.template env.sh audit-modules.py manifest.py report.py bookworm-pins.txt host-pins.txt bookworm-SHA256SUMS host-SHA256SUMS KERNEL_BUILD_INPUT.json; do
    install -m 644 "$input" stage/recipe/
done
python3 manifest.py stage
out=${1:-$base/kernel-6.12.117-rt23-daphile-v6-cloud.tar.gz}
tar --sort=name --mtime='2026-11-17 00:00:00 UTC' --owner=0 --group=0 --numeric-owner -cf - -C stage . | gzip -n -6 > "$out"
chmod 644 "$out"
scratch=$(mktemp -d)
trap 'rm -rf -- "$scratch"' EXIT
tar -xzf "$out" -C "$scratch"
(cd "$scratch"; sha256sum -c SHA256SUMS >/dev/null)
sha256sum "$out"
wc -c "$out"
