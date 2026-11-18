#!/bin/bash
set -euo pipefail
root=$(realpath "${1:?extracted archive root}")
: "${RC5_PROVIDER_PATH:?colon-separated RC5 fixture library directories}"
lib=$root/usr/lib64/perl5/5.40
arch=$lib/x86_64-linux-thread-multi
test "$(id -u)" -ne 0
export LD_LIBRARY_PATH="$arch/CORE:$RC5_PROVIDER_PATH" LIBPERL_STAGE_ARCH="$arch" LIBPERL_STAGE_LIB="$lib"
export LD_BIND_NOW=1
env -u PERL5LIB -u PERL5OPT "$root/usr/bin/perl" -I"$arch" -I"$lib" -MDigest::SHA=sha256_hex -MConfig -MFile::Spec -MEncode -MIO::Handle -e '
die "root" unless $> != 0;
die "version" unless $^V eq v5.40.5 && $Config{version} eq "5.40.5";
die "digest" unless sha256_hex("abc") eq "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";
for my $mod (qw(Digest/SHA.pm Config.pm File/Spec.pm Encode.pm IO/Handle.pm)) {
 my $p=$INC{$mod} // die "missing $mod";
 die "outside staging: $p" unless index($p, "$ENV{LIBPERL_STAGE_LIB}/")==0;
 open my $f, "<", $p or die "read $p: $!";
 die "empty $p" unless read($f, my $byte, 1)==1;
 print "READ/IMPORT $mod\n";
}
print "PASS uid=$> Perl=$^V SHA-256\n";
'
