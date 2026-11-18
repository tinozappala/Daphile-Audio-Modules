#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
runtime=$base/sdk/usr/lib/x86_64-linux-gnu
cd "$base/build"
export LD_LIBRARY_PATH="$PWD:$runtime"
./python -I -B - <<'PY'
import sys, ssl, hashlib, sqlite3, ctypes, multiprocessing, subprocess, fcntl, readline
import bz2, lzma, zlib, _decimal, pyexpat, dbm.gnu, _uuid, curses
assert sys.version_info[:3] == (3, 13, 18)
print(sys.version, ssl.OPENSSL_VERSION, sqlite3.sqlite_version)
PY
export TERM=xterm
{ time timeout -k 8 180 ./python -I -B -m test -u curses -j3 --timeout 55 --junit-xml ../upstream.xml \
 test_ssl test_hashlib test_tarfile test_http_cookies test_tempfile test_subprocess \
 test_multiprocessing_spawn test_ctypes test_sqlite3 test_import test_importlib test_json \
 test_pyexpat test_decimal test_dbm_gnu test_bz2 test_lzma test_zlib test_readline test_curses; } > ../upstream.log 2>&1
