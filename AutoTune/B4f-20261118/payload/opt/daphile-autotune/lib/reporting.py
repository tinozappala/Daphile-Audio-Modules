"""Human before/after report and complete sanitized reproducible export."""
import hashlib,json,os,tarfile,time
from pathlib import Path
import engine,gui,dashboard

JOURNALS=['baseline.json','current.json','last-known-good.json','transaction.json','benchmark.json','network.json','matrix.json','stability.json','activity.json','scheduler.json','comparison-ab.json','comparison-ports.json','adaptive.json','usb-wizard-report.json','experimental-trial.json','automation-health.json','automatic.json','automatic-policy.json','search.json','stress.json','nas-tuning.json','continuous.json','portability.json','boot-profile.json','usb-auto-progress.json','companion-migration.json']
PRIVATE={'initial_current','original_playback','original_preferences','playback','playback_resume','target','reference','urls','options','source','relative','directory','token','token_secret','companion'}

def public(value):
 if isinstance(value,dict):
  result={k:public(v) for k,v in value.items() if k not in PRIVATE}
  if result.get('parameter')=='player_buffer':
   from audio_setup import preferences
   for key in ['before','after']:
    if isinstance(result.get(key),str):result[key]=preferences(result[key])
  return gui.sanitize(result)
 if isinstance(value,list):return [public(v) for v in value]
 if isinstance(value,str):
  from detection import redact
  return redact(value)
 return value

def current_journal(record):
 """A journal describes active settings only after a current readback."""
 result=dict(record)
 if result.get('status')!='committed':return result
 reasons=[]
 if result.get('boot_id')!=engine.boot_id():reasons.append('Registrazione di un avvio precedente.')
 else:
  try:
   from detection import scan
   h=scan()
   if result.get('hardware_id')!=h.get('hardware_id'):reasons.append('Hardware non corrispondente.')
   for c in result.get('changes',[]):
    engine.guard(c,h)
    if engine.setting_value(c)!=c['after']:reasons.append('Valore corrente differente: '+c['parameter'])
  except (OSError,RuntimeError,ValueError,KeyError) as e:reasons.append('Lettura corrente incompleta: '+str(e))
 if reasons:result.update(status='historical-unverified',historical_status='committed',current_verification={'verified':False,'reasons':reasons})
 else:result['current_verification']={'verified':True,'checked':time.time()}
 return result

def collect(state=engine.STATE):
 root=Path(state);result={'version':'0.9.10','generated':time.time(),'dashboard':public(dashboard.report(root)),'journals':{}}
 for name in JOURNALS:
  p=root/name
  if p.is_file() and not p.is_symlink():
   record=engine.load(p);result['journals'][name]=public(current_journal(record) if name=='current.json' else record)
 return result

STATUS_LABELS={'historical-unverified':'registrazione storica; valori attuali non verificati','committed':'applicato e verificato nella finestra registrata','configured':'configurazione completata','finished-restored':'prove terminate; valori iniziali ripristinati','trial-passed-restored':'prova superata; valori iniziali ripristinati','recovery-required':'recupero da completare','rollback-incomplete':'ripristino incompleto','failed':'prova fallita','inactive':'nessun profilo attivo','running':'prova in corso','retain-current':'configurazione esistente mantenuta','inconclusive':'risultato inconcludente'}
SETTING_LABELS={'cpu_governor':'Governatore CPU','governor_search':'Governatore CPU','frequency_limits':'Limiti frequenza CPU','thread_affinity':'Affinita thread','usb_irq_affinity':'Affinita IRQ USB','thread_scheduler':'Schedulatore e priorita audio','audio_schedule_search':'Schedulatore e priorita audio','dac_usb_runtime_pm':'Sospensione del DAC','empty_usb_pm':'Sospensione controller USB inutilizzato','peripheral_pm':'Sospensione periferica inutilizzata','optional_service':'Servizio opzionale','scanner_nice':'Priorita scanner','player_buffer':'Buffer e periodi ALSA','clocksource':'Clocksource','cstate_disable':'C-state','network_features':'Offload di rete','network_coalesce':'Aggregazione interrupt di rete','network_eee':'Risparmio EEE di rete'}

def format_value(v):
 if v is None:return 'non osservato'
 if isinstance(v,dict) and set(v)=={'policy','priority'}:
  import os
  names={os.SCHED_FIFO:'FIFO',os.SCHED_RR:'RR',os.SCHED_OTHER:'OTHER',os.SCHED_BATCH:'BATCH',os.SCHED_IDLE:'IDLE'}
  return names.get(v['policy'],str(v['policy']))+' / priorita '+str(v['priority'])
 return json.dumps(v,ensure_ascii=False,sort_keys=True)

