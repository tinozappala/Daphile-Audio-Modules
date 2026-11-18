#!/bin/bash
set -euo pipefail
: "${1:?bundle}" "${2:?tar output}"
python3 "$(dirname "$0")/manifest.py" "$1"
tar --sort=name --owner=0 --group=0 --numeric-owner -czf "$2" -C "$1" .
chmod 644 "$2"
v=$(mktemp -d)
trap 'rm -rf -- "$v"' EXIT
tar -xzf "$2" -C "$v"
(cd "$v"; sha256sum -c SHA256SUMS >/dev/null)
sha256sum "$2"
wc -c "$2"
