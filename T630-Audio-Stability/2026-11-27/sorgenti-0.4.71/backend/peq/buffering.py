"""Explicit bounded buffer settings; never changes rates or filter counts."""
import json
from pathlib import Path
from .model import ValidationError


def parameters(state):
    path=Path(state)/'audio-buffer.json'
    if not path.exists() and not path.is_symlink():return 2048,4
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024:
        raise ValidationError('Impostazioni buffer non verificate')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValidationError('Parametro buffer duplicato')
            result[key]=value
        return result
    value=json.loads(path.read_text(encoding='utf8'),object_pairs_hook=unique)
    if not isinstance(value,dict) or set(value) not in ({'chunksize'},{'chunksize','target_chunks'}):
        raise ValidationError('Parametri buffer non supportati')
    chunk=value['chunksize'];target=value.get('target_chunks',4)
    if type(chunk) is not int or chunk not in (256,512,1024,2048,4096) or type(target) is not int or not 1<=target<=8:
        raise ValidationError('Dimensione buffer non supportata')
    return chunk,target


def apply_targets(config,route):
    for key in ('target_level','queuelimit'):
        if key in route:config['devices'][key]=route[key]
    return config


def capture_buffer(rate,chunksize):
    """Capacity for a blocked startup consumer; no additional prefill target.

    A fixed 8192-frame ring lasts only 21 ms at 384 kHz. arecord drains each
    period of at most 1024 frames as soon as possible; the larger ring absorbs startup
    backpressure without changing the DSP or playback target latency.
    """
    if type(rate) is not int or not 8000<=rate<=384000:
        raise ValidationError('Frequenza PCM fuori dai limiti verificabili')
    if type(chunksize) is not int or chunksize not in (256,512,1024,2048,4096):
        raise ValidationError('Blocco PCM non supportato')
    frames=max(8192,chunksize*8,(rate+4)//5)
    period=min(1024,chunksize)
    return ((frames+period-1)//period)*period,period
