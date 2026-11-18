"""Targeted GUI/cost/bootstrap regressions; no live playback or settings."""
from pathlib import Path
import argparse,ast,functools,json,sys,tempfile,types
from unittest.mock import patch
parser=argparse.ArgumentParser();parser.add_argument('--lib',type=Path,default=Path(__file__).parent/'working/lib');a=parser.parse_args();checks={}
def functions(path,names,namespace):
 tree=ast.parse(path.read_text(encoding='utf8'))
 body=[x for x in tree.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name in names]
 assert len(body)==len(names)
 exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),namespace)
g=dict(json=json,Path=Path,sanitize=lambda value:value,cancelled_result=lambda *args:dict(status='cancelled'))
functions(a.lib/'gui.py',{'completed_job_result','prune_completed_jobs'},g)
with tempfile.TemporaryDirectory() as temp:
 root=Path(temp);p=root/'gui-job-test.json';p.write_text('')
 error=p.with_suffix('.err');error.write_text(json.dumps(dict(status='failed',reason='LMS server unavailable: token=private123 http://name:secret456@host/')))
 result=g['completed_job_result'](p,root,types.SimpleNamespace(returncode=1,pid=123),{'owner_start':'1'})
 checks['structured_failure_is_visible']=result['status']=='failed' and 'LMS server unavailable' in result['message']
 checks['secrets_not_echoed']='private123' not in json.dumps(result) and 'secret456' not in json.dumps(result)
 error.write_text('x'*20000)
 checks['unbounded_error_not_echoed']='x'*100 not in json.dumps(g['completed_job_result'](p,root,types.SimpleNamespace(returncode=1,pid=123),{'owner_start':'1'}))
 p.write_text(json.dumps(dict(status='blocked',message='Stop required',player_commands=0)))
 checks['structured_blocked_result_preserved']=g['completed_job_result'](p,root,types.SimpleNamespace(returncode=1,pid=123),{'owner_start':'1'})['player_commands']==0
 workers={};metadata={}
 for i in range(1003):
  active=i>=1000;proc=types.SimpleNamespace(poll=(lambda:None) if active else (lambda:0));workers[str(i)]=(proc,p);metadata[str(i)]={'started':i}
 g['prune_completed_jobs'](workers,metadata)
 checks['status_history_bounded_with_all_running_preserved']=len(workers)==35 and all(str(i) in workers for i in range(1000,1003)) and len(metadata)==35
 calls=[]
 def run(*args,**kwargs):calls.append(args);return types.SimpleNamespace(stdout='Squeezelite vTEST',stderr='')
 d=dict(functools=functools,Path=Path,subprocess=types.SimpleNamespace(run=run))
 functions(a.lib/'detection.py',{'_executable_help','executable_help'},d)
 binary=root/'binary';binary.write_bytes(b'one')
 for _ in range(25):d['executable_help'](str(binary))
 checks['unchanged_binary_help_executed_once']=len(calls)==1
 binary.write_bytes(b'new executable')
 d['executable_help'](str(binary));checks['replaced_binary_refreshes_help']=len(calls)==2
