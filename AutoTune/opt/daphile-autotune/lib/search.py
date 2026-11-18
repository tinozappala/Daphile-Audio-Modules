"""Repeated, controlled coordinate search over explicitly admissible policies.

Candidates are measured and restored independently. Unknown metrics are never
converted to zero; no claim of a global or audible optimum is made.
"""
import copy,itertools,json,math,os,statistics,time
from pathlib import Path
import engine,player,power_policy,runtime_ext,comparison,measure,session
from detection import scan,read,numbers

def core_groups(h):
 groups={};online=set(numbers(h['cpu'].get('online')))
 for c in h['cpu'].get('cores',[]):
  key=(c['topology'].get('physical_package_id'),c['topology'].get('core_id'))
  if c['cpu'] in online and None not in key:groups.setdefault(key,[]).append(c['cpu'])
 return sorted([sorted(g) for g in groups.values()],key=min)

def specs(h):
 result=[{'name':'current','stage':'reference'}];groups=core_groups(h)
 isolated=set(numbers(h['cpu'].get('isolated')))
 available=[g for g in groups if set(g)<=isolated] if isolated else groups[2:]
 rt=h['kernel'].get('realtime')=='1'
 priorities=sorted(set([45,50,60,70]+[t['priority'] for p in h.get('squeezelite',[]) for t in p.get('threads',[]) if t['tid']==h['dac'].get('owner_pid') and 40<=t.get('priority',0)<=70]))
 for output,irq in itertools.permutations(available,2):
  for policy,priority in itertools.product([os.SCHED_FIFO,os.SCHED_RR] if rt else [None],priorities if rt else [None]):
   result.append({'name':'cpu-'+str(output[0])+'-irq-'+str(irq[0])+'-'+str(policy)+'-'+str(priority),'stage':'scheduling','output':output,'irq':irq,'policy':policy,'priority':priority})
 if len(available)<2 and rt:
  for policy,priority in itertools.product([os.SCHED_FIFO,os.SCHED_RR],priorities):result.append({'name':'scheduler-'+str(policy)+'-'+str(priority),'stage':'scheduling','policy':policy,'priority':priority})
 inv=power_policy.inventory()
 for ceiling in sorted({max(10,int(x['latency'])-1) for x in inv['cstates'] if (x.get('latency') or '').isdigit() and int(x['latency'])>10}):result.append({'name':'idle-'+str(ceiling),'stage':'energy','ceiling':ceiling})
 for clock in inv['available_clocksources']:
  if clock!=inv['clocksource']:result.append({'name':'clock-'+clock,'stage':'energy','clock':clock})
 governors=set()
 for c in h['cpu'].get('cores',[]):governors.update((c.get('cpufreq',{}).get('scaling_available_governors') or '').split())
 for governor in sorted(governors):result.append({'name':'governor-'+governor,'stage':'energy','governor':governor})
 # Fixed clocks only when the driver exposes discrete hardware frequencies.
 for policy in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*')):
  values=[int(v) for v in (read(policy/'scaling_available_frequencies') or '').split() if v.isdigit()]
  if values:
   for frequency in sorted({max(values),min(v for v in values if v>=max(values)*.7)}):result.append({'name':policy.name+'-fixed-'+str(frequency),'stage':'energy','frequency_policy':str(policy),'frequency':frequency})
 network=runtime_ext.network_inventory()
 if network['status']=='available':
  f=network.get('features') or {};keys=[k for k in ['tcp-segmentation-offload','generic-segmentation-offload','generic-receive-offload'] if k in f]
  for values in itertools.product(['off','on'],repeat=len(keys)):
   settings=dict(f,**dict(zip(keys,values)))
   if settings!=f:result.append({'name':'offloads-'+''.join(values),'stage':'network','features':settings})
  co=network.get('coalesce')
  if co:
   for usecs in sorted({0,25,50,max(co.values())}):
    if usecs<=1000:
     settings={k:usecs for k in co}
     if settings!=co:result.append({'name':'coalesce-'+str(usecs),'stage':'network','coalesce':settings})
 for r in result:
  r['coordinate']=r['name'].split('-',1)[0] if r['stage'] in ['energy','network'] else r['stage']
 return {'candidates':result,'count':len(result),'physical_groups':groups,'network':network,'coverage':'Every generated candidate is tried within each coordinate stage; joint winner is revalidated. Exhaustive Cartesian global optimization is not claimed.','sound_quality_claim':False}

