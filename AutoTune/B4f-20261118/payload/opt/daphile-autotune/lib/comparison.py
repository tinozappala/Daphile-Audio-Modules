"""Comparable passive A/B and USB-port reports, no inferred sound ranking."""
import hashlib,time
from pathlib import Path
import engine,player
from detection import scan
from runtime import observe

def playback(mac):
 import math
 s=player.rpc(mac,['status','-','1','tags:u']);tracks=s.get('playlist_loop',[])
 if s.get('mode')!='play' or len(tracks)!=1 or not tracks[0].get('url') or s.get('mixer volume') is None:raise RuntimeError('Playing sample and volume must be observable')
 if 'time' not in s or type(s['time']) not in (int,float,str):raise RuntimeError('Playback position unavailable or invalid')
 try:position=float(s['time'])
 except (ValueError,OverflowError):raise RuntimeError('Playback position unavailable or invalid') from None
 if not math.isfinite(position) or position<0:raise RuntimeError('Playback position unavailable or invalid')
 return {'sample_hash':hashlib.sha256(tracks[0]['url'].encode()).hexdigest(),'volume':s['mixer volume'],'position':position,'mode':s['mode']}

def profile_issues(journal,h):
 if not journal or journal.get('status')!='committed':return []
 issues=[]
 if journal.get('hardware_id')!=h['hardware_id']:issues.append('Active profile belongs to different hardware')
 for c in journal.get('changes',[]):
  try:
   engine.guard(c,h)
   if engine.setting_value(c)!=c['after']:issues.append('Active profile setting overridden: '+c['parameter'])
  except (OSError,RuntimeError,ValueError) as e:issues.append('Active profile verification failed: '+str(e))
 return issues

def capture(kind,label,seconds=30,state=engine.STATE):
 if kind not in ['ab','ports'] or not label.isalnum() or len(label)>32 or (kind=='ab' and label not in ['A','B','C','D']):raise ValueError('Invalid comparison kind/label')
 if not 10<=seconds<=600:raise ValueError('Comparison observation 10..600 seconds')
 with engine.locked(state) as root:
  h=scan();t=player.target(h);before=playback(t['mac']);journal=engine.load(root/'current.json') if (root/'current.json').exists() else None;policy_issues=profile_issues(journal,h);measurement=observe(seconds);end=scan();after=playback(t['mac']);hw=h['dac']['pcm']['hw'];actual=end['dac'].get('pcm',{}).get('hw')
  same_sample=before['sample_hash']==after['sample_hash'] and before['volume']==after['volume'];advanced=after['position']-before['position']>=seconds*.7
  reasons=engine.compare(measurement,measurement)+policy_issues+profile_issues(journal,end)
  if not same_sample or not advanced:reasons.append('Sample/volume changed or playback did not advance')
  if end['hardware_id']!=h['hardware_id'] or actual!=hw:reasons.append('Hardware or ALSA format changed')
  if not all(engine.health(end).values()):reasons.append('Playback health incomplete')
  identity={k:v for k,v in t['identity'].items() if k!='path'}
  report={'kind':kind,'label':label,'status':'observed-window' if not reasons else 'inconclusive','created':time.time(),'host_uuid':t['host_uuid'],'dac_identity':identity,'audio_format':hw,'playback':before,'measurement':measurement,'issues':reasons,'usb':h['dac'].get('usb'),'profile':engine.load(root/'current.json').get('profile') if (root/'current.json').exists() else 'pre-AutoTune','sound_quality_claim':False}
  engine.atomic(root/(('ab-' if kind=='ab' else 'port-')+label+'.json'),report)
 return report

def compare(rows):
 reasons=[]
 if len(rows)<2:reasons.append('At least two captures required')
 reference=rows[0] if rows else {}
 for row in rows:
  if row.get('status')!='observed-window':reasons.append(row.get('label','?')+': capture incomplete or legacy report without comparable metadata')
  for key in ['host_uuid','dac_identity','audio_format']:
   if key not in row or row.get(key)!=reference.get(key):reasons.append(row.get('label','?')+': '+key+' differs or unknown')
  for key in ['sample_hash','volume']:
   if key not in row.get('playback',{}) or row.get('playback',{}).get(key)!=reference.get('playback',{}).get(key):reasons.append(row.get('label','?')+': sample/volume differs or unknown')
  metrics=row.get('measurement',{})
  if metrics.get('kernel_log_observable') is not True or metrics.get('cpu_percent',{}).get('cpu') is None or metrics.get('observed_alsa_xrun_states') is None or metrics.get('new_kernel_errors') is None:reasons.append(row.get('label','?')+': measurement observability incomplete')
  duration=row.get('measurement',{}).get('duration');base=reference.get('measurement',{}).get('duration')
  if not duration or not base or abs(duration-base)>max(1,base*.1):reasons.append(row.get('label','?')+': observation durations differ or unknown')
  pos=row.get('playback',{}).get('position');basepos=reference.get('playback',{}).get('position')
  if pos is None or basepos is None or abs(pos-basepos)>2:reasons.append(row.get('label','?')+': starting sample positions differ or unknown')
 summaries=[]
 for row in rows:
  m=row.get('measurement',{});usb=row.get('usb') or {}
  summaries.append({'label':row.get('label'),'profile':row.get('profile'),'usb_path':usb.get('path'),'controller':usb.get('controller'),'cpu_percent':m.get('cpu_percent',{}).get('cpu'),'context_switches_per_second':m.get('context_switches_per_second'),'interrupts_per_second':m.get('interrupts_per_second'),'observed_alsa_xrun_states':m.get('observed_alsa_xrun_states'),'kernel_log_observable':m.get('kernel_log_observable'),'new_kernel_errors':m.get('new_kernel_errors')})
 return {'status':'comparable-observed-windows' if not reasons else 'inconclusive','reasons':sorted(set(reasons)),'captures':summaries,'sound_quality_claim':False,'automatic_winner':None,'limitations':['Repeated controlled trials required to infer a performance difference','No electrical audio test or inferred audible ranking','Missing or different material, volume, start position or format prevents comparison']}

def report(kind,state=engine.STATE):
 if kind not in ['ab','ports']:raise ValueError('Invalid comparison kind')
 with engine.locked(state) as root:
  rows=[engine.load(p) for p in sorted(root.glob('ab-[ABCD].json' if kind=='ab' else 'port-*.json'))];result=compare(rows);engine.atomic(root/('comparison-'+kind+'.json'),result)
 return result
