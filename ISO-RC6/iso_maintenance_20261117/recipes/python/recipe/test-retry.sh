#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base/build"
export LD_LIBRARY_PATH="$PWD:$base/sdk/usr/lib/x86_64-linux-gnu"
{ time timeout -k 6 100 ./python -I -B -m test -j2 --timeout 85 \
 -i test.test_subprocess.POSIXProcessTestCase.test_group \
 -i test.test_subprocess.POSIXProcessTestCase.test_user \
 -i test_boundaries --junit-xml ../upstream-retry.xml \
 test_subprocess test_multiprocessing_spawn.test_processes; } > ../upstream-retry.log 2>&1
