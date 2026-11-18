#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
release=$(realpath "${1:-bundle}")
arch=x86_64-linux-thread-multi
lib=$release/usr/lib64/perl5/5.40
rc=$(realpath "$base/../../python313/rc5-full/lib")
crypt=$(realpath "$base/../openssh/sdk/lib")
export LD_LIBRARY_PATH="${LIBPERL_SEARCH_DIR:-$lib/$arch/CORE}:$crypt:$rc"
export LD_BIND_NOW=1
export PERL5LIB="$lib/$arch:$lib"
data=$(mktemp -d)
trap 'rm -rf -- "$data"' EXIT
python3 - "$data" <<'PY'
import json, pathlib, struct, sys, wave, zlib
root=pathlib.Path(sys.argv[1])
with wave.open(str(root/'test.wav'),'wb') as w:
 w.setparams((2,2,44100,0,'NONE',''))
 w.writeframes(struct.pack('<hh',123,-456)*4410)
def chunk(kind,data):
 return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\x00\x00'*2)*2))+chunk(b'IEND',b'')
(root/'test.png').write_bytes(png)
(root/'modules.txt').write_text('\n'.join(json.load(open('input.json'))['required_import_modules'])+'\n')
PY
f=$base/fixture/lms-CPAN
export EXPECTED_LIBPERL="$release/usr/lib64/libperl.so.5.40.2"
timeout 40 "$release/usr/bin/perl" -I"$f/arch/5.40/$arch" -I"$f/arch/5.40" -I"$f" -I"$base/template-support/Template-Toolkit-2.21/lib" \
 fixture.pl "$data/test.wav" "$data/test.png" "$data/modules.txt" > fixture-r2.log 2>&1
