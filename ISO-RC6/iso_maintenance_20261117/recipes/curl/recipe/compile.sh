#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
cd "$base"
python3 setup-sdk.py
: "${PYTHON_RC5_LIB:?set to verified Python RC5 SDK lib directory}"
tls=$(realpath "$PYTHON_RC5_LIB")
mkdir -p pc fixture quic
ln -sfn /usr/lib/x86_64-linux-gnu/libpsl.so.5 fixture/libpsl.so.5
ln -sfn /usr/lib/x86_64-linux-gnu/libpsl.so.5 fixture/libpsl.so
export LD_LIBRARY_PATH="$base/fixture:$base/rc5/lib:$tls"
export PKG_CONFIG_PATH="$base/pc:$base/quic/lib/pkgconfig"
export CFLAGS="-Os -march=x86-64 -mtune=generic -pipe"
export LDFLAGS="-L$base/fixture -L$base/rc5/lib -L$tls -Wl,-rpath-link,$base/rc5/lib -Wl,-rpath-link,$tls"
{
  while read -r package version headers libdir arg; do
    cat > "pc/$package.pc" <<PC
Name: $package
Description: RC5 link SDK
Version: $version
Cflags: $headers
Libs: -L$libdir $arg
PC
  done
} <<PC
libcares 1.34.5 -I$base/c-ares-1.34.5/include $base/rc5/lib -lcares
libnghttp2 1.65.0 -I$base/nghttp2-1.65.0/lib/includes $base/rc5/lib -lnghttp2
libnghttp3 1.8.0 -I$base/nghttp3-1.8.0/lib/includes $base/rc5/lib -lnghttp3
libpsl 0.21.5 -I$base/libpsl-0.21.5/include $base/rc5/lib -lpsl
zlib 1.3.1 -I$base/zlib-1.3.1 $tls -lz
openssl 3.5.9 -I/usr/include $tls -lssl -lcrypto
PC
if [ "${REUSE_QUIC:-0}" != 1 ]; then
cd ngtcp2-1.26.0
{ time ./configure --enable-option-checking=fatal --prefix="$base/quic" --enable-lib-only --enable-static --disable-shared --enable-pic --with-openssl --without-libnghttp3; } > "$base/quic-config.log" 2>&1
{ time make -j4 && make install; } > "$base/quic-build.log" 2>&1
cd "$base"
fi
cd "$base"
test -f quic/lib/libngtcp2_crypto_ossl.a
cd curl-8.23.0
export LDFLAGS="$LDFLAGS -Wl,--exclude-libs,libngtcp2.a:libngtcp2_crypto_ossl.a"
{ time ./configure --enable-option-checking=fatal --prefix=/usr --libdir=/usr/lib64 --sysconfdir=/etc \
 --enable-shared --disable-static --disable-dependency-tracking --enable-symbol-hiding --disable-versioned-symbols \
 --with-openssl --with-libpsl --with-zlib --without-brotli --without-zstd --without-libidn2 --without-libssh2 --without-gssapi --without-libgsasl \
 --enable-ares --disable-threaded-resolver --with-nghttp2 --with-nghttp3 --with-ngtcp2 \
 --disable-ldap --disable-ldaps --disable-gopher --disable-telnet \
 --enable-mqtt --enable-websockets --enable-httpsrr --disable-ech --enable-ntlm \
 --with-ca-bundle=/etc/ssl/certs/ca-certificates.crt --with-ca-path=/etc/ssl/certs --without-ca-fallback; } > "$base/curl-config.log" 2>&1
{ time make -j4; } > "$base/curl-build.log" 2>&1
{ time make DESTDIR="$base/stage" install; } > "$base/stage.log" 2>&1
