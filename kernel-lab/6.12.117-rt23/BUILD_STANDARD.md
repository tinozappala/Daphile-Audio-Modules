# Build resource standard

Tino Zappala, Milano - 2026

## Approved 2026-11-25
- WSL2 memory cap: 10 GB; 8 logical processors; swap 2 GB.
- Kernel builds and modules_install: `make -j8` by default. Reduce concurrency only if measured memory pressure or thermal instability warrants it.
- Existing builds: controlled interruption, preserve completed objects, append to log, resume incrementally, verify all output hashes.
- No WSL restart while compilation or qualification is active without a verified checkpoint.
- QEMU comparisons run after builds, not simultaneously with heavy compilation. Physical performance qualification on t630 remains separate.
- Store public source and test tooling in GitHub; private artifacts and verified backups in Google Drive. Preserve rollback and avoid music storage.

The resource standard does not imply a measured compilation speedup or real-time performance improvement.
