"""Build a local Camilla 0.4.49 maintenance candidate from immutable RC6 0.4.47.

Changes exactly native_player.py and two current version declarations. The
classic UI, engine, routing, 2 ms guard, DoP gate and historical provenance
remain the bytes of the 0.4.47 package. No installation or repository edits.
"""
import argparse,ast,copy,hashlib,io,json,re,stat,zipfile
from pathlib import Path,PurePosixPath
from xml.etree import ElementTree

HERE=Path(__file__).resolve().parent
NAME='DaphileCamillaDSP'
VERSION='0.4.49'
BASE_VERSION='0.4.47'
BASE_SHA='f0194ae2b1aeba2303f9ccf3c5a923aaf35534aade8c24626e6e9833a6cb5729'
SOURCE_SHA='22a2e57ed5ef76984b27f5e1845de82c54036355872444ed7561feee5fc5f7b4'
CHANGED={'backend/native_player.py','install.xml','backend/peq/__init__.py'}


def need(value,message):
    if not value:raise RuntimeError(message)


def sha(raw,kind='sha256'):return hashlib.new(kind,raw).hexdigest()


def safe_members(archive):
    result={};seen=set()
    for member in archive.infolist():
        name=member.filename;prefix=NAME+'/'
        need(member.orig_filename==name and name.startswith(prefix) and '\\' not in name,'Unexpected archive member')
        relative=name[len(prefix):];parts=relative.split('/')
        need(not member.is_dir() and not PurePosixPath(relative).is_absolute()
             and all(part not in ('','.','..') and ':' not in part and not re.search(r'[\x00-\x1f\x7f]',part)
                     for part in parts),'Unsafe archive member: '+name)
        need(relative.casefold() not in seen and stat.S_IFMT(member.external_attr>>16) in (0,stat.S_IFREG),
             'Duplicate, symlink or nonregular archive member: '+name)
        seen.add(relative.casefold());result[relative]=(member,archive.read(member))
    need(archive.testzip() is None,'Archive CRC verification failed')
    return result


def write_exact(path,raw):
    if path.exists():need(path.read_bytes()==raw,'Existing candidate artifact differs; refusing overwrite: '+path.name)
    else:path.write_bytes(raw)


