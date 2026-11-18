#!/bin/bash
set -euo pipefail
if [ "$#" -ne 3 ]; then
    echo "Usage: $0 SOURCE.tar.xz SOURCE.tar.xz.asc RELEASE-KEY.asc" >&2
    exit 2
fi
src=$(realpath "$1")
signature=$(realpath "$2")
key=$(realpath "$3")
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
test "$(wc -c < "$src")" = 897120
printf '18de51b393bac4a6827879e1a7ff377c169e414bae92cd245091d80fc2601d13  %s\n' "$src" | sha256sum -c
export GNUPGHOME
GNUPGHOME=$(mktemp -d)
trap 'gpgconf --homedir "$GNUPGHOME" --kill gpg-agent || :; rm -rf -- "$GNUPGHOME"' EXIT
gpg --batch --import "$key"
gpg --batch --status-fd 1 --verify "$signature" "$src" > signature.log
awk '$2=="VALIDSIG" && $3=="ACAA8EC8C9703EAEB349258E86EFB5FBAF16D0F0" && $NF=="649D0DD767FF206202A76C5158F14A786FE198C8"{ok=1} END{exit !ok}' signature.log
mkdir -p apt/lists/partial apt/cache/archives/partial packages sdk
tar -xf "$src"
printf '%s  %s\n' '0e874a0500c3eded4a504c10539675f7715f22043cf8345e3e523552b09bddec' 'lighttpd-1.4.85-openssl35-eol.patch' | sha256sum -c
(cd lighttpd-1.4.85 && patch --fuzz=0 -p1 < "../lighttpd-1.4.85-openssl35-eol.patch")
printf 'deb [arch=amd64 signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main\n' > apt/sources.list
{ time ./apt-local update; } > apt.log 2>&1
(cd packages
 { time ../apt-local download autoconf=2.72-3.1 automake=1:1.17-4 libtool=2.5.4-4 m4=1.4.19-8 \
     autotools-dev=20240727.1 liblua5.1-0=5.1.5-11 liblua5.1-0-dev=5.1.5-11 nettle-dev=3.10.1-1; } > ../packages.log 2>&1
 for deb in *.deb; do dpkg-deb -x "$deb" ../sdk; done)
