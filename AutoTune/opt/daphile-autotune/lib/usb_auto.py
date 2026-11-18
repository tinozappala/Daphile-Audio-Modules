"""Two controlled captures after a physical cable move, with scoped recovery."""
from pathlib import Path
import engine,player,comparison,session,usb_wizard
from detection import scan

def capture(label,seconds=30,state=engine.STATE,confirmed=False):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 if not confirmed:raise ValueError('Consent for repeated USB audio segments required')
 if type(seconds)!=int or not 30<=seconds<=120:raise ValueError('USB repeated window30..120 seconds')
 root=Path(state);record=engine.load(root/'usb-wizard.json')
 if label not in record['labels']:raise ValueError('Socket outside current wizard')
 h=scan();target=player.target(h);identity={k:v for k,v in target['identity'].items() if k!='path'}
 if target['host_uuid']!=record['host_uuid'] or identity!=record['dac_identity']:raise RuntimeError('Original host/DAC not verified after cable move')
 player.assert_library_idle(h);saved=player.playback_snapshot(target['mac']);now=comparison.playback(target['mac']);reference=record['reference']
 if now['sample_hash']!=reference['sample_hash'] or now['volume']!=reference['volume']:raise RuntimeError('Original track and volume required; external material or volume is preserved')
 lease=session.Lease(root,saved,seconds*2+180,'usb',restore_playback=True)
 result={'status':'running','label':label,'completed':0,'repeats':2,'instructions':'Il segmento viene ripetuto automaticamente due volte. Sposta solo il cavo quando richiesto.'}
 engine.atomic(root/'usb-auto-progress.json',result)
 try:
  for repetition in range(2):
   player.wait_audio(target['mac'],20)
   player.rpc(target['mac'],['time',str(reference['position'])])
   result['capture']=usb_wizard.capture(label,seconds,root);result['completed']=repetition+1
   engine.atomic(root/'usb-auto-progress.json',result)
  result.update(status='captured-two-repetitions',progress=usb_wizard.progress(engine.load(root/'usb-wizard.json')))
 except BaseException as e:
  result.update(status='failed',error=type(e).__name__+': '+str(e));raise
 finally:
  try:
   result['playback_recovery']=lease.finish()
   if result['playback_recovery'].get('restored') is not True:result['status']='external-playback-preserved'
  except Exception as e:result.update(status='recovery-required',recovery_error=str(e))
  engine.atomic(root/'usb-auto-progress.json',result)
 return result
