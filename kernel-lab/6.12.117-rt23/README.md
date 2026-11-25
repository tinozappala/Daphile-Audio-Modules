# ALSA loopback hrtimer laboratory

Tino Zappala, Milano - 2026

**Experimental — NOT qualified for physical installation.** Linux release `6.12.117-rt23-daphile-v6-hrtimer-lab`. Kernel and modules compiled on 2026-11-25. Original kernel and physical t630 remain unchanged.

## Provenance

- Linux 6.12.117 tarball SHA-256: `74e99aa5e60e242e0a165fed8cb2ea0d054bf7a401f01861d403d75733456613`.
- PREEMPT_RT patch 6.12.117-rt23 SHA-256: `b243f72ec92f7e7653f896738d11307190f3c1af12b1123e5b7138e1a748c31c`.
- Upstream ALSA commits [bf08a5f698dc](https://github.com/torvalds/linux/commit/bf08a5f698dcb8495efd94f14d863a8e6b123d25) and [5ba790f097f6](https://github.com/torvalds/linux/commit/5ba790f097f6208fe1a1f840868dab0f7ae04a04), Yu-Hsuan Hsu, maintainers' original attribution preserved. The Linux derivative patch remains GPL-2.0-only; original independent lab tools are MIT.
- Backport SHA-256 `356d794f68aa2c7eced8105d390a04bdaa60e0632ec9ac59fc5d70bff06b8406` on MATTEO.
- Backport adapts callback context and stop-count initialization to 6.12, uses hrtimer_init and callback assignment, retains nonblocking STOP cancellation and synchronous cleanup.
- Archived RC6 configuration: 8055 symbols; olddefconfig changed eight toolchain-related symbols. Ubuntu GCC 12.4 and binutils 2.42 differ from archived Debian GCC 12.2/binutils 2.44. Not a bit-reproducible vendor kernel.
- Original jiffies remains default. Experimental hrtimer is selected using snd_aloop.timer_source=hrtimer. Note that upstream position accounting and timer interval calculation still use jiffies/HZ; high-resolution callback infrastructure does NOT by itself prove sub-tick audio pacing, especially at HZ300.

## Build evidence

Started 17:16:58, completed 18:00:58 CET on 2026-11-25. CONFIG_PREEMPT_RT=y, CONFIG_HZ=1000, CONFIG_SND_ALOOP=y, CONFIG_HIGH_RES_TIMERS=y. 1843 compressed modules staged. Full build manifest passed SHA256 readback.

| Artifact | Bytes | SHA256 |
|---|---:|---|
| bzImage | 14406656 | 48946b6307f334a45756cf8ad65536f90e183cc2c9d6da2912f535a8c74d8c78 |
| System.map | 8169534 | 27211bdd4652416964da5e8bac9e4d80d62ea6e232117002f7b112ad42719981 |
| Module.symvers | 1285961 | bb6c99b62680e2e4db77d3399594830dc935a561ef00f602c9b4e7eb086d34b9 |
| .config | 258852 | 9bf042ecf57ff8cbb0bcb084458d46a4b7403ebff1a99aedf917daad32e57da5 |

## Laboratory scope

The scripts use a local WSL2 Ubuntu directory /opt/daphile-kernel and a Windows work directory /mnt/c/Build/Daphile-Kernel. They assume a prepared patched tree, verified archived config and prerequisites; they are not yet a self-contained reproducible installer. The PCM probe uses synthetic paired integer sequences, validates continuity after signal start, and measures read-delivery intervals; it is not an end-to-end physical DAC or exhaustive bit-exact transport measurement. The QEMU KVM test compares A jiffies / B hrtimer / A2 jiffies at the same kernel and timer frequency. Physical qualification remains required. Only t630 is the physical reference; t620 excluded. No private configuration, credentials or user data belong in this public branch.