import importlib.util
spec=importlib.util.spec_from_file_location('host_fixture',a.lib/'host_identity.py');host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
dmi='/sys/class/dmi/id/product_uuid';machine='/etc/machine-id';boot='/proc/sys/kernel/random/boot_id'
checks['board_host_binding_unchanged']=host.current(lambda path:'87654321-4321-4321-4321-cba987654321' if path==dmi else None)=='87654321-4321-4321-4321-cba987654321'
answer=host.info(lambda path:{machine:'a123456789abcdef0123456789abcdef'}.get(path))
checks['no_dmi_machine_id_supported']=answer['persistent'] and answer['source']=='machine-id'
answer=host.info(lambda path:{dmi:'00000000-0000-0000-0000-000000000000',boot:'01234567-1234-1234-1234-123456789abc'}.get(path))
checks['boot_only_binding_is_explicit']=answer['source']=='boot-id' and answer['persistent'] is False
checks['placeholder_dmi_does_not_bind_different_hosts']=all(host.info(lambda path,bad=bad:{dmi:bad,machine:'a123456789abcdef0123456789abcdef'}.get(path))['source']=='machine-id' for bad in ('Unknown','Not Specified','ffffffff-ffff-ffff-ffff-ffffffffffff','00000000-0000-0000-0000-000000000000'))
caps=dict(Path=Path,time=types.SimpleNamespace(monotonic=lambda:1),_LMS_CAPABILITY_CACHE=None)
calls=[]
def capability_rpc(mac,command):calls.append(command);return {'_can':1}
caps['player']=types.SimpleNamespace(rpc=capability_rpc)
functions(a.lib/'quiet_playback.py',{'_lms_instance','qualified_lms'},caps)
caps['_lms_instance']=lambda:'server-A';caps['qualified_lms']();caps['qualified_lms']()
checks['lms_compatibility_is_capability_based']=len(calls)==6 and all(c[0]=='can' and c[-1]=='?' for c in calls)
caps['_lms_instance']=lambda:'server-B';caps['player']=types.SimpleNamespace(rpc=lambda *args:{'_can':0})
try:caps['qualified_lms']();checks['unsupported_lms_api_refused']=False
except RuntimeError:checks['unsupported_lms_api_refused']=True
# Exercise the actual direct-file bootstrap without running the worker entry.
if sys.platform=='linux':
 path=a.lib/'autotune_v2/budget_guard.py';tree=ast.parse(path.read_text())
 bootstrap=next(x for x in tree.body if isinstance(x,ast.If) and getattr(x,'lineno',0)<30)
 terminal=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='terminal')
 env=dict(__name__='__main__',__package__='',__file__=str(path),sys=sys,Path=Path,recovered=lambda value:False)
 exec(compile(ast.Module(body=[bootstrap,terminal],type_ignores=[]),str(path),'exec'),env)
 checks['direct_recovery_worker_relative_imports_work']=env['__package__']=='autotune_v2' and env['terminal']({'status':'completed'}) is True
nas={};functions(a.lib/'measure.py',{'nas_observation'},nas)
completion={};functions(a.lib/'autotune_v2/workflow.py',{'completion_outcome'},completion)
outcome=completion['completion_outcome']
checks['zero_comparisons_due_to_budget_are_inconclusive']=outcome(False,dict(planning=dict(available_families=['governor']),screening=[]))['status']=='inconclusive-restored'
checks['no_applicable_control_is_explicit']=outcome(False,dict(planning=dict(available_families=[]),screening=[]))['optimization_outcome']=='no-applicable-alternative'
checks['dummy_screening_without_complete_measurement_is_inconclusive']=outcome(False,dict(screening=[{}]))['status']=='inconclusive-restored'
checks['rejected_alternative_windows_do_not_qualify_calibration']=outcome(False,dict(screening=[dict(issues=['restored_baseline: CPU margin'])],complete_comparisons=0))['calibration_qualified'] is False
checks['actual_alternatives_and_proposal_have_distinct_success']=outcome(False,dict(screening=[dict(issues=[])],complete_comparisons=1))['status']=='retained-current' and outcome(True,{})['status']=='configured'
valid_regression=outcome(False,dict(screening=[dict(issues=['Measured scheduling latency regression'])],complete_comparisons=1))
checks['complete_but_regressing_alternative_is_a_real_comparison']=valid_regression['status']=='retained-current' and valid_regression['calibration_qualified'] is True
checks['boolean_complete_count_is_not_measurement_evidence']=outcome(False,dict(screening=[{}],complete_comparisons=True))['calibration_qualified'] is False
nas_calls=[]
with patch.dict(sys.modules,network_test=types.SimpleNamespace(audit=lambda:nas_calls.append(True) or dict(status='failed',nas=[dict(status='failed')]))):
 local=nas['nas_observation']('local-controlled-pcm')
 checks['local_pcm_does_not_claim_or_benchmark_nas']=not nas_calls and local['network_qualified'] is False and local['status']=='not-applicable'
 ordinary=nas['nas_observation']()
 checks['ordinary_nas_failures_remain_visible']=len(nas_calls)==1 and ordinary['status']=='failed'
out=dict(status='PASS' if all(checks.values()) else 'FAIL',scope='GUI/cost/bootstrap fixture; no audio or native tuning',checks=checks)
print(json.dumps(out,indent=2));assert out['status']=='PASS'
