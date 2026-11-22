from pathlib import Path
import json,sys
here=Path(__file__).resolve().parent
sys.path.insert(0,str(here.parent/'tmp'))
from t620_stock_ssh import run
entry=json.loads((here/'APPROVAL.json').read_text('utf8'))
defaults=json.loads((here/'HEADPHONE_PROFILES.json').read_text('utf8'))
source='DEFAULTS='+repr(defaults)+'\nENTRY='+repr(entry)+'\n'+(here/'install_body.py').read_text('utf8')
result=run(source,data=(here/entry['archive']).read_bytes(),timeout=120)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
if result.returncode==0:(here/'INSTALL_051.jsonl').write_bytes(result.stdout)
sys.exit(result.returncode)
