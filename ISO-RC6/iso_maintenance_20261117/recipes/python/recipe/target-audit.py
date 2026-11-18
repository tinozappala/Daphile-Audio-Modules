"""Compare staged ELF imports with the provided public RC5 runtime libraries."""
import json
from pathlib import Path
import re
import subprocess
import sys

stage = Path(sys.argv[1])
target_dirs = [Path(p) for p in sys.argv[2:]]
analysis = json.load(sys.stdin)
elf = analysis['elf_files']
libmap = {Path(name).name: stage / name for name in elf}
glibc = {'libc.so.6', 'libm.so.6', 'libdl.so.2', 'librt.so.1', 'libpthread.so.0', 'ld-linux-x86-64.so.2'}
for directory in target_dirs:
    for path in directory.glob('*.so*'):
        if path.name not in glibc:
            libmap.setdefault(path.name, path)
def readelf(opt, path):
    return subprocess.check_output(['readelf', '-W', opt, str(path)], text=True)

export_cache = {}
dep_cache = {}
def verify_info(name):
    if name not in export_cache:
        path = libmap[name]
        exports = set()
        for line in readelf('--dyn-syms', path).splitlines():
            fields = line.split()
            if len(fields) < 8 or fields[4] not in ('GLOBAL', 'WEAK', 'UNIQUE') or fields[6] == 'UND':
                continue
            symbol = fields[7]
            if '@@' in symbol:
                exports.update((symbol.replace('@@', '@'), symbol.split('@@')[0]))
            else:
                exports.add(symbol)
        export_cache[name] = exports
        dep_cache[name] = set(re.findall(r'\(NEEDED\).*?\[([^]]+)\]', readelf('-d', path)))
    return export_cache[name], dep_cache[name]

missing_providers = set()
unresolved = {}
for path, data in elf.items():
    # Extension modules also inherit the executable's global libpython scope.
    todo = set(data['needed']) | {'libpython3.13.so.1.0'}
    seen = set()
    exports = set()
    while todo:
        name = todo.pop()
        if name in seen or name in glibc:
            continue
        seen.add(name)
        if name not in libmap:
            missing_providers.add(name)
            continue
        names, children = verify_info(name)
        exports.update(names)
        todo.update(children - seen)
    failures = [item['name'] for item in data['undefined_dynamic_symbols']
                if '@GLIBC_' not in item['name'] and item['binding'] == 'GLOBAL'
                and item['name'] not in exports]
    if failures:
        unresolved[path] = sorted(set(failures))
print(json.dumps({'missing_non_glibc_sonames': sorted(missing_providers),
                  'unresolved_non_glibc_symbols': unresolved}, indent=2))
sys.exit(bool(missing_providers or unresolved))
