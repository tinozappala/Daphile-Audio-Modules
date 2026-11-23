"""Strict REW Generic text import; preview with explicit unsupported-line diagnostics."""
import re
from .model import new_band,validate,new_profile,ValidationError
MAX_BYTES=65536
NUM=r'([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?)'
TYPES={'PK':'Peaking','LSQ':'Lowshelf','HSQ':'Highshelf','LPQ':'Lowpass','HPQ':'Highpass','NO':'Notch'}
def preview(text,channel='L',rate=48000):
    if not isinstance(text,str) or len(text.encode('utf8'))>MAX_BYTES or '\x00' in text: raise ValidationError('Invalid or oversized REW text')
    if channel not in ('L','R'): raise ValidationError('Invalid channel')
    bands=[]; issues=[]; indices=set()
    for line_no,line in enumerate(text.splitlines(),1):
        if line_no==1:line=line.lstrip('\ufeff')
        entry=re.match(r'^\s*Filter\s+(\d+)\s*:',line,re.I)
        if not entry:
            if re.match(r'^\s*Filter\b',line,re.I) and not re.fullmatch(r'\s*Filter Settings file\s*',line,re.I):
                issues.append({'line':line_no,'reason':'Riga filtro non riconosciuta: controlla numero e separatore «:».'})
            continue
        index=entry[1].lstrip('0') or '0'
        if index=='0' or index in indices:
            issues.append({'line':line_no,'reason':'Numero del filtro nullo o duplicato.'});continue
        indices.add(index)
        # REW exports unused slots as ON/OFF None. They are not filters and
        # must not reject an otherwise valid export or consume a PEQ band.
        if re.fullmatch(r'\s*Filter\s+\d+\s*:\s*(?:ON|OFF)\s+None\s*',line,re.I):continue
        m=re.fullmatch(r'\s*Filter\s+\d+\s*:\s*(ON|OFF)\s+(\w+)\s+Fc\s+'+NUM+r'\s+Hz(?:\s+Gain\s+'+NUM+r'\s+dB)?(?:\s+Q\s+'+NUM+r')?\s*',line,re.I)
        if not m: issues.append({'line':line_no,'reason':'Sintassi del filtro non riconosciuta: nessuna approssimazione applicata.'}); continue
        on,kind,freq,gain,q=m.groups(); kind=kind.upper()
        # LS/HS in REW use shelf slope conventions; cannot silently map to a Q shelf.
        if kind not in TYPES or q is None or (TYPES.get(kind) in ('Peaking','Lowshelf','Highshelf') and gain is None):
            issues.append({'line':line_no,'reason':'Filtro non supportato o Q/guadagno esplicito mancante: '+kind}); continue
        b=new_band(TYPES[kind],float(freq),float(gain or 0),float(q)); b['enabled']=on.upper()=='ON'
        try:
            p=new_profile(); p['peq'][channel]=[b]; validate(p,rate)
        except ValidationError as e: issues.append({'line':line_no,'reason':str(e)}); continue
        bands.append(b)
    if len(bands)>24: issues.append({'line':0,'reason':'Sono presenti più di 24 filtri: il limite è 24 per canale.'})
    if not bands: issues.append({'line':0,'reason':'Nessun filtro supportato trovato. Esporta da REW le impostazioni dei filtri come testo Generic.'})
    return {'channel':channel,'bands':bands,'issues':issues,'can_apply':bool(bands) and not issues}
