"""Bounded JSON predicates and typed actions. No eval, commands or write paths."""
import json,math
from pathlib import Path
MISSING=object()
PROFILES={'safe','audio-rt','low-activity','network-stable'}
FIELDS={'dac.verified','dac.serial','dac.pm','cpu.physical_cores','cpu.load','cpu.smt','kernel.rt','audio.xruns','audio.errors','scanner.active','network.nas_ok'}
OPS={'eq','ne','gt','ge','lt','le','in','exists'}

def predicate(p,depth=0):
 if not isinstance(p,dict) or depth>4:raise ValueError('Predicate nesting/structure invalid')
 if set(p) in [{'all'},{'any'}]:
  rows=p.get('all',p.get('any'))
  if not isinstance(rows,list) or not 1<=len(rows)<=16:raise ValueError('Predicate group size1..16')
  for row in rows:predicate(row,depth+1)
 elif set(p)=={'field','op','value'}:
  if p['field'] not in FIELDS or p['op'] not in OPS:raise ValueError('Unknown fact or operator')
  v=p['value']
  if isinstance(v,float) and not math.isfinite(v):raise ValueError('Nonfinite rule value')
  if p['op']=='in':
   if not isinstance(v,list) or not 1<=len(v)<=16 or any(not isinstance(x,(str,int,float,bool)) for x in v):raise ValueError('Invalid membership values')
   if any(isinstance(x,float) and not math.isfinite(x) or isinstance(x,str) and len(x)>128 for x in v):raise ValueError('Invalid membership value bounds')
  elif not isinstance(v,(str,int,float,bool)) or len(str(v))>128:raise ValueError('Invalid rule value')
  if p['op']=='exists' and type(v)!=bool:raise ValueError('Exists needs boolean')
 else:raise ValueError('Invalid predicate keys')

def validate(document):
 if not isinstance(document,dict) or set(document)!={'domain','version','rules'} or document['version']!=4 or document['domain'] not in ['cpu','usb','alsa','irq','network','services','storage']:raise ValueError('Rule schema/domain invalid')
 rows=document['rules']
 if not isinstance(rows,list) or len(rows)>32:raise ValueError('Too many rules')
 seen=set()
 for rule in rows:
  if not isinstance(rule,dict) or set(rule)!={'id','when','action','classification','reason'}:raise ValueError('Rule keys invalid')
  import re
  if not re.fullmatch('[a-z0-9-]{1,64}',rule['id']) or rule['id'] in seen:raise ValueError('Rule identifier invalid/duplicate')
  seen.add(rule['id']);predicate(rule['when']);action=rule['action']
  if not isinstance(action,dict) or set(action)!={'type','profile'} or action['type']!='profile' or action['profile'] not in PROFILES:raise ValueError('Action outside typed profile allowlist')
  if rule['classification'] not in ['SAFE','ADVANCED'] or not isinstance(rule['reason'],str) or not 1<=len(rule['reason'])<=512:raise ValueError('Rule metadata invalid')
 return document

def match(p,facts):
 if 'all' in p:return all(match(x,facts) for x in p['all'])
 if 'any' in p:return any(match(x,facts) for x in p['any'])
 value=facts.get(p['field'],MISSING);expected=p['value'];op=p['op']
 if op=='exists':return (value is not MISSING and value is not None)==expected
 if value is MISSING or value is None:return False
 if op in ['gt','ge','lt','le']:
  if type(value) not in [int,float] or type(expected) not in [int,float]:return False
  return {'gt':lambda:value>expected,'ge':lambda:value>=expected,'lt':lambda:value<expected,'le':lambda:value<=expected}[op]()
 if op=='in':return any(type(value)==type(x) and value==x for x in expected)
 # Never equate bool and numeric facts (False == 0 in Python).
 if type(value)!=type(expected):return False
 return value==expected if op=='eq' else value!=expected

def facts(h,baseline=None):
 m=baseline or {};d=h.get('dac',{})
 serial=(d.get('identity') or {}).get('serial');rt=h['kernel'].get('realtime')
 return {'dac.verified':True if d.get('status')=='verified-active' else None,'dac.serial':bool(serial) if serial is not None else None,'dac.pm':(d.get('usb') or {}).get('power',{}).get('control'),'cpu.physical_cores':h['cpu'].get('physical_cores'),'cpu.smt':h['cpu'].get('smt'),'cpu.load':m.get('cpu_percent',{}).get('cpu'),'kernel.rt':rt=='1' if rt in ['0','1'] else None,'audio.xruns':m.get('observed_alsa_xrun_states'),'audio.errors':len(m['new_kernel_errors']) if isinstance(m.get('new_kernel_errors'),list) else None,'scanner.active':any(x.get('scanner') for x in h.get('squeezelite',[])) if 'squeezelite' in h else None,'network.nas_ok':True if m.get('nas_status')=='pass' else False if m.get('nas_status')=='failed' else None}

def evaluate(h,baseline=None,directory=None):
 directory=Path(directory or Path(__file__).parent.parent/'rules');f=facts(h,baseline);recommendations=[];ids=set()
 files=sorted(directory.glob('*.json'))
 if len(files)>7:raise ValueError('Rule domains exceed7')
 for path in files:
  if path.is_symlink() or path.stat().st_size>65536:raise ValueError('Unsafe rules document')
  doc=validate(json.loads(path.read_text(),object_pairs_hook=unique_keys))
  for rule in doc['rules']:
   if rule['id'] in ids:raise ValueError('Duplicate cross-domain rule identifier')
   ids.add(rule['id'])
   if match(rule['when'],f):recommendations.append({'rule':rule['id'],'action':rule['action'],'classification':rule['classification'],'reason':rule['reason']})
 return {'status':'evaluated','facts':f,'recommendations':recommendations,'writes_performed':False,'sound_quality_claim':False}

def unique_keys(pairs):
 result={}
 for key,value in pairs:
  if key in result:raise ValueError('Duplicate JSON rule key: '+key)
  result[key]=value
 return result


def compile_profiles(evaluation,h):
 """Compile matched typed profiles; application still uses the engine journal."""
 import engine
 rows=[];warnings=[];applied=[]
 if h.get('dac',{}).get('status')!='verified-active':return {'changes':[],'warnings':['DAC unverified; rule writes refused'],'rules_applied':[]}
 for recommendation in evaluation['recommendations']:
  action=recommendation['action']
  if action.get('type')!='profile' or action.get('profile') not in PROFILES:raise ValueError('Typed action outside allowlist')
  plan=engine.recommend(h,action['profile']);rows+=plan['changes'];warnings+=plan['warnings'];applied.append(recommendation['rule'])
 return {'changes':rows,'warnings':warnings,'rules_applied':applied}
