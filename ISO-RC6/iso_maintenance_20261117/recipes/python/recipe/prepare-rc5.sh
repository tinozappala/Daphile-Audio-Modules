#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
test "$#" -ge 1
archive=$(realpath "$1")
cd "$base"
python3 rc5-full-sdk.py "$archive" rc5-full
mkdir -p expat-input
cd expat-input
release=https://github.com/libexpat/libexpat/releases/download/R_2_7_1
for file in expat-2.7.1.tar.xz expat-2.7.1.tar.xz.asc; do
    curl --proto '=https' --proto-redir '=https' -fLsS --max-time 25 "$release/$file" -o "$file"
done
printf '%s  %s\n' 354552544b8f99012e5062f7d570ec77f14b412a3ff5c7d8d0dae62c0d217c30 expat-2.7.1.tar.xz | sha256sum -c
curl --proto '=https' --proto-redir '=https' -fLsS --max-time 15 https://github.com/hartwork.gpg -o hartwork.gpg
keyring=$(mktemp -d)
trap 'gpgconf --homedir "$keyring" --kill gpg-agent || :; rm -rf -- "$keyring"' EXIT
gpg --homedir "$keyring" --batch --import hartwork.gpg
gpg --homedir "$keyring" --batch --status-fd 1 --verify expat-2.7.1.tar.xz.asc expat-2.7.1.tar.xz > signature.log
awk '$2=="VALIDSIG" && $3=="CB8DE70A90CFBF6C3BF5CC5696262ACFFBD3AEC6" && $NF=="3176EF7DB2367F1FCA4F306B1F9B0E909AF37285"{ok=1} END{exit !ok}' signature.log
tar -xf expat-2.7.1.tar.xz expat-2.7.1/lib/expat.h expat-2.7.1/lib/expat_external.h
