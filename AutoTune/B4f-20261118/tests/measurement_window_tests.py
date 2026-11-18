"""Scheduler window and hardware-capability fixtures; no native audio/tracefs.

Run: python -B measurement_window_tests.py [--lib PATH_TO_WORKING_LIB]
Canonical scheduler event strings exercise the production accounting parser.
"""
import argparse
import contextlib
import copy
import importlib
import json
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.dont_write_bytecode = True


def switch(stamp, prev, next_tid, state='S'):
    return (' fixture-0 [000] d..2 %.9f: sched_switch: prev_comm=fixture prev_pid=%d '
            'prev_prio=120 prev_state=%s ==> next_comm=fixture next_pid=%d next_prio=120\n'
            % (stamp, prev, state, next_tid))


def wake(stamp, tid):
    return ' fixture-0 [000] d..2 %.9f: sched_wakeup: comm=fixture pid=%d prio=120 target_cpu=000\n' % (stamp, tid)


def events(include_irq=True, warmup_delay=.9):
    rows=[]
    for offset,tid,delay in ((0,11,.001),(.01,22,.002)):
        if tid==22 and not include_irq:continue
        rows.extend(((.01+offset,switch(.01+offset,0,tid)),
                     (.02+offset,switch(.02+offset,tid,0)),
                     (.1+offset,wake(.1+offset,tid)),
                     (.1+offset+warmup_delay,switch(.1+offset+warmup_delay,0,tid)),
                     (.11+offset+warmup_delay,switch(.11+offset+warmup_delay,tid,0)),
                     (2.1+offset,wake(2.1+offset,tid)),
                     (2.1+offset+delay,switch(2.1+offset+delay,0,tid)),
                     (2.11+offset,switch(2.11+offset,tid,0))))
    rows.extend(((4.,wake(4.,11)),(4.001,switch(4.001,0,11)),(4.01,switch(4.01,11,0)),
                 (5.1,wake(5.1,11)),(6.1,switch(6.1,0,11)),(6.11,switch(6.11,11,0))))
    return [line for _,line in sorted(rows,key=lambda row:row[0])]


