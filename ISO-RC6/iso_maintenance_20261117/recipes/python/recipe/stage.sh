#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
sdk=$base/sdk/usr/lib/x86_64-linux-gnu
cd "$base/build"
export LD_LIBRARY_PATH="$sdk:$PWD"
export PYTHONDONTWRITEBYTECODE=1
stage=$base/stage
{ time make DESTDIR="$stage" TEST_MODULES=no altbininstall libinstall sharedinstall; } > ../stage.log 2>&1
lib=$stage/usr/lib/python3.13
rm -rf -- "$lib"/{ensurepip,site-packages,test,tkinter}
find "$lib" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$lib/lib-dynload" -maxdepth 1 -type f \( -name '_test*.so' -o -name '_xx*.so' -o -name 'xxlimited*.so' \) -delete
find "$stage/usr" -type f \( -name "python3.13" -o -name "*.so" -o -name "*.so.1.0" \) -exec strip --strip-debug {} +
mkdir -p "$stage/licenses"
install -m 644 ../Python-3.13.18/LICENSE "$stage/licenses/Python-LICENSE"
