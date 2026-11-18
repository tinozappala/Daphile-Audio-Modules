#!/bin/bash
set -euo pipefail
: "${PATCHELF:?verified Debian patchelf executable}"
libdir=usr/lib64/perl5/5.40
install -d bundle/usr/bin bundle/usr/lib64/perl5
cp -a stage/usr/bin/{perl,perl5.40.5} bundle/usr/bin/
cp -a "stage/$libdir" bundle/usr/lib64/perl5/
core="bundle/$libdir/x86_64-linux-thread-multi/CORE/libperl.so.5.40.2"
chmod u+w "$core"
"$PATCHELF" --set-soname libperl.so.5.40.2 "$core"
chmod 444 "$core"
find "bundle/$libdir" -type f \( -name '*.h' -o -name '.packlist' -o -name '*.a' \) -delete
bash "$(dirname "$0")/relocate-libperl.sh" bundle
mkdir -p bundle/recipe
for recipe_file in audit.py build.sh check-core-nonroot.sh fixture.pl input.json manifest.py normalize-modes.sh package.sh patchelf-SHA256SUMS relocate-libperl.sh source.py stage-release.sh test.sh; do
 install -m 0644 "$(dirname "$0")/$recipe_file" "bundle/recipe/$recipe_file"
done
bash "$(dirname "$0")/normalize-modes.sh" bundle
