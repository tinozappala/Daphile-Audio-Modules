#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
rc=$(realpath ../../python313/rc5-full/lib)
stage="$PWD/stage/usr/lib64"
export LD_LIBRARY_PATH="$stage:$PWD/fixture:$PWD/sdk/lib:$rc"
export LD_BIND_NOW=1
gcc -march=x86-64 -mtune=generic -Wall -Wextra -Werror -Iffmpeg-6.1.6 client.c \
 -L"$stage" "-Wl,-rpath-link,$stage" "-Wl,-rpath-link,$PWD/fixture" "-Wl,-rpath-link,$PWD/sdk/lib" "-Wl,-rpath-link,$rc" \
 -lavformat -lavcodec -lavutil -o api-client
{ time timeout 100 python3 smoke.py; } > smoke.json 2> smoke.log
for x in encoders decoders protocols formats buildconf; do stage/usr/bin/ffmpeg -hide_banner -"$x" > "$x.txt" 2>&1; done
