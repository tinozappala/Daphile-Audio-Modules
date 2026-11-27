"""Ricostruisce e verifica la ISO .75 dalle otto parti nella stessa cartella."""
import hashlib
import json
from pathlib import Path
import os
import tempfile


EXPECTED = '76081f0321226e3dac8a8d6fd7f821a340a640c7e35ff2a24497f12742c5abd8'
NAME = 'Daphile-0.4.75-live-install-candidate.iso'


def assemble(folder):
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    names = [f'image.{i:03d}' for i in range(1, 9)]
    if (manifest['name'] != NAME or manifest['sha256'] != EXPECTED
            or [p['name'] for p in manifest['parts']] != names):
        raise ValueError('manifesto incompatibile con questa ISO')
    target = folder / NAME
    if target.exists():
        with target.open('rb') as f:
            if hashlib.file_digest(f, 'sha256').hexdigest() != EXPECTED:
                raise FileExistsError('esiste una ISO con lo stesso nome e un digest diverso')
        return target
    total = hashlib.sha256()
    written = 0
    with tempfile.NamedTemporaryFile(dir=folder, suffix='.partial', delete=False) as file:
        tmp = Path(file.name)
    try:
        with tmp.open('wb') as output:
            for part in manifest['parts']:
                count = 0
                digest = hashlib.sha256()
                with (folder / part['name']).open('rb') as source:
                    while block := source.read(4 * 1024 ** 2):
                        output.write(block)
                        total.update(block)
                        digest.update(block)
                        count += len(block)
                if count != part['size'] or digest.hexdigest() != part['sha256']:
                    raise ValueError('parte errata o incompleta: ' + part['name'])
                written += count
        if total.hexdigest() != EXPECTED or written != manifest['size']:
            raise ValueError('dimensione o SHA-256 finale errati')
        if os.name == 'nt':
            tmp.rename(target)
        else:
            os.link(tmp, target)
        return target
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == '__main__':
    iso = assemble(Path(__file__).resolve().parent)
    print(f'{iso.name}\nSHA-256 {EXPECTED}')
