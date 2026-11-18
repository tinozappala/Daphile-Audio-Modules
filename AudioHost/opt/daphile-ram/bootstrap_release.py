"""Generic DATA initialization for the ISO; no lab identities or enrollment."""
from pathlib import Path
import json, os, pwd, shutil, subprocess, uuid, hashlib, importlib.util
import yaml

BASE=Path('/mnt/hdd/system/daphile-ram')
FACTORY=Path('/usr/local/share/daphile-release')

def publish(path, data, mode=0o600, uid=0, gid=0):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fchmod(f.fileno(),mode);os.fchown(f.fileno(),uid,gid);os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:tmp.unlink(missing_ok=True)

def ram_config():
    user=pwd.getpwnam('user')
    BASE.mkdir(parents=True,exist_ok=True,mode=0o700)
    p=BASE/'config.json';cfg=json.loads(p.read_text()) if p.exists() else {}
    roots=list(cfg.get('source_roots',[]))
    prefs=Path('/var/daphile/mediaserver/prefs/server.prefs')
    if prefs.exists():
        value=yaml.safe_load(prefs.read_text()) or {}
        for key in ('mediadirs','audiodir'):
            v=value.get(key,[])
            if isinstance(v,str):v=[v]
            if isinstance(v,list):roots.extend(x for x in v if isinstance(x,str))
    roots.extend(['/media/music','/srv/mediaserver/music','/mnt/hdd/user/music'])
    roots=list(dict.fromkeys(str(Path(x).resolve()) for x in roots if x.startswith('/') and Path(x).is_dir()))
    defaults=dict(state_root=str(BASE/'state'),run_root='/run/daphile-ram',source_roots=roots,
                  native_uid=user.pw_uid,native_gid=user.pw_gid,http_port=9000,
                  native_pidfile='/run/mediaserver/mediaserver.pid',player_id=None,
                  cache_bytes=67108864,max_files=64,native_readiness_seconds=600,
                  native_queue_restore_seconds=120)
    defaults.update(cfg);defaults['source_roots']=roots
    publish(p,(json.dumps(defaults,sort_keys=True)+'\n').encode())
    initialize_navigation()

def initialize_navigation(cgi_dir=Path('/var/www/localhost/cgi-bin')):
    # Optional navigation must not block RAM preparation or native LMS.
    try:
        source=Path(__file__).with_name('ram_navigation.py')
        spec=importlib.util.spec_from_file_location('_actual_ram_navigation_initializer',source)
        navigation=importlib.util.module_from_spec(spec);spec.loader.exec_module(navigation)
        return navigation.initialize(cgi_dir)
    except (OSError,ValueError,RuntimeError,ImportError):
        import sys
        print('RAM_NAVIGATION_INIT_UNAVAILABLE',file=sys.stderr)
        return {'header_changed':False,'fixed_error':'RAM_NAVIGATION_INIT_UNAVAILABLE'}


def modules():
    user=pwd.getpwnam('user')
    machine=Path('/etc/machine-id')
    if not machine.read_text().strip():publish(machine,(uuid.uuid4().hex+'\n').encode(),0o644)
    media=Path('/var/daphile/mediaserver')
    if not media.exists():subprocess.run(['/usr/local/share/mediaserver-init/mediaserver-init.sh'],check=True,timeout=120)
    plugins=media/'cache/InstalledPlugins/Plugins'
    prefs=media/'prefs/plugin/state.prefs'
    # Installation and initial plugin preferences belong to the native factory.
    host=Path('/mnt/hdd/system/daphile-audio-host')
    host.mkdir(parents=True,exist_ok=True,mode=0o755)
    for source in (FACTORY/'audio-host').rglob('*'):
        rel=source.relative_to(FACTORY/'audio-host');dest=host/rel
        if source.is_dir():dest.mkdir(parents=True,exist_ok=True,mode=0o755)
        elif not dest.exists():publish(dest,source.read_bytes(),0o644)
    spec=importlib.util.spec_from_file_location('_camilla_release_consolidation',Path(__file__).with_name('consolidate_camilla.py'))
    consolidation=importlib.util.module_from_spec(spec);spec.loader.exec_module(consolidation)
    consolidation.consolidate(FACTORY,host,plugins,publish,repository_payloads)
    config=host/'config.json'
    if not config.exists():
        value=dict(plugins=str(plugins),prefs=str(prefs),cgi='/var/www/localhost/cgi-bin',
                   rme_state='/mnt/hdd/daphile-autotune',lms_uid=user.pw_uid,
                   ports={'DaphileRME':8891,'DaphileCamillaDSP':8892})
        publish(config,(json.dumps(value)+'\n').encode())
    if not (host/'.owned').exists():publish(host/'.owned',b'DAPHILE_NATIVE_AUDIO_HOST_V1',0o644)
    if not (host/'repository-cgi.json').exists():
        payloads=repository_payloads(host)
        destination=Path('/var/www/localhost/cgi-bin/AudioPlugins')
        if destination.exists() and destination.read_bytes()!=payloads['CGI']:
            raise RuntimeError('EXISTING_AUDIO_REPOSITORY_PRESERVED')
        publish(host/'repository.xml',payloads['repository.xml'],0o644)
        publish(destination,payloads['CGI'],0o755)
        publish(host/'repository-cgi.json',payloads['repository-cgi.json'])
    # A UI default, not a calibration or an audio activation.
    conf=Path('/var/daphile/conf/config.sys')
    raw=conf.read_text() if conf.exists() else ''
    if not any(line.startswith('hw_autotune_gui=') for line in raw.splitlines()):
        publish(conf,(raw.rstrip('\n')+'\nhw_autotune_gui=enable\n').encode(),0o644)


def repository_payloads(host,base_url='http://127.0.0.1/cgi-bin/AudioPlugins'):
    host=Path(host);source=host/'code/repository.py'
    spec=importlib.util.spec_from_file_location('_actual_qualified_audio_repository',source)
    repo=importlib.util.module_from_spec(spec);spec.loader.exec_module(repo)
    template=source.read_text();placeholder="Path('/__DAPHILE_AUDIO_HOST__')"
    if template.count(placeholder)!=1:raise RuntimeError('UNKNOWN_REPOSITORY_TEMPLATE')
    candidate=('#!/usr/bin/python3\n'+template.replace(placeholder,'Path('+repr(str(host))+')')).encode()
    digest=hashlib.sha256(candidate).hexdigest()
    bridge=repo.recorded_bridge(template,host,digest)
    catalog=repo.catalog(json.loads((host/'approvals.json').read_text()),base_url)
    return {'CGI':bridge,'repository.xml':catalog,'repository-cgi.json':(json.dumps({'sha256':digest,'base_url':base_url})+'\n').encode()}

if __name__=='__main__':
    import sys
    if sys.argv[1]=='modules':modules()
    elif sys.argv[1]=='ram-config':ram_config()
    else:raise SystemExit('Unknown initialization action')
