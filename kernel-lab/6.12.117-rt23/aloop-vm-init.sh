#!/bin/busybox sh
# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
export PATH=/bin:/sbin:/usr/bin
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /tmp
mount -t tmpfs tmpfs /tmp
exec >/tmp/results.txt 2>&1
echo DAPHILE_ALOOP_VM_BEGIN
uname -a
cat /proc/cmdline
cat /sys/module/snd_aloop/parameters/timer_source
cat /proc/asound/cards
cat /proc/asound/timers
failed=0
for period in 1024 512 256; do
    timeout 12 /bin/pcm-probe 192000 "$period" 3 7 || failed=1
done
for period in 512 256; do
    timeout 12 /bin/pcm-probe 44100 "$period" 2 7 || failed=1
    timeout 12 /bin/pcm-probe 96000 "$period" 2 7 || failed=1
done
for i in 1 2 3 4 5 6; do
    timeout 8 /bin/pcm-probe 192000 256 1 7 >/tmp/p1 2>&1 &
    p1=$!
    timeout 8 /bin/pcm-probe 192000 256 1 6 >/tmp/p2 2>&1 &
    p2=$!
    wait "$p1" || failed=1
    wait "$p2" || failed=1
    cat /tmp/p1 /tmp/p2
    echo "START_STOP_ROUND=$i"
done
dmesg | tail -80
echo "DAPHILE_ALOOP_VM_END failed=$failed"
sync
i=0
while [ ! -b /dev/vda ] && [ "$i" -lt 10 ]; do sleep 1;i=$((i+1));done
if [ -b /dev/vda ]; then
    dd if=/tmp/results.txt of=/dev/vda bs=4096 conv=fsync
fi
sync
poweroff -f