def narrative(data):
 d=data['dashboard'];j=data['journals'];current=j.get('current.json',{});state=current.get('status','inactive');system=d.get('SYSTEM',{});dac=d.get('DAC',{})
 model=(system.get('system') or {}).get('product_name','non osservato');k=system.get('kernel');kernel=k.get('release','non osservato') if isinstance(k,dict) else k or 'non osservato'
 lines=['ESITO E RIEPILOGO', 'Computer: '+str(model)+'. Kernel: '+str(kernel)+'.', ('Profilo registrato: ' if state=='historical-unverified' else 'Profilo attivo: ')+str(current.get('profile','nessuno'))+'; '+STATUS_LABELS.get(state,state)+'.']
 identity=dac.get('identity') or {};lines.append('DAC: '+str(identity.get('product','non osservato'))+'; identificazione '+str(dac.get('status','non osservata'))+'.')
 if state=='historical-unverified':lines.append('Questa registrazione non attesta impostazioni attive: '+ '; '.join(current.get('current_verification',{}).get('reasons',[])))
 lines.extend(['','MODIFICHE PRIMA / DOPO'])
 changes=current.get('changes',[])
 if not changes:lines.append('Non risultano modifiche attive di AutoTune. Le prove ripristinate non vengono presentate come configurazione applicata.')
 for c in changes:
  target=c.get('service') or c.get('name') or c.get('policy') or c.get('path') or c.get('tid') or 'DAC selezionato'
  meaning='applicato e verificato' if state=='committed' and c.get('write_attempted') else 'registrato; verificare stato della transazione'
  lines.append(SETTING_LABELS.get(c.get('parameter'),str(c.get('parameter')))+' ['+str(target)+']: '+format_value(c.get('before'))+' -> '+format_value(c.get('after'))+' ('+meaning+').')
 lines.extend(['','CONFRONTO MISURATO PRIMA / DOPO'])
 b=current.get('benchmark') or j.get('benchmark.json',{});a=b.get('before',{});z=b.get('after',{})
 for key,label,unit in [('duration','Durata osservata','s'),('context_switches_per_second','Context switch','/s'),('interrupts_per_second','Interrupt','/s'),('max_scheduling_latency','Latenza massima dello schedulatore osservata','ms'),('wakeups_per_second','Risvegli','/s'),('observed_alsa_xrun_states','Stati XRUN osservati','')]:
  lines.append(label+': '+str(a.get(key,'non misurato'))+' -> '+str(z.get(key,'non misurato'))+' '+unit+'.')
 lines.append('Utilizzo CPU: '+str(a.get('cpu_percent',{}).get('cpu','non misurato'))+' -> '+str(z.get('cpu_percent',{}).get('cpu','non misurato'))+' %.')
 lines.extend(['','PROVE E COPERTURA'])
 m=j.get('matrix.json');q=j.get('search.json');t=j.get('stress.json');u=j.get('usb-wizard-report.json');ab=j.get('comparison-ab.json');nas=j.get('nas-tuning.json')
 if m:
  counts={}
  for r in m.get('results',[]):counts[r.get('status','unknown')]=counts.get(r.get('status','unknown'),0)+1
  if m.get('exhaustive') is False:lines.append('Autocalibrazione18minuti: formati rappresentativi; '+str(len(m.get('deferred_entries',[])))+'formati della matrice estesa rinviati, non certificati.')
  lines.append('Matrice PCM/DSD: '+format_value(counts)+'; gruppi completi '+str(sum(g.get('complete') is True for g in m.get('groups',[])))+' di '+str(len(m.get('groups',[])))+'; ripristino '+str(m.get('restoration','non verificato'))+'.')
 else:lines.append('Matrice PCM/DSD: nessuna prova di questa esecuzione registrata.')
 if q:
  lines.append('Ricerca: '+str(q.get('completed',0))+' di '+str(q.get('total','non noto'))+' finestre; '+STATUS_LABELS.get(q.get('status'),str(q.get('status')))+'.')
  for r in q.get('selected',[]):
   dec=r.get('decision',{});winner=dec.get('winner') or {};lines.append('  '+str(r.get('coordinate'))+': '+str(winner.get('name','nessun vincitore'))+'; '+str(dec.get('reason',''))+'.')
 else:lines.append('Ricerca comparativa: non registrata in questa esecuzione.')
 if t:lines.append('Stabilita: '+str(round(t.get('elapsed',0)/3600,2))+' ore realmente osservate; esito '+str(t.get('status','non noto'))+'; anomalie '+str(len(t.get('issues',[])))+'; sospetti di crescita memoria '+str(len(t.get('memory_growth_suspicions',[])))+'.')
 else:lines.append('Stabilita prolungata: non eseguita in questa esecuzione; nessuna qualifica di24ore dichiarata.')
 if u:lines.append('Porte USB: '+str(u.get('status'))+'; porta suggerita '+str(u.get('recommended_socket') or 'nessuna separazione dimostrata')+'. Il cavo deve essere spostato fisicamente.')
 else:lines.append('Porte USB: confronto fisico completo non registrato.')
 if ab:lines.append('Confronto A/B: '+str(ab.get('status'))+'; preferenze registrate '+str(len(ab.get('votes',[])))+'. Le preferenze di ascolto sono distinte dalle misure software.')
 else:lines.append('Ascolto A/B: sessione e preferenze non registrate.')
 if nas:
  for n in nas.get('mounts',[]):lines.append('Opzioni NAS: '+str(n.get('status'))+'; '+str(n.get('reason','consultare risultati tecnici'))+'.')
 else:lines.append('Opzioni NAS: confronto non registrato; i mount di produzione vengono conservati.')
 lines.extend(['','LIMITI DELLE CONCLUSIONI','Le impostazioni provate vengono accettate solo entro la copertura e la durata registrate. Un valore non osservato resta sconosciuto.','Le metriche dello schedulatore non misurano la latenza elettrica completa o la qualita sonora. Un risultato inconcludente mantiene la configurazione esistente.','Power auto consente la sospensione: lo stato runtime suspended e la disalimentazione fisica sono concetti diversi.','La qualifica fisica riguarda il computer e il DAC riportati; la portabilita su altri sistemi richiede nuove prove.',''])
 return lines