def run_all(lib):
    sys.path.insert(0,str(lib))
    scheduler=importlib.import_module('scheduler_trace')
    feed=importlib.import_module('trace_feed')
    contract=importlib.import_module('measurement_contract')
    measure=importlib.import_module('measure')
    spool_module=importlib.import_module('trace_spool')
    scope_module=importlib.import_module('autotune_v2.search_scope')
    known={11:dict(pid=10,process_start='100',thread_start='101',name='audio-output'),
           22:dict(pid=22,process_start='202',thread_start='202',name='irq/17-xhci_hcd')}
    specs={11:dict(start='101',role='audio-output'),22:dict(start='202',role='usb-irq',irq=17)}
    results=[]

    def check(name,action):
        try:
            action()
            results.append(dict(name=name,status='PASS'))
        except BaseException as error:
            results.append(dict(name=name,status='FAIL',error=type(error).__name__+': '+str(error)))

    def counter(lines,include_irq=True,window=(2.,5.)):
        identities=copy.deepcopy(known if include_irq else {11:known[11]})
        value=scheduler.Accounting(identities,window)
        for line in lines:value.feed(line)
        return value

    def report(value,duration):
        with patch.object(scheduler,'ticks',lambda tid:known[tid]['thread_start']):
            return value.report(duration)

    def observation(include_irq=True,cores=4,output_kind=None,lines=None,stop=5.1,capture_end=8.):
        lines=events(include_irq) if lines is None else lines
        lines=[line for line in lines if float(scheduler.LINE.search(line)[1]+'.'+scheduler.LINE.search(line)[2])<=capture_end]
        value=counter(lines,include_irq)
        trace=report(value,3.)
        targets,role_requests=scheduler.target_specification(specs if include_irq else {11:specs[11]})
        roles,scope=scheduler.observed_roles(role_requests,value.known)
        trace.update(status='observed',duration=capture_end,trace_clock='mono',wakeups_measured=True,
            max_scheduling_latency_measured=True,monotonic_start=0.,monotonic_end=capture_end,
            target_tids=sorted(targets),unobserved_target_tids=[],measurement_scope=scope,
            target_roles=roles,measurement_window_protocol=scheduler.WINDOW_PROTOCOL,
            measurement_monotonic_start=2.,measurement_monotonic_end=5.,measurement_duration=3.,
            buffer_loss_count=0,per_cpu_buffers_checked=cores)
        trace.update(feed.capture_boundary(value,stop,capture_end=capture_end))
        cpu=dict(cpu=5.,**{'cpu'+str(i):5. for i in range(cores)})
        peak={key:6. for key in cpu}
        return dict(kernel_log_observable=True,observed_alsa_xrun_states=0,new_kernel_errors=[],
            observation_monotonic_start=2.,observation_monotonic_end=5.,
            duration=3.,interrupts_per_second=100.,context_switches_per_second=100.,
            cpu_percent=cpu,max_cpu_percent=peak,
            max_scheduling_latency=max(row['max_runnable_to_run_ns'] for row in trace['threads'])/1e6,
            wakeups_per_second=sum(row['wakeups_per_second'] for row in trace['threads']),
            instrumented=True,instrumentation_requested_seconds=3.,scheduler_trace=trace,
            instrumentation_output_kind=output_kind or ('usb' if include_irq else 'alsa'),
            instrumentation_usb_irq=17 if include_irq or output_kind=='usb' else None,
            instrumentation_targets=copy.deepcopy(roles),network_counter_errors=[],
            nas_status='not-applicable',network_inventory_comparable=True,
            telemetry=dict(status='observed',sample_count=4,max_sample_gap_seconds=1.,
                online='0' if cores==1 else '0-'+str(cores-1),
                temperatures={'fixture-cpu':dict(peak_c=40.,limit_c=85.,headroom_c=45.,
                    critical_c=None,thermal_policy='operating-85-critical-reserve-v1')},
                minimum_available_memory_kib=262144,selected_pcm_running_every_sample=True))

    def warmup_only():
        raw=report(counter(events(),window=None),8.)
        measured=report(counter(events()),3.)
        assert max(row['max_runnable_to_run_ns'] for row in raw['threads'])>=900000000
        assert max(row['max_runnable_to_run_ns'] for row in measured['threads'])==2000000
        assert sum(row['wakeups'] for row in measured['threads'])==3
        assert sum(row['wakeups_per_second'] for row in measured['threads'])==1.
    check('warmup/tail spikes excluded; wakeups use exact sampled duration',warmup_only)

    def one_replay():
        capture=scheduler.Accounting(copy.deepcopy(known))
        capture.measurement_mirror=scheduler.Accounting(copy.deepcopy(known),(2.,5.))
        for line in events():capture.feed(line)
        assert report(capture.measurement_mirror,3.)==report(counter(events()),3.)
        assert max(row['max_runnable_to_run_ns'] for row in report(capture,8.)['threads'])>=900000000
    check('one spool replay retains whole-capture integrity and sampled metrics',one_replay)

    def integrity_outside_window():
        for stamp in (.5,6.5):
            malformed=' fixture-0 [000] d..2 %.9f: sched_switch: prev_pid=11 prev_state=R\n' % stamp
            value=counter(events()+[malformed])
            assert value.unparsed==1
            assert max(row['max_runnable_to_run_ns'] for row in report(value,3.)['threads'])==2000000
        value=counter(events()+['CPU: 1 [LOST 4 EVENTS]\n'])
        assert value.lost==1
        value=counter(events()+[wake(6.05,11)])
        assert value.clock_errors>0
    check('warmup and post-tail malformed, lost and reordered events remain global errors',integrity_outside_window)

    def parsed_once():
        capture=scheduler.Accounting(copy.deepcopy(known))
        capture.measurement_mirror=scheduler.Accounting(copy.deepcopy(known),(2.,5.))
        lines=events()
        with patch.object(feed,'parse_line',wraps=feed.parse_line) as parser:
            for line in lines:capture.feed(line)
            assert parser.call_count==len(lines)
    check('each scheduler line is parsed once even for compatibility views',parsed_once)

    def identities_outside_metrics():
        value=scheduler.Accounting(copy.deepcopy(known),(2.,5.))
        for line in events(False):value.feed(line)
        with patch.object(scheduler,'ticks',lambda tid:'101' if tid==11 else None):
            result=value.report(3.)
        assert result['identity_unverified_threads']==1
    check('a changed target identity is detected even without sampled metric rows',identities_outside_metrics)

    def clock_examples():
        value=counter(events())
        for index in range(8):value.feed(wake(6.05-index*.001,11))
        assert value.clock_errors>=8
        assert len(value.clock_error_examples)==4
        first=value.clock_error_examples[0]
        assert first['tid']==11 and first['previous_stamp_ns']>first['new_stamp_ns']
        assert first['kind']=='sched_wakeup' and first['phase']=='post-tail'
        assert 'sched_wakeup' in first['payload'] and len(first['payload'])<=384
    check('clock diagnostics retain at most four causal examples without changing rejection',clock_examples)

    def private_raw_diagnostics():
        with tempfile.TemporaryDirectory(prefix='autotune-raw-fixture-') as folder:
            root=Path(folder)/'failures';payload=b'canonical raw fixture\n'*100
            spool=spool_module.TraceSpool()
            try:
                spool.append(payload)
                first=spool.save_private(root,'dat-'+'1'*32,dict(schema='fixture'),limit=len(payload))
                assert first['raw_complete'] is True and first['saved_bytes']==len(payload)
                assert Path(first['raw_path']).read_bytes()==payload
                assert first['sha256']==hashlib.sha256(payload).hexdigest()
                assert json.loads(Path(first['metadata_path']).read_text())['raw']==first
                partial=spool.save_private(root,'dat-'+'2'*32,dict(schema='fixture'),limit=37)
                assert partial['raw_complete'] is False and partial['saved_bytes']==37
                assert Path(partial['raw_path']).read_bytes()==payload[:37]
                third=spool.save_private(root,'dat-'+'3'*32,dict(schema='fixture'))
                blocked=spool.save_private(root,'dat-'+'4'*32,dict(schema='fixture'))
                assert blocked['status']=='not-saved' and not (root/('dat-'+'4'*32)).exists()
                if os.name=='posix':
                    assert root.stat().st_mode&0o777==0o700
                    for item in (first,partial,third):
                        assert Path(item['case_path']).stat().st_mode&0o777==0o700
                        assert Path(item['raw_path']).stat().st_mode&0o777==0o600
                        assert Path(item['metadata_path']).stat().st_mode&0o777==0o600
            finally:spool.close()
    check('private failure spool preserves exact bytes, explicit truncation and three-case bound',private_raw_diagnostics)

    def offline_oracle():
        with tempfile.TemporaryDirectory(prefix='autotune-replay-fixture-') as folder:
            spool=spool_module.TraceSpool()
            try:
                spool.append(''.join(events()+[wake(6.05,11)]).encode())
                saved=spool.save_private(Path(folder)/'failures','dat-'+'5'*32,
                    dict(known_targets=known,capture=dict(monotonic_start=0.,monotonic_end=8.,
                        stop_requested=5.1,measurement_window=[2.,5.])))
                captured=io.StringIO()
                with contextlib.redirect_stdout(captured):replay_case(lib,Path(saved['case_path']))
                value=json.loads(captured.getvalue())
                assert value['qualification_permitted'] is False
                assert value['source_sha256']==saved['sha256']
                assert value['integrity']['clock_order_errors']>0 and value['clock_error_examples']
            finally:spool.close()
    check('offline raw oracle reads identical bytes and cannot qualify captured evidence',offline_oracle)

    def indexed_replay(lines,identities=None,window=None):
        spool=spool_module.TraceSpool();value=scheduler.Accounting(identities or copy.deepcopy(known),window)
        try:
            payload=''.join(lines).encode();spool.append(payload);receipt=spool.replay(value)
            spool.stream.seek(0);assert spool.stream.read()==payload,'Original spool was modified'
            return value,receipt
        finally:spool.close()

    def delivery_fixture(same_cpu=False):
        first_cpu='[001]';wake_cpu='[001]' if same_cpu else '[000]'
        return [switch(1.002819,11,0).replace('[000]',first_cpu),
            switch(1.007405,0,11).replace('[000]',first_cpu),
            wake(1.007390,11).replace('[000]',wake_cpu),
            switch(1.007491,11,0).replace('[000]',first_cpu)]

    def verified_cross_cpu_merge():
        lines=delivery_fixture();identities={11:known[11]}
        raw=scheduler.Accounting(identities,(1.003,1.008))
        for line in lines:raw.feed(line)
        assert raw.clock_errors==1 and raw.rows[11].get('wakeup_while_running')==1
        value,receipt=indexed_replay(lines,identities,(1.003,1.008))
        assert receipt['validated'] and receipt['mode']=='stable-cpu-merge'
        assert receipt['raw_global_backtracks']==1 and receipt['per_cpu_clock_errors']==0
        assert value.clock_errors==0 and value.events==4
        assert value.rows[11]['wake_to_run_samples']==1 and value.rows[11]['max_runnable_to_run_ns']==15000
        assert value.rows[11].get('wakeup_while_running',0)==0
    check('independent four-event remote-wake fixture reconstructs the actual 15 us sample',verified_cross_cpu_merge)

    def standard_header_variants():
        for tgid in (False,True):
            lines=[line.replace(' d..2 ',' ').replace(' d..2. ',' ') for line in delivery_fixture()]
            if tgid:lines=[line.replace(' [',' ( 42) [',1) for line in lines]
            value,receipt=indexed_replay(lines,{11:known[11]},(1.003,1.008))
            assert receipt['validated'] and value.clock_errors==0
            assert value.rows[11]['max_runnable_to_run_ns']==15000
    check('standard headers without IRQ flags and with record-tgid retain CPU provenance',standard_header_variants)

    def real_cpu_regressions_stay_invalid():
        value,receipt=indexed_replay(delivery_fixture(True),{11:known[11]},(1.003,1.008))
        assert not receipt['validated'] and receipt['mode']=='invalid'
        assert receipt['per_cpu_clock_errors']==1 and value.clock_errors>=1
        unknown=[wake(3.1,999),wake(3.,999)]
        value,receipt=indexed_replay(unknown,{11:known[11]})
        assert not receipt['validated'] and value.clock_errors>=1
    check('true per-CPU regression remains a hard failure even for an untracked task',real_cpu_regressions_stay_invalid)

    def invalid_headers_and_loss():
        streams=[[' fixture-11 d..2 3.0: sched_wakeup: comm=x pid=11 prio=120 target_cpu=0\n'],
            [' fixture-11 d..2 3.0: sched_wakeup: comm=x-9 [007] d..2 4.0: pid=11 prio=120 target_cpu=0\n'],
            [wake(3.,11).rstrip('\n')],['CPU: 0 [LOST 1 EVENTS]\n']]
        for lines in streams:
            value,receipt=indexed_replay(lines)
            assert not receipt['validated'] and (value.unparsed or value.lost or value.clock_errors)
        value,receipt=indexed_replay([' fixture-11 [000] d..2 3.0: sched_wakeup: comm=x pid=bad prio=120 target_cpu=0\n'])
        assert value.unparsed>0,'Header reconstruction must not hide invalid payloads'
    check('missing CPU, partial records, loss and malformed payloads cannot pass ordering gates',invalid_headers_and_loss)

    def equal_timestamp_ordinals():
        class Recorder:
            def __init__(self):self.unparsed=self.lost=self.clock_errors=0;self.rows=[]
            def feed(self,line):self.rows.append(line)
            def feed_merged(self,line,stamp):self.rows.append(line)
        lines=[switch(1.,11,0).replace('[000]','[001]'),
            switch(1.005,0,11).replace('[000]','[001]'),wake(1.004,22),wake(1.005,11),
            switch(1.01,11,0).replace('[000]','[001]')]
        spool=spool_module.TraceSpool();value=Recorder()
        try:
            spool.append(''.join(lines).encode());receipt=spool.replay(value)
            assert receipt['validated'] and receipt['mode']=='stable-cpu-merge'
            assert value.rows.index(lines[1])<value.rows.index(lines[3]),'Equal stamps must retain raw ordinal'
        finally:spool.close()
    check('equal timestamps retain source ordinal without invented sub-timestamp causality',equal_timestamp_ordinals)

    def many_cpu_indices():
        lines=[wake(2.+cpu*.0001,11).replace('[000]','['+str(cpu).zfill(3)+']') for cpu in reversed(range(64))]
        value,receipt=indexed_replay(lines)
        assert receipt['validated'] and len(receipt['cpu_ids'])==64
        assert receipt['max_open_index_files']==16 and receipt['index_bytes']==64*spool_module.INDEX.size
        assert receipt['raw_bytes_duplicated']==0 and value.clock_errors==0 and value.events==64
    check('64 CPU streams merge through bounded index descriptors without duplicating raw bytes',many_cpu_indices)

    def equal_windows():
        a=observation(lines=events(warmup_delay=.9))
        b=observation(lines=events(warmup_delay=.01))
        assert contract.optimization_issues(a)==[],contract.optimization_issues(a)
        assert contract.optimization_issues(b)==[],contract.optimization_issues(b)
        assert contract.metric(a)==contract.metric(b)
        assert contract.benefit_metrics(contract.metric(a),contract.metric(b))==set()
    check('warmup-only difference cannot manufacture an improvement',equal_windows)

    def crossing_left():
        lines=events(False)
        lines.extend((wake(1.999,11),switch(2.003,0,11),switch(2.004,11,0)))
        lines.sort(key=lambda line:float(scheduler.LINE.search(line)[1]+'.'+scheduler.LINE.search(line)[2]))
        value=counter(lines,False)
        assert report(value,3.)['threads'][0]['max_runnable_to_run_ns']==4000000
    check('wait beginning before sample retains its full observed delay',crossing_left)

    def right_censor():
        lines=events()
        lines.extend((wake(4.9,11),switch(5.5,0,11)))
        lines.sort(key=lambda line:float(scheduler.LINE.search(line)[1]+'.'+scheduler.LINE.search(line)[2]))
        measured=observation(lines=lines)
        issues=contract.optimization_issues(measured)
        assert any('right-censored' in issue for issue in issues),issues
        assert measured['scheduler_trace']['pending_at_window_end']==1
    check('wait completed beyond the bounded tail remains censored',right_censor)

    def ordered(lines):
        return sorted(lines,key=lambda line:float(scheduler.LINE.search(line)[1]+'.'+scheduler.LINE.search(line)[2]))

    def closing_tail(stamp=5.05):
        return observation(lines=ordered(events()+[wake(4.999,11),switch(stamp,0,11)]))

    def known_tail_completion():
        measured=closing_tail();trace=measured['scheduler_trace'];tail=trace['completion_tail']
        assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
        assert measured['max_scheduling_latency']==51.
        assert trace['pending_at_window_end']==1 and trace['pending_after_completion_tail']==0
        assert tail['completed_count']==1 and tail['remaining_count']==0
        assert tail['completed_intervals']==[dict(tid=11,runnable_since_ns=4999000000,
            cause='wake',scheduled_at_ns=5050000000,latency_ns=51000000)]
        assert feed.boundary_status(measured)['completed_in_tail']==1
    check('known sampled wait may complete within 100 ms and retains full latency',known_tail_completion)

    def tail_limits():
        edge=closing_tail(5.1)
        assert contract.optimization_issues(edge)==[],contract.optimization_issues(edge)
        for stamp in (5.100000001,5.5):
            measured=closing_tail(stamp)
            assert any('right-censored' in issue for issue in contract.optimization_issues(measured))
            assert measured['scheduler_trace']['completion_tail']['completed_count']==0
    check('completion at deadline is admitted; later completions remain censored',tail_limits)

    def absent_or_short_tail():
        for measured in (observation(lines=ordered(events()+[wake(4.999,11)])),
                         observation(lines=ordered(events()+[wake(4.999,11),switch(5.05,0,11)]),stop=5.02,capture_end=5.02)):
            assert any('right-censored' in issue for issue in contract.optimization_issues(measured))
            assert measured['scheduler_trace']['completion_tail']['remaining_count']==1
    check('absent completion and short capture never fabricate recovery',absent_or_short_tail)

    def tail_only_activity():
        lines=ordered(events()+[wake(5.001,22),switch(5.09,0,22),
            switch(5.001,11,0,'R'),switch(5.09,0,11)])
        measured=observation(lines=lines)
        assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
        assert measured['max_scheduling_latency']==2.
        assert measured['wakeups_per_second']==1.
        assert measured['scheduler_trace']['completion_tail']['pending_at_observation_end_count']==0
    check('new tail wakeups and preemptions cannot affect measured metrics',tail_only_activity)

    def one_completion_then_noise():
        lines=ordered(events()+[wake(4.999,11),switch(5.01,0,11),switch(5.011,11,0),
                               wake(5.02,11),switch(5.09,0,11)])
        measured=observation(lines=lines)
        assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
        assert measured['max_scheduling_latency']==11.
        assert sum(row['wakeups'] for row in measured['scheduler_trace']['threads'])==4
        assert measured['scheduler_trace']['completion_tail']['completed_count']==1
    check('one sampled wait closes once; later same-task tail spike is ignored',one_completion_then_noise)

    def tail_preemption():
        lines=ordered(events()+[switch(4.998,0,11),switch(4.999,11,0,'R'),switch(5.02,0,11)])
        measured=observation(lines=lines)
        assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
        assert measured['max_scheduling_latency']==21.
        assert measured['scheduler_trace']['completion_tail']['completed_intervals'][0]['cause']=='preempt'
    check('preemption already open at sample end receives the same bounded completion',tail_preemption)

    def false_tail_receipts():
        valid=closing_tail()
        mutations=[lambda m:m['scheduler_trace']['completion_tail']['completed_intervals'][0].update(scheduled_at_ns=5200000000,latency_ns=201000000),
            lambda m:m['scheduler_trace']['completion_tail']['completed_intervals'][0].update(runnable_since_ns=5001000000,latency_ns=49000000),
            lambda m:m['scheduler_trace']['completion_tail']['completed_intervals'][0].update(tid=999),
            lambda m:m['scheduler_trace']['completion_tail'].update(capture_end_ns=5040000000),
            lambda m:m['scheduler_trace']['completion_tail'].update(budget_ns=200000000),
            lambda m:m['scheduler_trace']['completion_tail'].update(completed_count=0),
            lambda m:m['scheduler_trace']['completion_tail']['completed_intervals'].append(copy.deepcopy(m['scheduler_trace']['completion_tail']['completed_intervals'][0])),
            lambda m:next(row for row in m['scheduler_trace']['threads'] if row['tid']==11).update(completion_tail_samples=0),
            lambda m:next(row for row in m['scheduler_trace']['threads'] if row['tid']==11).update(runnable_to_run_samples=0),
            lambda m:next(row for row in m['scheduler_trace']['threads'] if row['tid']==11).update(max_runnable_to_run_ns=2000000),
            lambda m:m.update(max_scheduling_latency=2.),
            lambda m:m['scheduler_trace'].update(pending_at_window_end=0),
            lambda m:m['scheduler_trace'].pop('completion_tail'),
            lambda m:m['scheduler_trace'].update(measurement_window_protocol='scheduler-observation-window-v1')]
        for mutation in mutations:
            value=copy.deepcopy(valid);mutation(value)
            assert contract.optimization_issues(value),value
    check('false, missing, oversized and legacy tail receipts cannot qualify',false_tail_receipts)

    for cores in (1,2,4,64):
        def arbitrary_cores(cores=cores):
            measured=observation(cores=cores)
            assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
        check('physical USB measurement with '+str(cores)+' online CPUs',arbitrary_cores)

    for output_kind in ('alsa','usb'):
        def audio_only(output_kind=output_kind):
            measured=observation(False,output_kind=output_kind)
            assert measured['scheduler_trace']['measurement_scope']=='verified-audio'
            assert contract.optimization_issues(measured)==[],contract.optimization_issues(measured)
            assert any('requested control scope' in issue for issue in
                contract.optimization_issues(measured,required_scope='verified-audio-and-usb-irq'))
            measured['scheduler_trace']['measurement_scope']='verified-audio-and-usb-irq'
            assert any('roles' in issue for issue in contract.optimization_issues(measured))
        check(output_kind+' audio-only remains valid; combined IRQ claim refused',audio_only)

    def independent_roles():
        _,requests=scheduler.target_specification(specs)
        changed=copy.deepcopy(known);changed[22]['name']='irq/18-xhci_hcd'
        try:scheduler.observed_roles(requests,changed)
        except RuntimeError:pass
        else:raise AssertionError('An unrelated physical IRQ acquired the requested role')
        changed=copy.deepcopy(known);changed[22]['thread_start']='999'
        try:scheduler.observed_roles(requests,changed)
        except RuntimeError:pass
        else:raise AssertionError('A reused IRQ TID acquired the requested role')
    check('IRQ role requires actual requested IRQ name and original task birth',independent_roles)

    def legacy_scope():
        births,roles=scheduler.target_specification({11:'101',22:'202'})
        observed,scope=scheduler.observed_roles(roles,known)
        assert births=={11:'101',22:'202'} and observed==[] and scope=='verified-target-threads'
        measured=observation()
        measured['scheduler_trace'].pop('target_roles')
        measured['scheduler_trace'].pop('measurement_window_protocol')
        assert contract.optimization_issues(measured)
    check('legacy captures stay readable but cannot qualify current combined claims',legacy_scope)

    def misaligned_window():
        measured=observation()
        measured['scheduler_trace']['measurement_monotonic_start']=1.99
        assert any('exact observation window' in issue for issue in contract.optimization_issues(measured))
    check('metrics for a different window cannot qualify',misaligned_window)

    def discover_targets():
        with tempfile.TemporaryDirectory(prefix='autotune-irq-fixture-') as folder:
            proc=Path(folder)
            for tid,name in ((11,'audio-output'),(22,'irq/17-xhci_hcd')):
                path=proc/str(tid);path.mkdir();(path/'comm').write_text(name+'\n')
            with patch.object(measure,'ticks',lambda tid:known[tid]['thread_start']):
                irq,targets=measure.measurement_targets({'usb':{'irq':'17'}},11,proc)
                assert irq==17 and targets==specs
                irq,targets=measure.measurement_targets({},11,proc)
                assert irq is None and targets=={11:specs[11]}
                (proc/'22'/'comm').write_text('irq/18-xhci_hcd\n')
                irq,targets=measure.measurement_targets({'usb':{'irq':'17'}},11,proc)
                assert irq==17 and targets=={11:specs[11]}
                irq,targets=measure.measurement_targets({'usb':{}},11,proc)
                assert irq is None and targets=={11:specs[11]}
    check('actual IRQ discovery: sysfs string, non-USB, missing and unrelated IRQ',discover_targets)

    def framed_window():
        reader,writer=os.pipe()
        try:
            payload=dict(protocol=scheduler.WINDOW_PROTOCOL,start=2.,end=5.)
            os.write(writer,b'D'+json.dumps(payload).encode()+b'\n');os.close(writer);writer=None
            assert os.read(reader,1)==b'D'
            # Windows select only accepts sockets; the pipe read is real.
            with patch.object(scheduler.select,'select',lambda *args:([reader],[],[])):
                assert scheduler.completion_window(reader)==(2.,5.)
        finally:
            os.close(reader)
            if writer is not None:os.close(writer)
    check('bounded observation-end handshake preserves exact window',framed_window)

    def later(value,offset=10.):
        value=copy.deepcopy(value);trace=value['scheduler_trace'];ns=int(offset*10**9)
        for name in ('observation_monotonic_start','observation_monotonic_end'):value[name]+=offset
        for name in ('monotonic_start','monotonic_end','measurement_monotonic_start','measurement_monotonic_end'):trace[name]+=offset
        for row in trace['initial_state_boundary']['states']:
            if row['first_state_at_ns'] is not None:row['first_state_at_ns']+=ns
        boundary=trace['runnable_boundary']
        if boundary.get('stop_requested_monotonic_ns') is not None:boundary['stop_requested_monotonic_ns']+=ns
        for row in boundary['intervals']:row['runnable_since_ns']+=ns
        tail=trace['completion_tail']
        for key in ('observation_end_ns','deadline_ns','capture_stop_requested_ns','capture_end_ns','completion_limit_ns'):tail[key]+=ns
        for row in tail['pending_at_observation_end']:row['runnable_since_ns']+=ns
        for row in tail['completed_intervals']:
            row['runnable_since_ns']+=ns;row['scheduled_at_ns']+=ns
        return value

    def beneficial(value):
        value=copy.deepcopy(value);value['cpu_percent']['cpu']=3.
        return value

    def confirmation_scope_change():
        before=observation();after=later(beneficial(observation(False,output_kind='usb')))
        assert contract.optimization_issues(before)==[] and contract.optimization_issues(after)==[]
        assert any('scope' in issue for issue in contract.confirmation_issues(before,after))
    check('fresh holdout rejects loss of IRQ coverage despite lower scalar latency',confirmation_scope_change)

    def confirmation_irq_change():
        before=observation();after=later(beneficial(observation()))
        after['instrumentation_usb_irq']=18
        for collection in (after['scheduler_trace']['target_roles'],after['instrumentation_targets']):
            for row in collection:
                if row['role']=='usb-irq':row['irq']=18
        next(row for row in after['scheduler_trace']['threads'] if row['tid']==22)['name']='irq/18-xhci_hcd'
        assert contract.optimization_issues(after)==[],contract.optimization_issues(after)
        assert any('scope' in issue for issue in contract.confirmation_issues(before,after))
    check('fresh holdout rejects a different physical IRQ measurement target',confirmation_irq_change)

    def confirmation_duration_change():
        before=observation();after=later(beneficial(observation()))
        after['instrumentation_requested_seconds']=2.
        assert contract.optimization_issues(after)==[],contract.optimization_issues(after)
        assert any('duration changed' in issue for issue in contract.confirmation_issues(before,after))
    check('fresh holdout rejects unequal requested measurement exposure',confirmation_duration_change)

    def confirmation_identity_change():
        before=observation();after=later(beneficial(observation()));trace=after['scheduler_trace']
        ids={11:111,22:222}
        trace['target_tids']=[ids[tid] for tid in trace['target_tids']]
        for collection in (trace['target_roles'],after['instrumentation_targets'],trace['threads']):
            for row in collection:
                row['tid']=ids[row['tid']];row['thread_start']='1001' if row['tid']==111 else '2002'
        for row in trace['initial_state_boundary']['states']:row['tid']=ids[row['tid']]
        assert contract.optimization_issues(after)==[],contract.optimization_issues(after)
        assert contract.confirmation_issues(before,after)==[],contract.confirmation_issues(before,after)
    check('semantic coverage permits separately verified new task identities',confirmation_identity_change)

    def choose_scope_change():
        def row(spec,irq_scope,candidate):
            pairs=[]
            for repeat in range(3):
                a=observation(irq_scope);b=beneficial(a) if candidate else copy.deepcopy(a)
                pairs.append(dict(before=later(a,repeat*20.),after=later(b,repeat*20.+4.),
                    restored_baseline=later(a,repeat*20.+8.)))
            return dict(spec=spec,measurements=pairs,issues=[],application_observed=candidate,
                application_observations=[candidate]*3,comparison_protocol=contract.BRACKET_PROTOCOL)
        reference=row(dict(name='current',stage='reference'),True,False)
        candidate=row(dict(name='governor-performance',stage='energy',governor='performance'),False,True)
        assert contract.choose([reference,candidate])['status']=='retain-current'
    check('repeated selection cannot rank different target scopes against each other',choose_scope_change)

    def search_report(values):
        row=dict(spec=dict(name='governor-performance',stage='energy',governor='performance'),
            application_observed=True,measurements=[dict(zip(('before','after','restored_baseline'),values))])
        return dict(screening=[row],planning=dict(available_families=['governor','priority','clock']),
            deferred=[dict(name='fifo-50',family='priority',reason='budget'),dict(name='unknown',reason='unsupported')])

    def report_for(values):
        return scope_module.describe(search_report(values),dict(scope='owned-native-pcm',rate=44100,bits=32,channels=2))

    def scope_empty():
        result=scope_module.describe({},dict(scope='owned-native-pcm'))
        assert result['measurement_coverage']['common_scope'] is None
        assert result['measurement_coverage']['observation_count']==0
        assert 'USB' not in result['latency_metric'] and 'unavailable' in result['latency_metric']
    check('report makes no latency or USB claim before current measurements exist',scope_empty)

    def scope_audio_only():
        result=report_for([later(observation(False,output_kind='usb'),i*4.) for i in range(3)])
        assert result['measurement_coverage']['common_scope']=='verified-audio'
        assert result['measurement_coverage']['usb_irq_covered_every_observation'] is False
        assert 'USB' not in result['latency_metric']
    check('audio-only reporting never expands to a USB IRQ claim',scope_audio_only)

    def scope_complete():
        result=report_for([later(observation(),i*4.) for i in range(3)])
        assert result['measurement_coverage']['common_scope']=='verified-audio-and-usb-irq'
        assert result['measurement_coverage']['usb_irq_covered_every_observation'] is True
        assert 'USB IRQ' in result['latency_metric']
        coverage=result['control_family_coverage']
        assert coverage['supported_control_families']==['clock','governor','priority']
        assert coverage['observed_control_families']==['governor']
        assert coverage['attempted_control_families']==['governor']
        assert coverage['deferred_candidate_families']=={'priority':1}
        assert coverage['unclassified_deferred_entries']==1
        assert result['acceptance_authority'] is False and result['sound_quality_claim'] is False
    check('report separates inventory support, actual observation and deferred families',scope_complete)

    def scope_mixed_or_bad():
        for invalid in (False,True):
            values=[later(observation(),i*4.) for i in range(3)]
            if invalid:values[1]['scheduler_trace'].pop('measurement_window_protocol')
            else:values[1]=later(observation(False,output_kind='usb'),4.)
            result=report_for(values)
            assert result['measurement_coverage']['consistent_coverage'] is False
            assert result['measurement_coverage']['usb_irq_covered_every_observation'] is False
            assert result['measurement_coverage']['roles_covered_every_observation']==[]
            assert 'USB' not in result['latency_metric']
    check('mixed and invalid measurement coverage cannot claim complete USB observation',scope_mixed_or_bad)

    def scope_aliases():
        values=[later(observation(),i*4.) for i in range(3)];search=search_report(values)
        search['combined_verification']=copy.deepcopy(search['screening'][0])
        result=scope_module.describe(search,{})
        assert result['measurement_coverage']['observation_count']==3
        search['combined_verification']['measurements'][0]['after']['max_scheduling_latency']=9.
        result=scope_module.describe(search,{})
        assert result['measurement_coverage']['conflicting_observation_windows']==1
        assert result['measurement_coverage']['usb_irq_covered_every_observation'] is False
    check('result aliases are deduplicated and contradictory windows are flagged',scope_aliases)

    failed=sum(row['status']=='FAIL' for row in results)
    print(json.dumps(dict(environment='canonical-event and hardware-capability fixtures; no native audio/tracefs',
        linux_private_diagnostic_io_checked=os.name=='posix',
        lib=str(lib),passed=len(results)-failed,failed=failed,checks=results),indent=2))
    return 1 if failed else 0


