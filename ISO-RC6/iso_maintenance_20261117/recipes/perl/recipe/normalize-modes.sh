#!/bin/bash
set -euo pipefail
root=${1:?staging root}
test -f "$root/usr/lib64/libperl.so.5.40.2"
find "$root" -type d -exec chmod 0755 {} +
find "$root" -type f -exec chmod 0644 {} +
chmod 0755 "$root/usr/bin/perl" "$root/usr/bin/perl5.40.5"
find "$root/usr/lib64/perl5" -type f -name '*.so' -exec chmod 0555 {} +
find "$root/recipe" -type f -name '*.sh' -exec chmod 0755 {} +
