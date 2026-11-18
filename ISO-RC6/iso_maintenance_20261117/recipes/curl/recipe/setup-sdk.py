import hashlib,json,pathlib,zipfile,stat,subprocess
p=pathlib.Path('CURL_RC5_LINK_SDK.zip')
assert p.stat().st_size==291998 and hashlib.sha256(p.read_bytes()).hexdigest()=='9597926939c84c1778d15fd05a725bd023114934583c6bda962fa99328f0d4e0'
with zipfile.ZipFile(p) as z:
 m=json.loads(z.read('MANIFEST.json')); root=pathlib.Path('rc5'); (root/'lib').mkdir(parents=True,exist_ok=True)
 assert len(z.namelist())==13 and set(z.namelist())==set(m['files'])|set(m['relative_symlinks'])|{'MANIFEST.json'}
 for name,digest in m['files'].items():
  assert pathlib.PurePosixPath(name).parent==pathlib.PurePosixPath('lib')
  data=z.read(name); assert data.startswith(b'\x7fELF') and hashlib.sha256(data).hexdigest()==digest
  f=root/name; assert not f.is_symlink(); f.write_bytes(data)
 for name,target in m['relative_symlinks'].items():
  assert pathlib.PurePosixPath(name).parent==pathlib.PurePosixPath('lib') and 'lib/'+target in m['files']
  assert z.read(name).decode()==target and stat.S_ISLNK(z.getinfo(name).external_attr>>16)
  f=root/name
  if f.exists(): assert f.is_symlink() and str(f.readlink())==target
  else: f.symlink_to(target)
 (root/'MANIFEST.json').write_bytes(z.read('MANIFEST.json'))
