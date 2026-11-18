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

LMS_PINS={'Control/Commands.pm':'78f5411c3e058945b546431369debd1449989875d443c632493fb85889ab7949',
          'Control/Request.pm':'a8faaf05abb7aa1ffe906380980e83086277c95fe37614ebf164c0dd3355a1b7',
          'Control/Queries.pm':'10e0c4c5698fc3bb4b19123e105e9a72d201e6b9ff3550537eece585156c4f97',
          'Player/Playlist.pm':'205c36b9561e61417dc99271fc8572e7bab6599f68185e47b1a7b339be65e789',
          'Player/Client.pm':'751431d37b42711fcadf7dbff83b30539081fc5ee864863fa15a90cd0433b82c',
          'Player/Sync.pm':'958fae520dba8174dc98c6a84bd0bb35b92883d8ce6f23b5e0dfc38a1500a080',
          'Player/StreamingController.pm':'1e31177baa93f61328db30a1ab6cd2d289d70778e515adab9c503b3c10b3e215',
          'Player/Source.pm':'38478a08386e277db71fcb5bc6dd9ec9100d876434d39586361eaf970c0e11be',
          'Player/Song.pm':'e31f8093139ffae5e4e6626e42e97e8d2ab798f9791b68a69d6a8bae99708e57',
          'Web/JSONRPC.pm':'0a400e61a792d31871f24b18a997fe8f5cad197174c7ab0ed5f164ade1389014'}

# Exact native local volatile-file semantics used by playlist_key.
LMS_PINS.update({'Player/Protocols/Volatile.pm': 'e2d91583ecb47fd877c6430fa094164067e239b1af8f953782baceb3a81a9e71', 'Music/Info.pm': '66093fc1abd5befa3bcb6b51896bb4379ee44de719d66d2d3dcdf4ff2c1fcd84', 'Utils/Misc.pm': 'c3bb6587406c75243d12d289e30a010affdab4f8232dfd1394e00d01606a23c6'})


def qualified_lms():
    for name,digest in LMS_PINS.items():
        path=Path('/opt/mediaserver/Slim')/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Native stopped-playlist API not qualified for this LMS build')


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
