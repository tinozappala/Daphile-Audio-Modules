from pathlib import Path
import json,sys
here=Path(__file__).resolve().parent
sys.path.insert(0,str(here.parent/'tmp'))
from t620_stock_ssh import run
profiles=json.loads((here/'HEADPHONE_PROFILES.json').read_text('utf8'))
source='PROFILES='+repr(profiles)+'\n'+(here/'install_profiles_body.py').read_text('utf8')
result=run(source,timeout=100)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
if result.returncode==0:(here/'PROFILI_VERIFICATI.jsonl').write_bytes(result.stdout)
sys.exit(result.returncode)