def changes(h,spec):
 from advanced import plan
 rows=[];template,notes=plan(h,'audio-rt')
 def add(c):
  if c and c['before']!=c['after']:rows.append(c)
 if 'output' in spec:
  if not re_usb_owners(next((x['description'] for x in h.get('irq') or [] if str(x['irq'])==str(h['dac']['usb'].get('irq'))),'')):raise RuntimeError('USB IRQ owners are not independently assignable')
  for old in template:
   c=copy.deepcopy(old)
   if c['parameter']=='usb_irq_affinity':c['after']=','.join(map(str,spec['irq']));add(c)
   elif c['parameter']=='thread_affinity':c['after']=spec['output'] if c['role']=='audio-output' else spec['irq'];add(c)
  # Already-correct affinity is absent from the normal plan. Recover targets
  # from live IRQ and audio ownership instead of assuming a plan row exists.
  u=h['dac']['usb'];entry=next((x for x in h.get('irq') or [] if str(x['irq'])==str(u.get('irq'))),None)
  if entry and not any(c['parameter']=='usb_irq_affinity' for c in rows):
   owners=re_usb_owners(entry['description'])
   if owners:add(runtime_ext.change(h,'usb_irq_affinity',entry['affinity'],','.join(map(str,spec['irq'])),path='/proc/irq/'+str(entry['irq'])+'/smp_affinity_list',irq=entry['irq'],controller=u['controller'],description=entry['description']))
  for p in h.get('squeezelite',[]):
   for t in p.get('threads',[]):
    role='audio-output' if t['tid']==h['dac'].get('owner_pid') else 'usb-irq' if (t.get('name') or '').startswith('irq/'+str(u.get('irq'))+'-') else None
    if role and not any(c.get('tid')==t['tid'] for c in rows):add(runtime_ext.change(h,'thread_affinity',t['affinity'],spec['output'] if role=='audio-output' else spec['irq'],tid=t['tid'],start=engine.start_ticks(t['tid']),name=t['name'],role=role))
 if spec.get('policy') is not None:
  t=next((t for p in h.get('squeezelite',[]) for t in p['threads'] if t['tid']==h['dac'].get('owner_pid')),None)
  if t:add(runtime_ext.change(h,'audio_schedule_search',{'policy':t['policy'],'priority':t['priority']},{'policy':spec['policy'],'priority':spec['priority']},tid=t['tid'],start=engine.start_ticks(t['tid']),name=t['name']))
 if 'clock' in spec or 'ceiling' in spec:rows+=power_policy.plan(h,spec.get('ceiling'),spec.get('clock'))['changes']
 if 'governor' in spec:
  for p in sorted(Path('/sys/devices/system/cpu/cpufreq').glob('policy*')):
   if spec['governor'] in (read(p/'scaling_available_governors') or '').split():add(runtime_ext.change(h,'governor_search',read(p/'scaling_governor'),spec['governor'],policy=str(p),driver=read(p/'scaling_driver')))
 frequencies=spec.get('frequencies',{})
 if 'frequency' in spec:frequencies={**frequencies,spec['frequency_policy']:spec['frequency']}
 for path,frequency in frequencies.items():
  p=Path(path);add(runtime_ext.change(h,'frequency_limits',{'min':int(read(p/'scaling_min_freq')),'max':int(read(p/'scaling_max_freq'))},{'min':frequency,'max':frequency},policy=str(p),driver=read(p/'scaling_driver')))
 if 'features' in spec or 'coalesce' in spec:
  n=runtime_ext.network_inventory()
  if n['status']!='available':raise RuntimeError('Network trial no longer supported')
  for key in ['features','coalesce']:
   if key in spec:add(runtime_ext.change(h,'network_'+key,n[key],spec[key],nic=n['identity']))
 for change in rows:
  if change['parameter']=='usb_irq_affinity':change['effective_before']=__import__('irq_route_restore').snapshot(change,read,numbers)
 return rows

