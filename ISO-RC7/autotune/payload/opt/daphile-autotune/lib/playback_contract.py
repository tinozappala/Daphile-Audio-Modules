"""Observed native player state; absent values are never synthetic zeroes.

The native LMS status omits time/index when no playing song exists. Read its
documented query results in that case. These requests do not change playback.
"""
import math
import re

CURSOR_TOLERANCE_SECONDS = .25


class PreparationRequired(RuntimeError):
    """A known user action, with the original diagnostic kept for support."""

    def __init__(self, code, diagnostic):
        super().__init__(diagnostic)
        self.code = code


def number(value, label, low, high=None, integer=False):
    if type(value) not in (int, float, str):
        raise RuntimeError('Player '+label+' is unavailable or invalid')
    if integer and (type(value) is float or (isinstance(value,str) and not re.fullmatch(r'-?\d+',value))):
        raise RuntimeError('Player '+label+' must be an observed integer')
    try:
        result=int(value) if integer else float(value)
        finite=math.isfinite(result)
    except (ValueError,OverflowError):
        raise RuntimeError('Player '+label+' is unavailable or invalid') from None
    if not finite or result<low or (high is not None and result>high):
        raise RuntimeError('Player '+label+' is outside its valid range')
    return result


def gains(mac, rpc):
    volume=number(rpc(mac,['mixer','volume','?']).get('_volume'),'volume',-100,100)
    mute=number(rpc(mac,['mixer','muting','?']).get('_muting'),'mute',0,1,integer=True)
    return dict(volume=volume,mute=mute)


def validate(saved):
    if not isinstance(saved,dict) or not isinstance(saved.get('mac'),str) or not saved['mac']:
        raise RuntimeError('Explicit player identity required')
    urls=saved.get('urls')
    if not isinstance(urls,list) or len(urls)>1000 or any(not isinstance(u,str) or not u for u in urls):
        raise RuntimeError('Complete observed playlist required')
    if type(saved.get('index')) is not int or not 0<=saved['index']<max(1,len(urls)):
        raise RuntimeError('Observed playlist index required')
    if saved.get('mode') not in ('play','pause','stop'):
        raise RuntimeError('Observed playback mode required')
    if type(saved.get('time')) not in (int,float):raise RuntimeError('Observed finite playback cursor required')
    number(saved['time'],'cursor',0)
    for key,maximum in (('repeat',2),('shuffle',2),('mute',1),('power',1)):
        if type(saved.get(key)) is not int or not 0<=saved[key]<=maximum:
            raise RuntimeError('Observed player '+key+' required')
    if type(saved.get('volume')) not in (int,float):raise RuntimeError('Observed player volume required')
    number(saved['volume'],'volume',-100,100)
    return saved


def snapshot(mac, rpc):
    state=rpc(mac,['status','0','1000','tags:u'])
    count=number(state.get('playlist_tracks'),'playlist count',0,1000,integer=True)
    # An explicitly observed count of zero proves an empty queue. A missing
    # count or a missing loop for a nonempty queue proves nothing.
    tracks=state.get('playlist_loop',[] if count==0 else None)
    if not isinstance(tracks,list) or len(tracks)!=count or any(not isinstance(t,dict) or not t.get('url') for t in tracks):
        raise RuntimeError('Playlist cannot be captured completely')
    index=state['playlist_cur_index'] if 'playlist_cur_index' in state else rpc(mac,['playlist','index','?']).get('_index')
    cursor=state['time'] if 'time' in state else rpc(mac,['time','?']).get('_time')
    saved=dict(mac=mac,urls=[t['url'] for t in tracks],mode=state.get('mode'),
        index=number(index,'index',0,max(0,count-1),integer=True),time=number(cursor,'cursor',0),
        repeat=number(state.get('playlist repeat'),'repeat',0,2,integer=True),
        shuffle=number(state.get('playlist shuffle'),'shuffle',0,2,integer=True),
        power=number(state.get('power'),'power',0,1,integer=True),**gains(mac,rpc))
    return validate(saved)


