#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
source_url=https://www.python.org/ftp/python/3.13.18/Python-3.13.18.tar.xz
source_file=Python-3.13.18.tar.xz
curl --proto '=https' --proto-redir '=https' -fLsS --max-time 30 -o "$source_file" "$source_url"
test "$(wc -c < "$source_file")" = 23243936
printf '%s  %s\n' abb0102e6d4ae027509bcb82327d9dd85515b44a3ba8ad538cfc5e9847029f68 "$source_file" | sha256sum -c
tar -xf "$source_file"
mkdir -p apt/lists/partial apt/cache/archives/partial sdk packages
printf '%s\n' 'deb [arch=amd64 signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main' > apt/sources.list
./apt-local update
(cd packages
 mapfile -t pins < ../sdk-pins.txt
 ../apt-local download "${pins[@]}"
 sha256sum -c ../sdk-SHA256SUMS
 for package in *.deb; do dpkg-deb -x "$package" ../sdk; done)
