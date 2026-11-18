"""Plan/split one delivery file; only explicit --split writes workspace output."""
from pathlib import Path
import argparse,hashlib,json,os,sys,tempfile

WORKSPACE=Path(__file__).resolve().parent.parent
PART_BYTES=96*1024*1024
BLOCK=1024*1024
MANIFEST='MANIFEST_PARTI.json'

# Standalone stdlib reassembler, shipped beside the parts and their manifest.
REASSEMBLER=r'''"""Verify all delivery parts and reconstruct the original file atomically."""
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
'''

def absolute(path,label):
    assert path.is_absolute(),label+' must be an absolute path'
    return path.resolve()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',type=Path,required=True);parser.add_argument('--outdir',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--plan',action='store_true');mode.add_argument('--split',action='store_true');args=parser.parse_args()
    source=absolute(args.input,'--input');destination=absolute(args.outdir,'--outdir')
    assert source.is_file() and not args.input.is_symlink(),'Input must be a regular file'
    assert destination.is_relative_to(WORKSPACE.resolve()) and destination!=WORKSPACE.resolve(),'Output must be a workspace subdirectory'
    original=source.stat();size=original.st_size;count=max(1,(size+PART_BYTES-1)//PART_BYTES)
    plan=dict(mode='SPLIT' if args.split else 'PLAN_READ_ONLY',input=str(source),outdir=str(destination),bytes=size,part_bytes=PART_BYTES,parts=count)
    if not args.split:print(json.dumps(plan,indent=2));return 0
    if destination.exists():assert destination.is_dir() and not any(destination.iterdir()),'Output is not empty; preserve and reconcile existing files'
    else:destination.mkdir(parents=True)
    parts=[];whole=hashlib.sha256();total=0
    with source.open('rb') as stream:
        for ordinal in range(1,count+1):
            name=source.name+'.part'+str(ordinal).zfill(4);path=destination/name;part=hashlib.sha256();written=0
            with path.open('xb') as target:
                while written<PART_BYTES:
                    block=stream.read(min(BLOCK,PART_BYTES-written))
                    if not block:break
                    target.write(block);part.update(block);whole.update(block);written+=len(block);total+=len(block)
                target.flush();os.fsync(target.fileno())
            parts.append(dict(name=name,size=written,sha256=part.hexdigest()))
        assert not stream.read(1) and total==size,'Input size changed during splitting'
    after=source.stat();assert (after.st_size,after.st_mtime_ns)==(original.st_size,original.st_mtime_ns),'Input changed during splitting'
    manifest=dict(format='DAPHILE_DELIVERY_PARTS_V1',original_name=source.name,size=size,sha256=whole.hexdigest(),part_bytes=PART_BYTES,parts=parts)
    namespace={'__name__':'delivery_readback','__file__':str(destination/'RICOMPONI.py')};exec(compile(REASSEMBLER,'RICOMPONI.py','exec'),namespace)
    # Full streamed reassembly readback, without allocating a second ISO file.
    readback=namespace['verify'](manifest,destination)
    with (destination/MANIFEST).open('x',encoding='utf-8') as f:json.dump(manifest,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    with (destination/'RICOMPONI.py').open('x',encoding='utf-8',newline='\n') as f:f.write(REASSEMBLER);f.flush();os.fsync(f.fileno())
    print(json.dumps(dict(plan,manifest=str(destination/MANIFEST),sha256=manifest['sha256'],streamed_readback=readback),indent=2));return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,KeyError,AssertionError,TypeError) as error:
        print('DELIVERY_PREPARATION_FAILED: '+str(error),file=sys.stderr);raise SystemExit(1)
