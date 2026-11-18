"""Read-only process activity; context switches are explicitly not wakeups."""
import os,time
from pathlib import Path
from detection import read

def values(path):
 result={}
 for line in (read(path) or '').splitlines():
  key,sep,value=line.partition(':')
  try:result[key]=int(value.strip().split()[0])
  except (ValueError,IndexError):pass
 return result

def snapshot(base=Path('/proc')):
 result={}
 for directory in base.glob('[0-9]*'):
  try:
   text=(directory/'stat').read_text();tail=text[text.rfind(')')+2:].split()
   if len(tail)<22:continue
   threads={}
   for task in (directory/'task').glob('[0-9]*'):
    text=(task/'stat').read_text();stat=text[text.rfind(')')+2:].split();status=values(task/'status');sched=(read(task/'schedstat') or '').split()
    threads[task.name]={'start':stat[19],'voluntary':status.get('voluntary_ctxt_switches'),'involuntary':status.get('nonvoluntary_ctxt_switches'),'wait_ns':int(sched[1]) if len(sched)>=3 else None,'slices':int(sched[2]) if len(sched)>=3 else None}
   result[directory.name]={'name':read(directory/'comm'),'start':tail[19],'ticks':int(tail[11])+int(tail[12]),'rss_bytes':int(tail[21])*os.sysconf('SC_PAGE_SIZE'),'threads':threads,'io':values(directory/'io')}
  except (OSError,ValueError,IndexError):continue
 return result

def delta(a,b):return b-a if isinstance(a,int) and isinstance(b,int) and b>=a else None

def calculate(first,last,elapsed,hz,schedstats=False):
 rows=[]
 for pid,old in first.items():
  new=last.get(pid)
  if not new or old['start']!=new['start']:continue
  ticks=delta(old['ticks'],new['ticks']);matched=[(a,new['threads'][tid]) for tid,a in old['threads'].items() if tid in new['threads'] and new['threads'][tid]['start']==a['start']]
  context={}
  for field in ['voluntary','involuntary']:
   counts=[delta(a[field],b[field]) for a,b in matched];context[field]=sum(counts)/elapsed if counts and all(x is not None for x in counts) else None
  waits=[delta(a['wait_ns'],b['wait_ns']) for a,b in matched];slices=[delta(a['slices'],b['slices']) for a,b in matched]
  wait=sum(waits) if schedstats and waits and all(x is not None for x in waits) else None
  count=sum(slices) if schedstats and slices and all(x is not None for x in slices) else None
  io={key:delta(old['io'].get(key),new['io'].get(key)) for key in ['read_bytes','write_bytes','rchar','wchar']}
  rows.append({'pid':int(pid),'name':new['name'],'cpu_percent_one_core':100*ticks/hz/elapsed if ticks is not None and hz else None,'voluntary_context_switches_per_second':context['voluntary'],'involuntary_context_switches_per_second':context['involuntary'],'mean_runnable_wait_per_timeslice_ns':wait/count if wait is not None and count else None,'rss_bytes':new['rss_bytes'],'rss_delta_bytes':new['rss_bytes']-old['rss_bytes'],'io_delta_bytes':io,'matched_threads':len(matched),'threads_changed':set(old['threads'])!=set(new['threads']),'wakeups_per_second':None})
 return sorted(rows,key=lambda x:x['cpu_percent_one_core'] if x['cpu_percent_one_core'] is not None else -1,reverse=True)

def run(seconds=30):
 if not 1<=seconds<=600:raise ValueError('Process observation 1..600 seconds')
 first=snapshot();start=time.monotonic();time.sleep(seconds);last=snapshot();elapsed=time.monotonic()-start
 try:hz=os.sysconf('SC_CLK_TCK')
 except (OSError,ValueError):hz=None
 schedstats=read('/proc/sys/kernel/sched_schedstats')=='1'
 return {'status':'observed','duration':elapsed,'processes':calculate(first,last,elapsed,hz,schedstats),'newly_observed_pids':len(set(last)-set(first)),'no_longer_observed_pids':len(set(first)-set(last)),'scheduler_accounting_enabled':schedstats,'wakeups_measured':False,'max_scheduling_latency_measured':False,'limitations':['Only processes with readable counters at both endpoints are listed; short-lived processes may be missed','CPU100% means one core, processes may exceed100%','Context switches are not wakeups','Mean runnable wait per timeslice is not maximum scheduling latency','Only threads present with the same start time at both endpoints are counted','RSS growth in one window is not proof of a memory leak','Physical and logical I/O counters do not certify NAS throughput']}
