#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
test "$(wc -c < KERNEL_CONFIG_RC5.txt)" = 258796
echo '17bd74a9fc2fd7ac17d639c6ae44e89aabc39e8d1e67b301d77db46a433dc325  KERNEL_CONFIG_RC5.txt' | sha256sum -c
download() {
    local url=$1 file=$2 size=$3 digest=$4
    curl -fLSs --proto '=https' --proto-redir '=https' --max-time 150 -o "$file" "$url"
    test "$(wc -c < "$file")" = "$size"
    printf '%s  %s\n' "$digest" "$file" | sha256sum -c
}
download https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.117.tar.xz linux-6.12.117.tar.xz 148959092 74e99aa5e60e242e0a165fed8cb2ea0d054bf7a401f01861d403d75733456613
download https://cdn.kernel.org/pub/linux/kernel/projects/rt/6.12/older/patch-6.12.117-rt23.patch.xz patch-6.12.117-rt23.patch.xz 18180 b243f72ec92f7e7653f896738d11307190f3c1af12b1123e5b7138e1a748c31c
tar -xf linux-6.12.117.tar.xz
xz -dk patch-6.12.117-rt23.patch.xz
fetch_sdk() {
    local name=$1 release=$2
    mkdir -p "$name"/{apt/lists/partial,apt/cache/archives/partial,packages,sdk}
    install -m 755 apt-local.template "$name/apt-local"
    printf 'deb [arch=amd64 signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian %s main\n' "$release" > "$name/apt/sources.list"
    "$name/apt-local" update
    (cd "$name/packages"
     mapfile -t pins < "../../$name-pins.txt"
     ../apt-local download "${pins[@]}"
     sha256sum -c "../../$name-SHA256SUMS"
     for p in *.deb; do dpkg-deb -x "$p" ../sdk; done)
}
fetch_sdk bookworm bookworm
fetch_sdk host trixie
ln -sfn /usr/lib/x86_64-linux-gnu/libelf.so.1 host/sdk/usr/lib/x86_64-linux-gnu/libelf.so.1