def human(data):
 d=data['dashboard'];j=data['journals'];lines=['DAPHILE AUTOTUNE REPORT','Versione: '+data['version'],'Data UTC: '+time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime(data['generated'])),'']+narrative(data)+['DETTAGLI TECNICI E PROVE']
 for title in ['SYSTEM','DAC','USB','CPU','IRQ','ALSA','NETWORK','SERVICES']:
  lines.extend([title,json.dumps(d.get(title,{}),ensure_ascii=False,indent=2),''])
 lines.extend(['SQUEEZELITE',json.dumps(d.get('SERVICES',{}).get('processes',[]),ensure_ascii=False,indent=2),'','CHANGES'])
 current=j.get('current.json',{})
 changes=current.get('changes',[])
 if not changes:lines.append('Nessuna modifica attiva registrata in questo profilo.')
 for c in changes:lines.append(c.get('parameter','?')+': '+json.dumps(c.get('before'),ensure_ascii=False)+' -> '+json.dumps(c.get('after'),ensure_ascii=False))
 benchmark=current.get('benchmark') or j.get('benchmark.json',{})
 for title,key in [('BENCHMARK BEFORE','before'),('BENCHMARK AFTER','after')]:lines.extend(['',title,json.dumps(benchmark.get(key,{'status':'not-measured'}),ensure_ascii=False,indent=2)])
 lines.extend(['','RICERCA E CONFRONTI'])
 for name in ['automatic.json','search.json','nas-tuning.json','usb-wizard-report.json','comparison-ab.json','stress.json','continuous.json','portability.json']:
  r=j.get(name)
  if r:lines.append(name+': '+str(r.get('status','unknown')))
 lines.extend(['','WARNINGS','Finestre osservate e metriche software non certificano qualità sonora o latenza peggiore assoluta.','Power auto non equivale a periferica sospesa. Formati non supportati restano dichiarati tali.'])
 for r in j.values():
  for field in ['issues','warnings','recovery_errors','memory_growth_suspicions']:
   if r.get(field):lines.append(field+': '+json.dumps(r[field],ensure_ascii=False))
 lines.extend(['','FINAL STATUS',current.get('status','inactive')+'; validazione limitata alle prove e durate esplicitamente registrate.'])
 return '\n'.join(lines)+'\n'

def report(state=engine.STATE):
 data=collect(state);return {'status':'generated','report_text':human(data),'evidence':data}

def export(destination,state=engine.STATE):
 destination=Path(destination)
 if destination.exists() or destination.is_symlink():raise RuntimeError('Export destination already exists')
 data=collect(state);entries={'hardware.json':{k:data['dashboard'].get(k) for k in ['SYSTEM','DAC','USB','CPU','IRQ','ALSA','NETWORK']},'profile.json':data['journals'].get('current.json',{'status':'inactive'}),'evidence.json':data}
 with tarfile.open(destination,'x:gz') as archive:
  os.chmod(destination,0o600)
  import io
  entries['report.txt']=human(data)
  for name,value in entries.items():
   body=(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)).encode();info=tarfile.TarInfo(name);info.size=len(body);info.mode=0o600;archive.addfile(info,io.BytesIO(body))
  # Only parsed and sanitized journals are included, never original preferences,
  # credentials, queue URLs, raw stderr or arbitrary directory contents.
  for name,value in data['journals'].items():
   body=json.dumps(value,ensure_ascii=False,indent=2).encode();info=tarfile.TarInfo('logs/'+name);info.size=len(body);info.mode=0o600;archive.addfile(info,io.BytesIO(body))
  import tempfile
  for name in ['changes.jsonl','stress-observations.jsonl','stress-memory.jsonl']:
   p=Path(state)/name
   if not p.is_file() or p.is_symlink():continue
   with tempfile.SpooledTemporaryFile(max_size=65536) as stream:
    with p.open() as source:
     for line in source:
      if len(line)>4*1024*1024:raise RuntimeError('Export journal line exceeds bounded size')
      stream.write((json.dumps(public(json.loads(line)),ensure_ascii=False)+'\n').encode())
    size=stream.tell();stream.seek(0);info=tarfile.TarInfo('logs/'+name);info.size=size;info.mode=0o600;archive.addfile(info,stream)
 return {'status':'exported','export':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'bytes':destination.stat().st_size,'journals':len(data['journals'])}
