"""Build the local RME update and apply it through the existing t620 broker."""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from plugin_framework.lms_package import package

VERSION = '0.5.22'
BASE = ROOT / 'iso_rc7_20261118/github_release/Pacchetti/plugin/DaphileRME-0.5.7.zip'


def build():
    with TemporaryDirectory(prefix='daphile-rme-build-') as folder:
        stage = Path(folder)
        with ZipFile(BASE) as original:
            original.extractall(stage)
            installed = stage / 'DaphileRME'
        delta = HERE / 'DaphileRME'
        for path in delta.rglob('*'):
            if path.is_file():
                (installed / path.relative_to(delta)).write_bytes(path.read_bytes())
        manifest = installed / 'install.xml'
        text = manifest.read_bytes()
        assert text.count(b'<version>0.5.7</version>') == 1
        manifest.write_bytes(text.replace(b'<version>0.5.7</version>',f'<version>{VERSION}</version>'.encode()))
        # Keep the settings URI already loaded by the running LMS process.
        # These describe the archived release, not the local update.
        (installed / 'RELEASE_SIGNATURE.json').unlink(missing_ok=True)
        (installed / 'backend/PROVENANCE.json').unlink(missing_ok=True)
        data, entry = package(installed,'DaphileRME')
    entry['archive'] = f'DaphileRME-{VERSION}.zip'
    return data, entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install',action='store_true')
    args = parser.parse_args()
    data, entry = build()
    output = HERE / entry['archive']
    output.write_bytes(data)
    print(f"{output.name}  {entry['sha256']}",flush=True)
    if not args.install:
        return
    from ram_continuous.remote import run
    installer = (HERE / 'install_rme_on_t620.py').read_text(encoding='utf-8')
    source = 'ENTRY='+repr(entry)+'\n'+installer
    result = run(source,data=data,timeout=85)
    if result.stdout:
        sys.stdout.buffer.write(result.stdout)
        (HERE / f't620_install_{VERSION}_receipt.jsonl').write_bytes(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
