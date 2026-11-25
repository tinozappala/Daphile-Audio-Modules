# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Analyze an eight-second stock snapshot without changing the target."""
import json,sys
from pathlib import Path
def parse(text):
    out={}
    for line in text.splitlines():
        parts=line.split()
        if parts and parts[0] in ('cpu','cpu0','cpu1','cpu2','cpu3','ctxt','intr','softirq'):
            out[parts[0]]=list(map(int,parts[1:]))
    return out
def irqs(text):
    lines=text.splitlines()
    n=len(lines[0].split())
    out={}
    for line in lines[1:]:
        if ':' not in line:continue
        name,rest=line.split(':',1);p=rest.split()
        if len(p)>=n and all(x.isdigit() for x in p[:n]):
            out[name.strip()]={'counts':list(map(int,p[:n])),'name':' '.join(p[n:])}
    return out
def analyze(j):
    a,b=j['first'],j['last'];sec=(b['t_ns']-a['t_ns'])/1e9
    assert sec>0
    x,y=parse(a['stat']),parse(b['stat'])
    cpus={}
    for k in x:
        if k.startswith('cpu'):
            delta=[v-u for u,v in zip(x[k],y[k])][:8]
            assert all(i>=0 for i in delta)
            total=sum(delta)
            cpus[k]=100*(total-delta[3]-delta[4])/total if total else None
    ia,ib=irqs(a['interrupts']),irqs(b['interrupts'])
    rates={}
    for k in ia.keys()&ib.keys():
        if ia[k]['name']!=ib[k]['name']:continue
        delta=[v-u for u,v in zip(ia[k]['counts'],ib[k]['counts'])]
        if min(delta)<0:continue
        if sum(delta)>0:rates[k]={'name':ib[k]['name'],'total_per_s':sum(delta)/sec,'per_cpu_per_s':[v/sec for v in delta]}
    return {'kernel':j['kernel'],'duration_s':sec,'cpu_busy_percent':cpus,'context_switch_per_s':(y['ctxt'][0]-x['ctxt'][0])/sec,'interrupt_per_s':(y['intr'][0]-x['intr'][0])/sec,'softirq_per_s':(y['softirq'][0]-x['softirq'][0])/sec,'top_active_irqs':sorted(rates.items(),key=lambda kv:-kv[1]['total_per_s'])[:10],'xhci_irq':{k:v for k,v in rates.items() if 'xhci' in v['name']},'isolated':j['isolated'],'nohz_full':j['nohz_full'],'trace_available':j['trace_available'],'lockstat_available':j['lockstat_available'],'loaded_module_count':len(j['module_names']),'scope':'uncontrolled observational stock run, not comparable performance verdict'}
def main():
    p=Path(sys.argv[1]);out=Path(sys.argv[2])
    d=json.loads(p.read_text(encoding='utf-8-sig'))
    s=d['sections']['optimization_snapshot']
    assert s['exit']==0
    result=analyze(json.loads(s['output']))
    with out.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
