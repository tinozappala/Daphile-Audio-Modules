import hashlib
import json
from pathlib import Path
import sys
import zipfile

archive, out = map(Path, sys.argv[1:])
assert archive.stat().st_size == 416207
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '427adba19353636fe87d8dee4a7d04d6763d644034b3076cd44dcbe8295ba152'
with zipfile.ZipFile(archive) as z:
    manifest = json.loads(z.read('PUBLIC_INPUT.json'))['files']
    assert len(z.namelist()) == 23 and set(z.namelist()) == set(manifest) | {'PUBLIC_INPUT.json'}
    for name, checksum in manifest.items():
        p = Path(name)
        assert not p.is_absolute() and '..' not in p.parts
        content = z.read(name)
        assert hashlib.sha256(content).hexdigest() == checksum, name
        target = out / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
print('23 fixture members verified, 22 payload SHA-256 verified')
