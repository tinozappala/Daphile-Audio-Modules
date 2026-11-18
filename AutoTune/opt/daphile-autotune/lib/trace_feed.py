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
ACCOUNTING_VERSION='runnable-interval-v4'


def _fields(data):
    values=FIELDS.findall(data)
    if len({key for key,value in values})!=len(values):raise ValueError('Ambiguous repeated scheduler field')
    return dict(values)


def _ordered(counter,tids,stamp):
    # A trace_pipe stream can mix CPUs. Require causal order only for the
    # SAME observed task, including wakes while running and switch-outs;
    # checking only completed ready->run delays misses those reversals.
    if not hasattr(counter,'last_event_timestamps'):counter.last_event_timestamps={}
    valid=True
    for tid in set(tids):
        if tid not in counter.known or tid in counter.exited:continue
        previous=counter.last_event_timestamps.get(tid,stamp)
        if stamp<previous:valid=False
        counter.last_event_timestamps[tid]=max(previous,stamp)
    return valid


def _state_observed(counter,tid,stamp,event):
    # Identity at trace start says nothing about runnable/running/sleeping
    # state. A wake alone may also describe a still-running wait loop. Only a
    # switch or exit anchors a known state and closes initial uncertainty.
    if tid not in counter.known or tid in counter.exited:return
    if not hasattr(counter,'initial_state_observations'):counter.initial_state_observations={}
    counter.initial_state_observations.setdefault(tid,dict(first_state_at_ns=stamp,event=event))


def feed(self,line):
    # A sched_wakeup can occur while the task still executes its wait loop.
    # See Linux v6.12 ttwu_runnable(). Only time actually off CPU counts.
    if not hasattr(self,'running'):self.running=set()
    if 'LOST' in line and 'EVENT' in line:self.lost+=1;return
    match=LINE.search(line)
    if not match:
        if line.strip() and not line.lstrip().startswith('#'):self.unparsed+=1
        return
    seconds,decimal,event,data=match.groups()
    if len(decimal)>9:self.unparsed+=1;return
    stamp=int(seconds)*10**9+int(decimal.ljust(9,'0')[:9]);self.events+=1
    if event not in EVENTS:return
    try:
        if event=='sched_switch':
            parsed=SWITCH.search(data)
            if parsed and all(data.count(field+'=')==1 for field in ('prev_pid','prev_state','next_pid')):prev,state,nextpid=parsed.groups();prev=int(prev);nextpid=int(nextpid)
            else:
                fields=_fields(data);prev=int(fields['prev_pid']);state=fields['prev_state'];nextpid=int(fields['next_pid'])
            ordered=_ordered(self,(prev,nextpid),stamp);prior_errors=self.clock_errors
            _state_observed(self,prev,stamp,'switch-out')
            _state_observed(self,nextpid,stamp,'switch-in')
            self.running.discard(prev)
            if prev in self.known and prev not in self.exited:
                # prev was on CPU immediately before this event, even when
                # the capture boundary omitted its earlier switch-in.
                old=self.ready.pop(prev,None)
                if old is not None:
                    row=self.row(prev)
                    key='pending_replaced_on_preemption' if state.startswith('R') else 'pending_cleared_on_block'
                    row[key]=row.get(key,0)+1
                if state.startswith('R'):self.ready[prev]=(stamp,'preempt')
            self.scheduled(nextpid,stamp)
            if not ordered and self.clock_errors==prior_errors:self.clock_errors+=1
            if nextpid in self.known and nextpid not in self.exited:self.running.add(nextpid)
            return
        parsed=PID.search(data)
        tid=int(parsed[1]) if parsed and data.count('pid=')==1 else int(_fields(data)['pid'])
    except (KeyError,ValueError):self.unparsed+=1;return
    if not _ordered(self,(tid,),stamp):self.clock_errors+=1
    if event=='sched_process_exit':
        _state_observed(self,tid,stamp,'exit')
        self.exited.add(tid);self.ready.pop(tid,None);self.running.discard(tid);return
    if tid not in self.known or tid in self.exited:return
    row=self.row(tid);row['wakeups']+=1
    if tid in self.running:
        row['wakeup_while_running']=row.get('wakeup_while_running',0)+1
        return
    if tid in self.ready:row['duplicate_ready_events']+=1
    else:self.ready[tid]=(stamp,'wake')


def _nanoseconds(value, round_up=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Finite nonnegative monotonic time required')
    scaled = value * 10**9
    if not math.isfinite(scaled):
        raise ValueError('Monotonic time is not representable')
    return math.ceil(scaled) if round_up else math.floor(scaled)


def capture_boundary(counter, stop_requested):
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
    return dict(runnable_boundary=boundary,
                initial_state_boundary=dict(status='observed',target_count=len(states),states=states))


def boundary_status(observation):
    """Validate raw boundary evidence and scope censoring to the sampled window.

    A pending interval that began only after the passive observation ended is
    retained as capture-tail evidence. An unfinished interval that overlaps
    that observation prevents optimization qualification; its completed-delay
    maximum cannot establish latency safety. Neither case changes the raw
    trace status or claims a hardware fault.
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
        pending = trace['pending_at_window_end']
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
                    issues=issues)
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return unavailable

__release_signature__ = 'Tino Zappala'
