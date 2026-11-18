"""Evidence-based resource classification; actual suspension is reported."""
from pathlib import Path
import engine,runtime_ext
from detection import read,command

def services(h):
 from advanced import service_value
 result=[]
 for name in sorted(p.name for p in Path('/etc/init.d').glob('*') if p.is_file()):
  eligible=False;reason='Essential or dependency/usage unknown; retained'
  if name=='bluetooth':
   eligible=not list(Path('/sys/class/bluetooth').glob('hci*'));reason='No Bluetooth controller' if eligible else 'Bluetooth controller present'
  elif name=='cupsd':
   printers=list(Path('/etc/cups/ppd').glob('*.ppd'));connections=command(['ss','-tn','state','established'])
   eligible=not printers and connections is not None and ':631' not in connections;reason='No configured printers or active printing connection' if eligible else 'Printer configuration or usage unknown'
  elif name=='crond':
   paths=list(Path('/var/spool/cron/crontabs').glob('*'))+[Path('/etc/crontab')]
   periodic=any(p.is_file() for d in ['hourly','daily','weekly','monthly'] for p in Path('/etc/cron.'+d).glob('*'))
   jobs=any(any(line.strip() and not line.lstrip().startswith('#') for line in (read(p) or '').splitlines()) for p in paths if p.is_file())
   eligible=not jobs and not periodic;reason='No cron jobs' if eligible else 'Scheduled tasks present'
  if name not in ['bluetooth','cupsd','crond']:state='protected'
  else:
   try:state=service_value(name)
   except Exception:state='unknown';eligible=False
  result.append({'name':name,'state':state,'eligible':eligible,'reason':reason})
 return result

def plan(h):
 from advanced import service_plan
 if h.get('dac',{}).get('status')!='verified-active' or not __import__('usb_target').identified(h.get('dac',{}).get('identity',{})):return {'changes':[],'services':[],'peripherals':[],'unknown_retained':True,'reason':'Verified DAC required before changes'}
 changes=[];classified=services(h)
 names=[r['name'] for r in classified if r['eligible'] and r['state']=='started']
 if names:
  rows=service_plan(h,names)
  for c in rows:c['automatic_unused_check']=True
  changes+=rows
 for row in runtime_ext.peripheral_inventory():
  if row['eligible'] and row['control']=='on':changes.append(runtime_ext.change(h,'peripheral_pm','on','auto',path=row['path'],address=row['address'],driver=row['driver'],pci_class=row['class']))
 return {'changes':changes,'services':classified,'peripherals':runtime_ext.peripheral_inventory(),'unknown_retained':True,'sound_quality_claim':False}

def verify_services(c):
 h={};row=next((r for r in services(h) if r['name']==c['service']),None)
 if not row or not row['eligible']:raise RuntimeError('Service no longer proven unused')

def status(state=engine.STATE):
 from detection import scan
 h=scan();result=plan(h)
 result['actual_suspended']=[r['address'] for r in result['peripherals'] if r['actual_suspended']]
 result['auto_but_active']=[r['address'] for r in result['peripherals'] if r['control']=='auto' and not r['actual_suspended']]
 return result

__release_signature__ = 'Tino Zappala'
