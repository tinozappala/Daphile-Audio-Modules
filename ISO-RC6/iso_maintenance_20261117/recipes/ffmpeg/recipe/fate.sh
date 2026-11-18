#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
base=$PWD
rc=$(realpath ../../python313/rc5-full/lib)
export LD_LIBRARY_PATH="$base/stage/usr/lib64:$base/fixture:$base/sdk/lib:$rc"
export LD_BIND_NOW=1
cd ffmpeg-6.1.6
{ time timeout -k 5 70 make -j4 fate-acodec-pcm-s16le fate-acodec-pcm-s24le fate-acodec-adpcm-ima_wav; } > ../fate.log 2>&1
