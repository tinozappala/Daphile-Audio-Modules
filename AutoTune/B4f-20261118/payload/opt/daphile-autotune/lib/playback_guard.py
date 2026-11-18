"""Restore only state owned by a measurement; never clear an external queue."""
import hashlib
import player

def equivalent_queue(saved,current):
 return [player.playlist_key(u) for u in saved['urls']]==[player.playlist_key(u) for u in current['urls']]

def restore_position(saved):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 current=player.playback_snapshot(saved['mac'])
 if not equivalent_queue(saved,current):return {'status':'external-queue-preserved','restored':False}
 if current['index']!=saved['index']:
  # A naturally advanced or externally selected track is not forced backwards.
  return {'status':'different-track-preserved','restored':False}
 player.rpc(saved['mac'],['time',str(saved['time'])])
 player.settle_mode(saved['mac'],saved['mode'])
 final=player.playback_snapshot(saved['mac'])
 if not equivalent_queue(saved,final) or final['index']!=saved['index']:raise RuntimeError('Queue or track changed during position restoration')
 if any(final[key]!=saved[key] for key in ['mode','repeat','shuffle']):raise RuntimeError('Playback mode/flags were not restored')
 if abs(final['time']-saved['time'])>6:raise RuntimeError('Saved playback position did not settle')
 return {'status':'position-restored','restored':True}

def finish_search(saved):
 raise RuntimeError('Legacy playback execution is unavailable: use the owned V2 workflow. Saved playback bookmarks are retained; no old music is replayed.')
 current=player.playback_snapshot(saved['mac'])
 if not equivalent_queue(saved,current):return {'status':'external-queue-preserved','restored':False}
 return restore_position(saved)
