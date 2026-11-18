#!/bin/bash
set -euo pipefail
base=$(cd "$(dirname "$0")" && pwd)
sdk=$(realpath "$base/../../python313/sdk/usr")
rc=$(realpath "$base/../../python313/rc5-full/lib")
crypt=$(realpath "$base/../openssh/sdk/lib")
arch=x86_64-linux-thread-multi
export LD_LIBRARY_PATH="$crypt:$rc"
cd "$base/perl-5.40.5"
{ time sh Configure -des -Dprefix=/usr -Dinstallprefix=/usr -Dinstallusrbinperl=n \
 -Dsiteprefix=/usr/local -Dvendorprefix=/usr -Dscriptdir=/usr/bin \
 -Dprivlib=/usr/lib64/perl5/5.40 -Darchlib="/usr/lib64/perl5/5.40/$arch" \
 -Dsitelib=/usr/local/lib64/perl5/5.40 -Dsitearch="/usr/local/lib64/perl5/5.40/$arch" \
 -Dvendorlib=/usr/lib64/perl5/vendor_perl/5.40 -Dvendorarch="/usr/lib64/perl5/vendor_perl/5.40/$arch" \
 -Darchname=x86_64-linux-thread -Dusethreads -Duse64bitall -Duse64bitint -Duseshrplib -Dlibperl=libperl.so.5.40.2 \
 -Ui_xlocale -Ui_ndbm -Di_gdbm -Ui_db -Dnoextensions=ODBM_File -DDEBUGGING=none \
 -Dcc=gcc '-Doptimize=-O2 -march=x86-64 -mtune=generic -pipe -fno-strict-aliasing' \
 "-Accflags=-DNO_PERL_RAND_SEED -I$sdk/include" \
 "-Dldflags=-L$crypt -L$rc" "-Dlibpth=$crypt $rc /usr/lib/x86_64-linux-gnu /usr/lib64 /lib64" \
 -Dmyhostname=localhost -Dcf_by=builder -Dperladmin=root@localhost -Duselargefiles; } > ../configure.log 2>&1
{ time timeout -k 10 20m make -j4; } > ../build.log 2>&1
{ time make install DESTDIR="$base/stage"; } > ../install.log 2>&1
