from collections import Counter
import json
import lzma
from pathlib import Path
import re
import struct
import sys

inputs = json.loads(Path(sys.argv[1]).read_text())
stage = Path(sys.argv[2])
release = (stage / 'kernelrelease').read_text().strip()
assert release == inputs['candidate_release']
modroot = stage / 'usr/lib/modules' / release
baseline = inputs['baseline_release']
baseline_prefix = f'usr/lib/modules/{baseline}/'
old_paths = inputs['baseline_module_paths']
assert len(old_paths) == len(set(old_paths)) == 1843
assert all(p.startswith(baseline_prefix) and p.endswith('.ko.xz') for p in old_paths)
previous = {p.removeprefix(baseline_prefix) for p in old_paths}
current = {}
vermagics = Counter()
config_lines = set((stage / 'boot/kernel.config').read_text().splitlines())
assert 'CONFIG_PREEMPT_RT=y' in config_lines
# Upstream include/linux/vermagic.h tests PREEMPT_BUILD before PREEMPT_RT.
preempt_token = 'preempt' if 'CONFIG_PREEMPT_BUILD=y' in config_lines else 'preempt_rt' 
for file in sorted(modroot.rglob('*.ko.xz')):
    content = lzma.decompress(file.read_bytes(), format=lzma.FORMAT_XZ)
    assert content[:6] == b'\x7fELF\x02\x01', file
    assert struct.unpack_from('<HH', content, 16) == (1, 62), file
    # ELF64 little-endian, normal section numbering for kernel modules.
    e_shoff = struct.unpack_from('<Q', content, 40)[0]
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHH', content, 58)
    assert e_shnum and 0 < e_shstrndx < e_shnum
    def section(i):
        return struct.unpack_from('<IIQQQQIIQQ', content, e_shoff + e_shentsize*i)
    string_section = section(e_shstrndx)
    names = content[string_section[4]:string_section[4]+string_section[5]]
    for i in range(e_shnum):
        sh = section(i)
        name = names[sh[0]:names.index(b'\0', sh[0])]
        if name == b'.modinfo':
            metadata = content[sh[4]:sh[4]+sh[5]].split(b'\0')
            break
    else:
        raise AssertionError(f'missing modinfo {file}')
    magic, = [line.removeprefix(b'vermagic=').decode() for line in metadata if line.startswith(b'vermagic=')]
    assert magic.split()[0] == release and preempt_token in magic.lower().split(), (file, magic)
    vermagics[magic] += 1
    real_name, = [line.removeprefix(b'name=').decode() for line in metadata if line.startswith(b'name=')]
    assert real_name.replace('-', '_') == file.name.removesuffix('.ko.xz').replace('-', '_'), (file, real_name)
    rel = file.relative_to(modroot).as_posix()
    current[rel] = real_name
all_modules = [p for p in modroot.rglob('*') if p.is_file() and re.search(r'\.ko(?:\.[^.]+)?$', p.name)]
assert len(all_modules) == len(current) and all(p.name.endswith('.ko.xz') for p in all_modules)
assert not any(p.is_symlink() for p in modroot.rglob('*'))
indexed = set()
for line in (modroot / 'modules.dep').read_text().splitlines():
    module, deps = line.split(':', 1)
    indexed.add(module)
    assert all(p in current for p in deps.split())
assert indexed == set(current)
assert (modroot/'modules.dep.bin').is_file()
order = {name + '.xz' for name in (modroot/'modules.order').read_text().splitlines()}
assert order == set(current), (len(order), len(current))
before_names = Counter(Path(p).name.removesuffix('.ko.xz').replace('-', '_') for p in previous)
after_names = Counter(n.replace('-', '_') for n in current.values())
added, removed = set(current)-previous, previous-set(current)
result = {'release':release,'previous_count':len(previous),'new_count':len(current),
          'added_paths':sorted(added),'removed_paths':sorted(removed),
          'added_names_with_multiplicity':dict(after_names-before_names),
          'removed_names_with_multiplicity':dict(before_names-after_names),'vermagic_counts':dict(vermagics),
          'depmod_indexed':len(indexed),'compression':'XZ (every stream decompressed and checked)',
          'module_names_and_paths':current}
print(json.dumps(result, indent=2))
