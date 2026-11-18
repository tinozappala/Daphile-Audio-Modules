import json,pathlib,subprocess,hashlib,sys,urllib.parse
s=json.load(open('input.json'))['source']
p=pathlib.Path(s['file'])
assert p.name==str(p) and urllib.parse.urlsplit(s['url']).hostname in ('www.cpan.org','cdn.openbsd.org','ffmpeg.org')
subprocess.run(['curl','-fLSs','--proto','=https','--proto-redir','=https','--max-time','60','-o',str(p),s['url']],check=True)
assert p.stat().st_size==s['bytes'] and hashlib.sha256(p.read_bytes()).hexdigest()==s['sha256']
subprocess.run(['tar','-xf',p],check=True)
assert hashlib.sha256(pathlib.Path(s['license_member']).read_bytes()).hexdigest()==s['license_sha256']
print(s['file'],s['bytes'],s['sha256'])
