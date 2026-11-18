"""Optional host's additive Power launcher; never part of the base ISO."""
import hashlib,json,os,pathlib,shlex
from common import atomic

def replace(target,data):
 """Replace only a regular owned CGI, retaining its executable mode."""
 if target.is_symlink() or not target.is_file():raise RuntimeError('Indirect Settings launcher target')
 mode=target.stat().st_mode&0o777
 temp=target.with_name('.'+target.name+'.audio-launcher.new')
 fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
  temp.chmod(mode);os.replace(temp,target)
 finally:
  temp.unlink(missing_ok=True)

ANCHOR='\t\techo "<h3 id=power>Power</h3><div><table>"\n'
MARKER='data-daphile-audio-launcher'

def fragment(root):
 flag=shlex.quote(str(pathlib.Path(root)/'menu.enabled'))
 return '\t\t[ -f '+flag+" ] && echo '<tr "+MARKER+"><td class=cfg_label>Plugin audio:</td><td><a class=button href=\"/cgi-bin/AudioPlugins/launcher\" target=\"_blank\" rel=\"noopener\">Apri plugin audio</a></td></tr>'\n"

def _ensure_power(root,cgi):
 root=pathlib.Path(root);target=pathlib.Path(cgi)/'Settings'
 if not target.exists():return False
 if target.is_symlink():raise RuntimeError('Indirect Settings launcher target')
 original=target.read_bytes();text=original.decode('utf8');addition=fragment(root)
 if addition in text:
  if text.count(addition)!=1:raise RuntimeError('Repeated optional launcher')
  return True
 if MARKER in text or text.count(ANCHOR)!=1:raise RuntimeError('Unknown Power launcher contract')
 before=hashlib.sha256(original).hexdigest();backup=root/'menu-backups'
 if backup.is_symlink():raise RuntimeError('Indirect launcher backup directory')
 backup.mkdir(mode=0o700,exist_ok=True)
 saved=backup/(before+'.Settings')
 if saved.is_symlink():raise RuntimeError('Indirect launcher backup file')
 if saved.exists() and saved.read_bytes()!=original:raise RuntimeError('Launcher backup mismatch')
 saved.write_bytes(original);saved.chmod(0o600)
 data=text.replace(ANCHOR,ANCHOR+addition).encode('utf8');replace(target,data)
 atomic(root/'menu-settings.json',{'original_sha256':before,'patched_sha256':hashlib.sha256(data).hexdigest()})
 return True

def ensure_sidebar(root,cgi):
 from sidebar import MARKER as marker,TAG,configure
 root=pathlib.Path(root);target=pathlib.Path(cgi)/'pageheader'
 configure(root)
 if not target.exists():return False
 if target.is_symlink() or not target.is_file():raise RuntimeError('Indirect sidebar header')
 original=target.read_bytes();text=original.decode('utf8')
 if TAG in text:
  if text.count(TAG)!=1:raise RuntimeError('Repeated sidebar script')
  return True
 if marker in text or text.count('</head>\n')!=1:raise RuntimeError('Unknown sidebar header contract')
 before=hashlib.sha256(original).hexdigest();backup=root/'menu-backups'
 if backup.is_symlink():raise RuntimeError('Indirect sidebar backup directory')
 backup.mkdir(mode=0o700,exist_ok=True);saved=backup/(before+'.pageheader')
 if saved.is_symlink() or (saved.exists() and saved.read_bytes()!=original):raise RuntimeError('Sidebar backup mismatch')
 saved.write_bytes(original);saved.chmod(0o600)
 data=text.replace('</head>\n',TAG+'</head>\n').encode('utf8');replace(target,data)
 atomic(root/'menu-pageheader.json',{'original_sha256':before,'patched_sha256':hashlib.sha256(data).hexdigest()})
 return True

def ensure(root,cgi):
 power=_ensure_power(root,cgi);sidebar=ensure_sidebar(root,cgi)
 return power or sidebar

def remove(root,cgi):
 from sidebar import TAG
 target=pathlib.Path(cgi)/'Settings'
 if not target.exists():return
 if target.is_symlink():raise RuntimeError('Indirect Settings launcher target')
 text=target.read_text(encoding='utf8');addition=fragment(root)
 if text.count(addition)!=1:raise RuntimeError('Owned launcher fragment unavailable')
 replace(target,text.replace(addition,'').encode('utf8'))
 header=pathlib.Path(cgi)/'pageheader'
 if header.exists():
  if header.is_symlink():raise RuntimeError('Indirect sidebar header')
  text=header.read_text(encoding='utf8')
  if TAG in text:
   if text.count(TAG)!=1:raise RuntimeError('Repeated sidebar script')
   replace(header,text.replace(TAG,'').encode('utf8'))

def publish(root,modules):
 root=pathlib.Path(root);active=[name for name in ('DaphileRME','DaphileCamillaDSP') if modules.get(name,{}).get('running')]
 atomic(root/'launcher.json',{'modules':active});(root/'launcher.json').chmod(0o644)
 flag=root/'menu.enabled'
 if active:
  flag.write_text('enabled\n');flag.chmod(0o644)
 else:flag.unlink(missing_ok=True)

def refresh(root,modules):
 """A failed convenience link must never stop either audio controller."""
 try:publish(root,modules)
 except Exception as exc:atomic(pathlib.Path(root)/'menu-error.json',{'error':str(exc)})
