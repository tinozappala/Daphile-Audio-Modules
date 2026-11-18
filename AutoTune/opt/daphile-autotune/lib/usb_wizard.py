"""Physical socket mapping wizard with repeated comparable captures."""
import time,uuid
from pathlib import Path
import engine,comparison,player
from detection import scan,read

def start(labels,state=engine.STATE):
 if not isinstance(labels,list) or not 2<=len(labels)<=8 or len(set(labels))!=len(labels) or any(not isinstance(x,str) or not x.isalnum() or len(x)>24 for x in labels):raise ValueError('Provide2..8unique alphanumeric physical socket labels')
 h=scan();t=player.target(h);audio=comparison.playback(t['mac'])
 r={'id':uuid.uuid4().hex,'status':'awaiting-port','host_uuid':t['host_uuid'],'dac_identity':{k:v for k,v in t['identity'].items() if k!='path'},'labels':labels,'captures':[],'reference':audio,'created':time.time(),'instructions':'Move the same DAC to a labelled socket, restart the same track at the saved position and volume, then Capture twice for each socket.'}
 with engine.locked(state) as root:engine.atomic(root/'usb-wizard.json',r)
 return progress(r)

def progress(r):
 counts={label:sum(c['label']==label for c in r['captures']) for label in r['labels']}
 return {'status':r['status'],'labels':r['labels'],'captures_by_socket':counts,'next_socket':next((label for label,count in counts.items() if count<2),None),'reference':r['reference'],'instructions':r['instructions']}

def topology_score(h):
 u=h['dac'].get('usb') or {};controller=u.get('controller');irq=u.get('irq')
 if not controller or irq is None:return {'score':None,'reason':'Controller/IRQ unknown'}
 peers=[x for x in h['usb'] if x.get('controller')==controller and not x['path'].startswith('usb') and x['path']!=u['path']]
 entry=next((x for x in h.get('irq') or [] if str(x['irq'])==str(irq)),None)
 if not entry:return {'score':None,'reason':'IRQ ownership unknown'}
 score=max(0,100-min(60,len(peers)*10)-(15 if ',' in entry['description'] else 0)-(10 if '.' in u['path'].split('-',1)[-1] else 0))
 return {'score':score,'peers':len(peers),'irq_description':entry['description'],'controller':controller,'usb_path':u['path'],'criterion':'Documented heuristic:100 minus10per controller peer(cap60),15for shared IRQ,10for downstream hub','sound_quality_claim':False}

def capture(label,seconds=30,state=engine.STATE):
 r=engine.load(Path(state)/'usb-wizard.json')
 if label not in r['labels']:raise ValueError('Socket label not in active wizard')
 h=scan();t=player.target(h);identity={k:v for k,v in t['identity'].items() if k!='path'}
 if t['host_uuid']!=r['host_uuid'] or identity!=r['dac_identity']:raise RuntimeError('Host/DAC changed')
 now=comparison.playback(t['mac']);ref=r['reference']
 if any(now[k]!=ref[k] for k in ['sample_hash','volume']) or abs(now['position']-ref['position'])>2:raise RuntimeError('Seek the original track to the displayed reference time and retain its volume')
 prior=[x for x in r['captures'] if x['label']!=label]
 if any(x['capture']['usb']['path']==h['dac']['usb']['path'] for x in prior):raise RuntimeError('This USB path already belongs to another physical socket label')
 c=comparison.capture('ports',label,seconds,state);score=topology_score(h)
 if c['status']!='observed-window':raise RuntimeError('Capture is incomplete; not added to wizard')
 r['captures'].append({'label':label,'capture':c,'topology':score});r['status']='ready-to-compare' if all(sum(x['label']==k for x in r['captures'])>=2 for k in r['labels']) else 'awaiting-port'
 with engine.locked(state) as root:engine.atomic(root/'usb-wizard.json',r)
 return dict(progress(r),label=label,topology=score)

def report(state=engine.STATE):
 r=engine.load(Path(state)/'usb-wizard.json');result=comparison.compare([x['capture'] for x in r['captures']]);reasons=result['reasons'];scores=[]
 for label in r['labels']:
  rows=[x for x in r['captures'] if x['label']==label]
  if len(rows)<2:reasons.append(label+': two repeated captures required')
  values=[x['topology']['score'] for x in rows]
  if not values or any(x is None for x in values) or len(set(values))>1:reasons.append(label+': stable known topology required')
  scores.append({'label':label,'score':values[0] if values and None not in values else None,'captures':len(rows)})
 ranked=sorted(scores,key=lambda x:x['score'] if x['score'] is not None else -1,reverse=True);winner=None
 if not reasons and len(ranked)>1 and ranked[0]['score']>ranked[1]['score']:winner=ranked[0]['label']
 result.update(status='compared' if not reasons else 'inconclusive',reasons=sorted(set(reasons)),topology_scores=scores,recommended_socket=winner,sound_quality_claim=False,instructions='Recommendation concerns observable topology/isolation only; no USB port switches are performed')
 with engine.locked(state) as root:engine.atomic(root/'usb-wizard-report.json',result)
 return result
