import csv
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path('tmp/rme_eq_scipy').resolve()))
import numpy as np
from scipy.optimize import least_squares

RATE = 48000
BASE = Path('CONSOLIDAMENTO_T620_20261121/PROFILI_CUFFIE_RME.json')
DATA = json.loads(BASE.read_text(encoding='utf8'))
rng = np.random.default_rng(6411)

def response(freqs, kind, fc, gain, q):
    w=2*np.pi*fc/RATE
    c=np.cos(w)
    a=np.sin(w)/(2*q)
    A=10**(gain/40)
    k=2*np.sqrt(A)*a
    if kind=='peak':
        b=np.array([1+a*A,-2*c,1-a*A])
        d=np.array([1+a/A,-2*c,1-a/A])
    elif kind=='low':
        b=A*np.array([(A+1)-(A-1)*c+k,2*((A-1)-(A+1)*c),(A+1)-(A-1)*c-k])
        d=np.array([(A+1)+(A-1)*c+k,-2*((A-1)+(A+1)*c),(A+1)+(A-1)*c-k])
    elif kind=='high':
        b=A*np.array([(A+1)+(A-1)*c+k,-2*((A-1)+(A+1)*c),(A+1)+(A-1)*c-k])
        d=np.array([(A+1)-(A-1)*c+k,2*((A-1)-(A+1)*c),(A+1)-(A-1)*c-k])
    z=np.exp(-2j*np.pi*freqs/RATE)
    return 20*np.log10(np.abs((b[0]+b[1]*z+b[2]*z*z)/(d[0]+d[1]*z+d[2]*z*z)))

def curve(freq, bands):
    result=np.zeros(freq.shape)
    for band in bands:
        result+=response(freq, band['kind'], band['frequency'], band['gain'], band['q'])
    return result

def arr_to_bands(arr, last):
    return [dict(kind='low' if i==0 else last if i==4 else 'peak',
                 frequency=float(np.exp(arr[i*3])), gain=float(arr[i*3+1]),
                 q=float(np.exp(arr[i*3+2]))) for i in range(5)]

def packed(bands):
    return np.array([v for b in bands for v in (np.log(b['frequency']),b['gain'],np.log(b['q']))])

def quantize(bands):
    rounded=[]
    for item in bands:
        row=dict(item)
        f=row['frequency']
        row['frequency']=int(round(f/10)*10 if f>2040 else round(f))
        row['gain']=round(row['gain']*2)/2
        row['q']=max(.5,round(row['q']*10)/10)
        rounded.append(row)
    return rounded

def make_baseline(i):
    result=[]
    for b in DATA[i]['channels']['left']:
        result.append(dict(kind='low' if b['band']==1 and b['type']==1 else 'high' if b['band']==5 and b['type']==1 else 'peak',
                           frequency=b['frequency'],gain=b['gain'],q=b['q']))
    return result

def read_data(filename):
    with open(filename,encoding='utf8') as handle:
        rows=list(csv.DictReader(handle))
    f=np.array([float(r['frequency']) for r in rows])
    target=np.array([float(r['equalization']) for r in rows])
    keep=(f>=20)&(f<=16000)
    return f[keep],target[keep]

def rms(v):
    return float(np.sqrt(np.mean(v*v)))

def metrics(f,target,bands):
    diff=curve(f,bands)-target
    return {'20_16000_hz_rms_db':rms(diff),
            '20_8000_hz_rms_db':rms(diff[f<=8000]),
            '8000_16000_hz_rms_db':rms(diff[f>=8000]),
            'maximum_boost_db':float(max(curve(f,bands))),
            'weighted_rms_db':float(np.sqrt(np.average(diff*diff,weights=np.where(f>8000,.4,1))))}

def fit(f,target,baseline):
    weights=np.sqrt(np.where(f>8000,.4,1))
    boundsf=[(35,250),(35,800),(250,3000),(600,5500),(2000,14000)]
    lo=np.array([x for fmin,fmax in boundsf for x in (np.log(fmin),-10,np.log(.5))])
    hi=np.array([x for i,(fmin,fmax) in enumerate(boundsf) for x in (np.log(fmax),10,np.log(9.9 if i<3 else 5))])
    candidates=[]
    for last in ('peak','high'):
        seeds=[baseline,
               [dict(kind=k,frequency=freq,gain=g,q=q) for k,freq,g,q in
                 [('low',105,4,.7),('peak',190,-3,1),('peak',900,-2,1.5),('peak',2000,5,1), (last,7000,-2,2)]]]
        for n in range(5):
            fs=[rng.uniform(70,160),rng.uniform(80,500),rng.uniform(600,1600),rng.uniform(1700,4000),rng.uniform(5000,10000)]
            gs=rng.uniform(-4,5,5)
            seeds.append([dict(kind='low' if i==0 else last if i==4 else 'peak',frequency=fs[i],gain=gs[i],q=rng.uniform(.6,2.5)) for i in range(5)])
        for seed in seeds:
            x0=np.clip(packed(seed),lo+1e-7,hi-1e-7)
            res=least_squares(lambda x:(curve(f,arr_to_bands(x,last))-target)*weights,x0,
                              bounds=(lo,hi),max_nfev=250,ftol=1e-7)
            bs=quantize(arr_to_bands(res.x,last))
            score=metrics(f,target,bs)['weighted_rms_db']
            candidates.append((score,bs))
    return min(candidates,key=lambda v:v[0])

output={}
for i,model in enumerate(('Denon AH-D9200','HiFiMAN Edition XS')):
    f,target=read_data(['tmp/denon_autoeq.csv','tmp/xs_autoeq.csv'][i])
    base=make_baseline(i)
    score,new=fit(f,target,base)
    output[model]={'before':{'bands':base,'metrics':metrics(f,target,base)},
                   'after':{'bands':new,'metrics':metrics(f,target,new)}}
    print(model,json.dumps(output[model],ensure_ascii=False),flush=True)
Path('tmp/rme_headphone_fit.json').write_text(json.dumps(output,indent=2,ensure_ascii=False),encoding='utf8')
