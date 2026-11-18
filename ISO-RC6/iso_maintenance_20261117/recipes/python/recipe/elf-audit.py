import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
result = {}
violations = []
def command(*args):
    return subprocess.check_output(args, text=True)

def glibc_key(version):
    return tuple(map(int, version.removeprefix('GLIBC_').split('.')))

for path in sorted((root / 'usr').rglob('*')):
    if path.is_symlink() or not path.is_file():
        continue
    with path.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':
            continue
        stream.seek(0)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    name = str(path.relative_to(root))
    hdr = command('readelf', '-Wh', path)
    dynamic = command('readelf', '-Wd', path)
    notes = command('readelf', '-Wn', path)
    needs = {}
    provider = None
    versions = command('readelf', '-WV', path).partition('Version needs section')[2]
    for row in versions.splitlines():
        if match := re.search(r'File: (\S+)', row):
            provider = match[1]
            needs[provider] = []
        if match := re.search(r'Name: (\S+)', row):
            needs[provider].append(match[1])
    glibc = sorted({v for vs in needs.values() for v in vs if v.startswith('GLIBC_')},
                   key=lambda v: glibc_key(v) if v != 'GLIBC_PRIVATE' else (999,))
    if any(v == 'GLIBC_PRIVATE' or glibc_key(v) > (2, 40) for v in glibc):
        violations.append((name, glibc))
    if re.search(r'\((?:RPATH|RUNPATH)\)', dynamic):
        violations.append((name, 'RPATH/RUNPATH'))
    if 'Advanced Micro Devices X86-64' not in hdr or re.search(r'ISA needed:.*x86-64-v[234]', notes):
        violations.append((name, 'ISA note or machine'))
    disasm = command('objdump', '-d', '--no-show-raw-insn', path)
    higher = sorted(set(re.findall(r'^\s*[\da-f]+:\s+(v(?!err\b|erw\b)[a-z][a-z\d]+|popcnt\w*|lzcnt\w*|tzcnt\w*|crc32\w*|aes\w*|sha(?:1|256)\w*|pdep\w*|pext\w*|endbr64)\b', disasm, re.M)))
    # CET landing pads are harmless on baseline x86; TZCNT decodes as BSF
    # there. Keep both in the evidence and review their use individually.
    if set(higher) - {'endbr64', 'tzcnt'}:
        violations.append((name, higher))
    symbols = []
    for row in command('readelf', '-W', '--dyn-syms', path).splitlines():
        if match := re.search(r'\s(GLOBAL|WEAK)\s+\S+\s+UND\s+(\S+)', row):
            symbols.append({'name': match[2], 'binding': match[1]})
    result[name] = {'sha256': digest, 'needed': re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic),
                    'soname': re.findall(r'\(SONAME\).*?\[([^]]+)\]', dynamic),
                    'version_needs_by_library': needs, 'max_glibc': glibc[-1] if glibc else None,
                    'undefined_dynamic_symbols': symbols, 'isa_review_mnemonics': higher}
assert 'usr/bin/python3.13' in result and 'usr/lib64/libpython3.13.so.1.0' in result
print(json.dumps({'elf_count': len(result), 'elf_files': result, 'violations': violations}, indent=2))
sys.exit(bool(violations))
