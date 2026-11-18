#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
sdk=$base/sdk/usr
export PATH="$sdk/bin:$PATH"
for name in aclocal automake; do ln -sfn "$name-1.17" "$sdk/bin/$name"; done
for tool in aclocal-1.17 automake-1.17 libtoolize; do
    sed -i "s@'/usr/share@'$sdk/share@g; s@\"/usr/share@\"$sdk/share@g" "$sdk/bin/$tool"
done
export AUTOMAKE_UNINSTALLED=1 PERL5LIB="$sdk/share/automake-1.17"
export ACLOCAL_AUTOMAKE_DIR="$sdk/share/aclocal-1.17" AUTOMAKE_LIBDIR="$sdk/share/automake-1.17"
export ACLOCAL_PATH="$sdk/share/aclocal:/usr/share/aclocal"
export M4="$sdk/bin/m4" AUTOCONF="$sdk/bin/autoconf" AUTOHEADER="$sdk/bin/autoheader" AUTOM4TE="$sdk/bin/autom4te"
export autom4te_perllibdir="$sdk/share/autoconf" AC_MACRODIR="$sdk/share/autoconf"
export trailer_m4="$sdk/share/autoconf/autoconf/trailer.m4"
export LUA_CFLAGS="-I$sdk/include/lua5.1 -I$sdk/include/x86_64-linux-gnu"
# Keep the Lua link path separate from the extracted Nettle static archive.
mkdir -p "$base/lua-link"
for lib in "$sdk"/lib/x86_64-linux-gnu/liblua5.1.so*; do
    ln -sfn "$lib" "$base/lua-link/${lib##*/}"
done
export LUA_LIBS="-L$base/lua-link -llua5.1"
export LD_LIBRARY_PATH="$base/lua-link"
export CPPFLAGS="-I$sdk/include"
export CFLAGS='-O2 -march=x86-64 -mtune=generic'
export CC=gcc
export TMPDIR=$(mktemp -d /tmp/lighttpd-check.XXXXXX)
trap 'rm -rf -- "$TMPDIR"' EXIT
ulimit -c 0
cd "$base/lighttpd-1.4.85"
mkdir -p m4
export AUTOM4TE_CFG="$sdk/share/autoconf/autom4te.cfg"
sed -i "s@'/usr/share/autoconf@'$sdk/share/autoconf@g" "$AUTOM4TE_CFG"
{ time autoreconf --force --install; } > "$base/autoreconf.log" 2>&1
{ time ./configure --enable-option-checking=fatal --prefix=/usr --sbindir=/usr/bin --libdir=/usr/lib64 --sysconfdir=/etc \
    --with-openssl --with-nettle --with-pcre2 --with-zlib --with-zstd --with-lua=lua5.1 \
    --without-bzip2 --without-brotli --without-libdeflate --without-webdav-props --without-webdav-locks; } > "$base/configure.log" 2>&1
cat > "$base/sdk-probe.c" <<'C'
#include <lua.h>
#include <lauxlib.h>
#include <stdint.h>
#include <time.h>
_Static_assert(LUA_VERSION_NUM == 501, "Lua 5.1 required");
_Static_assert(sizeof(time_t) >= 8, "64-bit time_t required");
int main(void) {
    lua_State *state = luaL_newstate();
    if (!state) return 1;
    lua_close(state);
    return 0;
}
C
gcc $CFLAGS $LUA_CFLAGS "$base/sdk-probe.c" $LUA_LIBS -o "$base/sdk-probe"
"$base/sdk-probe"
make -n libdir=/usr/lib64/lighttpd > "$base/make-dry.log"
{ time make -j4 libdir=/usr/lib64/lighttpd; } > "$base/build.log" 2>&1
{ time timeout 60 make -j4 check libdir=/usr/lib64/lighttpd; } > "$base/check.log" 2>&1
{ time make DESTDIR="$base/stage" libdir=/usr/lib64/lighttpd install; } > "$base/install.log" 2>&1
mkdir -p "$base/stage/usr/sbin"
ln -sfn ../bin/lighttpd "$base/stage/usr/sbin/lighttpd"
"$base/stage/usr/bin/lighttpd" -V > "$base/features.txt"
python3 "$base/elf-audit.py" "$base/stage" > "$base/elf.json"
python3 "$base/smoke.py" "$base/stage" > "$base/smoke.json" 2> "$base/smoke.log"
python3 "$base/delta-test.py" "$base/lighttpd-1.4.85/src/mod_openssl.c" "$base/stage" > "$base/delta.json"
