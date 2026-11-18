"""Bounded scheduler event capture in an owned tracefs instance.

Reference: kernel.org/doc/html/latest/trace/{ftrace,events}.html.
This measures an observation window, never a worst-case latency guarantee.
No global tracer, global event filter or global ring buffer is changed.
"""
import collections,json,os,re,select,signal,subprocess,sys,time,uuid
from pathlib import Path

ROOTS=(Path('/sys/kernel/tracing'),Path('/sys/kernel/debug/tracing'))
RUNTIME=Path('/run/daphile-autotune-tracing')
EVENTS=('sched_wakeup','sched_wakeup_new','sched_switch','sched_process_exit')
NAME=re.compile(r'dat-[0-9a-f]{32}')
LINE=re.compile(r'\s(\d+)\.(\d+):\s+(?:sched:)?(sched_\w+):\s+(.*)')

def ticks(pid):
    try:
        data=Path('/proc',str(pid),'stat').read_text();tail=data[data.rfind(')')+2:].split()
        return tail[19] if tail[0] not in ['Z','X'] else None
    except (OSError,IndexError):return None

def boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def identities():
    result={}
    for proc in Path('/proc').glob('[0-9]*'):
        owner=ticks(proc.name)
        if not owner:continue
        for task in (proc/'task').glob('[0-9]*'):
            start=ticks(task.name)
            if start:
                try:result[int(task.name)]={'pid':int(proc.name),'process_start':owner,'thread_start':start,'name':(task/'comm').read_text().strip()}
                except OSError:pass
    return result

class Accounting:
    def __init__(self,known):
        self.known=known;self.rows={};self.ready={};self.exited=set();self.events=0;self.unparsed=0;self.lost=0;self.clock_errors=0
    from trace_feed import feed
    def row(self,tid):
        if tid not in self.rows:
            self.rows[tid]={**self.known[tid],'tid':tid,'wakeups':0,'wake_to_run_samples':0,'wake_to_run_sum_ns':0,'max_wake_to_run_ns':None,'runnable_to_run_samples':0,'max_runnable_to_run_ns':None,'duplicate_ready_events':0}
        return self.rows[tid]
    def scheduled(self,tid,stamp):
        entry=self.ready.pop(tid,None)
        if entry is None or tid not in self.known or tid in self.exited:return
        delta=stamp-entry[0]
        if delta<0:self.clock_errors+=1;return
        row=self.row(tid);row['runnable_to_run_samples']+=1
        row['max_runnable_to_run_ns']=max(row['max_runnable_to_run_ns'] or 0,delta)
        if entry[1]=='wake':
            row['wake_to_run_samples']+=1;row['wake_to_run_sum_ns']+=delta
            row['max_wake_to_run_ns']=max(row['max_wake_to_run_ns'] or 0,delta)
    def report(self,elapsed):
        rows=[];unverified=0
        for tid,row in self.rows.items():
            if tid not in self.exited and ticks(tid)!=row['thread_start']:unverified+=1;continue
            row=dict(row,wakeups_per_second=row['wakeups']/elapsed,exited_in_window=tid in self.exited,
                     mean_wake_to_run_ns=row['wake_to_run_sum_ns']/row['wake_to_run_samples'] if row['wake_to_run_samples'] else None)
            row.pop('wake_to_run_sum_ns');rows.append(row)
        rows.sort(key=lambda x:x['wakeups'],reverse=True)
        processes={}
        for row in rows:
            key=(row['pid'],row['process_start'])
            item=processes.setdefault(key,{'pid':row['pid'],'start':row['process_start'],'wakeups':0,'threads_measured':0,'max_wake_to_run_ns':None,'max_runnable_to_run_ns':None})
            item['wakeups']+=row['wakeups'];item['threads_measured']+=1
            for field in ['max_wake_to_run_ns','max_runnable_to_run_ns']:
                if row[field] is not None:item[field]=max(item[field] or 0,row[field])
        for item in processes.values():item['wakeups_per_second']=item['wakeups']/elapsed
        return dict(accounting_version=__import__('trace_feed').ACCOUNTING_VERSION,threads=rows,processes=sorted(processes.values(),key=lambda x:x['wakeups'],reverse=True),events_parsed=self.events,unparsed_lines=self.unparsed,lost_event_markers=self.lost,
                    clock_order_errors=self.clock_errors,identity_unverified_threads=unverified,pending_at_window_end=len(self.ready))

