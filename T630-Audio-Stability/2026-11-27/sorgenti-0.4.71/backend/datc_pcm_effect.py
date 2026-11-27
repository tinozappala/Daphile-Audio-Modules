# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Synchronous DATC PCM effect adapter; no parallel state machine or worker.

The physical owner proof is sampled, not a kernel reservation. RME lock proof
is cooperative exclusion only, not hardware setting readback.
"""
from dataclasses import dataclass
from uuid import uuid4
import re
import time
import math

class PCMCommitError(RuntimeError):pass
class EffectDeadlineExceeded(PCMCommitError):pass

# Conservative acceptance budgets, not audible transition targets.
# Existing native timeouts remain in place. Cleanup is never prevented by expiry.
EFFECT_BUDGET_SECONDS={
    "pcm-commit":60.0,
    "pcm-suspend":30.0,
    "pcm-stop":45.0,
    "native-prepare":90.0,
    "native-handoff":90.0,
}


@dataclass(frozen=True)
class EffectToken:
    session:str
    sequence:int
    epoch:int
    generation:int
    operation:str
    issued_at:float
    deadline:float

class EffectAuthority:
    """Issued under NativeAudio's existing session lock, not another FSM."""
    def __init__(self,clock=None):
        self.clock=clock or time.monotonic
        self.session=uuid4().hex
        self.sequence=0
        self.current=None
    def issue(self,epoch,generation,operation,outer_deadline=None):
        now=self.clock()
        deadline=now+EFFECT_BUDGET_SECONDS[operation]
        if outer_deadline is not None:
            if not math.isfinite(outer_deadline):raise ValueError("Invalid outer deadline")
            deadline=min(deadline,outer_deadline)
        if now>=deadline:raise EffectDeadlineExceeded("Budget transizione esaurito")
        self.sequence+=1
        self.current=EffectToken(self.session,self.sequence,epoch,generation,operation,now,deadline)
        return self.current
    def check(self,token,epoch,generation,operation,*,allow_expired=False):
        if (self.current is not token or token.epoch!=epoch or token.generation!=generation
                or token.operation!=operation):
            raise PCMCommitError("Transizione superata o ricevuta non pertinente")
        if not allow_expired and self.clock()>=token.deadline:
            raise EffectDeadlineExceeded("Budget transizione esaurito: "+operation)
    def finish(self,token):
        if self.current is token:self.current=None

def owner_key(proof,rate):
    if (not isinstance(proof,dict) or proof.get("owner_verified") is not True
        or type(proof.get("rate")) is not int or proof["rate"]!=rate
        or proof.get("format")!="S32_LE"
        or not isinstance(proof.get("card"),str) or not re.fullmatch(r"card[0-9]+",proof["card"])
        or not isinstance(proof.get("substream"),str) or not re.fullmatch(r"sub[0-9]+",proof["substream"])):
        raise PCMCommitError("Conferma ownership PCM incompleta")
    return proof["card"],proof["substream"]

@dataclass(frozen=True)
class PCMCommitReceipt:
    rate:int
    device:str
    owner_before:tuple
    owner_after:tuple
    mute:bool
    state:str
    config_matches:bool
    token:EffectToken|None=None
    def verify(self,route,token=None):
        playback=route.get("playback",{})
        if (self.token is not token or type(self.rate) is not int or self.rate!=route.get("rate")
            or self.device!=playback.get("device") or self.mute is not False
            or self.state!="Running" or self.config_matches is not True
            or self.owner_before!=self.owner_after
            or not isinstance(self.owner_before,tuple) or len(self.owner_before)!=2
            or not all(isinstance(x,str) for x in self.owner_before)
            or not re.fullmatch(r"card[0-9]+",self.owner_before[0])
            or not re.fullmatch(r"sub[0-9]+",self.owner_before[1])):
            raise PCMCommitError("Postcondizione commit PCM non verificata")

def execute_pcm_transition(prepare,commit,fence,journal,check_lease,params,playback,token,preflight):
    """Execute one serialized prepare/commit; caller owns cleanup and publication."""
    preflight()
    check_lease()
    route=prepare()
    if (not isinstance(route,dict) or route.get("rate")!=params["rate"]
        or route.get("playback")!=playback or route.get("verified_pcm") is not True
        or route.get("capture",{}).get("format")!=params["format"]):
        raise PCMCommitError("Preparazione PCM non corrispondente alla richiesta")
    fence()
    journal("pcm-ready-muted")
    fence()
    receipt=commit(fence,token)
    if not isinstance(receipt,PCMCommitReceipt):
        raise PCMCommitError("Conferma commit PCM mancante")
    receipt.verify(route,token)
    fence()
    journal("pcm-active")
    fence()
    return route

class PCMReleaseError(RuntimeError):pass

@dataclass(frozen=True)
class PCMReleaseReceipt:
    warm:bool
    remaining_children:int
    output_scope:str
    journal_retired:bool
    token:EffectToken|None=None
    def verify(self,warm,token=None):
        if (self.token is not token or self.warm is not warm or type(self.remaining_children) is not int
                or self.remaining_children!=(1 if warm else 0)
                or self.output_scope not in (('closed',) if warm else ('closed','not-opened'))
                or self.journal_retired is not (not warm)):
            raise PCMReleaseError('Rilascio PCM non confermato')

def execute_pcm_release(pipeline,warm,token):
    receipt=(pipeline.suspend if warm else pipeline.stop)(token)
    if not isinstance(receipt,PCMReleaseReceipt):
        raise PCMReleaseError('Ricevuta rilascio PCM mancante')
    receipt.verify(warm,token)
    return receipt
