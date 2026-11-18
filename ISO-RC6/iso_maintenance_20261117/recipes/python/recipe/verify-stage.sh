#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
stage=$base/stage
runtime=$base/rc5-full/lib
export LD_LIBRARY_PATH="$stage/usr/lib64:${RC5_TEST_EXTRA_LIBDIR:+$RC5_TEST_EXTRA_LIBDIR:}$runtime"
export LD_BIND_NOW=1
export PYTHONHOME="$stage/usr"
if [ "$#" != 1 ]; then echo 'Usage: verify-stage.sh PYYAML_RC5_PUBLIC_TEST_INPUT.zip' >&2; exit 2; fi
env -u PYTHONHOME python3 "$base/fixture.py" "$1" "$base/fixture"
"$stage/usr/bin/python3.13" -I -B "$base/smoke.py" "$stage" "$base/fixture" > "$base/smoke.json"
"$stage/usr/bin/python3.13" -s -B -c 'import sys; assert sys.version_info[:3] == (3,13,18) and sys.prefix == sys.argv[1]' "$stage/usr"
"$stage/usr/bin/python3.13" -I -B - "$stage/usr/lib/python3.13/lib-dynload" > "$base/import-all.log" <<'PY'
import importlib
from pathlib import Path
import sys
for file in sorted(Path(sys.argv[1]).glob('*.so')):
    name = file.name.split('.')[0]
    mod = importlib.import_module(name)
    assert Path(mod.__file__).resolve() == file.resolve()
    print(name)
PY
"$stage/usr/bin/python3.13" -I -B "$base/tls-test.py" > "$base/tls.json"
"$stage/usr/bin/python3.13" -I -B "$base/capabilities.py" > "$base/capabilities.json"
env -u PYTHONHOME python3 "$base/elf-audit.py" "$stage" > "$base/elf.json"
