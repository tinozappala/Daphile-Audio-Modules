"""Versioned PCM profiles and deterministic RBJ coefficients, Python stdlib only."""
import cmath, copy, math, re, uuid
from functools import lru_cache
RATES=(44100,48000,88200,96000,176400,192000,352800,384000)
TYPES=('Peaking','Lowshelf','Highshelf','Lowpass','Highpass','Notch')
GAIN_TYPES=TYPES[:3]
class ValidationError(ValueError): pass

def number(v,lo,hi,name):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
        raise ValidationError(f'{name}: expected finite number in [{lo}, {hi}]')
    return float(v)
def boolean(v,name):
    if type(v) is not bool: raise ValidationError(name+': expected boolean')
    return v
def keys(obj,allowed,required=None):
    if not isinstance(obj,dict) or set(obj)-set(allowed) or (set(required or allowed)-set(obj)):
        raise ValidationError('Invalid or missing fields: '+str(allowed))
def identifier(v):
    if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',v): raise ValidationError('Invalid identifier')
    return v
def flat():
    return {'schema_version':1,'id':'flat','name':'Flat','mode':'independent','enabled':False,'bypass':False,
            'peq':{'L':[],'R':[]},'channel_bypass':{'L':False,'R':False},
            'levels':{'pre_gain':0.,'L':0.,'R':0.,'auto_headroom':False,'margin_db':1.},
            'routing':{'format':'PCM'},'drc':{'enabled':False,'fir_refs':[]},
            'compatibility':{'rates':list(RATES)}}
def new_profile(name='Nuovo preset'):
    p=flat(); p['id']=uuid.uuid4().hex; p['name']=name; return p
def new_band(kind='Peaking',freq=1000,gain=0,q=1):
    return {'id':uuid.uuid4().hex,'type':kind,'freq':freq,'gain':gain if kind in GAIN_TYPES else 0,'q':q,'enabled':True,'label':''}
def validate(p,rate=48000):
    keys(p,flat().keys())
    if type(p['schema_version']) is not int or p['schema_version']!=1: raise ValidationError('Unsupported schema version')
    identifier(p['id'])
    if not isinstance(p['name'],str) or not 1<=len(p['name'].strip())<=80: raise ValidationError('Invalid preset name')
    if p['mode'] not in ('independent','linked'): raise ValidationError('Invalid channel mode')
    if type(rate) is not int or rate not in RATES: raise ValidationError('Unsupported sample rate')
    boolean(p['enabled'],'enabled'); boolean(p['bypass'],'bypass')
    keys(p['levels'],flat()['levels'].keys()); keys(p['peq'],['L','R']); keys(p['channel_bypass'],['L','R'])
    for name in ('pre_gain','L','R'): number(p['levels'][name],-120,0,name)
    number(p['levels']['margin_db'],0,12,'margin'); boolean(p['levels']['auto_headroom'],'auto_headroom')
    keys(p['routing'],['format'])
    if p['routing']['format']!='PCM': raise ValidationError('DSD requires original direct path; PCM DSP disabled')
    keys(p['drc'],['enabled','fir_refs'])
    if p['drc']!={'enabled':False,'fir_refs':[]}: raise ValidationError('DRC FIR reserved for future module; not implemented')
    keys(p['compatibility'],['rates'])
    if not isinstance(p['compatibility']['rates'],list) or not p['compatibility']['rates'] or any(type(r) is not int or r not in RATES for r in p['compatibility']['rates']) or rate not in p['compatibility']['rates']:
        raise ValidationError('Invalid compatibility rates')
    ids=set()
    for ch in ('L','R'):
        boolean(p['channel_bypass'][ch],'channel bypass')
        bands=p['peq'][ch]
        if not isinstance(bands,list) or len(bands)>24: raise ValidationError('Maximum 24 configured bands per channel')
        for b in bands:
            keys(b,new_band().keys()); identifier(b['id'])
            if b['id'] in ids: raise ValidationError('Duplicate band identifier')
            ids.add(b['id'])
            if b['type'] not in TYPES: raise ValidationError('Unsupported filter')
            number(b['freq'],20,min(20000,rate/2*(1-1e-6)),'frequency')
            number(b['gain'],-18,12,'gain'); number(b['q'],.1,20,'Q'); boolean(b['enabled'],'band enabled')
            if b['type'] not in GAIN_TYPES and b['gain']!=0: raise ValidationError('Gain is not applicable for this filter')
            if not isinstance(b['label'],str) or len(b['label'])>80: raise ValidationError('Invalid label')
            coefficients(b,rate)
    if p['mode']=='linked':
        strip=lambda bands:[{k:v for k,v in b.items() if k!='id'} for b in bands]
        if strip(p['peq']['L'])!=strip(p['peq']['R']): raise ValidationError('Linked channels must contain equivalent bands')
    return copy.deepcopy(p)
