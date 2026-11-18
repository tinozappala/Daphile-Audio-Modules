# Source from Bash to select the kernel and host tools.
base=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
src=$base/linux-6.12.117
obj=$base/objects
host=$base/host/sdk/usr
export PATH="$host/bin:$host/sbin:$PATH"
export BISON_PKGDATADIR="$host/share/bison"
export M4="$host/bin/m4"
export LC_ALL=C TZ=UTC
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=daphile-build
export KBUILD_BUILD_TIMESTAMP='Tue Nov 17 00:00:00 UTC 2026' KBUILD_BUILD_VERSION=1
export ARCH=x86_64
kernel_cc=$base/bookworm/sdk/usr/bin/gcc-12
kernel_make() {
  make -C "$src" O="$obj" CC="$kernel_cc" HOSTCC=gcc \
     HOSTCFLAGS="-I$host/include" HOSTLDFLAGS="-L$host/lib/x86_64-linux-gnu" "$@"
}