def re_usb_owners(description):
 import re
 suffix=re.split(r'(?:fasteoi|edge|level)\s+',description,maxsplit=1)
 owners=[x.strip() for x in suffix[-1].split(',')] if len(suffix)==2 else []
 return bool(owners and all(re.fullmatch(r'(?:ehci|xhci|ohci|uhci)_hcd:usb[0-9]+',x) for x in owners))

from measurement_contract import metric, choose


def run(seconds=30,repeats=3,state=engine.STATE,confirmed=False,commit=False,stages=None,calibration=None,experimental=False):
 raise RuntimeError('Extended search execution is unavailable: use the bounded V2 automatic workflow (1800 seconds total).')
 if not confirmed or not (10 if calibration else 30)<=seconds<=120 or not 3<=repeats<=6:raise ValueError('Confirmed search requires30..120 seconds and3..6 repetitions; bounded calibration10seconds minimum')
 root=Path(state);h=scan();player.assert_library_idle(h);target=player.target(h);original=player.playback_snapshot(target['mac']);audio=comparison.playback(target['mac'])
 s=player.rpc(target['mac'],['status','-','1','tags:u']);duration=float(s.get('duration',0));position=0 if duration-audio['position']<seconds*2+20 else audio['position']
 if duration-position<seconds*2+20:raise RuntimeError('Current track too short for equivalent before/after trials')
 inventory=specs(h);selected=[x for x in inventory['candidates'] if x['stage']=='reference' or stages is None or x['stage'] in stages]
 coverage={'exhaustive':True,'scope':inventory['coverage']}
 if calibration:
  import calibration_budget
  coverage=calibration_budget.shortlist(h,inventory,experimental);selected=coverage['candidates'];calibration.require(190)
 from continuous import key
 report={'status':'running','host_uuid':target['host_uuid'],'hardware_id':h['hardware_id'],'kernel':h['kernel']['release'],'binding_key':key(h),'total':len(selected)*repeats,'completed':0,'candidates':[],'selected':[],'started':time.time(),'sound_quality_claim':False,'coverage':coverage}
 engine.atomic(root/'search.json',report)
 lease=session.Lease(root,original,len(selected)*repeats*(seconds*2+90)+300,'search')
 try:
  for spec in selected:
   row={'spec':spec,'measurements':[],'issues':[],'application_observed':False,'application_observations':[]}
   for repetition in range(repeats):
    if calibration:calibration.require(seconds*2+35)
    progress={'status':'running','completed':report['completed'],'total':report['total'],'candidate':spec['name'],'repetition':repetition+1,'repeats':repeats,'phase':'preparing-controlled-segment','message':'Collaudo comparativo: il brano viene ripetuto intenzionalmente.'}
    engine.atomic(root/'search-progress.json',progress)
    phase_count=[0]
    def observe_phase(duration):
     progress['phase']='baseline' if phase_count[0]==0 else 'candidate';phase_count[0]+=1
     engine.atomic(root/'search-progress.json',progress);return measure.observe(duration)
    now=comparison.playback(target['mac'])
    if any(now[k]!=audio[k] for k in ['sample_hash','volume']):raise RuntimeError('External track or volume change; stop comparative search and preserve playback')
    player.rpc(target['mac'],['time',str(position)]);time.sleep(.5)
    try:
     result=engine.trial(lambda fresh:changes(fresh,spec),'search-'+spec['name'],seconds,root,False,controlled_position=position,observer=observe_phase,calibration=bool(calibration))
     if result['status']=='no-change':
      before=result['baseline'];now=comparison.playback(target['mac'])
      if any(now[k]!=audio[k] for k in ['sample_hash','volume']):raise RuntimeError('External track or volume change before paired observation')
      player.rpc(target['mac'],['time',str(position)]);time.sleep(.5);after=observe_phase(seconds);now=comparison.playback(target['mac'])
      if any(now[k]!=audio[k] for k in ['sample_hash','volume']):raise RuntimeError('External track or volume change during paired observation')
      row['measurements'].append({'before':before,'after':after});row['application_observations'].append(False)
     else:row['measurements'].append(result['benchmark']);row['application_observations'].append(True)
    except Exception as e:
     row['issues'].append(type(e).__name__+': '+str(e))
     if spec['stage']=='reference':
      report['completed']+=1;report['candidates'].append(row)
      raise RuntimeError('Reference window refused; further candidates not tested: '+str(e)) from e
     pending=root/'transaction.json'
     if pending.exists() and engine.load(pending).get('status')=='rollback-incomplete':raise RuntimeError('Search stopped: recovery incomplete') from e
    report['completed']+=1;report['current']=spec['name'];report['current_candidate']=row;engine.atomic(root/'search.json',report)
   row['application_observed']=bool(row['application_observations']) and all(row['application_observations'])
   if len(row['application_observations'])!=repeats or any(value is not (spec['stage']!='reference') for value in row['application_observations']):row['issues'].append('Every repeat must observe its expected applied or unchanged controls')
   report['candidates'].append(row);report.pop('current_candidate',None)
   if spec['stage']=='reference' and (row['issues'] or any(metric(x['after']) is None for x in row['measurements'])):raise RuntimeError('Reference windows incomplete or unstable; further candidates refused')
  reference=[x for x in report['candidates'] if x['spec']['stage']=='reference']
  for coordinate in sorted({x['spec']['coordinate'] for x in report['candidates'] if x['spec']['stage']!='reference'}):
   decision=choose(reference+[x for x in report['candidates'] if x['spec']['coordinate']==coordinate]);report['selected'].append({'coordinate':coordinate,'decision':decision})
  merged={'name':'combined','stage':'combined'}
  for row in report['selected']:
   winner=row['decision'].get('winner')
   if winner and winner['name']!='current':
    merged.update({k:v for k,v in winner.items() if k not in ['name','stage','coordinate','frequency_policy','frequency']})
    if 'frequency' in winner:merged.setdefault('frequencies',{})[winner['frequency_policy']]=winner['frequency']
  # Independently revalidate the combined policy. No-change remains a valid
  # result when repeated intervals fail to establish an improvement.
  report['combined_spec']=merged
  if len(merged)>2:report['final']=engine.trial(lambda fresh:changes(fresh,merged),'search-combined',seconds,root,commit,controlled_position=position,observer=measure.observe,calibration=bool(calibration))
  else:report['final']={'status':'retained-current','changes':[]}
  if report['final'].get('status')=='committed':engine.atomic(root/'automatic-policy.json',{'status':'verified','host_uuid':target['host_uuid'],'hardware_id':h['hardware_id'],'kernel':h['kernel']['release'],'binding_key':key(h),'selected':merged})
  report['status']='committed' if report['final'].get('status')=='committed' else 'finished-restored'
 except BaseException as e:
  report.update(status='failed',error=type(e).__name__+': '+str(e));raise
 finally:
  # Queue/mode restoration is always attempted, including failed candidates.
  try:
   recovery=lease.finish();report['playback_recovery']=recovery
   report['playlist_restoration']=recovery['status']
   if recovery.get('restored') is not True:report['status']='finished-external-playback-preserved'
  except Exception as e:report.update(status='recovery-required',playlist_restoration=str(e))
  report['finished']=time.time();engine.atomic(root/'search.json',report)
  engine.atomic(root/'search-progress.json',{k:report.get(k) for k in ['status','completed','total','finished','playlist_restoration','error']})
 return report

__release_signature__ = 'Tino Zappala'