def benchmark(lib,repetitions=3):
    """Replay the same realistic 16 MB spool repeatedly, excluding generation."""
    sys.path.insert(0,str(lib))
    scheduler=importlib.import_module('scheduler_trace');feed=importlib.import_module('trace_feed')
    spool_module=importlib.import_module('trace_spool')
    known={11:dict(pid=10,process_start='100',thread_start='101',name='audio-output'),
           22:dict(pid=22,process_start='202',thread_start='202',name='irq/17-xhci_hcd')}
    def lines():
        # Roughly 100,000 events over 11.12 s, alternating actual audio/IRQ
        # targets, with a real wait spanning the observation-end boundary.
        for index in range(33333):
            tid=11 if index%2==0 else 22
            stamp=10000.+index*.0003336
            if tid==11 and stamp>=10010.9999:continue
            yield wake(stamp,tid)
            yield switch(stamp+.000015,0,tid)
            yield switch(stamp+.000025,tid,0)
        yield wake(10010.99999,11)
        yield switch(10011.00002,0,11)
    sequence=sorted(lines(),key=lambda line:float(scheduler.LINE.search(line)[1]+'.'+scheduler.LINE.search(line)[2]))
    payload=''.join(sequence).encode()
    # Prefix padding represents ordinary task/CPU/flags fields, without
    # modifying scheduler payloads or the independent target event sequence.
    payload=payload.replace(b' fixture-0 ',b' audio_output-1234567890123456789 ')
    source_hash=hashlib.sha256(payload).hexdigest();window=(10001.,10011.)
    durations=[];fingerprints=[];snapshot=None
    spool=spool_module.TraceSpool()
    try:
        spool.append(payload)
        for _ in range(repetitions):
            single=getattr(scheduler,'SINGLE_PASS_WINDOW_ACCOUNTING',False)
            root=scheduler.Accounting(known,window if single else None)
            if not single:root.measurement_mirror=scheduler.Accounting(known,window)
            started=time.process_time();receipt=spool.replay(root);durations.append(time.process_time()-started)
            measured=root if single else root.measurement_mirror
            with patch.object(scheduler,'ticks',lambda tid:known[tid]['thread_start']):
                report=measured.report(10.)
            snapshot=dict(threads=report['threads'],processes=report['processes'],
                identity_unverified_threads=report['identity_unverified_threads'],
                boundary=feed.capture_boundary(measured,10011.1,10011.12),
                integrity=dict(events_parsed=root.events,unparsed_lines=root.unparsed,
                    lost_event_markers=root.lost,clock_order_errors=root.clock_errors))
            fingerprints.append(hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    finally:spool.close()
    assert len(set(fingerprints))==1,'Repeated decoder output changed'
    print(json.dumps(dict(environment='local canonical-event spool replay; no native audio/tracefs',
        source_bytes=len(payload),source_lines=payload.count(b'\n'),source_sha256=source_hash,
        repetitions=repetitions,decode_cpu_seconds=durations,
        median_cpu_seconds=sorted(durations)[len(durations)//2],
        output_sha256=fingerprints[0],output=snapshot,
        accounting_version=feed.ACCOUNTING_VERSION,stream_order=receipt,
        single_pass_window_accounting=getattr(scheduler,'SINGLE_PASS_WINDOW_ACCOUNTING',False)),indent=2))
    return 0


def replay_case(lib,case):
    """Read-only offline comparison; this cannot qualify captured observations."""
    sys.path.insert(0,str(lib))
    scheduler=importlib.import_module('scheduler_trace');feed=importlib.import_module('trace_feed')
    spool_module=importlib.import_module('trace_spool')
    case=Path(case);meta=json.loads((case/'metadata.json').read_text())
    raw=meta['raw'];source=case/'events.trace'
    if raw.get('raw_complete') is not True:raise RuntimeError('Offline oracle requires the complete preserved spool')
    payload=source.read_bytes()
    if len(payload)!=raw['saved_bytes'] or hashlib.sha256(payload).hexdigest()!=raw['sha256']:
        raise RuntimeError('Preserved spool size or SHA differs')
    known={int(key):value for key,value in meta['known_targets'].items()}
    capture=meta['capture'];window=capture.get('measurement_window')
    window=tuple(window) if window is not None else None
    single=getattr(scheduler,'SINGLE_PASS_WINDOW_ACCOUNTING',False)
    root=scheduler.Accounting(known,window if single else None)
    if not single and window is not None:root.measurement_mirror=scheduler.Accounting(known,window)
    spool=spool_module.TraceSpool()
    try:
        spool.append(payload);started=time.process_time();receipt=spool.replay(root);elapsed=time.process_time()-started
        measured=root if single or window is None else root.measurement_mirror
        duration=window[1]-window[0] if window is not None else capture['monotonic_end']-capture['monotonic_start']
        with patch.object(scheduler,'ticks',lambda tid:known[tid]['thread_start']):report=measured.report(duration)
        boundary=feed.capture_boundary(measured,capture['stop_requested'],capture['monotonic_end'])
        print(json.dumps(dict(environment='read-only offline replay; no native qualification',qualification_permitted=False,
            identity_validation='not live; captured birth metadata used for replay only',source_sha256=raw['sha256'],
            source_bytes=len(payload),decode_cpu_seconds=elapsed,
            stream_order=receipt,
            integrity=dict(events_parsed=root.events,unparsed_lines=root.unparsed,lost_event_markers=root.lost,
                clock_order_errors=root.clock_errors),clock_error_examples=report.get('clock_error_examples'),
            measured_threads=report['threads'],boundary=boundary),indent=2))
    finally:spool.close()
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lib',type=Path,default=Path(__file__).resolve().parent/'working'/'lib')
    parser.add_argument('--benchmark',action='store_true')
    parser.add_argument('--repetitions',type=int,default=3)
    parser.add_argument('--replay-case',type=Path)
    args=parser.parse_args()
    raise SystemExit(replay_case(args.lib.resolve(),args.replay_case) if args.replay_case else
        benchmark(args.lib.resolve(),args.repetitions) if args.benchmark else run_all(args.lib.resolve()))
