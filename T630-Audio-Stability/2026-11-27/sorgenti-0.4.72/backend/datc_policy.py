# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""DATC A1 decision slice: single synchronous authority for active PCM.
No I/O, hardware ownership claims or second background supervisor.
NativeAudio owns serialized effects and all existing guards/journals.
"""
from enum import Enum,auto
class Action(Enum):
    BYPASS=auto(); WAIT=auto(); REJECT=auto(); INSPECT=auto()
    KEEP=auto(); SUSPEND=auto(); REOPEN=auto(); FAULT=auto(); STOP=auto(); FILTERS=auto(); ARM=auto()

def source_action(kind,source,mode):
    if kind=="DSD":return Action.BYPASS
    if source is None and mode=="stop":return Action.WAIT
    return Action.INSPECT if kind=="PCM" else Action.REJECT

def route_action(mode,current,observed,expected,alive):
    if observed is None or (expected is not None and observed["rate"]!=expected):
        return Action.KEEP if mode=="waiting-pcm" else Action.SUSPEND
    if not alive:return Action.FAULT
    return Action.REOPEN if observed!=current else Action.KEEP

def local_action(current,observed):
    return Action.KEEP if current==observed else Action.SUSPEND

def timeout_action(local_verified,since,now):
    return Action.KEEP if local_verified and (since is None or now-since<12) else Action.STOP


def armed_action(kind,mode):
    if kind=="DSD":return Action.BYPASS
    if mode!="play":return Action.WAIT
    return Action.INSPECT if kind=="PCM" else Action.REJECT


def intent_action(requested,enabled,mode,ledger,route):
    if mode in ("pcm-active","waiting-pcm") and ledger:
        return Action.KEEP if requested==enabled else Action.FILTERS
    if requested and (ledger or route) and not enabled:return Action.REJECT
    if requested==enabled and (requested or not (ledger or route)):return Action.KEEP
    return Action.ARM if requested else Action.BYPASS

def verify_native_snapshot(before,after):
    """Readback contract for a native handoff; does not attest raw DSD."""
    if not isinstance(after,dict) or any(before.get(k)!=after.get(k) for k in
            ('urls','index','repeat','shuffle','volume','mode','source','topology')):
        raise RuntimeError('Stato nativo o coda non conservati')
    if before.get('mode') in ('pause','stop') and before.get('time')!=after.get('time'):
        raise RuntimeError('Posizione nativa non conservata')
    if before.get('mode')=='play':
        import math
        a=before.get('time');b=after.get('time')
        if (type(a) not in (int,float) or type(b) not in (int,float)
                or not math.isfinite(a) or not math.isfinite(b) or a<0 or b<0 or b<a-.5):
            raise RuntimeError('Posizione di riproduzione nativa non confermata')


def control_needs_work(enabled, mode):
    return (enabled and mode != "fault-paused") or mode in ("pcm-active", "waiting-pcm")
