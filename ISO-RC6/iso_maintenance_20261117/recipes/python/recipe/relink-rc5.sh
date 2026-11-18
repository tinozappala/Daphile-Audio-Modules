#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base/build"
rc5=$base/rc5-full/lib
expat_headers=$base/expat-input/expat-2.7.1/lib
suffix=.cpython-313-x86_64-linux-gnu.so
# Absolute link-time inputs carry the RC5 SONAME, never their input paths.
make -j4 -W Modules/_cursesmodule.o -W Modules/_curses_panel.o -W Modules/readline.o \
 "MODULE__CURSES_LDFLAGS=$rc5/libncursesw.so.6 $rc5/libtinfow.so.6" \
 "MODULE__CURSES_PANEL_LDFLAGS=$rc5/libpanelw.so.6 $rc5/libncursesw.so.6 $rc5/libtinfow.so.6" \
 "MODULE_READLINE_LDFLAGS=$rc5/libreadline.so.8 $rc5/libtinfow.so.6" \
 "Modules/_curses$suffix" "Modules/_curses_panel$suffix" "Modules/readline$suffix"
make -j2 -W ../Python-3.13.18/Modules/pyexpat.c -W ../Python-3.13.18/Modules/_elementtree.c \
 "MODULE_PYEXPAT_CFLAGS=-I$expat_headers" "MODULE_PYEXPAT_LDFLAGS=$rc5/libexpat.so.1" \
 "MODULE__ELEMENTTREE_CFLAGS=-I$expat_headers" "Modules/pyexpat$suffix" "Modules/_elementtree$suffix"
for name in _curses _curses_panel readline pyexpat _elementtree; do
    file=$name$suffix
    install -m 755 "Modules/$file" "$base/stage/usr/lib/python3.13/lib-dynload/$file"
    strip --strip-debug "$base/stage/usr/lib/python3.13/lib-dynload/$file"
done
make -W Modules/_bz2module.o "MODULE__BZ2_LDFLAGS=$rc5/libbz2.so.1" "Modules/_bz2$suffix"
# CPython configures this target when the GDBM compatibility headers are present.
# The additional make fragment covers reuse of an initially configured build.
make -f Makefile -f "$base/dbm.mk" -W Modules/_dbmmodule.o \
 "RC5_DBM_LIB=$rc5/libgdbm_compat.so.4" "Modules/_dbm$suffix"
ln -sfn "../../Modules/_dbm$suffix" "$(cat pybuilddir.txt)/_dbm$suffix"
make -j3 -W ../Python-3.13.18/Modules/_blake2/blake2module.c \
 -W ../Python-3.13.18/Modules/_blake2/blake2b_impl.c \
 -W ../Python-3.13.18/Modules/_blake2/blake2s_impl.c \
 "MODULE__BLAKE2_CFLAGS=-DHAVE_LIBB2=1" "MODULE__BLAKE2_LDFLAGS=$rc5/libb2.so.1" "Modules/_blake2$suffix"
for name in _bz2 _dbm _blake2 _ctypes_test xxsubtype; do
    file=$name$suffix
    install -m 755 "Modules/$file" "$base/stage/usr/lib/python3.13/lib-dynload/$file"
    strip --strip-debug "$base/stage/usr/lib/python3.13/lib-dynload/$file"
done
