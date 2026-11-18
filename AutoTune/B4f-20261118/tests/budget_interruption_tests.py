"""Cancellation/interruption must not masquerade as elapsed 30-minute budget."""
from pathlib import Path
import argparse,ast,contextlib,copy,json,os,sys,tempfile,time
from unittest.mock import patch
p=argparse.ArgumentParser();p.add_argument('--lib',type=Path,default=Path(__file__).parent/'working/lib');a=p.parse_args();sys.path.insert(0,str(a.lib.resolve()))
import calibration_budget as budget
with patch.object(budget,'boot_id',lambda:'fixture-boot'),patch.object(budget,'pid_namespace',lambda:'fixture-ns'):
 timing=budget.record();deadline=budget.Deadline(timing)
 for hard in (False,True):
  deadline.hard=hard
  try:deadline.interrupted()
  except budget.BudgetExpired as error:assert 'interrupted' in str(error) and type(error) is budget.BudgetExpired
  else:raise AssertionError('Interruption swallowed')
  try:deadline.alarm()
  except budget.BudgetExpired as error:assert type(error) is (budget.HardDeadline if hard else budget.BudgetExpired)
  else:raise AssertionError('Deadline swallowed')
 tree=ast.parse((a.lib/'autotune_v2/budget_guard.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='expired_result')
 env=dict(Path=Path,time=time,os=os,budget_api=budget,budget_fields=lambda value:{key:value[key] for key in budget.KEYS})
 exec(compile(ast.Module(body=[node],type_ignores=[]),'budget_guard.py','exec'),env)
 class Engine:
  locked=staticmethod(contextlib.nullcontext)
  load=staticmethod(lambda path:json.loads(path.read_text()))
  atomic=staticmethod(lambda path,value:path.write_text(json.dumps(value)))
  start_ticks=staticmethod(lambda pid:'birth')
  boot_id=staticmethod(lambda:'fixture-boot')
 with tempfile.TemporaryDirectory() as d:
  root=Path(d)
  for expired in (False,True):
   t=copy.deepcopy(timing)
   if expired:
    for key in ('calibration_started','work_deadline','calibration_deadline','calibration_monotonic_started','work_monotonic_deadline','calibration_monotonic_deadline'):t[key]-=1801
   record=dict(t,kind='automatic-configuration-v2',owner_pid=os.getpid(),owner_start='birth',boot_id='fixture-boot')
   (root/'automatic.json').write_text(json.dumps(record))
   result=env['expired_result'](root,t,budget.BudgetExpired('interrupted'),Engine,lambda value:value)
   assert result['status']==('timed-out-recovery-required' if expired else 'recovery-required')
 print(dict(status='PASS',interrupt_and_deadline_cases=6))
