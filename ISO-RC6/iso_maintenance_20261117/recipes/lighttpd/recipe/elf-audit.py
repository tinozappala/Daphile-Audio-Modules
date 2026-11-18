#!/usr/bin/env python3
"""Record ABI requirements for all files and enforce the requested Cloud gates."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1])
shared = sorted((root / 'usr/lib64/lighttpd').glob('*.so'))
names = set('accesslog ajp13 auth authn_file cgi deflate dirlisting extforward h2 magnet openssl proxy rrdtool sockproxy ssi status userdir vhostdb webdav wstunnel'.split())
assert {f.stem.removeprefix('mod_') for f in shared} == names
allowed = set('libc.so.6 libcrypt.so.1 libcrypto.so.3 liblua5.1.so.0 libm.so.6 libnettle.so.8 libpcre2-8.so.0 libssl.so.3 libz.so.1 libzstd.so.1'.split())
expect = {
    'lighttpd': {'libc.so.6', 'libnettle.so.8', 'libpcre2-8.so.0'},
    'mod_magnet.so': {'liblua5.1.so.0', 'libnettle.so.8'},
    'mod_openssl.so': {'libssl.so.3', 'libcrypto.so.3'},
    'mod_deflate.so': {'libz.so.1', 'libzstd.so.1'},
    'mod_authn_file.so': {'libcrypt.so.1'},
}
def readelf(flag, file):
    return subprocess.check_output(['readelf', '-W', flag, file], text=True)
def version_key(s):
    return tuple(map(int, s.removeprefix('GLIBC_').split('.')))

result = {}
for file in [root / 'usr/bin/lighttpd', *shared]:
    dynamic = readelf('-d', file)
    needed = re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic)
    assert set(needed) <= allowed, (file, needed)
    assert expect.get(file.name, set()) <= set(needed), (file, needed)
    assert not re.search(r'\((?:RPATH|RUNPATH)\)', dynamic), file
    hdr = readelf('-h', file)
    assert 'Advanced Micro Devices X86-64' in hdr, file
    notes = readelf('-n', file)
    assert not re.search(r'ISA needed:.*x86-64-v[234]', notes), file
    requirements = {}
    provider = None
    versions = readelf('-V', file)
    for line in versions.partition('Version needs section')[2].splitlines():
        if match := re.search(r'File: (\S+)', line):
            provider = match[1]
            requirements[provider] = []
        if match := re.search(r'Name: (\S+)', line):
            requirements[provider].append(match[1])
    glibc = sorted({v for values in requirements.values() for v in values if v.startswith('GLIBC_')}, key=version_key)
    assert all(version_key(v) <= (2, 40) for v in glibc), (file, glibc)
    undefined = []
    for line in readelf('--dyn-syms', file).splitlines():
        if fields := re.search(r'\s(GLOBAL|WEAK)\s+\S+\s+UND\s+(\S+)', line):
            undefined.append({'name': fields[2], 'binding': fields[1]})
    result[str(file.relative_to(root))] = {
        'sha256': hashlib.file_digest(file.open('rb'), 'sha256').hexdigest(),
        'needed': needed, 'version_needs_by_library': requirements,
        'max_glibc': glibc[-1] if glibc else None, 'undefined_dynamic_symbols': undefined,
        'elf_notes': notes.strip(),
    }
print(json.dumps(result, indent=2, sort_keys=True))