def build(args):
    raw=args.base.read_bytes();need(sha(raw)==BASE_SHA,'Base is not the exact immutable RC6 0.4.47 ZIP')
    replacement=args.native_player.read_bytes();need(sha(replacement)==SOURCE_SHA,'Replacement differs from the qualified guard prototype')
    compile(replacement,'backend/native_player.py','exec')
    linux_raw=args.benchmark.read_bytes();linux=json.loads(linux_raw)
    need(linux.get('status')=='PASS' and len(linux.get('checks',{}))==45 and all(linux['checks'].values()),'Qualified 45-case Linux fixture result required')
    bench=linux.get('benchmark',{})
    need(bench.get('kind')=='regular-file-fixture-only' and bench.get('rounds')==6
         and bench.get('iterations_per_round')==1000 and bench.get('output_checksums_equal') is True,
         'Qualified benchmark scope/checksums differ')
    entry=json.loads(args.approval47.read_bytes())
    required={'archive','bytes','files','maxTarget','minTarget','name','sha1','sha256','version'}
    need(set(entry)==required and entry['name']==NAME and entry['version']==BASE_VERSION
         and entry['archive']==args.base.name and entry['bytes']==len(raw)
         and entry['sha256']==BASE_SHA and entry['sha1']==sha(raw,'sha1'),'Actual 0.4.47 host approval differs from the immutable package')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:original=safe_members(archive)
    original_hashes={name:sha(value) for name,(_,value) in original.items()}
    need(original_hashes==entry['files'],'Actual approved member set/hashes do not match the base ZIP')
    need(original['backend/native_player.py'][1]==args.original_player.read_bytes(),'Prototype baseline reader differs from package 0.4.47')
    manifest=ElementTree.fromstring(original['install.xml'][1])
    need(manifest.findtext('version')==BASE_VERSION,'Unexpected base install.xml version')
    need(manifest.findtext('targetApplication/minVersion')==entry['minTarget']
         and manifest.findtext('targetApplication/maxVersion')==entry['maxTarget'],'Approved LMS limits differ from base install.xml')
    updated={name:value for name,(_,value) in original.items()}
    updated['backend/native_player.py']=replacement
    old=b'<version>0.4.47</version>';new=b'<version>0.4.49</version>'
    need(updated['install.xml'].count(old)==1,'Expected one current plugin version declaration')
    updated['install.xml']=updated['install.xml'].replace(old,new)
    old_init=updated['backend/peq/__init__.py']
    parsed=ast.parse(old_init)
    need(len(parsed.body)==1 and isinstance(parsed.body[0],ast.Assign)
         and [node.id for node in parsed.body[0].targets if isinstance(node,ast.Name)]==['__version__']
         and ast.literal_eval(parsed.body[0].value)==BASE_VERSION,'Expected pure backend version metadata')
    need(old_init.count(BASE_VERSION.encode())==1,'Ambiguous backend version metadata')
    updated['backend/peq/__init__.py']=old_init.replace(BASE_VERSION.encode(),VERSION.encode())
    changed={name for name in original if updated[name]!=original[name][1]}
    need(changed==CHANGED,'Candidate contains changes beyond the three approved members')
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for relative,(member,_) in original.items():archive.writestr(copy.copy(member),updated[relative])
    candidate=stream.getvalue();target=args.out/(NAME+'-'+VERSION+'.zip')
    with zipfile.ZipFile(io.BytesIO(candidate)) as archive:verified=safe_members(archive)
    need(list(verified)==list(original),'Candidate archive member order/set differs')
    for name,(member,value) in verified.items():
        need(value==updated[name],'Candidate archive readback differs: '+name)
        need(member.external_attr==original[name][0].external_attr,'Candidate executable/permission metadata changed: '+name)
        if name not in CHANGED:need(value==original[name][1],'Untouched member bytes changed: '+name)
    need(ElementTree.fromstring(verified['install.xml'][1]).findtext('version')==VERSION,'Candidate plugin version differs')
    need(ast.literal_eval(ast.parse(verified['backend/peq/__init__.py'][1]).body[0].value)==VERSION,'Candidate API version differs')
    need(verified['backend/PROVENANCE.json'][1]==original['backend/PROVENANCE.json'][1],'Historical provenance must remain byte-identical')
    approval=copy.deepcopy(entry)
    approval.update(version=VERSION,archive=target.name,bytes=len(candidate),sha1=sha(candidate,'sha1'),sha256=sha(candidate),
                    files={name:sha(value) for name,(_,value) in verified.items()})
    need(set(approval)==set(entry) and approval['minTarget']==entry['minTarget'] and approval['maxTarget']==entry['maxTarget'],
         'Host approval schema or target limits changed')
    report=dict(status='PASS_CANDIDATE_COMPOSITION_ONLY',component=NAME,version=VERSION,
        base=dict(version=BASE_VERSION,archive=args.base.name,bytes=len(raw),sha1=sha(raw,'sha1'),sha256=BASE_SHA,
                  approved_entry_sha256=sha(args.approval47.read_bytes())),
        candidate={key:approval[key] for key in ('archive','bytes','sha1','sha256','minTarget','maxTarget')},
        member_count=len(verified),changed_members=[dict(path=name,before_sha256=original_hashes[name],sha256=approval['files'][name]) for name in sorted(CHANGED)],
        untouched_members=len(verified)-len(CHANGED),
        checks=dict(exact_base=True,prototype_sha_verified=True,crc_pass=True,unsafe_members_absent=True,
                    complete_approved_member_set=True,exactly_three_changed_members=True,
                    all_other_member_bytes_identical=True,archive_modes_preserved=True,plugin_and_api_version_49=True,
                    classic_gui_47_bytes_identical=True,engine_binary_identical=True,historical_provenance_identical=True,
                    sha1_sha256_and_relative_module_hashes_verified=True),
        guard_fixture=dict(file='LINUX_FIXTURE_BENCHMARK.json',sha256=sha(linux_raw),status=linux['status'],cases=len(linux['checks']),
                           scope=linux['scope'],kind=bench['kind'],rounds=bench['rounds'],iterations_per_round=bench['iterations_per_round'],
                           output_checksums_equal=bench['output_checksums_equal'],median_cpu_ns_per_call=bench['median_cpu_ns_per_call'],
                           median_cpu_reduction_percent=bench['median_cpu_reduction_percent'],qualifier=bench['qualifier']),
        semantics=dict(guard_interval_ms=2,classic_ui_base=BASE_VERSION,experimental_ui_48_included=False,
                       routing_changed=False,dop_gate_changed=False,peq_algorithms_changed=False),
        historical_metadata_note='backend/PROVENANCE.json is unchanged historical metadata inherited from 0.4.47; this external report identifies the 0.4.49 maintenance candidate.',
        limits=['Composition and regular-file fixtures only; no native install or live audio qualification by this builder.',
                'The measured reduction concerns guard reader calls, not total worker CPU or audio quality.',
                'The original target platform and LMS limits are preserved.'],
        production_updated=False,github_repository_updated=False,source_047_archive_unchanged=True)
    guide=(
        '# Camilla 0.4.49 — candidata di manutenzione CPU\n\n'
        'Questo pacchetto deriva dalla **0.4.47 esatta della RC6** e ne conserva l’interfaccia classica. '
        'La 0.4.48 sperimentale presente in GitHub rimane separata e non viene inclusa o modificata.\n\n'
        'Cambia soltanto la lettura fresca dei file del monitor PCM in `backend/native_player.py`; '
        '`install.xml` e la versione esposta dall’API vengono aggiornati a 0.4.49. '
        'Gli altri '+str(len(verified)-3)+' file sono identici alla base, inclusi GUI HTML/JS/CSS, motore CamillaDSP, '
        'routing e gate DoP. L’intervallo del guard rimane 2 ms; ogni ciclo apre e chiude nuovamente i file.\n\n'
        'Le 45 fixture differenziali Linux sono PASS. In sei round da 1000 chiamate su file regolari, '
        'la mediana CPU per chiamata è scesa da 1,899 ms a 0,559 ms (−70,56%), con risultati/checksum identici. '
        '**La misura riguarda questa lettura, non la CPU totale del worker**, procfs dal vivo o un miglioramento sonoro.\n\n'
        'La composizione ZIP e gli hash sono verificati in `BUILD_REPORT.json`; '
        '`APPROVED_ENTRY_0.4.49.json` mantiene lo schema e i limiti LMS dell’approvazione 0.4.47 effettiva. '
        'La provenienza storica interna della 0.4.47 è conservata byte per byte; il rapporto esterno descrive questa candidata.\n\n'
        'Il semplice download non aggiorna il player. L’installazione deve creare una nuova release Audio Host, '
        'rendere coerenti catalogo e sorgenti LMS e preservare preset, dati utente e release precedenti. '
        'Questo builder non installa e non modifica produzione, approvazioni attive o GitHub.\n\n'
        'Tino Zappala, Milano - 2026\n').encode('utf8')
    args.out.mkdir(parents=True,exist_ok=True)
    for path,value in ((target,candidate),(args.out/'APPROVED_ENTRY_0.4.49.json',(json.dumps(approval,indent=2)+'\n').encode()),
                       (args.out/'BUILD_REPORT.json',(json.dumps(report,ensure_ascii=False,indent=2)+'\n').encode()),
                       (args.out/'LINUX_FIXTURE_BENCHMARK.json',linux_raw),(args.out/'LEGGIMI_CAMILLA_0.4.49_IT.md',guide)):
        write_exact(path,value)
    need(args.base.read_bytes()==raw,'Immutable base package changed during build')
    print(json.dumps(dict(status=report['status'],archive=str(target),sha256=approval['sha256'],sha1=approval['sha1'],
                         bytes=len(candidate),members=len(verified),changed_members=sorted(CHANGED),production_updated=False),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,default=HERE/'github_autotune_b4f_20261118/Pacchetti/plugin/DaphileCamillaDSP-0.4.47.zip')
    parser.add_argument('--native-player',type=Path,default=HERE/'camilla_guard_fix_20261118/working/native_player.py')
    parser.add_argument('--original-player',type=Path,default=HERE/'camilla_guard_fix_20261118/original/native_player.py')
    parser.add_argument('--approval47',type=Path,default=HERE/'camilla_guard_fix_20261118/APPROVAL_47.json')
    parser.add_argument('--benchmark',type=Path,default=HERE/'camilla_guard_fix_20261118/LINUX_FIXTURE_BENCHMARK.json')
    parser.add_argument('--out',type=Path,default=HERE.parent/'DAPHILE_MANUTENZIONE_RC6_20261117/AutoTune-B4f-20261118/compatibility')
    try:build(parser.parse_args())
    except (OSError,ValueError,RuntimeError,zipfile.BadZipFile) as error:raise SystemExit('Candidate build refused: '+str(error))
