"""Restore an owned inactive playlist without starting audio or seeking.

The caller must establish playlist ownership before calling this adapter.
A paused decoder cannot be reconstructed from STOP without playing; retain
its bookmark and report that limit instead of pretending its cursor survived.
"""
import hashlib,math,time
from pathlib import Path
import player
try:from playback_contract import standalone
except ImportError:from autotune_review.playback_contract import standalone

_LMS_CAPABILITY_CACHE=None

def _lms_instance():
    try:
        raw=Path('/run/mediaserver/mediaserver.pid').read_text().strip()
        if not raw.isdigit():return None
        fields=Path('/proc',raw,'stat').read_text().rsplit(') ',1)[1].split()
        return raw,fields[19]
    except (OSError,IndexError):return None

def qualified_lms():
    """Probe the needed protocol capabilities, independently of LMS version."""
    global _LMS_CAPABILITY_CACHE
    instance=_lms_instance();now=time.monotonic()
    if (instance is not None and _LMS_CAPABILITY_CACHE is not None
            and _LMS_CAPABILITY_CACHE[0]==instance and now<_LMS_CAPABILITY_CACHE[1]):return
    for command in (('playlist','index'),('playlist','delete'),('playlist','add'),('playerpref',),('sync',),('stop',)):
        reply=player.rpc('', ['can',*command,'?'])
        if not isinstance(reply,dict) or reply.get('_can') not in (1,'1',True):
            raise RuntimeError('Il server LMS non espone tutte le API necessarie al ripristino AutoTune: '+' '.join(command))
    if instance is not None:_LMS_CAPABILITY_CACHE=(instance,now+30)


def same_queue(a,b):return [player.playlist_key(v) for v in a['urls']]==[player.playlist_key(v) for v in b['urls']]


def restore_inactive(saved):
    if saved.get('mode') not in ('stop','pause'):raise ValueError('Inactive playback required')
    cursor=saved.get('time')
    if isinstance(cursor,bool) or not isinstance(cursor,(int,float)) or not math.isfinite(cursor) or cursor<0:raise ValueError('Finite saved position required')
    mac=saved['mac'];standalone(mac,player.rpc,qualified_lms);deadline=time.monotonic()+20
    def write(command):
        standalone(mac,player.rpc,qualified_lms)
        return player.rpc(mac,command)
    while time.monotonic()<deadline:
        if player.rpc(mac,['connected','?']).get('_connected')==1:break
        time.sleep(.2)
    else:raise RuntimeError('Native player did not reconnect')
    current=player.playback_snapshot(mac)
    same=same_queue(saved,current) and current['index']==saved['index']
    if same and current['mode'] in ('pause','stop'):
        pass
    elif same and saved['mode']=='pause' and current['mode']=='play':
        write(['pause',1])
    else:
        write(['stop'])
        if not same_queue(saved,current):
            # The new controlled workload appends exactly one owned source.
            # Removing just that suffix keeps all original music entries and
            # avoids an unowned empty-queue gap during interruption recovery.
            appended=(len(current['urls'])==len(saved['urls'])+1
                and [player.playlist_key(u) for u in current['urls'][:-1]]==[player.playlist_key(u) for u in saved['urls']])
            if appended:
                fresh=player.playback_snapshot(mac)
                if not same_queue(current,fresh) or fresh['mode']!='stop':raise RuntimeError('External queue change preserved before owned suffix removal')
                write(['playlist','delete',len(saved['urls'])])
            else:
                ids=player.catalog_ids(saved['urls'])
                write(['playlist','clear']);write(['playlist','shuffle',0]);write(['playlist','repeat',0])
                for url,ident in zip(saved['urls'],ids):
                    command=['playlistcontrol','cmd:add','track_id:'+str(ident)] if ident is not None else ['playlist','add',player.lms_item(url)]
                    write(command)
            if not same_queue(saved,player.playback_snapshot(mac)):raise RuntimeError('Original queue not restored')
        if saved['urls']:write(['playlist','index',saved['index'],0,1])
    current=player.playback_snapshot(mac)
    if not same_queue(saved,current) or current['index']!=saved['index']:raise RuntimeError('Queue changed before flags restoration')
    if saved['mode']=='stop' and current['mode']!='stop':write(['stop'])
    for key,command in (('repeat',['playlist','repeat',saved['repeat']]),('shuffle',['playerpref','shuffle',int(saved['shuffle'])])):
        current=player.playback_snapshot(mac)
        if not same_queue(saved,current) or current['index']!=saved['index']:raise RuntimeError('External queue change preserved')
        if current[key]!=saved[key]:write(command)
    final=player.playback_snapshot(mac)
    if not same_queue(saved,final) or any(final[k]!=saved[k] for k in ('index','repeat','shuffle')) or final['mode'] not in ('pause','stop'):
        raise RuntimeError('Inactive restoration incomplete')
    standalone(mac,player.rpc,qualified_lms)
    observed=final.get('time')
    mode=final['mode']==saved['mode'];position=(type(observed) in (int,float) and math.isfinite(observed) and abs(observed-cursor)<.25)
    return dict(status='inactive-playback-restored' if mode and position else 'inactive-queue-restored-bookmark-retained',
                restored=mode and position,queue_restored=True,mode_preserved=mode,position_preserved=position,
                no_play_or_seek_commands=True,playback_not_automatically_resumed=True)

__release_signature__ = 'Tino Zappala'
