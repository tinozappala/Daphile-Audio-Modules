"""Ready-synchronized tracing whose recorded interval encloses passive sampling."""
import json,os,select,subprocess,sys,time
from pathlib import Path
import runtime
from detection import scan,read
from scheduler_trace import ticks
from trace_feed import boundary_status
import telemetry


def observe(seconds=30):
    if type(seconds) not in (int,float) or not 1<=seconds<=600:raise ValueError('Instrumented window 1..600 seconds')
    h=scan();dac=h.get('dac',{});owner=dac.get('owner_pid')
    if dac.get('status')!='verified-active' or type(owner)!=int:raise RuntimeError('Verified audio owner required for instrumentation')
    tids={owner};irq=(dac.get('usb') or {}).get('irq')
    if irq is not None:
        for path in Path('/proc').glob('[0-9]*/comm'):
            if (read(path) or '').startswith('irq/'+str(irq)+'-'):tids.add(int(path.parent.name))
    targets={tid:ticks(tid) for tid in tids}
    if not all(targets.values()):raise RuntimeError('Audio thread identity unavailable')
    pcm_path=telemetry.selected_pcm(h)
    if not pcm_path:raise RuntimeError('Verified PCM coordinates unavailable')
    reader,writer=os.pipe();stop_reader,stop_writer=os.pipe();child=None
    try:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('instrument_worker.py')),str(int(seconds)+5),str(writer),json.dumps(targets),str(stop_reader),str(seconds)],
                               pass_fds=(writer,stop_reader),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        os.close(writer);writer=None
        os.close(stop_reader);stop_reader=None
        if not select.select([reader],[],[],15)[0] or os.read(reader,1)!=b'R':raise RuntimeError('Scheduler capture did not become ready')
        # The collector already drains while we allow first task-state events
        # to arrive. This bounded warmup is NOT evidence by itself: v4 still
        # refuses any target whose first certain state overlaps measurement.
        warmup_start=time.monotonic();time.sleep(1.0)
        warmup_elapsed=time.monotonic()-warmup_start
        measured=runtime.observe(seconds,pcm_path=pcm_path)
        os.write(stop_writer,b'D');os.close(stop_writer);stop_writer=None
        raw,error=child.communicate(timeout=seconds+25)
        if child.returncode:raise RuntimeError('Scheduler capture failed')
        trace=json.loads(raw);measured['scheduler_trace']=trace
    finally:
        os.close(reader)
        if writer is not None:os.close(writer)
        if stop_reader is not None:os.close(stop_reader)
        if stop_writer is not None:os.close(stop_writer)
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=15)
            except subprocess.TimeoutExpired:raise RuntimeError('Scheduler cleanup unconfirmed; independent watchdog retained')
    measured.update(instrumented=True,instrumentation_requested_seconds=seconds,
                    instrumentation_warmup_seconds=warmup_elapsed)
    if trace.get('status')=='observed' and trace.get('max_scheduling_latency_measured'):
        values=[x['max_runnable_to_run_ns'] for x in trace['threads'] if x.get('max_runnable_to_run_ns') is not None]
        measured['max_scheduling_latency']=max(values)/1e6 if values else None
        measured['wakeups_per_second']=sum(x['wakeups_per_second'] for x in trace['threads'])
    measured['scheduler_latency_boundary']=boundary_status(measured)
    def network(text):
        rows={}
        for line in (text or '').splitlines():
            if ':' not in line:continue
            name,data=line.split(':',1);fields=data.split()
            if len(fields)==16:rows[name.strip()]=[int(fields[i]) for i in (2,3,10,11)]
        return rows
    first,last=network(measured['network_before']),network(measured['network_after'])
    measured['network_counter_errors']=[name for name in first if name not in last or any(b!=a for a,b in zip(first[name],last.get(name,[])))]
    measured['network_inventory_comparable']=bool(first) and set(first)==set(last)
    import network_test
    nas=network_test.audit();measured['nas_status']=nas['status'] if nas.get('nas') else 'not-applicable';measured['NAS']=nas
    return measured

__release_signature__ = 'Tino Zappala'
