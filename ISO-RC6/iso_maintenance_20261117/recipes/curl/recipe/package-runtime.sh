#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
: "${PATCHELF:?select patchelf from the verified Debian package}"
mkdir -p bundle/usr/{bin,lib64}
cp -a stage/usr/bin/{curl,curl-config} bundle/usr/bin/
cp -a stage/usr/lib64/libcurl.so* bundle/usr/lib64/
strip --strip-debug bundle/usr/bin/curl bundle/usr/lib64/libcurl.so.4.*
"$PATCHELF" --remove-rpath bundle/usr/bin/curl
python3 - <<'PY'
from pathlib import Path
import re
p=Path('bundle/usr/bin/curl-config')
s=p.read_text()
s=re.sub(r'(?m)^(\s*echo "/usr/lib64/libcurl\.a)[^"\n]*"', r'\1"', s)
s=re.sub(r" 'LDFLAGS=[^']*'", " 'LDFLAGS=-Wl,--exclude-libs,libngtcp2.a:libngtcp2_crypto_ossl.a'", s)
assert '/workspace/' not in s
p.write_text(s)
PY
