"""Verify all delivery parts and reconstruct the original file atomically."""
from pathlib import Path
import argparse,hashlib,json,os,re,sys,tempfile
BLOCK=1024*1024
def safe_name(name):
    return isinstance(name,str) and name not in ('','.','..') and '/' not in name and '\\' not in name and ':' not in name
def verify(manifest,directory,sink=None):
    assert manifest.get('format')=='DAPHILE_DELIVERY_PARTS_V1'
    assert safe_name(manifest.get('original_name')) and type(manifest.get('size')) is int and manifest['size']>=0
    assert re.fullmatch('[0-9a-f]{64}',manifest.get('sha256',''))
    parts=manifest['parts'];assert isinstance(parts,list) and parts
    assert len({p['name'] for p in parts})==len(parts)
    whole=hashlib.sha256();total=0
    for part in parts:
        name=part['name'];assert safe_name(name) and type(part['size']) is int and 0<=part['size']<=96*1024*1024
        assert re.fullmatch('[0-9a-f]{64}',part.get('sha256',''))
        path=directory/name;assert path.is_file() and not path.is_symlink(),'Missing/nonregular part: '+name
        assert path.stat().st_size==part['size'],'Part size mismatch: '+name
        digest=hashlib.sha256();count=0
        with path.open('rb') as source:
            while block:=source.read(BLOCK):
                count+=len(block);digest.update(block);whole.update(block)
                if sink is not None:sink.write(block)
        assert count==part['size'] and digest.hexdigest()==part['sha256'],'Corrupted part: '+name
        total+=count
    assert total==manifest['size'] and whole.hexdigest()==manifest['sha256'],'Original size/SHA256 mismatch'
    return {'status':'PASS','bytes':total,'sha256':whole.hexdigest(),'parts':len(parts)}
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',type=Path,default=Path(__file__).resolve().parent/'MANIFEST_PARTI.json');parser.add_argument('--output',type=Path);parser.add_argument('--verify-only',action='store_true');args=parser.parse_args()
    manifest_path=args.manifest.resolve();manifest=json.loads(manifest_path.read_text(encoding='utf-8'));folder=manifest_path.parent
    if args.verify_only:print(json.dumps(verify(manifest,folder)));return 0
    assert safe_name(manifest.get('original_name')),'Invalid original filename'
    output=args.output or folder/manifest['original_name'];output=output.absolute()
    assert not os.path.lexists(output),'Output already exists; preserve it and choose another destination'
    assert output.parent.is_dir(),'Output parent does not exist'
    fd,temporary=tempfile.mkstemp(prefix='.'+output.name+'.partial-',dir=output.parent)
    try:
        with os.fdopen(fd,'wb') as sink:result=verify(manifest,folder,sink);sink.flush();os.fsync(sink.fileno())
        # Same-filesystem hardlink publishes a fully written file without ever
        # replacing an existing destination, including a concurrent creation.
        os.link(temporary,output);os.unlink(temporary)
        if os.name=='posix':
            parent=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(parent)
            finally:os.close(parent)
        print(json.dumps(dict(result,output=str(output))));return 0
    finally:Path(temporary).unlink(missing_ok=True)
if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,AssertionError,TypeError) as error:
        print('REASSEMBLY_FAILED: '+str(error),file=sys.stderr);raise SystemExit(1)
