import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

archive, root = map(Path, sys.argv[1:])
assert archive.stat().st_size == 4761698
assert hashlib.sha256(archive.read_bytes()).hexdigest() == '7391cbab5c31b47ea59bf5e573338fc4ba78c31f157ea6b4f9af9653613cd2b9'
with zipfile.ZipFile(archive) as z:
    manifest = json.loads(z.read('MANIFEST.json'))
    files = manifest['files']
    links = manifest['relative_symlinks']
    assert len(files) == 17 and len(links) == 32
    assert len(z.namelist()) == 51 and set(z.namelist()) == set(files) | set(links) | {'MANIFEST.json', 'SHA256SUMS'}
    exports = {}
    (root / 'lib').mkdir(parents=True, exist_ok=True)
    for name, details in files.items():
        path = PurePosixPath(name)
        assert path.parent == PurePosixPath('lib') and not stat.S_ISLNK(z.getinfo(name).external_attr >> 16)
        data = z.read(name)
        assert data.startswith(b'\x7fELF') and len(data) == details['bytes']
        assert hashlib.sha256(data).hexdigest() == details['sha256']
        dest = root / name
        assert not dest.is_symlink()
        dest.write_bytes(data)
        dest.chmod(int(details['mode'], 8))
        elf = subprocess.check_output(['readelf', '-Wd', dest], text=True)
        assert f'[{details["soname"]}]' in elf
    for name, target in links.items():
        assert PurePosixPath(name).parent == PurePosixPath('lib')
        assert stat.S_ISLNK(z.getinfo(name).external_attr >> 16)
        assert z.read(name).decode() == target == PurePosixPath(target).name
        assert 'lib/' + target in set(files) | set(links)
        dest = root / name
        if dest.is_symlink():
            assert dest.readlink() == Path(target)
        else:
            dest.symlink_to(target)
    for name in links:
        assert (root / name).resolve().is_file()
    for item in ('MANIFEST.json', 'SHA256SUMS'):
        (root / item).write_bytes(z.read(item))
subprocess.run(['sha256sum', '-c', 'SHA256SUMS'], cwd=root, check=True)
