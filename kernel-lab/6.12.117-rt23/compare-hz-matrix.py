# SPDX-License-Identifier: MIT
# Tino Zappala, Milano - 2026
"""Bounded local HZ x timer comparison. No physical-device access."""
from pathlib import Path
import hashlib,json,re,resource,subprocess,time,datetime,statistics,sys
R=Path('/opt/daphile-kernel')
lab=R/'vm-lab/hz-comparison'
status=R/'logs/hz300-build.status'
initrd=lab/'aloop-hz-comparison.cpio.gz'
variants={
 '1000-jiffies':('6.12.117-rt23-daphile-v6-hrtimer-lab',''),
 '1000-hrtimer':('6.12.117-rt23-daphile-v6-hrtimer-lab','snd_aloop.timer_source=hrtimer'),
 '300-jiffies':('6.12.117-rt23-daphile-v6-hrtimer-hz300-lab',''),
 '300-hrtimer':('6.12.117-rt23-daphile-v6-hrtimer-hz300-lab','snd_aloop.timer_source=hrtimer')
}
orders=[
 ['1000-jiffies','300-hrtimer','1000-hrtimer','300-jiffies'],
 ['300-jiffies','1000-hrtimer','300-hrtimer','1000-jiffies'],
 ['1000-hrtimer','300-jiffies','1000-jiffies','300-hrtimer']
]
def verify():
 for hz,rel in [('1000',variants['1000-jiffies'][0]),('300',variants['300-jiffies'][0])]:
  d=R/'artifacts'/rel
  subprocess.run(['sha256sum','-c','SHA256SUMS'],cwd=d,check=True)
  c=(d/'.config').read_text()
  assert re.search(r'^CONFIG_HZ='+hz+r'$',c,re.M)
  assert 'CONFIG_PREEMPT_RT=y' in c and 'CONFIG_SND_ALOOP=y' in c
def read_counter(s,tag):
 m=re.search(r'^GUEST_COUNTERS_'+tag+r'\n(.*?)^GUEST_COUNTERS_'+tag+r'_END$',s,re.M|re.S)
 if not m:return {}
 c=m.group(1)
 out={}
 for key in ('intr','ctxt','processes'):
  z=re.search(r'^'+key+r'\s+(\d+)',c,re.M)
  if z:out[key]=int(z.group(1))
 for key in ('TIMER','SCHED','HRTIMER'):
  z=re.search(r'^\s*'+key+r':\s+([0-9 \t]+)$',c,re.M)
  if z:out[key+'_softirq']=sum(map(int,z.group(1).split()))
 return out
def run(v,n,dest):
 rel,arg=variants[v];base=R/'artifacts'/rel
 prefix=dest/f'{n:02d}-{v}'
 img=prefix.with_suffix('.raw')
 with img.open('xb') as f:f.truncate(4*1024*1024)
 cmd=['qemu-system-x86_64','-machine','accel=kvm','-cpu','host','-smp','2','-m','1024','-display','none','-serial','none','-monitor','none','-nic','none','-no-reboot','-kernel',str(base/'bzImage'),'-initrd',str(initrd),'-append','rdinit=/init panic=10 '+arg,'-drive',f'file={img},if=virtio,format=raw,cache=none']
 t=time.monotonic();u0=resource.getrusage(resource.RUSAGE_CHILDREN)
 print('BOOT',n,v,flush=True)
 with prefix.with_suffix('.qemu.log').open('xb') as log:
  try:rc=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
  except subprocess.TimeoutExpired:rc=124
 u1=resource.getrusage(resource.RUSAGE_CHILDREN)
 raw=img.read_bytes().split(b'\0',1)[0]
 prefix.with_suffix('.txt').write_bytes(raw)
 s=raw.decode(errors='replace')
 rows=[dict(re.findall(r'(\w+)=([^\s]+)',l)) for l in s.splitlines() if l.startswith('PCM_RESULT ')]
 timer=s.splitlines()[3].split(',')[0] if len(s.splitlines())>3 else None
 before,after=read_counter(s,'BEFORE'),read_counter(s,'AFTER')
 result={'variant':v,'run':n,'qemu_rc':rc,'elapsed_s':time.monotonic()-t,'qemu_cpu_s':u1.ru_utime+u1.ru_stime-u0.ru_utime-u0.ru_stime,'timer_readback':timer,'rows':rows,'guest_counters_delta':{k:after[k]-before[k] for k in before.keys()&after.keys()},'raw_sha256':hashlib.sha256(raw).hexdigest(),'stop_rounds':len(re.findall(r'^START_STOP_ROUND=',s,re.M))}
 result['pass']=rc==0 and bool(re.search(r'^DAPHILE_ALOOP_VM_END failed=0$',s,re.M)) and len(rows)==19 and all(x.get('status')=='PASS' and x.get('integrity_errors')=='0' for x in rows) and result['stop_rounds']==6 and timer==('hrtimer' if arg else '(null)')
 print(json.dumps({k:x for k,x in result.items() if k!='rows'}),flush=True)
 return result
def main():
 if sys.argv[1:] == ['--preflight']:
  assert initrd.is_file();assert len(orders)==3 and all(set(o)==set(variants) for o in orders)
  print('MATRIX_PREFLIGHT_PASS 12 guests 228 PCM probes');return 0
 deadline=time.monotonic()+7200
 print('WAIT_FOR_LOCAL_BUILD',flush=True)
 while time.monotonic()<deadline:
  s=status.read_text() if status.exists() else ''
  if s.startswith('COMPILED '):break
  if s.startswith('FAILED '):raise RuntimeError(s)
  time.sleep(15)
 else:raise TimeoutError('Local build did not finish in two hours')
 verify()
 dest=lab/('matrix-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
 dest.mkdir()
 print('RESULT_DIR',dest,flush=True)
 results=[]
 for n,v in enumerate((v for order in orders for v in order),1):
  results.append(run(v,n,dest))
  (dest/'results.json').write_text(json.dumps(results,indent=2))
  time.sleep(3)
 summary={}
 for v in variants:
  rows=[x for x in results if x['variant']==v];probes=[p for x in rows for p in x['rows']]
  summary[v]={'passed_guests':sum(x['pass'] for x in rows),'guests':len(rows),'probes':len(probes),'passed_probes':sum(p.get('status')=='PASS' for p in probes),'integrity_errors':sum(int(p['integrity_errors']) for p in probes),'late_intervals':sum(int(p['gap_gt_2period']) for p in probes),'total_intervals':sum(int(p['intervals']) for p in probes),'median_qemu_cpu_s':statistics.median(x['qemu_cpu_s'] for x in rows),'median_guest_ctxt':statistics.median(x['guest_counters_delta']['ctxt'] for x in rows if 'ctxt' in x['guest_counters_delta']) if all('ctxt' in x['guest_counters_delta'] for x in rows) else None}
 (dest/'summary.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps(summary,indent=2),flush=True)
 return 0 if all(x['pass'] for x in results) else 1
if __name__=='__main__':raise SystemExit(main())
