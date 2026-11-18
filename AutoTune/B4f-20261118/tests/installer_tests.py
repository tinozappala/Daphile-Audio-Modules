"""Exercise the actual updater/rollback flow in an isolated temporary tree."""
from pathlib import Path
import argparse,contextlib,hashlib,importlib.util,json,sys,tempfile
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--installer',type=Path,default=Path(__file__).parent/'install_b4f.py');a=p.parse_args()
spec=importlib.util.spec_from_file_location('b4f_update_fixture',a.installer);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
sha=lambda raw:hashlib.sha256(raw).hexdigest();commands=[]
with tempfile.TemporaryDirectory(prefix='b4f-installer-') as d:
 root=Path(d);package=root/'package';package.mkdir();installed=root/'installed';backups=root/'backups';m.HERE=package
 rows=[]
 for i in range(2):
  name='opt/daphile-autotune/lib/file'+str(i)+'.py';before=b'print("before")\n';after=b'print("after")\n'
  for base,raw in ((installed,before),(package/'payload',after)):
   dest=base/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
  rows.append(dict(path=name,sha256=sha(after),mode=0o644,accepted_before_sha256=[sha(before)]))
 (package/'INSTALL_MANIFEST.json').write_text(json.dumps(rows))
 def mapped(value):
  text=str(value);prefix='/mnt/hdd/system/autotune-updates'
  return backups/text[len(prefix):].lstrip('/') if text.startswith(prefix) else Path(value)
 def command(action):commands.append(action)
 with patch.object(m,'Path',mapped),patch.object(m,'target',lambda name:installed/name),patch.object(m,'exclusion',contextlib.nullcontext),patch.object(m,'command',command),patch.object(m,'status',lambda:dict(enabled=True)):
  def run(*args):
   with patch.object(sys,'argv',['install_b4f.py',*args]):m.main()
  run('--install');assert commands==['stop','start']
  assert all(sha((installed/r['path']).read_bytes())==r['sha256'] for r in rows)
  run('--verify');run('--install');assert commands==['stop','start'],'Second install restarted services'
  backup=next(backups.iterdir());run('--rollback',str(backup));assert all((installed/r['path']).read_bytes()==b'print("before")\n' for r in rows)
  starts=[0]
  def failing_start(action):
   commands.append(action)
   if action=='start':
    starts[0]+=1
    if starts[0]==1:raise RuntimeError('fixture failed GUI start')
  with patch.object(m,'command',failing_start):
   try:run('--install')
   except RuntimeError as error:assert 'fixture failed' in str(error)
   else:raise AssertionError('GUI failure was not propagated')
  assert all((installed/r['path']).read_bytes()==b'print("before")\n' for r in rows),'Incomplete automatic rollback'
  (installed/rows[0]['path']).write_bytes(b'user changed source');count=len(commands)
  try:run('--install')
  except RuntimeError as error:assert 'Modified source preserved' in str(error)
  else:raise AssertionError('Modified user source overwritten')
  assert len(commands)==count and (installed/rows[0]['path']).read_bytes()==b'user changed source'
 print(json.dumps(dict(status='PASS',isolated_linux_filesystem=True,cases=['install','readback','idempotent second install','explicit rollback','automatic rollback after GUI failure','concurrent source preserved before effects'],native_service_commands=0)))
