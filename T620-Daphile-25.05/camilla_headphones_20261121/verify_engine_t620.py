from pathlib import Path
import json,subprocess,sys,tempfile
h=Path('/mnt/hdd/system/daphile-audio-host')
m=h/'modules/DaphileCamillaDSP'
r=m/'releases'/json.loads((m/'current.json').read_text())['release']
state=m/'data';sys.path.insert(0,str(r))
from peq.model import compile_config,RATES
store=json.loads((state/'presets.json').read_text())['presets']
with tempfile.TemporaryDirectory(prefix='camilla-check-') as d:
 for ident in ('headphones-denon-ah-d9200','headphones-hifiman-edition-xs'):
  p=store[ident]
  for rate in RATES:
   config=compile_config(p,rate,512,
       {'type':'Stdin','channels':2,'format':'S32_LE'},
       {'type':'Stdout','channels':2,'format':'S32_LE'})
   filename=Path(d)/'verify.json';filename.write_text(json.dumps(config))
   process=subprocess.run([str(r/'bin/camilladsp'),'--check',str(filename)],capture_output=True,text=True,timeout=10)
   if process.returncode!=0:
    print('FAIL',p['name'],rate,process.stdout[-500:],process.stderr[-500:]);raise SystemExit(1)
  print('VALIDATED_BY_ENGINE',p['name'],len(RATES),'rates',flush=True)
