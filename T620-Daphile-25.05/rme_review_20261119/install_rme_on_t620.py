"""Remote installer body. ENTRY is supplied with the package on stdin."""
import ctypes
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from tempfile import TemporaryDirectory, mkdtemp
from zipfile import ZipFile


HOST = Path('/mnt/hdd/system/daphile-audio-host')
MODULE = 'DaphileRME'


def read_json(path):
    return json.loads(path.read_bytes())


def emit(**record):
    print(json.dumps(record,ensure_ascii=True),flush=True)


def atomic_bytes(path, data, mode):
    temporary = path.with_name('.'+path.name+'.rme-update')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    os.replace(temporary,path)


def exchange(first, second):
    rename = ctypes.CDLL(None,use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
    if rename(-100,os.fsencode(first),-100,os.fsencode(second),2) != 0:
        number = ctypes.get_errno()
        raise OSError(number,os.strerror(number))


def native_sync():
    result = subprocess.run([sys.executable,'-B',str(HOST/'code/hostctl.py'),'sync',
                             '--root',str(HOST),'--module',MODULE],capture_output=True,timeout=28)
    if result.returncode:
        raise RuntimeError((result.stdout+result.stderr).decode(errors='replace'))
    return json.loads(result.stdout)


def endpoint(path='', body=None, cookie=None, expected_status=200):
    conn = http.client.HTTPConnection('127.0.0.1',80,timeout=18)
    try:
        headers = {'Cookie':cookie} if cookie else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        conn.request('GET' if body is None else 'POST','/cgi-bin/RMEPlugin/'+path,
                     None if body is None else json.dumps(body),headers)
        result = conn.getresponse()
        data = result.read(1048576)
        if result.status != expected_status:
            raise RuntimeError(f'CGI RME {path or "/"}: {result.status} {data[:250]!r}')
        return data,result.getheader('Set-Cookie')
    finally:
        conn.close()


def session():
    answer, cookie = endpoint('api/session')
    if not json.loads(answer)['authenticated'] or not cookie:
        raise RuntimeError('Sessione RME non disponibile')
    return cookie.split(';',1)[0]


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Serve il servizio di installazione del t620')
    data = sys.stdin.buffer.read(8*1024*1024)
    if hashlib.sha256(data).hexdigest() != ENTRY['sha256']:
        raise RuntimeError('Pacchetto RME incompleto')
    config = read_json(HOST/'config.json')
    state = Path(config['rme_state'])
    auto = state/'automatic.json'
    if auto.exists() and read_json(auto).get('status') in (
        'running','recovering','recovery-required','timed-out-recovery-required'
    ):
        raise RuntimeError('AutoTune è in uso. Installazione sospesa.')
    approvals = HOST/'approvals.json'
    original_approvals = approvals.read_bytes()
    catalog = json.loads(original_approvals)
    live = HOST/'modules'/MODULE
    old = read_json(live/'current.json')
    old_approval = next(entry for entry in catalog[MODULE] if entry['sha256']==old['release'])
    if old['status']!='running' or old['release']==ENTRY['sha256']:
        raise RuntimeError('Versione RME di partenza inattesa')
    camilla = HOST/'modules/DaphileCamillaDSP/process.json'
    other = camilla.read_bytes() if camilla.exists() else None
    native = Path(config['plugins'])/MODULE
    if native.is_symlink() or not native.is_dir():
        raise RuntimeError('Percorso LMS del modulo inatteso')
    initial = json.loads(endpoint('api/rme/snapshot',{},session())[0])
    base = HOST/'recovery'
    base.mkdir(mode=0o700,exist_ok=True)
    backup = Path(mkdtemp(prefix='rme-'+ENTRY['version']+'-',dir=base))
    (backup/'approvals.json').write_bytes(original_approvals)
    (backup/'current.json').write_text(json.dumps(old),encoding='utf-8')
    shutil.copytree(native,backup/'DaphileRME')
    emit(phase='BACKUP',previous=old['version'],backup=str(backup))
    archive = HOST/'bundles'/ENTRY['archive']
    if archive.exists() and archive.read_bytes() != data:
        raise RuntimeError('Esiste già un altro pacchetto con la stessa versione')
    if not archive.exists():
        atomic_bytes(archive,data,0o600)
    with TemporaryDirectory(prefix='.rme-upgrade-',dir=native.parent) as folder:
        staged = Path(folder)/MODULE
        staged.mkdir(mode=native.stat().st_mode & 0o777)
        with ZipFile(io.BytesIO(data)) as zipped:
            if sorted(zipped.namelist()) != sorted(MODULE+'/'+name for name in ENTRY['files']):
                raise RuntimeError('Contenuto del pacchetto RME inatteso')
            for name,digest in ENTRY['files'].items():
                relative = Path(name)
                if relative.is_absolute() or '..' in relative.parts:
                    raise RuntimeError('Percorso inatteso nel pacchetto')
                content = zipped.read(MODULE+'/'+name)
                if hashlib.sha256(content).hexdigest() != digest:
                    raise RuntimeError('File RME incompleto')
                dest = staged/relative
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(content)
        if approvals.read_bytes() != original_approvals:
            raise RuntimeError('Catalogo modificato durante la preparazione')
        if ENTRY not in catalog[MODULE]:
            catalog[MODULE].append(ENTRY)
        exchanged = False
        try:
            atomic_bytes(approvals,json.dumps(catalog).encode(),0o600)
            exchange(native,staged)
            exchanged = True
            result = native_sync()
            assert result['running'] and result['current']['release']==ENTRY['sha256'], result
            assert result['current']['previous']==old_approval['sha256']
            settings = 'HTML/EN/plugins/DaphileRME/settings-0.5.7.html'
            assert b'/cgi-bin/RMEPlugin/' in (native/settings).read_bytes()
            page,_ = endpoint()
            assert all(marker in page for marker in (b'id="store-hardware"',b'id="preset-name"',b'role="switch"',b'data-page="setups"'))
            assert b'id="read-hardware"' not in page and b'id="activate-hardware"' not in page
            script,_ = endpoint('studio.js')
            assert hashlib.sha256(script).hexdigest()==ENTRY['files']['backend/rme/ui/studio.js']
            cookie = session()
            started = time.monotonic()
            read_state = json.loads(endpoint('api/rme/state',cookie=cookie)[0])
            initial_status_seconds = round(time.monotonic()-started,3)
            while (not read_state['state'] or read_state['refreshing']) and time.monotonic()-started<24:
                time.sleep(.5)
                read_state = json.loads(endpoint('api/rme/state',cookie=cookie)[0])
            assert read_state['state'] and not read_state['refreshing'],read_state.get('error')
            after = read_state['state']
            first_read_seconds = round(time.monotonic()-started,3)
            assert initial['identity']==after['identity']
            unchanged = all(initial[key]==after[key] for key in ('outputs','device','current_eq'))
            assert unchanged,'Le impostazioni del DAC sono cambiate durante la verifica'
            target = json.loads(endpoint('api/rme/memory/read',{'slot':20},cookie)[0])
            assert not target['empty'] and target['read_only']
            request = {'confirm':True,'patch':{'output':'line','slot':20,'expected':after['revision'],
                                               'fingerprint':target['fingerprint'],'replace':False,'name':'x'*15}}
            validation = json.loads(endpoint('api/rme/memory/store',request,cookie,400)[0])
            assert '14' in validation['error']
            current = json.loads(endpoint('api/rme/snapshot',{},cookie)[0])
            name = target['hardware_name']
            if name:
                assert {'identifier':20,'name':name} in current['hardware_names']
            manual = next((output for output,values in current['outputs'].items() if values['eq_preset']==0),None)
            if manual:
                check = {'confirm':True,'patch':{'output':manual,'expected':current['revision'],'slot':'manual'}}
                recalled = json.loads(endpoint('api/rme/preset/apply',check,cookie)[0])
                assert recalled['status']=='no-change' and recalled['readback_verified']
            assert (camilla.read_bytes() if camilla.exists() else None)==other
        except BaseException:
            if exchanged:
                exchange(native,staged)
            atomic_bytes(approvals,original_approvals,0o600)
            recovered = native_sync()
            emit(phase='ROLLBACK',version=recovered.get('current',{}).get('version'))
            raise
    emit(phase='INSTALLED_AND_VERIFIED',version=ENTRY['version'],release=ENTRY['sha256'],
         pid=read_json(live/'process.json')['pid'],old_release=old['release'],physical_slot_20_verified=True,
         slot_20_name=name,native_name_validation=True,manual_noop_checked=manual is not None,
         dac_unchanged=unchanged,camilla_process_unchanged=True,
         initial_status_seconds=initial_status_seconds,first_read_seconds=first_read_seconds)


main()
