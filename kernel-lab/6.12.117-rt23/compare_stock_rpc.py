# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Strict comparison of matched physical LMS RPC runs; no network or writes."""
import argparse,json,math,statistics,sys
from pathlib import Path
KEYS=('lms_version_rpc_ms','library_count_rpc_ms','library_search_rpc_ms')
LIMITS={'median':.05,'p95':.10,'p99':.10}
def load(p):
    return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def quantile(s,q):
    return sorted(s)[math.ceil(len(s)*q)-1]
def extract(doc,k):
    m=doc['metrics'][k]
    assert m['status']=='PASS_WITH_LIMITATIONS' and not m['errors'],k
    r=m['raw']
    assert len(r)>=100,k
    assert all(isinstance(x['ms'],(int,float)) and math.isfinite(x['ms']) and x['ms']>0 for x in r),k
    values={str(x['value']) for x in r}
    assert len(values)==1,(k,'result changes within run')
    s=[x['ms'] for x in r]
    return str(m['query']),values.pop(),{ 'median':statistics.median(s),'p95':quantile(s,.95),'p99':quantile(s,.99)}
def compare(a,b,a2):
    result={'scope':'LMS LAN RPC only; cannot qualify kernel on its own','metrics':{},'gate':'PASS'}
    for k in KEYS:
        try:
            x,y,z=(extract(t,k) for t in (a,b,a2))
            assert x[:2]==y[:2]==z[:2], 'query or catalog/result changed'
            for stat,limit in LIMITS.items():
                l,c,r=x[2][stat],y[2][stat],z[2][stat]
                ref=(l+r)/2
                drift=abs(l-r)/ref
                delta=(c-ref)/ref
                status='RETEST' if drift>limit else ('REGRESSION' if delta>limit else 'PASS')
                result['metrics'][k+'.'+stat]={'baseline_before_ms':l,'candidate_ms':c,'baseline_after_ms':r,'baseline_drift_pct':100*drift,'change_pct':100*delta,'limit_pct':100*limit,'status':status}
        except (AssertionError,KeyError,TypeError,ValueError,ZeroDivisionError,IndexError) as e:
            result['metrics'][k]={'status':'INVALID','reason':str(e)}
    states={m['status'] for m in result['metrics'].values()}
    result['gate']='FAIL' if states&{'INVALID','REGRESSION'} else ('RETEST' if 'RETEST' in states else 'PASS_RPC_ONLY')
    return result
def test():
    def fake(factor=1,value='123'):
        return {'metrics':{k:{'status':'PASS_WITH_LIMITATIONS','errors':[],'query':[k],'raw':[{'ms':(10+i/100)*factor,'value':value} for i in range(100)]} for k in KEYS}}
    assert compare(fake(),fake(),fake())['gate']=='PASS_RPC_ONLY'
    assert compare(fake(),fake(1.2),fake())['gate']=='FAIL'
    assert compare(fake(),fake(),fake(1.3))['gate']=='RETEST'
    assert compare(fake(),fake(value='124'),fake())['gate']=='FAIL'
    short=fake();short['metrics'][KEYS[0]]['raw'].pop()
    assert compare(fake(),short,fake())['gate']=='FAIL'
    print('SELFTEST PASS 5/5: identical, regression, drift, changed result, short sample')
def main():
    p=argparse.ArgumentParser();p.add_argument('--selftest',action='store_true');p.add_argument('--before');p.add_argument('--candidate');p.add_argument('--after');p.add_argument('--out')
    a=p.parse_args()
    if a.selftest:test();return 0
    if not all((a.before,a.candidate,a.after,a.out)):p.error('Three matched runs and output path required')
    result=compare(load(a.before),load(a.candidate),load(a.after))
    with open(a.out,'x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
    return 0 if result['gate']=='PASS_RPC_ONLY' else 1
if __name__=='__main__':raise SystemExit(main())
