import hashlib,json,pathlib,re,subprocess,sys
stage, rc5, python_rc5 = map(pathlib.Path,sys.argv[1:4])
data=json.load(sys.stdin)
def read(flag,p):
 return subprocess.check_output(['readelf','-W',flag,str(p)],text=True)
def exports(p):
 result=set()
 for line in read('--dyn-syms',p).splitlines():
  f=line.split()
  if len(f)>=8 and f[4] in ('GLOBAL','WEAK','UNIQUE') and f[6]!='UND':
   name=f[7]
   if '@@' in name: result.update((name.replace('@@','@'),name.split('@@')[0]))
   else: result.add(name)
 return result
lib=stage/'usr/lib64/libcurl.so.4'
nm=read('-s',lib)
assert re.search(r'LOCAL\s+DEFAULT\s+\d+\s+ngtcp2_conn_client_new_versioned',nm)
assert re.search(r'LOCAL\s+DEFAULT\s+\d+\s+ngtcp2_crypto_ossl_ctx_new',nm)
pub=exports(lib)
assert not any(t.startswith(('ngtcp2_', 'nghttp3_')) for t in pub)
reference=pathlib.Path('/usr/lib/x86_64-linux-gnu/libcurl.so.4')
baseline={n.split('@')[0] for n in exports(reference) if n.startswith('curl_')}
missing=baseline-pub
assert not missing,missing
assert re.search(r'\(SONAME\).*\[libcurl\.so\.4\]',read('-d',lib))
providers={'libcurl.so.4':lib}
glibc={'libc.so.6','libm.so.6','ld-linux-x86-64.so.2','libdl.so.2','libpthread.so.0','librt.so.1'}
result={}
for name,d in data['elf_files'].items():
 emap={}
 version_nodes={}
 for needed in d['needed']:
  if needed in glibc: continue
  assert not needed.startswith('libngtcp2'),needed
  path=providers.get(needed)
  if path is None:
   path=next(root/needed for root in (rc5,python_rc5) if (root/needed).is_file())
  direct=read('-d',path)
  assert f'[{needed}]' in direct,needed
  emap[needed]=exports(path)
  version_nodes[needed]={'file':path.resolve().name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
   'needed':re.findall(r'\(NEEDED\).*?\[([^]]+)\]',direct),
   'required_versions':d['version_needs_by_library'].get(needed,[])}
 mappings={}
 unknown_weaks=[]
 for item in d['undefined_dynamic_symbols']:
  symbol=item['name']
  if '@GLIBC_' in symbol: continue
  hits=[p for p, names in emap.items() if symbol in names]
  if not hits:
   assert item['binding']=='WEAK',(name,symbol)
   unknown_weaks.append(symbol)
  else: mappings[symbol]=hits
 result[name]={'direct_providers':version_nodes,'non_glibc_symbol_providers':mappings,'optional_weak_unresolved':unknown_weaks}
print(json.dumps({'libcurl_soname':'libcurl.so.4','embedded_ngtcp2_symbols_local':True,
 'curl_api_reference':'Debian Cloud libcurl.so.4 exported curl_* API subset; RC5 consumer runtime test pending',
 'public_reference_apis_preserved':len(baseline),'audit':result},indent=2))
