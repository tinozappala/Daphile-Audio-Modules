#!/bin/bash
set -euo pipefail
mkdir -p openssl-headers
cd openssl-headers
origin=https://github.com/openssl/openssl/releases/download/openssl-3.5.9
curl --proto '=https' --proto-redir '=https' -fLSs --max-time 90 "$origin/openssl-3.5.9.tar.gz" -o openssl-3.5.9.tar.gz
test "$(wc -c < openssl-3.5.9.tar.gz)" = 53279637
echo '603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a  openssl-3.5.9.tar.gz' | sha256sum -c
tar xf openssl-3.5.9.tar.gz
cd openssl-3.5.9
perl Configure linux-x86_64 --prefix=/usr
targets=()
for template in include/openssl/*.h.in; do targets+=("${template%.in}"); done
make "${targets[@]}"
