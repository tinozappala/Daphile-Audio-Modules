# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
set -euo pipefail
r=/opt/daphile-kernel
lab=$r/vm-lab
root=$lab/initramfs
mkdir -p "$root"/{bin,sbin,proc,sys,dev,tmp,usr/share}
cp "$(command -v busybox)" "$root/bin/busybox"
cp "$lab/pcm-probe" "$root/bin/"
cp /mnt/c/Build/Daphile-Kernel/aloop-vm-init.sh "$root/init"
chmod +x "$root/init"
ldd "$lab/pcm-probe" | awk '/=> \// {print $3} /^[[:space:]]*\// {print $1}' | while read -r lib; do
    mkdir -p "$root$(dirname "$lib")"
    cp -L "$lib" "$root$lib"
done
cp -a /usr/share/alsa "$root/usr/share/"
chroot "$root" /bin/busybox echo INITRAMFS_BUSYBOX_PASS
set +e
chroot "$root" /bin/pcm-probe
rc=$?
set -e
test "$rc" = 2
(cd "$root"; find . -print0 | cpio --null -o --format=newc 2>"$lab/cpio.log" | gzip -1 > "$lab/aloop-lab-initramfs.cpio.gz")
gzip -t "$lab/aloop-lab-initramfs.cpio.gz"
ls -lh "$lab/aloop-lab-initramfs.cpio.gz"
sha256sum "$lab/aloop-lab-initramfs.cpio.gz"
echo VM_INITRAMFS_BUILD_PASS