def preparation_requirements(saved, *, allow_playing=False):
    validate(saved)
    if len(saved['urls'])>=1000:
        raise PreparationRequired('queue-full', 'The owned fixture would exceed the bounded 1000-item playlist observation')
    if saved['power']!=1:
        raise PreparationRequired('player-off', 'AutoTune requires a powered player')
    if saved['mode']=='play' and not allow_playing:
        raise PreparationRequired('pause-first', 'AutoTune requires the player to stop before a trial')
    if saved['shuffle']!=0:
        # LMS exposes permutation order, not its raw queue and shufflelist.
        # Even a direct shuffle preference write followed by append invokes
        # reshuffle and can irreversibly change this saved public order.
        raise PreparationRequired('shuffle-on', 'AutoTune requires an already disabled shuffle mode; the native queue permutation cannot be preserved safely')


def replaceable(saved):
    preparation_requirements(saved)
    if saved['mode']!='stop' or saved['time']>=CURSOR_TOLERANCE_SECONDS:
        raise PreparationRequired('prepare-player', 'AutoTune requires an already stopped, powered player with cursor below 0.25 s; paused, playing or retained bookmarks cannot be replaced safely')
    return saved


def append_capacity(rpc,queue_count):
    """Fresh server preference required before an append, never guessed.

    The pinned LMS addTracks can delete already traversed ORIGINAL entries to
    make room. Explicit zero disables this playlist count limit only. Missing,
    noncanonical or malformed observations do not mean unlimited. This read
    is not an atomic reservation against another LMS client.
    """
    if type(queue_count) is not int or not 0<=queue_count<1000:
        raise RuntimeError('Complete bounded playlist count required before append')
    result=rpc('',['pref','maxPlaylistLength','?'])
    value=result.get('_p2') if isinstance(result,dict) else None
    if type(value) is int:
        limit=value
    elif type(value) is str and re.fullmatch(r'0|[1-9][0-9]*',value):
        try:limit=int(value)
        except ValueError:raise RuntimeError('Native playlist capacity is malformed') from None
    else:
        raise RuntimeError('Explicit native maxPlaylistLength preference is unavailable or malformed')
    if limit<0:
        raise RuntimeError('Native playlist capacity must be nonnegative')
    if limit and queue_count+1>limit:
        raise PreparationRequired('queue-full', 'Native playlist has no spare capacity for one owned fixture; original entries are preserved')
    return dict(server_preference='maxPlaylistLength',observed_value=limit,
                queue_count=queue_count,required_slots=1,limit_disabled=limit==0)


def standalone(mac,rpc,qualify):
    """Observe one player, including inactive peers and persistent rejoin.

    Required native APIs are probed before interpreting either reply.
    syncQuery explicitly emits '-' when Client::isSynced(allPlayers) is false.
    prefQuery/addResult always emits _p2, including JSON null for an undefined
    syncgroupid. A missing key is NOT the same observation. The explicit false
    values below cannot trigger Sync::restoreSync in this qualified build.
    """
    if not callable(qualify):
        raise RuntimeError('Qualified native sync API required')
    qualify()
    live=rpc(mac,['sync','?'])
    if not isinstance(live,dict) or type(live.get('_sync')) is not str:
        raise RuntimeError('Native sync topology is grouped or unavailable; single-player ownership required')
    if live['_sync']!='-':
        raise PreparationRequired('player-group', 'Native sync topology is grouped or unavailable; single-player ownership required')
    persistent=rpc(mac,['playerpref','syncgroupid','?'])
    if not isinstance(persistent,dict) or '_p2' not in persistent:
        raise RuntimeError('Explicit persistent syncgroupid observation required')
    value=persistent['_p2']
    empty=(value is None or type(value) is int and value==0 or type(value) is str and value in ('','0'))
    if not empty:
        raise PreparationRequired('player-group', 'Persistent sync group or malformed topology must be preserved')
    return dict(protocol='native-single-player-v1',live_sync='-',persistent_syncgroupid=value)


def same_state(saved,current,playlist_key):
    """Full readback, including strict cursor tolerance and untouched gains."""
    validate(saved);validate(current)
    return ([playlist_key(u) for u in saved['urls']]==[playlist_key(u) for u in current['urls']]
        and all(current[k]==saved[k] for k in ('mac','index','mode','repeat','shuffle','volume','mute','power'))
        and abs(current['time']-saved['time'])<CURSOR_TOLERANCE_SECONDS)

__release_signature__ = 'Tino Zappala'
