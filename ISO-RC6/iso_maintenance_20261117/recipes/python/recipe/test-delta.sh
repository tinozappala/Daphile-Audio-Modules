#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base/build"
export LD_LIBRARY_PATH="$PWD:${RC5_TEST_EXTRA_LIBDIR:+$RC5_TEST_EXTRA_LIBDIR:}$base/rc5-full/lib"
export LD_BIND_NOW=1 TERM=xterm
{ time timeout -k 6 70 ./python -I -B -m test -u curses -j3 --junit-xml ../final-delta.xml \
 test_bz2 test_dbm test_dbm_gnu test_dbm_ndbm test_pyexpat test_xml_etree test_curses \
 test_readline test_hashlib test_ssl test_sqlite3 test_ctypes test_decimal; } > ../final-delta.log 2>&1
