"""Build the Camilla plugin for RC6 using the deployed engine or the intact base ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

PARENT = "f0194ae2b1aeba2303f9ccf3c5a923aaf35534aade8c24626e6e9833a6cb5729"
ENGINE_SHA = "e69090287d9af87eeb5720d59cf8ebf8cab1ed3adbe24de1dd60b66a47f5385b"
NAME = "DaphileCamillaDSP"
VERSION = "0.4.48"
ENGINE = "backend/bin/camilladsp"

def digest(data):
    return hashlib.sha256(data).hexdigest()

def build(source, base, engine_path, output):
    files = Path(__file__).with_name('camilla-rc6-layout.txt').read_text().splitlines()
    if base:
        if digest(base.read_bytes()) != PARENT:
            raise ValueError("La base deve essere il pacchetto RC6 originale 0.4.47.")
        with ZipFile(base) as template:
            if template.namelist() != [f'{NAME}/{f}' for f in files]:
                raise ValueError("Composizione della base inattesa.")
            engine = template.read(f'{NAME}/{ENGINE}')
    else:
        engine = engine_path.read_bytes()
    if digest(engine) != ENGINE_SHA:
        raise ValueError("Il binario non corrisponde al motore 4.1.3 distribuito con RC6.")
    if ElementTree.parse(source / 'install.xml').findtext('version') != VERSION:
        raise ValueError("Versione sorgente diversa da 0.4.48.")
    output.mkdir(exist_ok=True, parents=True)
    target = output / f"{NAME}-{VERSION}.zip"
    paths = {}
    notice = (source/'backend/licenses/CamillaEQ-MIT.txt').read_text(encoding='utf-8').strip()
    with ZipFile(target, 'w') as archive:
        for relative in files:
            data = engine if relative == ENGINE else (source/relative).read_bytes()
            # The RC6 updater compares the package against its exact set of paths.
            if relative == 'backend/peq/server.py':
                marker = b'from .comparison import Comparison'
                if data.count(marker) != 1:
                    raise ValueError("Import del confronto non riconosciuto.")
                data = data.replace(marker, (source/'backend/peq/comparison.py').read_bytes())
                compile(data,relative,'exec')
            if relative in ('backend/ui/app.js','backend/ui/eq-graph.js','backend/ui/style.css'):
                data = ('/* CamillaEQ\n'+notice+'\n*/\n').encode()+data
            paths[relative] = digest(data)
            info = ZipInfo(f'{NAME}/{relative}')
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, data)
    approval = {'version':VERSION,'archive':target.name,'sha256':digest(target.read_bytes()),'files':paths}
    (output/'APPROVAL.json').write_text(json.dumps(approval, indent=2)+'\n',encoding='utf-8')
    return approval

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--base',type=Path)
    inputs.add_argument('--engine',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    print(build(args.source,args.base,args.engine,args.out)['sha256'])
