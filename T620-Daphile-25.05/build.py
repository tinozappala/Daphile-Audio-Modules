"""Rebuild the two installed packages and compare their exact release hashes."""
import hashlib
from pathlib import Path
from plugin_framework.lms_package import package

ROOT = Path(__file__).resolve().parent
for module, version, expected in (
    ('DaphileCamillaDSP', '0.4.51', '97dc7d2eb830364c0544ff4f58e47f51c52dd3588df995127b99eb1c03e28b19'),
    ('DaphileRME', '0.5.22', '494c7ceef04ffc8a115a82a24855a2fa2e885751d58166c0591ab02091fd71f2'),
):
    raw, _ = package(ROOT / module, module)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit(f'Contenuto inatteso per {module}')
    dest = ROOT / 'dist'
    dest.mkdir(exist_ok=True)
    path = dest / f'{module}-{version}.zip'
    path.write_bytes(raw)
    print(expected, path.name)
