#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
sdk=$base/sdk/lib
rc=$(realpath "$base/../../python313/rc5-full/lib")
dev=$(realpath "$base/../host/usr")
ssl_headers=$(realpath "$base/../openssl-headers/openssl-3.5.9/include")
mkdir -p "$base/pc" "$base/fixture"
ln -sfn /usr/lib/x86_64-linux-gnu/libxml2.so.2 "$base/fixture/libxml2.so"
ln -sfn /usr/lib/x86_64-linux-gnu/libxml2.so.2 "$base/fixture/libxml2.so.2"
export PATH="$dev/bin:$PATH"
export LD_LIBRARY_PATH="$base/fixture:$sdk:$rc"
export PKG_CONFIG_PATH="$base/pc"
while read -r name version inc lib flag; do
 cat > "$base/pc/$name.pc" <<PC
Name: $name
Description: signed Debian headers, RC5 link provider
Version: $version
Cflags: -I$inc
Libs: -L$lib $flag
PC
done <<PC
libxml-2.0 2.9.14 $dev/include/libxml2 $base/fixture -lxml2
opus 1.5.2 $dev/include/opus $sdk -lopus
ogg 1.3.5 $dev/include $sdk -logg
vorbis 1.3.7 $dev/include $sdk -lvorbis
vorbisenc 1.3.7 $dev/include $sdk -lvorbisenc
soxr 0.1.3 $dev/include $sdk -lsoxr
openssl 3.5.9 $ssl_headers $rc -lssl -lcrypto
zlib 1.3.1 /usr/include $rc -lz
PC
mapfile -t flags < <(python3 - "$base/input.json" <<'PY'
import json,re,sys
raw=json.load(open(sys.argv[1]))['buildconf']['text'].split('ffmpeg version')[0]
print('\n'.join(re.findall(r'(?m)^\s*(--(?:enable|disable)-[a-z0-9-]+)\s*$',raw)))
PY
)
cd "$base/ffmpeg-6.1.6"
{ time ./configure --prefix=/usr --libdir=/usr/lib64 --shlibdir=/usr/lib64 \
 --arch=x86_64 --cpu=x86-64 --disable-autodetect --disable-ffplay --enable-v4l2-m2m \
 --extra-cflags="-march=x86-64 -mtune=generic -pipe -I$dev/include" \
 --extra-ldflags="-L$base/fixture -L$sdk -L$rc -Wl,-rpath-link,$base/fixture -Wl,-rpath-link,$sdk -Wl,-rpath-link,$rc" "${flags[@]}"; } > "$base/configure.log" 2>&1
{ time timeout -k 12 28m make -j4 ffmpeg ffprobe; } > "$base/build.log" 2>&1
{ time make DESTDIR="$base/stage-full" install-progs install-libs; } > "$base/install.log" 2>&1
mkdir -p "$base/stage/usr/"{bin,lib64}
cp -a "$base/stage-full/usr/bin/"{ffmpeg,ffprobe} "$base/stage/usr/bin/"
for n in avutil avcodec avformat avdevice avfilter swscale swresample; do
 cp -a "$base/stage-full/usr/lib64/lib$n.so"* "$base/stage/usr/lib64/"
done