def coefficients(b,rate):
    """RBJ Audio EQ Cookbook; shelves specified by Q (not shelf slope)."""
    w=2*math.pi*b['freq']/rate; c=math.cos(w); s=math.sin(w); alpha=s/(2*b['q']); A=10**(b['gain']/40); kind=b['type']
    if kind=='Peaking': num=[1+alpha*A,-2*c,1-alpha*A]; den=[1+alpha/A,-2*c,1-alpha/A]
    elif kind=='Lowpass': num=[(1-c)/2,1-c,(1-c)/2]; den=[1+alpha,-2*c,1-alpha]
    elif kind=='Highpass': num=[(1+c)/2,-(1+c),(1+c)/2]; den=[1+alpha,-2*c,1-alpha]
    elif kind=='Notch': num=[1,-2*c,1]; den=[1+alpha,-2*c,1-alpha]
    elif kind in ('Lowshelf','Highshelf'):
        k=2*math.sqrt(A)*alpha
        if kind=='Lowshelf':
            num=[A*((A+1)-(A-1)*c+k),2*A*((A-1)-(A+1)*c),A*((A+1)-(A-1)*c-k)]
            den=[(A+1)+(A-1)*c+k,-2*((A-1)+(A+1)*c),(A+1)+(A-1)*c-k]
        else:
            num=[A*((A+1)+(A-1)*c+k),-2*A*((A-1)+(A+1)*c),A*((A+1)+(A-1)*c-k)]
            den=[(A+1)-(A-1)*c+k,2*((A-1)-(A+1)*c),(A+1)-(A-1)*c-k]
    else: raise ValidationError('Unsupported biquad')
    sos=[v/den[0] for v in num]+[1.,den[1]/den[0],den[2]/den[0]]
    if not all(math.isfinite(v) for v in sos): raise ValidationError('Non-finite coefficients')
    a1,a2=sos[4:]; poles=[(-a1+sgn*cmath.sqrt(a1*a1-4*a2))/2 for sgn in (-1,1)]
    if max(map(abs,poles))>=1: raise ValidationError('Unstable biquad')
    return sos
def response(sos,freq,rate):
    z=cmath.exp(-2j*math.pi*freq/rate)
    n=sos[0]+sos[1]*z+sos[2]*z*z; d=1+sos[4]*z+sos[5]*z*z
    return 20*math.log10(max(abs(n/d),1e-15))
def max_gain(sos):
    """Conservative rational extrema using 70-digit arithmetic near DC/Nyquist."""
    from decimal import Decimal, localcontext
    with localcontext() as context:
        context.prec=70
        v=[Decimal.from_float(float(x)) for x in sos]
        def poly(a,b,c): return [(a-c)**2+b*b,2*b*(a+c),4*a*c]
        p=poly(*v[:3]); q=poly(v[3],*v[4:])
        a=p[2]*q[1]-p[1]*q[2]; b=2*(p[2]*q[0]-p[0]*q[2]); c=p[1]*q[0]-p[0]*q[1]
        candidates=[Decimal(-1),Decimal(1)]
        if a:
            disc=b*b-4*a*c
            if disc>=0: candidates += [(-b+sign*disc.sqrt())/(2*a) for sign in (-1,1)]
        elif b: candidates.append(-c/b)
        ratios=[Decimal(1)]
        for x in candidates:
            if -1<=x<=1:
                den=q[0]+q[1]*x+q[2]*x*x
                if den<=0: raise ValidationError('Ill-conditioned headroom denominator')
                ratios.append(max(Decimal(0),(p[0]+p[1]*x+p[2]*x*x)/den))
        return float(10*max(ratios).log10())+1e-5

