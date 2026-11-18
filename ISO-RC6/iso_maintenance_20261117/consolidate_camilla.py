"""Consolidate the shipped Camilla package before the audio host starts.

Only package files and the existing repository catalog change. DATA, presets,
plugin preferences and previous approved releases remain in place.
"""
from pathlib import Path
import hashlib,io,json,os,zipfile
import xml.etree.ElementTree as ET

NAME='DaphileCamillaDSP'

def sha(data):return hashlib.sha256(data).hexdigest()

def consolidate(factory,host,plugins,publish,repository_payloads):
    factory,host,plugins=Path(factory),Path(host),Path(plugins)
    shipped=json.loads((factory/'audio-host/approvals.json').read_text())[NAME]
    entry=max(shipped,key=lambda x:tuple(map(int,x['version'].split('.'))))
    archive=(factory/'audio-host/bundles'/entry['archive']).read_bytes()
    if sha(archive)!=entry['sha256']:raise RuntimeError('CAMILLA_FACTORY_ARCHIVE_CHANGED')
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        payload={name:z.read(NAME+'/'+name) for name in entry['files']}
        if any(sha(data)!=entry['files'][name] for name,data in payload.items()):
            raise RuntimeError('CAMILLA_FACTORY_MEMBER_CHANGED')
    if any(Path(n).is_absolute() or '..' in Path(n).parts for n in payload):
        raise RuntimeError('CAMILLA_INVALID_PACKAGE_PATH')
    xml=ET.fromstring(payload['install.xml']);application=xml.find('targetApplication')
    if xml.findtext('version')!=entry['version']:raise RuntimeError('CAMILLA_FACTORY_VERSION_MISMATCH')
    if (entry['minTarget'],entry['maxTarget'])!=(application.findtext('minVersion'),application.findtext('maxVersion')):
        raise RuntimeError('CAMILLA_FACTORY_COMPATIBILITY_MISMATCH')
    if hashlib.sha1(archive).hexdigest()!=entry['sha1']:raise RuntimeError('CAMILLA_FACTORY_REPOSITORY_HASH_MISMATCH')
    destination=plugins/NAME
    approvals_path=host/'approvals.json';approvals=json.loads(approvals_path.read_text())
    previous=approvals.get(NAME,[]);changed=[]
    installed=ET.fromstring((destination/'install.xml').read_bytes()).findtext('version')
    if tuple(map(int,installed.split('.')))>tuple(map(int,entry['version'].split('.'))):
        return dict(version=installed,preserved_newer=True,changed_plugin_files=0,catalog_updated=False)
    for relative,data in payload.items():
        p=destination/relative
        if not p.is_file() or p.is_symlink():raise RuntimeError('CAMILLA_PLUGIN_SOURCE_UNAVAILABLE')
        if p.read_bytes()!=data:changed.append(relative)
    if changed:
        # A resumed update may contain both previous and candidate members.
        # Accept only a complete known package preimage, or that exact mixture.
        old=next((r for r in previous if r.get('sha256')!=entry['sha256'] and all(
            sha((destination/n).read_bytes()) in (r.get('files',{}).get(n),entry['files'][n])
            for n in payload)),None)
        if old is None:raise RuntimeError('CAMILLA_CUSTOM_PLUGIN_SOURCE_PRESERVED')
        backup=host/'module-source-backups'/NAME/old['sha256']
        backup.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(backup,0o700)
        for n in changed:
            p=destination/n;saved=backup/n;data=p.read_bytes()
            if not saved.exists():publish(saved,data,0o600)
        applied=[]
        try:
            for n in changed:
                p=destination/n;st=p.stat();publish(p,payload[n],st.st_mode&0o777,st.st_uid,st.st_gid);applied.append(n)
        except BaseException:
            for n in reversed(applied):
                p=destination/n;st=p.stat();publish(p,(backup/n).read_bytes(),st.st_mode&0o777,st.st_uid,st.st_gid)
            raise
    bundle=host/'bundles'/entry['archive']
    if bundle.exists() and bundle.read_bytes()!=archive:raise RuntimeError('CAMILLA_EXISTING_BUNDLE_PRESERVED')
    if not bundle.exists():publish(bundle,archive,0o644)
    updated=[r for r in previous if r.get('sha256')!=entry['sha256']]+[entry]
    if updated!=previous:
        approvals[NAME]=updated;publish(approvals_path,(json.dumps(approvals,sort_keys=True,indent=2)+'\n').encode(),0o644)
    record=host/'repository-cgi.json'
    if record.exists():
        url=json.loads(record.read_text())['base_url']
        data=repository_payloads(host,url)['repository.xml'];catalog=host/'repository.xml'
        if not catalog.exists() or catalog.read_bytes()!=data:publish(catalog,data,0o644)
    return dict(version=entry['version'],changed_plugin_files=len(changed),catalog_updated=updated!=previous)
