# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Conservative, read-only ALSA playback ownership observation.
Not a kernel lease: a sampled postcondition for cooperative DAC transitions.
"""
import re
from pathlib import Path
class OutputNotReady(RuntimeError):pass
class OutputOwnershipMismatch(RuntimeError):pass


class OutputNotRunning(OutputNotReady):
    def __init__(self,state):
        self.state=state
        super().__init__(f'ALSA playback is {state}')

def _text(path):
    try:return path.read_text(encoding='ascii')
    except (OSError,UnicodeError) as exc:raise OutputNotReady('ALSA playback observation unavailable') from exc

def _field(text,name):
    matches=re.findall(r'^'+re.escape(name)+r'\s*:\s*(\S+)',text,re.M)
    if len(matches)!=1:raise OutputNotReady('ALSA playback observation incomplete')
    return matches[0]

def _tgid(proc,pid):
    if not pid.isdigit() or int(pid)<=0:raise OutputOwnershipMismatch('Invalid ALSA owner PID')
    value=_field(_text(proc/pid/'status'),'Tgid')
    if not value.isdigit():raise OutputNotReady('ALSA owner thread identity unavailable')
    return int(value)

def verify_output_owner(playback,rate,record,identity,proc=Path('/proc')):
    proc=Path(proc)
    if playback.get('type')!='Alsa' or playback.get('channels')!=2 or playback.get('format')!='S32_LE':
        raise OutputOwnershipMismatch('Unqualified ALSA output contract')
    match=re.fullmatch(r'hw:([A-Za-z0-9_]+),0',playback.get('device',''))
    if not match:raise OutputOwnershipMismatch('Unqualified direct ALSA device')
    if identity(record['pid'])!=record:raise OutputOwnershipMismatch('Camilla process identity changed')
    cards=[card for card in (proc/'asound').glob('card[0-9]*')
           if re.fullmatch(r'card[0-9]+',card.name) and _text(card/'id').strip()==match[1]]
    if len(cards)!=1:raise OutputNotReady('ALSA card identity is not unique')
    substreams=sorted((cards[0]/'pcm0p').glob('sub[0-9]*'))
    if not substreams:raise OutputNotReady('ALSA playback substreams unavailable')
    def observe():
        active=[]
        for sub in substreams:
            hw=_text(sub/'hw_params').strip()
            status=_text(sub/'status').strip()
            if hw=='closed' and status=='closed':continue
            if hw=='closed' or status=='closed':raise OutputNotReady('ALSA playback is transitioning')
            owner=_field(status,'owner_pid')
            if _tgid(proc,owner)!=record['pid']:
                raise OutputOwnershipMismatch('ALSA playback held by another process')
            if (_field(hw,'format')!='S32_LE' or _field(hw,'channels')!='2'
                    or _field(hw,'rate')!=str(rate)):
                raise OutputOwnershipMismatch('ALSA hardware format differs from qualified output')
            state=_field(status,'state')
            if state!='RUNNING':raise OutputNotRunning(state)
            active.append((sub.name,owner))
        if len(active)!=1:raise OutputNotReady('Single active ALSA playback substream not established')
        return active[0]
    first=observe()
    if identity(record['pid'])!=record:raise OutputOwnershipMismatch('Camilla process identity changed')
    if observe()!=first:raise OutputNotReady('ALSA playback owner changed during observation')
    return {'card':cards[0].name,'substream':first[0],'rate':rate,'format':'S32_LE','owner_verified':True}

def verify_output_released(playback,proc=Path('/proc')):
    """Require all playback substreams closed, twice; no foreign owner touched."""
    proc=Path(proc)
    if playback.get('type')!='Alsa':raise OutputOwnershipMismatch('Unqualified ALSA release contract')
    match=re.fullmatch(r'hw:([A-Za-z0-9_]+),0',playback.get('device',''))
    if not match:raise OutputOwnershipMismatch('Unqualified direct ALSA release device')
    def observe():
        cards=[p for p in (proc/'asound').glob('card[0-9]*')
               if re.fullmatch(r'card[0-9]+',p.name) and _text(p/'id').strip()==match[1]]
        if len(cards)!=1:raise OutputNotReady('Release: ALSA card identity not unique')
        subs=sorted(p for p in (cards[0]/'pcm0p').glob('sub[0-9]*') if re.fullmatch(r'sub[0-9]+',p.name))
        if not subs:raise OutputNotReady('Release: ALSA playback substreams unavailable')
        for sub in subs:
            if _text(sub/'hw_params').strip()!='closed' or _text(sub/'status').strip()!='closed':
                raise OutputNotReady('Release: ALSA playback still held')
        return cards[0].name,tuple(p.name for p in subs)
    first=observe()
    if observe()!=first:raise OutputNotReady('Release: ALSA device changed during observation')
    return first
