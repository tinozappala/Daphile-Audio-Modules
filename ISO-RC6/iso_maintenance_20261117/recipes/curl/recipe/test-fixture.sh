#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
: "${PYTHON_RC5_LIB:?set verified lib directory}"
release=${1:-stage}
export LD_LIBRARY_PATH="$base/$release/usr/lib64:$base/fixture:$base/rc5/lib:$PYTHON_RC5_LIB"
export LD_BIND_NOW=1
gcc -Os -march=x86-64 -mtune=generic -Wall -Wextra -Werror \
 -Icurl-8.23.0/include client.c -L"$release/usr/lib64" \
 "-Wl,-rpath-link,$base/fixture" "-Wl,-rpath-link,$base/rc5/lib" "-Wl,-rpath-link,$PYTHON_RC5_LIB" -lcurl -o "$base/client"
"$release/usr/bin/curl" -q --version > version.txt
{ time timeout -k 4 50 test-venv/bin/python fixture.py "$release/usr/bin/curl" ./client; } > fixture.json 2> fixture.log
