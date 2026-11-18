#!/usr/bin/env python3
"""Verifica in sola lettura i file e i pacchetti della raccolta Daphile."""
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check(test, message):
    if not test:
        raise ValueError(message)


def local_path(name):
    path = PurePosixPath(name)
    check(not path.is_absolute() and '..' not in path.parts and '\\' not in name,
          'Percorso non valido nel manifest')
    path = BASE / path
    check(BASE in path.resolve().parents, 'Collegamento esterno alla cartella')
    return path


def read_json(name):
    return json.loads(local_path(name).read_text(encoding='utf-8'))


def verify_package(package):
    path = local_path(package['file'])
    inventory = read_json(package['inventario'])
    check(path.name == inventory['archive'], 'Nome archivio diverso dall’inventario')
    check(path.stat().st_size == package['byte'], f'Dimensione errata per {path.name}')
    check(digest(path.read_bytes()) == inventory['sha256'] == package['sha256'],
          f'Impronta errata per {path.name}')
    plugin = package['tipo'] == 'plugin-lms'
    base = package['nome'] if plugin else path.stem
    with ZipFile(path) as archive:
        recorded = {base + '/' + member: value for member, value in inventory['files'].items()}
        names = archive.namelist()
        check(len(names) == len(recorded) == package['voci'] and set(names) == set(recorded),
              f'Elenco dei file inatteso in {path.name}')
        for name in names:
            check(digest(archive.read(name)) == recorded[name], f'File diverso dal manifest in {path.name}')
        if plugin:
            xml = ET.fromstring(archive.read(base + '/install.xml'))
            check(xml.findtext('version') == package['versione'] == inventory['version'],
                  f'Versione del plugin diversa dal manifest per {path.name}')
            check(xml.findtext('targetPlatform') == 'linux-x86_64' and all(
                xml.findtext('targetApplication/' + key) == '9.1.1' for key in ('minVersion','maxVersion')),
                f'Compatibilità LMS inattesa per {path.name}')
    print(f'OK  {path.name} ({len(names)} file)')


def main():
    sums = (BASE / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
    for line in sums:
        item = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        check(item is not None, 'Formato non valido in SHA256SUMS.txt')
        check(digest(local_path(item[2]).read_bytes()) == item[1], f'Impronta errata per {item[2]}')
    for package in read_json('manifest.json')['pacchetti']:
        verify_package(package)
    print('Verifica completata.')


if __name__ == '__main__':
    try:
        main()
    except (KeyError, OSError, ValueError, ET.ParseError, BadZipFile) as error:
        print(f'Verifica interrotta. {error}', file=sys.stderr)
        sys.exit(1)
