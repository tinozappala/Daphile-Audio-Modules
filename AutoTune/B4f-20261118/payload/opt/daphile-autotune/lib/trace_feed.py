"""Parse canonical scheduler payloads without building per-event dictionaries.

Unrecognized payloads use the original field grammar; malformed lines remain
counted and prevent qualification. Kernel timestamps stay at nanosecond precision.
"""
import math
import re

LINE=re.compile(r'\s(\d+)\.(\d+):\s+(?:sched:)?(sched_\w+):\s+(.*)')
SWITCH=re.compile(r'(?<!\S)prev_pid=(\d+)\s+.*?(?<!\S)prev_state=(\S+)\s+.*?(?<!\S)next_pid=(\d+)(?:\s|$)')
PID=re.compile(r'(?:^|\s)pid=(\d+)(?:\s|$)')
FIELDS=re.compile(r'(?<!\S)(pid|next_pid|prev_pid|prev_state)=([^\s]+)')
EVENTS=('sched_wakeup','sched_wakeup_new','sched_switch','sched_process_exit')
ACCOUNTING_VERSION='runnable-interval-v5-per-cpu-merge'
WINDOW_PROTOCOL='scheduler-observation-window-tail-v2'
TAIL_PROTOCOL='ready-at-window-end-v1'
COMPLETION_TAIL_NS=100_000_000


def _fields(data):
    values=FIELDS.findall(data)
    if len({key for key,value in values})!=len(values):raise ValueError('Ambiguous repeated scheduler field')
    return dict(values)


def clock_error_example(counter,tid,previous,stamp,kind,payload=''):
    examples=getattr(counter,'clock_error_examples',None)
    if examples is None:examples=[];counter.clock_error_examples=examples
    if len(examples)>=4:return
    window=getattr(counter,'window_ns',None)
    phase=('capture' if window is None else 'warmup' if stamp<window[0]
           else 'observation' if stamp<=window[1] else 'completion-tail'
           if stamp<=window[1]+COMPLETION_TAIL_NS else 'post-tail')
    examples.append(dict(tid=tid,previous_stamp_ns=previous,new_stamp_ns=stamp,
        kind=kind,phase=phase,payload=payload[:384]))


def _ordered(counter,tids,stamp,kind='unknown',payload=''):
    # A trace_pipe stream can mix CPUs. Require causal order only for the
    # SAME observed task, including wakes while running and switch-outs;
    # checking only completed ready->run delays misses those reversals.
    if not hasattr(counter,'last_event_timestamps'):counter.last_event_timestamps={}
    valid=True
    exited=getattr(counter,'integrity_exited',counter.exited)
    for tid in tids:
        if tid not in counter.known or tid in exited:continue
        previous=counter.last_event_timestamps.get(tid)
        if previous is not None and stamp<previous:
            valid=False;clock_error_example(counter,tid,previous,stamp,kind,payload)
        else:counter.last_event_timestamps[tid]=stamp
    return valid


def _state_observed(counter,tid,stamp,event):
    # Identity at trace start says nothing about runnable/running/sleeping
    # state. A wake alone may also describe a still-running wait loop. Only a
    # switch or exit anchors a known state and closes initial uncertainty.
    if tid not in counter.known or tid in counter.exited:return
    if not hasattr(counter,'initial_state_observations'):counter.initial_state_observations={}
    if tid not in counter.initial_state_observations:
        counter.initial_state_observations[tid]=dict(first_state_at_ns=stamp,event=event)


