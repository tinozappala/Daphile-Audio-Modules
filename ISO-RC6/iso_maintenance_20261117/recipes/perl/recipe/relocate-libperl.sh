#!/bin/bash
set -euo pipefail
lib=${1:?staging root}/usr/lib64
core=$lib/perl5/5.40/x86_64-linux-thread-multi/CORE
test -f "$core/libperl.so.5.40.2" && test ! -L "$core/libperl.so.5.40.2"
test ! -e "$lib/libperl.so.5.40.2"
mv "$core/libperl.so.5.40.2" "$lib/libperl.so.5.40.2"
for name in libperl.so libperl.so.5.40 libperl.so.5.40.2; do
    ln -sfn ../../../../libperl.so.5.40.2 "$core/$name"
done
for name in libperl.so libperl.so.5.40; do
    ln -sfn libperl.so.5.40.2 "$lib/$name"
done
