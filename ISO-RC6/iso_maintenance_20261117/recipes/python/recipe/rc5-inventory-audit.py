import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

elf, abi, focus = (json.loads(Path(p).read_text()) for p in sys.argv[1:4])
root = Path(sys.argv[4])
r = elf['elf_files']
provided = abi['dependency_providers']
internal = {soname for d in r.values() for soname in d['soname']}
needed = {n for d in r.values() for n in d['needed']}
unavailable = sorted(needed - provided.keys() - internal)
excluded = set('_testbuffer _testcapi _testclinic _testclinic_limited _testexternalinspection _testimportmultiple _testinternalcapi _testlimitedcapi _testmultiphase _testsinglephase _xxtestfuzz xxlimited xxlimited_35'.split())
baseline_names = {Path(name).name.split('.')[0] for name in abi['ELF'] if '/lib-dynload/' in name}
actual_names = {Path(name).name.split('.')[0] for name in r if '/lib-dynload/' in name}
assert len(baseline_names) == 77 and excluded <= baseline_names
expected_names = baseline_names - excluded
missing_runtime_modules = sorted(expected_names - actual_names)
unexpected_runtime_modules = sorted(actual_names - expected_names)
assert not missing_runtime_modules and not unexpected_runtime_modules, (missing_runtime_modules, unexpected_runtime_modules)
verified_focus = {}
for soname, details in focus.items():
    lib = root / soname
    assert hashlib.sha256(lib.read_bytes()).hexdigest() == details['sha256']
    exports = set()
    text = subprocess.check_output(['readelf','-W','--dyn-syms',lib], text=True)
    for row in text.splitlines():
        fields = row.split()
        if len(fields) >= 8 and fields[4] in ('GLOBAL','WEAK','UNIQUE') and fields[6] != 'UND':
            exports.add(fields[7].split('@')[0])
    assert set(details['exports']) <= exports, soname
    verified_focus[soname] = len(details['exports'])
out = {'unavailable_target_sonames': unavailable,
       'missing_non_test_stdlib_modules': missing_runtime_modules,
       'stdlib_extension_names': sorted(actual_names),
       'excluded_test_examples': sorted(excluded),
       'target_export_inventory_counts': verified_focus,
       'all_direct_needed': sorted(needed),
       'target_provider_paths': {n: provided[n]['real'] for n in sorted(needed & provided.keys())},
       'scope': 'SONAMEs matched to RC5 inventory; provider symbol resolution is reported separately.'}
print(json.dumps(out,indent=2))
sys.exit(bool(unavailable))
