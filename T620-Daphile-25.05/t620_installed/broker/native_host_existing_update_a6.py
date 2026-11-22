"""UNQUALIFIED A3 existing-host update with explicit expected complete preimages.
No service/player calls, no generic root approval. Caller must provide a
genuine scoped lifecycle and complete coherent backup before live use.
"""
import copy,hashlib,json,os,platform,re,shlex,signal,stat,sys
from pathlib import Path
MARKER = 'DAPHILE_NATIVE_AUDIO_HOST_V1'

def direct_absolute(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('Canonical absolute host destination required')
    return path

def safe_directory(info,ancestor=False):
    if stat.S_ISLNK(info.st_mode):raise RuntimeError('Indirect host destination refused')
    if not stat.S_ISDIR(info.st_mode):raise RuntimeError('Host destination parent is not a directory')
    # A root-owned sticky ancestor such as /tmp cannot have a root-owned child
    # replaced by an unrelated user. The actual destination is never writable
    # by others; newly created children are checked again through descriptors.
    sticky_ancestor=ancestor and info.st_mode&stat.S_ISVTX
    if info.st_uid!=0 or (info.st_mode&0o022 and not sticky_ancestor):
        raise RuntimeError('Unsafe host destination ownership')

def checked_directory(path):
    """Read-only preflight of every existing component, without resolve()."""
    path=direct_absolute(path)
    for item in reversed((path,*path.parents)):
        try:info=item.lstat()
        except FileNotFoundError:continue
        safe_directory(info,ancestor=item!=path)
    return path

def safe_file(info):
    if stat.S_ISLNK(info.st_mode):raise RuntimeError('Indirect host destination refused')
    if not stat.S_ISREG(info.st_mode):raise RuntimeError('Host destination is not a regular file')
    if info.st_uid!=0 or info.st_mode&0o022:raise RuntimeError('Unsafe host destination ownership')

def checked_destination(root,path):
    root=direct_absolute(root);path=direct_absolute(path)
    if not path.is_relative_to(root):raise RuntimeError('Host destination escapes its directory')
    checked_directory(path.parent)
    try:info=path.lstat()
    except FileNotFoundError:return
    safe_file(info)

def update_existing(root, bundle_dir, expected, plugins, prefs, cgi, rme_state, uid, hooks=None, repo_base_url=None, *, expected_preimages, maintenance):
    if os.geteuid()!=0: raise RuntimeError('Administrator installation required')
    if sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64') or not all(hasattr(os,n) for n in ('pidfd_open','unshare','setns','CLONE_NEWNET')) or not hasattr(signal,'pidfd_send_signal'):
        raise RuntimeError('Qualified Linux x86_64 and pidfd runtime required')
    # File-only update: no preference deserialization or native service calls.
    root=Path(root)
    from host_config_transaction_a7 import read as guarded_read
    from host_config_transaction_a7 import HostMaintenanceLease
    if type(maintenance) is not HostMaintenanceLease or maintenance.check().root != direct_absolute(root):
        raise RuntimeError('Genuine exact-root Host maintenance lease required before update')
    if type(expected_preimages) is not dict or not all(type(k) is str for k in expected_preimages):
        raise RuntimeError('Explicit complete expected host preimages required')
    expected_preimages=copy.deepcopy(expected_preimages)
    marker_raw,marker_state=guarded_read(root/'.owned')
    if marker_raw!=MARKER.encode() or marker_state is None:
        raise RuntimeError('Exact existing native host marker required')
    config_raw,config_state=guarded_read(root/'config.json')
    if config_raw is None or config_state is None:
        raise RuntimeError('Existing native host configuration required')
    if (expected_preimages.get(str(root/'config.json'))!=config_state
            or expected_preimages.get(str(root/'.owned'))!=marker_state):
        raise RuntimeError('Existing host config or marker differs from expected preimage')
    previous_config=json.loads(config_raw)
    required=dict(plugins=str(Path(plugins)),prefs=str(Path(prefs)),cgi=str(Path(cgi)),
                  rme_state=str(Path(rme_state)),lms_uid=uid)
    if (type(previous_config) is not dict or type(uid) is not int
            or any(type(previous_config.get(k)) is not type(v) or previous_config.get(k)!=v
                   for k,v in required.items())):
        raise RuntimeError('Explicit existing native host route binding differs')
    if root.is_symlink() or not root.is_absolute() or root in (Path('/'),Path('/mnt'),Path('/mnt/hdd'),Path('/opt'),Path('/tmp')):
        raise RuntimeError('Unsafe host root')
    if any(parent.is_symlink() for parent in root.parents):raise RuntimeError('Indirect host root ancestor')
    root=checked_directory(root)
    cgi_dir=checked_directory(cgi)
    hook_dir=checked_directory(hooks) if hooks else None
    bundle_dir=Path(bundle_dir)
    manifest=bundle_dir/'host-bundle.json'
    manifest_bytes=manifest.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest()!=expected: raise RuntimeError('External host manifest pin mismatch')
    contents=json.loads(manifest_bytes)
    # Read and verify every byte before installing any executable or approval.
    verified={}
    for relative,digest in contents['files'].items():
        path=Path(relative)
        if path.is_absolute() or '..' in path.parts or path.as_posix()!=relative or not re.fullmatch(r'[A-Za-z0-9_./-]+',relative): raise ValueError('Unsafe host member')
        source=bundle_dir/path
        if source.is_symlink() or not source.is_file(): raise RuntimeError('Host file not regular')
        data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=digest: raise RuntimeError('Host bundle byte mismatch')
        verified[relative]=data
    if root.exists() and any(root.iterdir()) and not (root/'.owned').exists(): raise RuntimeError('Nonempty unowned prefix')
    if root.exists() and (root.stat().st_uid!=0 or root.stat().st_mode&0o022):raise RuntimeError('Unsafe existing host ownership')
    for relative in (*verified,'.owned','config.json','process.json'):
        checked_destination(root,root/relative)
    if marker_raw!=MARKER.encode(): raise RuntimeError('Host marker mismatch')
    hook_content={}
    if hook_dir is not None:
        for action in ('start','stop'):
            path=hook_dir/('97_daphile_audio_host.'+action)
            content=('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(root/'code/hostctl.py'))+' '+action+' --root '+shlex.quote(str(root))+'\n').encode()
            hook_content[path]=content
    repository_code=None;repository_xml=None
    if repo_base_url:
        for name in ('repository.xml','repository-cgi.json'):
            checked_destination(root,root/name)
        # Execute only the exact repository source already authenticated by
        # the external manifest pin; never reuse a sys.modules/sys.path peer.
        repository_namespace={'__name__':'_verified_native_host_repository_a3',
                              '__file__':str(bundle_dir/'code/repository.py')}
        exec(compile(verified['code/repository.py'],repository_namespace['__file__'],'exec'),repository_namespace)
        repository_xml=repository_namespace['catalog'](json.loads(verified['approvals.json']),repo_base_url)
        template=verified['code/repository.py'].decode()
        if template.count("Path('/__DAPHILE_AUDIO_HOST__')")!=1:raise RuntimeError('Unknown repository CGI contract')
        repository_code=('#!/usr/bin/python3\n'+template.replace("Path('/__DAPHILE_AUDIO_HOST__')",'Path('+repr(str(root))+')')).encode()
    def external_preflight():
        checked_directory(cgi_dir)
        if hook_dir is not None:
            checked_directory(hook_dir)
            for path,content in hook_content.items():
                checked_destination(hook_dir,path)
                hook_raw,hook_state=guarded_read(path)
                if str(path) not in expected_preimages or hook_state!=expected_preimages[str(path)]:
                    raise RuntimeError('Existing host hook differs from expected preimage')
                if hook_raw is not None and hook_raw!=content:raise RuntimeError('Existing host hook differs')
        if repository_code is not None:
            destination=cgi_dir/'AudioPlugins';record=root/'repository-cgi.json'
            checked_destination(cgi_dir,destination)
            bridge_raw,bridge_state=guarded_read(destination)
            record_raw,record_state=guarded_read(record)
            if (str(destination) not in expected_preimages or str(record) not in expected_preimages
                    or bridge_state!=expected_preimages[str(destination)] or record_state!=expected_preimages[str(record)]):
                raise RuntimeError('Repository CGI or ownership record differs from expected preimage')
            if bridge_raw is not None:
                if record_raw is None or hashlib.sha256(bridge_raw).hexdigest()!=json.loads(record_raw)['sha256']:
                    raise RuntimeError('Repository CGI belongs to another component')

    def preflight():
        checked_directory(root)
        for relative in (*verified,'.owned','config.json','process.json'):
            checked_destination(root,root/relative)
        if repository_code is not None:
            for name in ('repository.xml','repository-cgi.json'):checked_destination(root,root/name)
        external_preflight()

    # Complete validation precedes even creation of the recovery stage. The
    # transaction refuses a running host: stopping its broker also invokes
    # audio recovery and cannot be used to manufacture an installable baseline.
    preflight()
    from host_config_transaction_a7 import apply as apply_transaction
    rme_states={str(direct_absolute(rme_state))}
    previous=previous_config
    if type(previous.get('rme_state')) is not str:
        raise RuntimeError('Existing host recovery state is unknown')
    rme_states.add(str(direct_absolute(previous['rme_state'])))
    payloads=[]
    for relative,data in verified.items():
        payloads.append(dict(path=root/relative,data=data,mode=0o644))
    external=[]
    if repository_code is not None:
        destination=cgi_dir/'AudioPlugins';external.append(destination)
        payloads.append(dict(path=destination,data=repository_code,mode=0o755))
        payloads.append(dict(path=root/'repository.xml',data=repository_xml,mode=0o644))
        record=dict(sha256=hashlib.sha256(repository_code).hexdigest(),base_url=repo_base_url)
        payloads.append(dict(path=root/'repository-cgi.json',data=(json.dumps(record,allow_nan=False)+'\n').encode(),mode=0o600))
    # Complete existing config bytes, including ports and unknown fields, stay untouched.
    if hook_dir is not None:
        for path,content in hook_content.items():
            external.append(path)
            payloads.append(dict(path=path,data=content,mode=0o755))
    if guarded_read(root/'config.json')!=(config_raw,config_state):
        raise RuntimeError('Existing native host config changed during read-only plan preparation')
    if guarded_read(root/'.owned')!=(marker_raw,marker_state):
        raise RuntimeError('Existing native host marker changed during plan preparation')
    expected_paths={str(item['path'])for item in payloads}|{str(root/'config.json'),str(root/'.owned')}
    if set(expected_preimages)!=expected_paths:
        raise RuntimeError('Exact complete expected host payload/config/marker preimage set required')
    result=apply_transaction(root,payloads,directories=[root,cgi_dir]+([hook_dir] if hook_dir else []),
                      expected_preimages=expected_preimages,external=external,rme_states=sorted(rme_states),maintenance=maintenance)
    return result