def active_bands(p,ch):
    if not p['enabled'] or p['bypass'] or p['channel_bypass'][ch]: return []
    return [b for b in p['peq'][ch] if b['enabled']]
@lru_cache(maxsize=1024)
def _cached_max_gain(sos):
    return max_gain(sos)

def _level_result(p,sos):
    bounds={ch:sum(_cached_max_gain(tuple(s)) for s in sos[ch]) for ch in sos}
    level=p['levels']; effective={ch:level['pre_gain']+level[ch] for ch in sos}
    headroom={ch:-(bounds[ch]+effective[ch]) for ch in sos}
    return {'bound_db':bounds,'auto_attenuation_db':0.,'effective_gain_db':effective,'headroom_db':headroom,
            'clipping_risk':any(v<0 for v in headroom.values()),
            'note':'Conservative steady-state bound; transient and intersample overs are not guaranteed absent.'}

def level_analysis(p,rate=48000):
    """Read-only margin calculation; never computes graph points or touches audio."""
    p=validate(p,rate)
    sos={ch:[coefficients(b,rate) for b in active_bands(p,ch)] for ch in ('L','R')}
    return _level_result(p,sos)

def analysis(p,rate=48000,points=600):
    p=validate(p,rate); freq=[20*(min(20000,rate/2*.999)/20)**(i/(points-1)) for i in range(points)]
    sos={ch:[coefficients(b,rate) for b in active_bands(p,ch)] for ch in ('L','R')}
    # Retain the legacy field for preset compatibility, but never insert an
    # automatic gain reduction. Headroom remains a measurement, not a control.
    result=_level_result(p,sos);effective=result['effective_gain_db']
    curves={ch:[effective[ch]+sum(response(s,f,rate) for s in sos[ch]) for f in freq] for ch in sos}
    return dict(result,frequency=freq,curves=curves)

def compile_config(p,rate,chunksize,capture,playback):
    p=validate(p,rate)
    if type(chunksize) is not int or chunksize not in (256,512,1024,2048,4096): raise ValidationError('Invalid chunksize')
    # Device definitions are trusted administrator inputs, never accepted from GUI/import.
    if capture.get('type') not in ('RawFile','Stdin','Alsa') or playback.get('type') not in ('File','Stdout','Alsa'): raise ValidationError('Unsupported trusted routing adapter')
    a=analysis(p,rate,2); filters={}; pipeline=[]
    for index,ch in enumerate(('L','R')):
        name='level_'+ch; names=[name]
        filters[name]={'type':'Gain','parameters':{'gain':a['effective_gain_db'][ch]}}
        for b in active_bands(p,ch):
            name=ch+'_'+b['id']; names.append(name); sos=coefficients(b,rate)
            filters[name]={'type':'Biquad','parameters':dict(type='Free',b0=sos[0],b1=sos[1],b2=sos[2],a1=sos[4],a2=sos[5])}
        pipeline.append({'type':'Filter','channels':[index],'names':names})
    return {'title':'Daphile PEQ '+p['id'],'devices':{'samplerate':rate,'chunksize':chunksize,'volume_ramp_time_ms':100.,'volume_limit':0.,'capture':copy.deepcopy(capture),'playback':copy.deepcopy(playback)},'filters':filters,'pipeline':pipeline}

def copy_channel(p,source,target):
    p=copy.deepcopy(p); p['peq'][target]=copy.deepcopy(p['peq'][source])
    for b in p['peq'][target]: b['id']=uuid.uuid4().hex
    return p
