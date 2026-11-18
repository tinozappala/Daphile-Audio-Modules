import hashlib
import json
from pathlib import Path
import os
import sys

root = Path(sys.argv[1])
records = {}
checksums = []
for path in sorted(root.rglob('*')):
    name = path.relative_to(root).as_posix()
    if name in ('SHA256SUMS', 'MANIFEST.json'):
        continue
    if path.is_symlink():
        target = os.readlink(path)
        assert not Path(target).is_absolute() and (path.parent / target).resolve().is_relative_to(root.resolve())
        records[name] = {'type': 'symlink', 'target': target,
                         'sha256_link_text': hashlib.sha256(os.fsencode(target)).hexdigest()}
        assert path.is_file(), path
    elif path.is_file():
        records[name] = {'type': 'file'}
    else:
        assert path.is_dir(), path
        continue
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    records[name]['sha256_file_bytes'] = digest
    checksums.append(f'{digest}  {name}\n')
(root / 'MANIFEST.json').write_text(json.dumps(records, indent=2)+'\n')
checksums.append(f'{hashlib.sha256((root / "MANIFEST.json").read_bytes()).hexdigest()}  MANIFEST.json\n')
(root / 'SHA256SUMS').write_text(''.join(checksums))
