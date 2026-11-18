#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
sdk=$base/sdk
mult=$sdk/usr/lib/x86_64-linux-gnu
for pc in "$mult"/pkgconfig/*.pc; do sed -i "s@^prefix=/usr\$@prefix=$sdk/usr@" "$pc"; done
export PKG_CONFIG_PATH="$mult/pkgconfig"
export LIBUUID_CFLAGS="-I$sdk/usr/include/uuid"
export LIBUUID_LIBS="-L$mult -luuid"
export LD_LIBRARY_PATH="$mult:$base/build"
export CPPFLAGS="-I$sdk/usr/include -I$sdk/usr/include/x86_64-linux-gnu -I$sdk/usr/include/ncursesw"
export LDFLAGS="-L$mult"
export CFLAGS="-Os -march=x86-64 -mtune=generic -pipe"
export py_cv_module__tkinter=disabled
mkdir -p "$base/build"
cd "$base/build"
{ time ../Python-3.13.18/configure --enable-option-checking=fatal --prefix=/usr --libdir=/usr/lib64 --with-platlibdir=lib \
 --enable-shared --enable-ipv6 --with-computed-gotos --with-dbmliborder=gdbm --enable-loadable-sqlite-extensions \
 --without-ensurepip --without-lto --with-system-expat --with-system-libmpdec --with-pkg-config=yes --enable-gil \
 --without-assertions --disable-experimental-jit --disable-optimizations --with-readline=readline --without-static-libpython; } > ../configure.log 2>&1
{ time make -j4; } > ../build.log 2>&1
