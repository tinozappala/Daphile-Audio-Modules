import concurrent.futures,hashlib,json,pathlib,subprocess,urllib.parse
def fetch(x):
 p=pathlib.Path(x['file'])
 assert p.name==str(p) and urllib.parse.urlsplit(x['url']).hostname in {'curl.se','github.com','zlib.net'}
 if not p.exists(): subprocess.run(['curl','--proto','=https','--proto-redir','=https','-fLsS','--max-time','35','-o',str(p),x['url']],check=True)
 assert p.stat().st_size==x['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==x['sha256'],p
 subprocess.run(['tar','-xf',str(p)],check=True)
 return p
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as q:
 for p in q.map(fetch,json.load(open('inputs.json'))['matching_sources']):print(p)
