"""Exercise the RC6 consolidation routine in temporary directories only."""
import argparse, hashlib, json, sys, tempfile
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree


def entry(archive):
    raw=archive.read_bytes()
    with ZipFile(archive) as z:
        files={name.partition('/')[2]:hashlib.sha256(z.read(name)).hexdigest() for name in z.namelist()}
        xml=ElementTree.fromstring(z.read('DaphileCamillaDSP/install.xml'))
    return dict(name='DaphileCamillaDSP',archive=archive.name,version=xml.findtext('version'),
                sha256=hashlib.sha256(raw).hexdigest(),sha1=hashlib.sha1(raw).hexdigest(),
                bytes=len(raw),minTarget=xml.findtext('targetApplication/minVersion'),
                maxTarget=xml.findtext('targetApplication/maxVersion'),files=files)

def run(host_source,base,new,approval):
    sys.path.insert(0,str(host_source.resolve()))
    from consolidate_camilla import consolidate
    previous,candidate=entry(base),entry(new)
    manifest=json.loads(approval.read_text())
    assert all(candidate[key]==value for key,value in manifest.items())
    assert set(candidate['files'])==set(previous['files'])
    def publish(path,data,mode,*owner):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);path.chmod(mode)
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); factory=root/'factory';host=root/'host';plugins=root/'plugins'
        publish(factory/'audio-host'/'approvals.json',json.dumps({'DaphileCamillaDSP':[candidate]}).encode(),0o644)
        publish(factory/'audio-host'/'bundles'/new.name,new.read_bytes(),0o644)
        publish(host/'approvals.json',json.dumps({'DaphileCamillaDSP':[previous]}).encode(),0o644)
        with ZipFile(base) as z:z.extractall(plugins)
        run_once=lambda:consolidate(factory,host,plugins,publish,None)
        first=run_once();second=run_once()
        for rel,sha in candidate['files'].items():
            assert hashlib.sha256((plugins/'DaphileCamillaDSP'/rel).read_bytes()).hexdigest()==sha
            if sha!=previous['files'][rel]:
                old=host/'module-source-backups'/'DaphileCamillaDSP'/previous['sha256']/rel
                assert hashlib.sha256(old.read_bytes()).hexdigest()==previous['files'][rel]
        assert second['changed_plugin_files']==0
        return {'first':first,'second':second,'files_verified':len(candidate['files'])}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('host-source','base','new','approval'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args(); print(json.dumps(run(args.host_source,args.base,args.new,args.approval)))
