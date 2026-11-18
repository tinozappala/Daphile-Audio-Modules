"""Explicit topology/support report; unsupported hardware is not fabricated."""
from pathlib import Path
import engine,search,power_policy
from detection import scan,read,numbers

def validate(h):
 groups=search.core_groups(h);online=set(numbers(h['cpu'].get('online')));covered={n for g in groups for n in g}
 reasons=[]
 if covered!=online:reasons.append('Physical topology incomplete; generic isolation refused')
 if len(groups)<3:reasons.append('Fewer than3physical cores; independent audio isolation unavailable')
 isolated=set(numbers(h['cpu'].get('isolated')))
 if any(set(g)&isolated and not set(g)<=isolated for g in groups):reasons.append('Existing isolation splits SMT siblings')
 return {'online':sorted(online),'physical_groups':groups,'SMT':any(len(g)>1 for g in groups),'topology_complete':covered==online,'reasons':reasons,'generic_isolation_eligible':covered==online and len(groups)>=3 and not any('splits' in x for x in reasons),'T630_profile_transferred':False}

def report(state=engine.STATE):
 h=scan();result={'status':'analyzed','model':h['system'].get('product_name'),'kernel':h['kernel']['release'],'topology':validate(h),'watchdogs':[],'NUMA_nodes':[p.name for p in Path('/sys/devices/system/node').glob('node[0-9]*')],'physical_qualification':'Only actual recorded hardware tests qualify a model; simulated topology tests are separately labelled'}
 for p in Path('/sys/class/watchdog').glob('watchdog[0-9]*'):
  result['watchdogs'].append({'name':p.name,**{k:read(p/k) for k in ['identity','state','status','nowayout','timeout','timeleft']},'device_present':Path('/dev/'+p.name).exists()})
 result['complete_hang_recovery']='Requires functioning hardware watchdog; software cannot power-cycle a frozen CPU'
 engine.atomic(Path(state)/'portability.json',result);return result
