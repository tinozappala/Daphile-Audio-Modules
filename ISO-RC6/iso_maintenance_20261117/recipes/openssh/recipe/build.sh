#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
rc=$(realpath "$base/../../python313/rc5-full/lib")
sdk=$base/sdk/lib
headers=$(realpath "$base/../host/usr/include")
ssl_headers=$(realpath "$base/../openssl-headers/openssl-3.5.9/include")
export CPPFLAGS="-I$ssl_headers -I$headers"
export CFLAGS="-O2 -march=x86-64 -mtune=generic -pipe"
export LDFLAGS="-L$sdk -L$rc -Wl,-rpath-link,$sdk -Wl,-rpath-link,$rc"
export LD_LIBRARY_PATH="$sdk:$rc"
cd "$base/openssh-10.6p2"
{ time ./configure --enable-option-checking=fatal --prefix=/usr --bindir=/usr/bin --sbindir=/usr/bin --libexecdir=/usr/lib64/misc --sysconfdir=/etc/ssh \
 --with-privsep-path=/var/empty --with-sandbox=seccomp_filter --with-pam; } > "$base/configure.log" 2>&1
{ time timeout -k 10 9m make -j4; } > "$base/build.log" 2>&1
mkdir -p "$base/stage/usr/"{bin,lib64/misc}
for f in ssh sshd scp sftp ssh-keygen ssh-keyscan ssh-agent ssh-add; do
 install -m 755 "$f" "$base/stage/usr/bin/$f"
done
for f in sshd-session sshd-auth sftp-server ssh-keysign ssh-pkcs11-helper ssh-sk-helper; do
 install -m 755 "$f" "$base/stage/usr/lib64/misc/$f"
done
grep -Eq '^#define USE_PAM 1' config.h
grep -Eq '^#define SANDBOX_SECCOMP_FILTER 1' config.h
