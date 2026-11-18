"""All original UI domains backed by actual observations and journals."""
from pathlib import Path
import engine,rule_engine,boot_recovery,automation
from detection import scan

def report(state=engine.STATE):
 h=scan();root=Path(state)
 def saved(name):
  p=root/name
  return engine.load(p) if p.exists() else {'status':'not-measured'}
 changes=[]
 if (root/'changes.jsonl').exists():
  import json
  with (root/'changes.jsonl').open() as stream:
   from collections import deque
   for line in deque(stream,maxlen=100):
    try:changes.append(json.loads(line))
    except ValueError:changes.append({'status':'invalid-log-line'})
 return {'SYSTEM':{'system':h['system'],'kernel':h['kernel'],'memory':h['memory'],'thermal':h['thermal']},'DAC':h['dac'],'USB':h['usb'],'ALSA':h['alsa'],'CPU':h['cpu'],'IRQ':h['irq'],'NETWORK':{'interfaces':h['network'],'storage':h['storage'],'probe':saved('network.json')},'SERVICES':{'classification':h['services'],'processes':h['squeezelite'],'openrc':h['openrc']},'BENCHMARK':{'baseline':saved('baseline.json'),'last':saved('benchmark.json'),'scheduler':saved('scheduler.json'),'stability':saved('stability.json'),'adaptive':saved('adaptive.json')},'PROFILES':{'supported':engine.PROFILES,'current':saved('current.json'),'last_known_good':saved('last-known-good.json'),'rules':rule_engine.evaluate(h),'boot':boot_recovery.status(root)},'LOGS':changes,'AUTOMATIONS':automation.status(root),'sound_quality_claim':False}
