#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
{ time timeout -k 20s 45m bash -c 'source "$1"; kernel_make -j4 bzImage modules' bash "$base/env.sh"; } > "$base/build.log" 2>&1
cmp "$base/pre-build.config" "$obj/.config"
