import hashlib,json,pathlib,re,subprocess,sys
stage=pathlib.Path(sys.argv[1]); dirs=[pathlib.Path(x) for x in sys.argv[2:]]
def read(opt,p): return subprocess.check_output(['readelf','-W',opt,str(p)],text=True)
def symbols(p):
 out=set()
 for row in read('--dyn-syms',p).splitlines():
  f=row.split()
  if len(f)>7 and f[4] in ('GLOBAL','WEAK','UNIQUE') and f[6]!='UND':
   sym=f[7];out.add(sym.replace('@@','@'))
   if '@@' in sym:out.add(sym.split('@@')[0])
 return out
local={}
files=[]
for p in sorted((stage/'usr').rglob('*')):
 if p.is_symlink() or not p.is_file():continue
 with p.open('rb') as f:
  if f.read(4)!=b'\x7fELF':continue
 d=read('-d',p)
 soname=re.findall(r'\(SONAME\).*\[([^]]+)\]',d)
 local.update({n:p for n in soname})
 files.append((p,d,soname))
glibc={'libc.so.6','libm.so.6','libpthread.so.0','librt.so.1','libdl.so.2','ld-linux-x86-64.so.2','libutil.so.1','libresolv.so.2'}
cache={}
result={}
problems=[]
for p,d,soname in files:
 name=p.relative_to(stage).as_posix()
 needed=re.findall(r'\(NEEDED\).*\[([^]]+)\]',d)
 path=re.findall(r'\((?:RPATH|RUNPATH)\).*\[([^]]+)\]',d)
 if any('/workspace' in n or '/tmp' in n for n in path):problems.append([name,'build RUNPATH',path])
 assert 'Advanced Micro Devices X86-64' in read('-h',p)
 if re.search(r'ISA needed:.*x86-64-v[234]',read('-n',p)):problems.append([name,'nonbaseline required ISA'])
 vs=read('-V',p).partition('Version needs section')[2]
 match=re.findall(r'Name: (GLIBC_[\d.]+)',vs)
 key=lambda x:tuple(map(int,x[6:].split('.')))
 if any(key(v)>(2,40) for v in match) or 'GLIBC_PRIVATE' in vs:problems.append([name,sorted(set(match),key=key)])
 providers={}
 global_perl=[n for n in local if n.startswith('libperl.so') and n not in soname]
 for n in set(needed+global_perl)-glibc:
  q=local.get(n) or next((x/n for x in dirs if (x/n).is_file()),None)
  if q is None:
   problems.append([name,'missing provider',n]);continue
  if q not in cache:cache[q]=symbols(q)
  providers[n]=q
 imports=[]
 for row in read('--dyn-syms',p).splitlines():
  m=re.search(r'\s(GLOBAL|WEAK)\s+\S+\s+UND\s+(\S+)',row)
  if m:
   sym=m[2]
   candidates=sorted(n for n,q in providers.items() if sym in cache[q])
   if not candidates and '@GLIBC_' not in sym and m[1]=='GLOBAL':problems.append([name,'unresolved',sym])
   imports.append({'symbol':sym,'binding':m[1],'non_glibc_providers':candidates})
 result[name]={'needed':needed,'soname':soname,'rpath_or_runpath':path,'max_glibc':max(match,key=key,default=None),
               'version_needs':vs.strip(),'imports':imports}
print(json.dumps({'elf_count':len(result),'elf':result,'problems':problems},indent=2))
sys.exit(bool(problems) or not result)
