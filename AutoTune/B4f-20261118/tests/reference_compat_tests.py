"""Historical evidence is readable for CAS, never current measurement authority."""
from pathlib import Path
import argparse,copy,json,sys,tempfile,uuid
p=argparse.ArgumentParser();p.add_argument('--lib',type=Path,default=Path(__file__).parent/'working/lib');a=p.parse_args();sys.path.insert(0,str(a.lib.resolve()))
from autotune_v2.reference_state import ReferenceStore,reference_proof,validate_proof
binding=dict(boot_id=str(uuid.uuid4()),hardware_sha256='1'*64,preferences_sha256='2'*64,workload_sha256='3'*64)
with tempfile.TemporaryDirectory() as temp:
 store=ReferenceStore(temp)
 old=reference_proof(binding,[dict(status='observed',historical_protocol='previous-window') for _ in range(3)],False)
 store.archive('history',old);path=Path(temp)/'verified-reference.json';path.write_text(json.dumps(old));before=path.read_bytes()
 assert store.head_id()==old['id'] and path.read_bytes()==before
 assert store.current(binding)['status']=='historical' and path.read_bytes()==before
 try:store.save(old,expected_id=old['id'])
 except ValueError:pass
 else:raise AssertionError('Historical measurements accepted as a new proof')
 assert path.read_bytes()==before
 malformed=copy.deepcopy(old);malformed['observations'][0]=None
 try:validate_proof(malformed)
 except ValueError:pass
 else:raise AssertionError('Malformed history accepted')
 print(json.dumps(dict(status='PASS',checks=4,historical_bytes_unchanged=True,new_authority_granted=False)))