def parse_line(line,known_stamp=None):
    """Decode a line once; errors retain their original accounting semantics."""
    if 'LOST' in line and 'EVENT' in line:return 'lost'
    match=LINE.search(line)
    if not match:
        return False if line.strip() and not line.lstrip().startswith('#') else None
    seconds,decimal,event,data=match.groups()
    if len(decimal)>9:return False
    if known_stamp is not None and (type(known_stamp) is not int or known_stamp<0):return False
    stamp=known_stamp if known_stamp is not None else int(seconds)*10**9+int(decimal.ljust(9,'0')[:9])
    if event not in EVENTS:return (stamp,event,None,None,None)
    try:
        if event=='sched_switch':
            parsed=SWITCH.search(data)
            if parsed and all(data.count(field+'=')==1 for field in ('prev_pid','prev_state','next_pid')):prev,state,nextpid=parsed.groups();prev=int(prev);nextpid=int(nextpid)
            else:
                fields=_fields(data);prev=int(fields['prev_pid']);state=fields['prev_state'];nextpid=int(fields['next_pid'])
            return stamp,event,prev,state,nextpid
        parsed=PID.search(data)
        tid=int(parsed[1]) if parsed and data.count('pid=')==1 else int(_fields(data)['pid'])
        return stamp,event,tid,None,None
    except (KeyError,ValueError):return stamp,'unparsed-payload',None,None,None


def apply_event(self,event,payload=''):
    if event is None:return
    if event is False:self.unparsed+=1;return
    if event=='lost':self.lost+=1;return
    stamp,kind,tid,state,nextpid=event;self.events+=1
    if kind=='unparsed-payload':self.unparsed+=1;return
    if kind not in EVENTS:return
    if not hasattr(self,'running'):self.running=set()
    if getattr(self,'measurement_window',None) is not None:
        if stamp>self.window_ns[1]:
            # The complete stream still establishes integrity after the
            # sampled window. It does not create new measured ready intervals.
            tids=(tid,nextpid) if kind=='sched_switch' else (tid,)
            if not _ordered(self,tids,stamp,kind,payload):self.clock_errors+=1
            if kind=='sched_process_exit':self.integrity_exited.add(tid)
            if kind=='sched_switch':complete_window_tail(self,event)
            return
        self.record_metrics=stamp>=self.window_ns[0]
    if kind=='sched_switch':
        ordered=_ordered(self,(tid,nextpid),stamp,kind,payload);prior_errors=self.clock_errors
        _state_observed(self,tid,stamp,'switch-out')
        _state_observed(self,nextpid,stamp,'switch-in')
        self.running.discard(tid)
        if tid in self.known and tid not in self.exited:
            old=self.ready.pop(tid,None)
            if old is not None:
                row=self.row(tid)
                key='pending_replaced_on_preemption' if state.startswith('R') else 'pending_cleared_on_block'
                row[key]=row.get(key,0)+1
            if state.startswith('R'):self.ready[tid]=(stamp,'preempt')
        self.scheduled(nextpid,stamp)
        if not ordered and self.clock_errors==prior_errors:self.clock_errors+=1
        if nextpid in self.known and nextpid not in self.exited:self.running.add(nextpid)
        return
    if not _ordered(self,(tid,),stamp,kind,payload):self.clock_errors+=1
    if kind=='sched_process_exit':
        _state_observed(self,tid,stamp,'exit')
        if hasattr(self,'integrity_exited'):self.integrity_exited.add(tid)
        self.exited.add(tid);self.ready.pop(tid,None);self.running.discard(tid);return
    if tid not in self.known or tid in self.exited:return
    row=self.row(tid);row['wakeups']+=1
    if tid in self.running:
        row['wakeup_while_running']=row.get('wakeup_while_running',0)+1
        return
    if tid in self.ready:row['duplicate_ready_events']+=1
    else:self.ready[tid]=(stamp,'wake')


def feed(self,line,known_stamp=None):
    event=parse_line(line,known_stamp)
    # Compatibility callers can still request two views, sharing this parse.
    mirror=getattr(self,'measurement_mirror',None)
    if mirror is not None:apply_event(mirror,event,line)
    apply_event(self,event,line)


def complete_window_tail(counter,event):
    """Complete only already-open sampled waits; never admit tail activity."""
    stamp,kind,prev,state,tid=event;end=counter.window_ns[1]
    if not end<stamp<=end+COMPLETION_TAIL_NS:return
    entry=counter.ready.get(tid)
    if entry is None or entry[0]>end or tid not in counter.known or tid in counter.exited:return
    counter.record_metrics=True
    counter.scheduled(tid,stamp)
    row=counter.row(tid)
    row['completion_tail_samples']=row.get('completion_tail_samples',0)+1
    counter.tail_completed.append(dict(tid=tid,runnable_since_ns=entry[0],cause=entry[1],
        scheduled_at_ns=stamp,latency_ns=stamp-entry[0]))


