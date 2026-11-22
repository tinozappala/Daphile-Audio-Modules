from pathlib import Path
import sys,json
here=Path(__file__).resolve().parent
root=here.parent
sys.path.insert(0,str(root))
from plugin_framework.lms_package import package
source=here/'DaphileCamillaDSP'
for name in ('backend/PROVENANCE.json','RELEASE_SIGNATURE.json'):
 (source/name).unlink(missing_ok=True)
raw,entry=package(source,'DaphileCamillaDSP')
entry['archive']='DaphileCamillaDSP-0.4.51.zip'
(here/entry['archive']).write_bytes(raw)
(here/'APPROVAL.json').write_text(json.dumps(entry,indent=2)+'\n','utf8')
print(entry['archive'],entry['sha256'],entry['bytes'],len(entry['files']))
