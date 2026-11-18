"""Adaptive recommendations from typed rules and repeated controlled trials."""
import statistics
from pathlib import Path
import engine,rule_engine,ab_session
from detection import scan

def plan(state=engine.STATE):
 h=scan();p=Path(state)/'baseline.json';saved=engine.load(p) if p.exists() else {}
 baseline=saved.get('measurement') if saved.get('hardware',{}).get('hardware_id')==h['hardware_id'] else None
 result=rule_engine.evaluate(h,baseline)
 result.update(baseline_available=baseline is not None,automatic_boot_changes=False)
 return result

def rank(session):
 groups={condition:[] for condition in session['mapping']};reasons=[]
 captures=[]
 for trial in session.get('trials',[]):
  groups[trial['condition']].append(trial['capture']);captures.append(trial['capture']);profile=session['mapping'][trial['condition']];applied=trial.get('application',{})
  if profile!='default' and (applied.get('status')!='committed' or applied.get('profile')!=profile or applied.get('effective_changes',0)<1 or trial['capture'].get('profile')!=profile):reasons.append(trial['condition']+': no verified effective profile changes; attribution refused')
 comparable=__import__('comparison').compare(captures)
 if comparable['status']!='comparable-observed-windows':reasons+=comparable['reasons']
 summary=[]
 for condition,rows in groups.items():
  loads=[r['measurement'].get('cpu_percent',{}).get('cpu') for r in rows]
  if len(rows)<3 or any(x is None for x in loads):reasons.append(condition+': at least3complete repeated windows required')
  valid=[x for x in loads if x is not None]
  summary.append({'condition':condition,'profile':session['mapping'][condition],'repetitions':len(rows),'median_cpu_percent':statistics.median(valid) if valid else None,'cpu_range':[min(valid),max(valid)] if valid else None})
 winner=None
 if not reasons:
  ordered=sorted(summary,key=lambda x:x['median_cpu_percent']);best,other=ordered[:2]
  # Require every measured candidate window to improve by2percentage points.
  if best['cpu_range'][1]+2<min(x['cpu_range'][0] for x in ordered[1:]):winner=best['profile']
  else:reasons.append('Observed activity ranges overlap or improvement is below2CPU percentage points')
 return {'status':'recommendation' if winner else 'inconclusive','recommended_profile':winner,'apply_automatically':False,'reason':reasons,'metrics':summary,'sound_quality_claim':False,'criterion':'Stable comparable repeated windows; objective CPU activity separation only'}

def run(seconds=30,repeats=3,state=engine.STATE,confirmed=False,apply_best=False):
 decision=plan(state);profiles=['default']
 for row in decision['recommendations']:
  name=row['action']['profile']
  if name not in profiles and name in ab_session.ALLOWED:profiles.append(name)
 if len(profiles)<2:return {'status':'no-supported-candidate','decision':decision,'writes_performed':False}
 ab_session.run(profiles[:4],repeats,seconds,False,state,confirmed)
 result=rank(engine.load(Path(state)/'ab-session.json'))
 if apply_best and result['recommended_profile']:
  if result['recommended_profile']=='default':result['application']={'status':'original-restored'}
  else:result['application']=engine.tune(result['recommended_profile'],seconds,state)
  result['apply_automatically']=True
 with engine.locked(state) as root:engine.atomic(root/'adaptive.json',result)
 return result
