#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
: "${PYTHON_RC5_LIB:?set to verified RC5 Python SDK lib directory}"
cd "$base/curl-8.23.0"
export LD_LIBRARY_PATH="$base/fixture:$base/rc5/lib:$PYTHON_RC5_LIB"
{ time timeout -k 8 100 make -j4 -C tests test TFLAGS="1 2 31 63 97 305 310 313 1300 1301 1700 1702 1800 2102 2103 2104"; } > ../upstream-delivery.log 2>&1
