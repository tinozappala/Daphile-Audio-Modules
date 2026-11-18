"""Select verified runtime files from one exact public build; never a live root."""
from pathlib import Path,PurePosixPath
import argparse,base64,hashlib,json,tarfile
HERE=Path(__file__).resolve().parent
SPECS={
 'ffmpeg':('6.1.6','ffmpeg-6.1.6-daphile-x86_64-cloud.tar.gz','fbc8dbfe5f4f26dd9c504fbeddd08f7d47bccc39c2c98fb8747216b365723776','FFMPEG_616_NATIVE.json'),
 'openssh':('10.6p2','openssh-10.6p2-daphile-x86_64-cloud.tar.gz','3652bc69143f2ebfb108f887164066738b5f9e7447ba0afce47f0dc66c47074d','OPENSSH_106P2_NATIVE.json'),
 'perl':('5.40.5','perl-5.40.5-daphile-x86_64-cloud-r3.tar.gz','d904e309ef6546cff6b252215cfc922dcaf6f8ba8b8e2a9357004e29fa6261e8','PERL_5405_NATIVE.json')}
def main():
 p=argparse.ArgumentParser();p.add_argument('component',choices=SPECS);a=p.parse_args()
 version,archive,expected,evidence=SPECS[a.component];source=HERE/'incoming'/archive
 assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
 native=json.loads((HERE/'builder'/evidence).read_text())
 assert native['status']==('PASS_NATIVE_RC5_FFMPEG616_CLI_LIBRARIES_CODECS' if a.component=='ffmpeg' else 'PASS') and native['artifact_sha256']==expected
 assert not native.get('cleanup',{}).get('errors')
 baseline=json.loads((HERE.parent/'DAPHILE_RAM_A7_RC5_20261117/RC5_FULL_FILESYSTEM_TABLE.json').read_text())
 target=HERE/'components'/(a.component+'-'+version);objects=target/'objects';objects.mkdir(parents=True,exist_ok=True)
 value=dict(version=version,native_verification='PASS',artifact_sha256=expected,native_evidence='builder/'+evidence,
            global_installation='PENDING_FINAL_ISO_BOOT',files=[],symlinks=[],license_files=[])
 with tarfile.open(source) as t:
  members={PurePosixPath(m.name).as_posix():m for m in t if m.name not in ('.','./')}
  for name,m in members.items():
   assert not name.startswith('/') and '..' not in PurePosixPath(name).parts
   if m.isdir() or not (name.startswith('usr/') or name.startswith('licenses/')):continue
   if m.issym():
    assert name.startswith('usr/')
    value['symlinks'].append(dict(destination=name,target=m.linkname));continue
   if m.islnk():
    # Follow the established Gentoo versioned-command alias convention.
    assert a.component=='perl' and name=='usr/bin/perl5.40.5' and PurePosixPath(m.linkname).as_posix()=='usr/bin/perl'
    value['symlinks'].append(dict(destination=name,target='perl'));continue
   assert m.isfile() and not m.mode&0o7000
   content=t.extractfile(m).read();sha=hashlib.sha256(content).hexdigest();obj=objects/sha
   if obj.exists():assert obj.read_bytes()==content
   else:obj.write_bytes(content)
   destination=name if name.startswith('usr/') else 'usr/local/share/daphile-maintenance/'+a.component+'/'+name
   mode=(baseline.get(name,{}).get('mode',m.mode&0o777) if a.component=='openssh' else m.mode&0o777)
   item=dict(source=obj.relative_to(HERE).as_posix(),destination=destination,mode=mode,sha256=sha)
   value['files' if name.startswith('usr/') else 'license_files'].append(item)
 if a.component=='ffmpeg':
  assert len(value['files'])==9 and len(value['symlinks'])==14
  value['remove_files']=['usr/lib64/libavformat.so.60.16.100']
 elif a.component=='openssh':assert len(value['files'])==14 and not value['symlinks']
 elif a.component=='perl':
  value['replace_trees']=['usr/lib64/perl5/5.40']
  assert sum(x['destination']=='usr/lib64/libperl.so.5.40.2' for x in value['files'])==1
  assert len(value['symlinks'])==6
 selection=HERE/'COMPONENTI_VERIFICATI.json';all_components=json.loads(selection.read_text());all_components[a.component]=value
 selection.write_text(json.dumps(all_components,indent=2)+'\n')
 print(json.dumps(dict(component=a.component,files=len(value['files']),links=len(value['symlinks']),licenses=len(value['license_files']),artifact_sha256=expected)))
if __name__=='__main__':main()