def _nanoseconds(value, round_up=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Finite nonnegative monotonic time required')
    scaled = value * 10**9
    if not math.isfinite(scaled):
        raise ValueError('Monotonic time is not representable')
    return math.ceil(scaled) if round_up else math.floor(scaled)


def capture_boundary(counter, stop_requested, capture_end=None):
    """Keep right-censored waits distinct from completed delays and trace loss.

    The stop request precedes disabling tracing. It gives a conservative lower
    bound: a last event received during that write can legitimately have a
    later timestamp, in which case its known elapsed lower bound is zero.
    """
    stop_ns = _nanoseconds(stop_requested)
    intervals = []
    for tid, entry in sorted(counter.ready.items()):
        if (type(tid) is not int or not 0 < tid < 2**31
                or tid not in counter.known or tid in counter.exited
                or not isinstance(entry, tuple) or len(entry) != 2
                or type(entry[0]) is not int or entry[0] < 0
                or entry[1] not in ('wake', 'preempt')):
            raise ValueError('Unverifiable pending runnable interval')
        intervals.append(dict(tid=tid, runnable_since_ns=entry[0], cause=entry[1],
                              elapsed_lower_bound_ns=max(0, stop_ns-entry[0])))
    boundary = dict(status='observed', pending_count=len(intervals), intervals=intervals,
                    max_pending_lower_bound_ns=max((r['elapsed_lower_bound_ns'] for r in intervals), default=0))
    if intervals:
        boundary['stop_requested_monotonic_ns'] = stop_ns
    initial=getattr(counter,'initial_state_observations',{})
    states=[dict(tid=tid,**initial.get(tid,dict(first_state_at_ns=None,event=None))) for tid in sorted(counter.known)]
    result=dict(runnable_boundary=boundary,
                initial_state_boundary=dict(status='observed',target_count=len(states),states=states))
    window=getattr(counter,'measurement_window',None)
    if window is not None:
        end=counter.window_ns[1];deadline=end+COMPLETION_TAIL_NS
        captured=_nanoseconds(capture_end if capture_end is not None else stop_requested,round_up=True)
        completed=[dict(row) for row in getattr(counter,'tail_completed',[])]
        initial=[dict(tid=row['tid'],runnable_since_ns=row['runnable_since_ns'],cause=row['cause'])
                 for row in intervals+completed]
        result['completion_tail']=dict(status='observed',protocol=TAIL_PROTOCOL,
            observation_end_ns=end,budget_ns=COMPLETION_TAIL_NS,deadline_ns=deadline,
            capture_stop_requested_ns=stop_ns,capture_end_ns=captured,
            completion_limit_ns=min(deadline,captured),full_tail_collected=stop_ns>=deadline,
            pending_at_observation_end=sorted(initial,key=lambda row:row['tid']),
            pending_at_observation_end_count=len(initial),completed_intervals=completed,
            completed_count=len(completed),remaining_count=len(intervals))
    return result


def _completion_tail(trace,trace_start,trace_end,end,targets,evidence):
    """Check the interval partition and every claimed post-window completion."""
    value=trace['completion_tail']
    if (trace.get('measurement_window_protocol')!=WINDOW_PROTOCOL or not isinstance(value,dict)
            or value.get('status')!='observed' or value.get('protocol')!=TAIL_PROTOCOL
            or any(type(value.get(key)) is not int for key in
                   ('observation_end_ns','budget_ns','deadline_ns','capture_end_ns'))
            or value.get('observation_end_ns')!=end or value.get('budget_ns')!=COMPLETION_TAIL_NS
            or value.get('deadline_ns')!=end+COMPLETION_TAIL_NS
            or value.get('capture_end_ns')!=trace_end):raise ValueError('Tail protocol or bounds')
    stop=value['capture_stop_requested_ns'];limit=value['completion_limit_ns']
    if (type(stop) is not int or not end<=stop<=trace_end or type(limit) is not int
            or limit!=min(value['deadline_ns'],trace_end)
            or type(value.get('full_tail_collected')) is not bool
            or value['full_tail_collected'] is not (stop>=value['deadline_ns'])):raise ValueError('Tail capture bounds')
    initial=value['pending_at_observation_end'];completed=value['completed_intervals']
    if not isinstance(initial,list) or not isinstance(completed,list):raise ValueError('Tail intervals')
    pending={}
    for row in initial:
        tid=row['tid'];since=row['runnable_since_ns']
        if (type(tid) is not int or tid not in targets or tid in pending
                or type(since) is not int or not trace_start<=since<=end
                or row.get('cause') not in ('wake','preempt')):raise ValueError('Tail initial interval')
        pending[tid]=(since,row['cause'])
    resolved={};threads={row['tid']:row for row in trace['threads']}
    if len(threads)!=len(trace['threads']):raise ValueError('Duplicate thread summaries')
    for row in completed:
        tid=row['tid'];since=row['runnable_since_ns'];scheduled=row['scheduled_at_ns'];delay=row['latency_ns']
        if (type(tid) is not int or type(since) is not int or tid in resolved or pending.get(tid)!=(since,row['cause'])
                or type(scheduled) is not int or not end<scheduled<=limit
                or type(delay) is not int or delay!=scheduled-since):raise ValueError('False tail completion')
        actual=threads[tid];maximum=actual.get('max_runnable_to_run_ns')
        if (type(maximum) is not int or maximum<delay
                or (row['cause']=='wake' and (type(actual.get('max_wake_to_run_ns')) is not int
                    or actual['max_wake_to_run_ns']<delay))):raise ValueError('Tail delay absent from metric')
        resolved[tid]=row
    remaining={row['tid']:(row['runnable_since_ns'],row['cause']) for row in evidence['intervals']}
    if (set(resolved)&set(remaining) or set(pending)!=set(resolved)|set(remaining)
            or any(pending[tid]!=entry for tid,entry in remaining.items())
            or type(value.get('pending_at_observation_end_count')) is not int
            or value['pending_at_observation_end_count']!=len(pending)
            or type(value.get('completed_count')) is not int or value['completed_count']!=len(resolved)
            or type(value.get('remaining_count')) is not int or value['remaining_count']!=len(remaining)
            or type(trace.get('pending_at_window_end')) is not int or trace.get('pending_at_window_end')!=len(pending)
            or trace.get('pending_after_completion_tail')!=len(remaining)):
        raise ValueError('Tail interval partition')
    for tid,actual in threads.items():
        expected=1 if tid in resolved else 0
        if type(actual.get('completion_tail_samples',0)) is not int or actual.get('completion_tail_samples',0)!=expected:
            raise ValueError('Tail sample accounting')
        if expected and (type(actual.get('runnable_to_run_samples')) is not int or actual['runnable_to_run_samples']<expected):
            raise ValueError('Tail completion has no runnable sample')
    return value


def boundary_status(observation):
    """Validate sample-boundary waits and their bounded known completions.

    Only waits already open at sample end may complete in the 100 ms tail.
    New tail activity cannot improve or worsen the sampled metrics. Every
    completed interval must remain in the reported maximum; unresolved waits
    still prevent qualification without being described as hardware faults.
    """
    unavailable = dict(status='unavailable', qualification_complete=False,
                       issues=['Scheduler runnable boundary evidence unavailable or inconsistent'])
    try:
        trace = observation['scheduler_trace']
        if not isinstance(trace, dict) or trace.get('accounting_version') != ACCOUNTING_VERSION:
            return unavailable
        trace_start = _nanoseconds(trace['monotonic_start'])
        trace_end = _nanoseconds(trace['monotonic_end'], round_up=True)
        start = _nanoseconds(observation['observation_monotonic_start'])
        end = _nanoseconds(observation['observation_monotonic_end'], round_up=True)
        if not trace_start <= start < end <= trace_end:
            return unavailable
        targets = trace['target_tids']
        if (not isinstance(targets, list) or not targets
                or any(type(t) is not int or not 0 < t < 2**31 for t in targets)
                or len(set(targets)) != len(targets)):
            return unavailable
        initial=trace['initial_state_boundary']
        if not isinstance(initial,dict) or initial.get('status')!='observed':return unavailable
        states=initial['states'];target_count=initial['target_count']
        if (not isinstance(states,list) or type(target_count) is not int or target_count!=len(targets)
                or len(states)!=target_count):return unavailable
        initial_seen=set();left_censored=[]
        for state in states:
            if not isinstance(state,dict):return unavailable
            tid=state['tid'];stamp=state['first_state_at_ns'];event=state['event']
            if type(tid) is not int or tid not in targets or tid in initial_seen:return unavailable
            initial_seen.add(tid)
            if stamp is None:
                if event is not None:return unavailable
                left_censored.append(tid)
            elif type(stamp) is not int or not trace_start<=stamp<=trace_end or event not in ('switch-in','switch-out','exit'):
                return unavailable
            elif stamp>start:left_censored.append(tid)
        evidence = trace['runnable_boundary']
        if not isinstance(evidence, dict) or evidence.get('status') != 'observed':
            return unavailable
        intervals = evidence['intervals']
        count = evidence['pending_count']
        maximum = evidence['max_pending_lower_bound_ns']
        pending = trace.get('pending_after_completion_tail')
        if (not isinstance(intervals, list) or type(count) is not int or count < 0
                or type(pending) is not int or pending != count
                or count != len(intervals) or count > len(targets)
                or type(maximum) is not int or maximum < 0):
            return unavailable
        stop = evidence.get('stop_requested_monotonic_ns')
        if count or stop is not None:
            if type(stop) is not int or not trace_start <= stop <= trace_end:
                return unavailable
        seen = set()
        relevant = []
        lower_bounds = []
        for interval in intervals:
            if not isinstance(interval, dict):
                return unavailable
            tid = interval['tid']
            since = interval['runnable_since_ns']
            lower = interval['elapsed_lower_bound_ns']
            if (type(tid) is not int or tid not in targets or tid in seen
                    or type(since) is not int or not 0 <= since <= trace_end
                    or interval.get('cause') not in ('wake', 'preempt')
                    or type(lower) is not int or lower != max(0, stop-since)):
                return unavailable
            seen.add(tid)
            lower_bounds.append(lower)
            if since <= end:
                relevant.append(lower)
        if maximum != max(lower_bounds, default=0):
            return unavailable
        tail=_completion_tail(trace,trace_start,trace_end,end,targets,evidence)
        if intervals and stop!=tail['capture_stop_requested_ns']:return unavailable
        maxima=[row.get('max_runnable_to_run_ns') for row in trace['threads']]
        measured=observation.get('max_scheduling_latency')
        if (not maxima or any(type(v) is not int or v<0 for v in maxima)
                or type(measured) not in (int,float) or not math.isfinite(measured)
                or abs(measured*1e6-max(maxima))>1):return unavailable
        issues = (['Scheduler latency is right-censored: unfinished runnable interval overlaps observation']
                  if relevant else [])
        if left_censored:
            issues.append('Scheduler latency is left-censored: initial runnable state is unknown during observation')
        return dict(status='censored-observation' if issues else 'complete',
                    qualification_complete=not issues, pending_in_observation=len(relevant),
                    pending_after_observation=count-len(relevant),
                    initial_unknown_target_tids=left_censored,
                    initial_state_known_before_observation=len(targets)-len(left_censored),
                    max_pending_lower_bound_ms=maximum/10**6,
                    max_relevant_pending_lower_bound_ms=max(relevant, default=0)/10**6,
                    completed_in_tail=tail['completed_count'],
                    completion_tail_budget_ms=tail['budget_ns']/1e6,
                    issues=issues)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return unavailable

__release_signature__ = 'Tino Zappala'