def instance(record):
    root=Path(record['root']);name=record['name']
    if not NAME.fullmatch(name) or (root not in ROOTS and root!=RUNTIME/(name+'.tracefs')):raise RuntimeError('Unowned trace instance')
    return root/'instances'/name

def cleanup(record):
    path=instance(record)
    if path.exists():
        (path/'tracing_on').write_text('0\n')
        (path/'events/enable').write_text('0\n')
        path.rmdir()
    if record.get('private_mount'):
        root=Path(record['root'])
        if os.path.ismount(root):subprocess.run(['umount',str(root)],check=True,capture_output=True,timeout=3)
        if root.exists():root.rmdir()

def ledger(name):
    if not NAME.fullmatch(name):raise RuntimeError('Invalid trace identifier')
    if RUNTIME.is_symlink() or RUNTIME.stat().st_uid!=0 or RUNTIME.stat().st_mode&0o077:raise RuntimeError('Unsafe trace watchdog directory')
    path=RUNTIME/(name+'.json')
    if path.is_symlink() or path.stat().st_uid!=0 or path.stat().st_mode&0o077:raise RuntimeError('Unsafe trace watchdog record')
    return path,json.loads(path.read_text())

def watchdog(name):
    path,record=ledger(name);instance(record)
    ready=RUNTIME/(name+'.ready')
    fd=os.open(ready,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    os.close(fd)
    try:
        end=time.monotonic()+min(635,int(record['duration'])+30)
        while path.exists() and time.monotonic()<end:
            if boot()!=record['boot'] or ticks(record['pid'])!=record['start']:break
            time.sleep(.25)
        if path.exists():
            cleanup(record);path.unlink(missing_ok=True)
    finally:ready.unlink(missing_ok=True)

def run(seconds=10,ready=None,targets=None,stop_fd=None,minimum_seconds=None):
    if isinstance(seconds,bool) or not isinstance(seconds,int) or not 1<=seconds<=605:raise ValueError('Scheduler capture 1..60 seconds')
    if minimum_seconds is None:minimum_seconds=seconds
    if type(minimum_seconds) not in (int,float) or not 1<=minimum_seconds<=seconds:raise ValueError('Invalid required scheduler interval')
    if os.geteuid()!=0:raise RuntimeError('Scheduler capture requires root')
    root=next((p for p in ROOTS if (p/'instances').is_dir()),None)
    name='dat-'+uuid.uuid4().hex;private_mount=root is None
    if private_mount:
        if 'tracefs' not in Path('/proc/filesystems').read_text():return {'status':'unsupported','reason':'Kernel does not provide tracefs','wakeups_measured':False,'max_scheduling_latency_measured':False}
        root=RUNTIME/(name+'.tracefs')
    if RUNTIME.is_symlink():raise RuntimeError('Unsafe watchdog directory')
    RUNTIME.mkdir(mode=0o700,exist_ok=True)
    if RUNTIME.stat().st_uid!=0 or RUNTIME.stat().st_mode&0o077:raise RuntimeError('Unsafe watchdog directory')
    path=root/'instances'/name
    record=dict(root=str(root),name=name,pid=os.getpid(),start=ticks(os.getpid()),boot=boot(),duration=seconds,private_mount=private_mount)
    meta=RUNTIME/(name+'.json');temporary=RUNTIME/(name+'.pending')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(record,stream);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,meta)
    fd=os.open(RUNTIME,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    previous=signal.getsignal(signal.SIGTERM);child=None;pipe=None;spool=None
    def interrupted(signum,frame):raise InterruptedError('Scheduler capture cancelled')
    signal.signal(signal.SIGTERM,interrupted)
    try:
        if private_mount:
            root.mkdir(mode=0o700)
            subprocess.run(['mount','-t','tracefs','-o','nodev,nosuid,noexec','tracefs',str(root)],capture_output=True,check=True,timeout=3)
        path.mkdir()
        (path/'tracing_on').write_text('0\n')
        for event in EVENTS:
            if not (path/'events/sched'/event/'enable').exists():return {'status':'unsupported','reason':'Scheduler tracepoint unavailable: '+event,'wakeups_measured':False,'max_scheduling_latency_measured':False}
        clocks=(path/'trace_clock').read_text().replace('[','').replace(']','').split()
        if 'mono' not in clocks:return {'status':'unsupported','reason':'Comparable cross-CPU mono trace clock unavailable','wakeups_measured':False,'max_scheduling_latency_measured':False}
        (path/'trace_clock').write_text('mono\n');(path/'current_tracer').write_text('nop\n')
        cpus=max(1,os.cpu_count() or 1);kb=min(1024,max(16,16384//cpus))
        (path/'buffer_size_kb').write_text(str(kb)+'\n')
        child=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'watchdog',name],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
        deadline=time.monotonic()+10
        while not (RUNTIME/(name+'.ready')).exists():
            if child.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Scheduler cleanup watchdog did not arm')
            time.sleep(.02)
        from trace_spool import TraceSpool
        spool=TraceSpool()
        known=identities() if targets is None else __import__('audio_trace').identities(targets)
        if targets is not None:
            from audio_trace import apply_filters
            known=apply_filters(path,known,targets)
        counter=Accounting(known);data=b'';pipe=os.open(path/'trace_pipe',os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
        for event in EVENTS:(path/'events/sched'/event/'enable').write_text('1\n')
        start=time.monotonic();(path/'tracing_on').write_text('1\n');end=start+seconds
        if ready is not None:ready()
        def drain():
            nonlocal data
            try:chunk=os.read(pipe,65536)
            except BlockingIOError:return False
            if not chunk:return False
            spool.append(chunk)
            return True
        from trace_completion import finished
        collector_start=time.process_time();read_batches=0;ended_by_observer=False
        while time.monotonic()<end:
            if finished(stop_fd):ended_by_observer=True;break
            if select.select([pipe],[],[],min(.1,max(0,end-time.monotonic())))[0]:
                # Kernel timestamps preserve event precision while bounded
                # batches avoid waking the collector for every audio event.
                for _ in range(8):
                    if not drain():break
                read_batches+=1
                time.sleep(min(.01,max(0,end-time.monotonic())))
        collector_cpu_seconds=time.process_time()-collector_start
        stop_requested=time.monotonic()
        (path/'tracing_on').write_text('0\n');elapsed=time.monotonic()-start
        while drain():pass
        decode_start=time.process_time();spool.replay(counter);decode_cpu_seconds=time.process_time()-decode_start
        lost=0;stats=[]
        for file in (path/'per_cpu').glob('cpu*/stats'):
            text=file.read_text();stats.append(file.parent.name)
            for key,value in re.findall(r'^(overrun|commit overrun|dropped events):\s*(\d+)',text,re.M):lost+=int(value)
        result=counter.report(elapsed)
        result.update(__import__('trace_feed').capture_boundary(counter,stop_requested))
        complete=elapsed>=minimum_seconds and bool(stats) and not (lost or counter.lost or counter.unparsed or counter.clock_errors)
        return dict(status='observed' if complete else 'inconclusive',observer_completion_received=ended_by_observer,required_seconds=minimum_seconds,duration=elapsed,collector_cpu_seconds=collector_cpu_seconds,collector_cpu_percent=100*collector_cpu_seconds/elapsed,collector_read_batches=read_batches,monotonic_start=start,monotonic_end=start+elapsed,target_tids=sorted(targets) if targets else None,unobserved_target_tids=[tid for tid in targets if not any(row['tid']==tid and row['runnable_to_run_samples']>0 for row in result['threads'])] if targets else [],measurement_scope='verified-audio-and-usb-irq' if targets else 'system-wide',capture_bytes=spool.size,decode_after_capture=True,decode_cpu_seconds=decode_cpu_seconds,trace_clock='mono',buffer_kb_per_cpu=kb,
                    buffer_loss_count=lost,per_cpu_buffers_checked=len(stats),wakeups_measured=complete,max_scheduling_latency_measured=complete and any(row['runnable_to_run_samples'] for row in result['threads']),
                    maximum_is_observed_only=True,measurement_has_overhead=True,**result,
                    limitations=['Tracing adds activity and must not be treated as a passive listening baseline',
                                 'Maximum is observed runnable/wakeup-to-run delay in this window, not a worst-case guarantee',
                                 'Only threads identified at capture start are attributed; boundary events can remain unmatched',
                                 'No audio fidelity or sonic ranking is inferred'])
    finally:
        if spool is not None:spool.close()
        if pipe is not None:os.close(pipe)
        try:cleanup(record)
        finally:meta.unlink(missing_ok=True)
        if child:
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
        signal.signal(signal.SIGTERM,previous)

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='watchdog':watchdog(sys.argv[2])
    else:raise SystemExit(2)

__release_signature__ = 'Tino Zappala'
