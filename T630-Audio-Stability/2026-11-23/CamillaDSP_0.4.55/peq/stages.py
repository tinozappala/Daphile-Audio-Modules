"""Future DRC interface: data contracts only, no convolution/optimizer implementation."""
from dataclasses import dataclass
from typing import Protocol
import math,re
from .model import ValidationError,RATES
@dataclass(frozen=True)
class FirReference:
 asset_id:str
 sha256:str
 samplerate:int
 channels:int
 taps:int
 def validate(self):
  if not isinstance(self.asset_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',self.asset_id):raise ValidationError('FIR must reference managed asset ID, never arbitrary path')
  if not isinstance(self.sha256,str) or not re.fullmatch(r'[0-9a-f]{64}',self.sha256):raise ValidationError('FIR checksum required')
  if type(self.samplerate) is not int or self.samplerate not in RATES or type(self.channels) is not int or self.channels!=2 or type(self.taps) is not int or not 1<=self.taps<=1048576:raise ValidationError('Invalid FIR compatibility metadata')
  return self
@dataclass(frozen=True)
class StageResult:
 filters:dict
 pipeline:list
 headroom_bound_db:dict
 channels:int=2
class StageCompiler(Protocol):
 stage_id:str
 def compile(self,profile:dict,samplerate:int)->StageResult:...
def compose(stages:list[StageResult]):
 filters={};pipeline=[];bounds={'L':0.,'R':0.}
 for stage in stages:
  if not isinstance(stage,StageResult) or type(stage.channels) is not int or stage.channels!=2 or not isinstance(stage.filters,dict) or not isinstance(stage.pipeline,list) or not isinstance(stage.headroom_bound_db,dict) or set(stage.headroom_bound_db)!=set(bounds) or set(filters)&set(stage.filters):raise ValidationError('Stage channel/name/headroom contract mismatch')
  if any(type(value) not in (int,float) or not math.isfinite(value) for value in stage.headroom_bound_db.values()):raise ValidationError('Finite stage headroom bounds required')
  filters.update(stage.filters);pipeline.extend(stage.pipeline)
  for ch in bounds:
   bounds[ch]+=stage.headroom_bound_db[ch]
   if not math.isfinite(bounds[ch]):raise ValidationError('Stage headroom sum overflow')
 return StageResult(filters,pipeline,bounds)
