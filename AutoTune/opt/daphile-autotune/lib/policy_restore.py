"""Revalidate an explicitly saved abstract policy on fresh audio identities.

Never starts music, repeats a search, reuses a thread ID, or replays a queue.
Native Settings changes invalidate the policy rather than being overwritten.
"""
import copy,hashlib,json,os,select,signal,subprocess,sys,time
from pathlib import Path
try:from measurement_contract import confirmation_issues,optimization_issues
except ImportError:from .measurement_contract import confirmation_issues,optimization_issues


def native_digest(path=Path('/var/daphile/conf/config.sys')):
    try:return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:return None


def issues(saved,hardware,automatic,current,digest):
    from continuous import compatible
    reasons=[]
    if saved.get('status')!='verified' or automatic.get('status')!='configured':reasons.append('No configured policy')
    if not compatible(saved,hardware):reasons.append('Host, DAC, kernel or topology changed')
    if not digest or saved.get('native_settings_sha256')!=digest:reasons.append('Native Settings changed or unavailable')
    if current.get('profile')!='automatic' or current.get('status')!='committed':reasons.append('Current transaction is not the saved automatic policy')
    if current.get('session_owner')!={'kind':'automatic','id':automatic.get('id')}:reasons.append('Another transaction owns the configuration')
    if saved.get('selected')!=current.get('selected_policy'):reasons.append('Saved policy does not match the committed transaction')
    pair=current.get('benchmark',{})
    if not isinstance(pair,dict):pair={}
    reasons+=['Saved policy: '+reason for reason in confirmation_issues(pair.get('before'),pair.get('after'))]
    return reasons


def control_key(change):
    return (change['parameter'],change.get('path'),change.get('policy'),change.get('tid'),change.get('service'))


def reapply(root,hardware):
    import engine,session,player,measure
    from core_policy import decide
    root=Path(root)
    with engine.locked(root):
        old=engine.load(root/'current.json');saved=engine.load(root/'automatic-policy.json');automatic=engine.load(root/'automatic.json')
        reasons=issues(saved,hardware,automatic,old,native_digest())
        if reasons:raise RuntimeError('; '.join(reasons))
        engine.ensure_session_owner(root);player.assert_library_idle(hardware)
        for change in old['changes']:
            if change.get('tid') and (old.get('boot_id')!=engine.boot_id() or engine.start_ticks(change['tid'])!=change.get('start')):continue
            engine.guard(change,hardware)
            if engine.setting_value(change) not in (change['before'],change['after']):raise RuntimeError('Manual runtime setting preserved')
        pending=engine.load(root/'transaction.json')
        if pending.get('id')!=old.get('id') or pending.get('status')!='committed':raise RuntimeError('Pending recovery or concurrent transaction')
        # The trial replaces this exact journal under its state lock and keeps
        # it as previous_profile, so a crash cannot lose the original undo data.
    lease=None
    try:
        lease=session.Lease(root,player.playback_snapshot(player.target(hardware)['mac']),180,'search',restore_playback=False)
        observations=[]
        def observe(seconds):
            value=measure.observe(seconds)
            if optimization_issues(value):raise RuntimeError('Incomplete legacy policy revalidation observation')
            # The native measurement is data, not a reusable pointer that a
            # later observation may mutate into apparently repeated evidence.
            observations.append(copy.deepcopy(value))
            if len(observations)>2:raise RuntimeError('Unexpected policy revalidation observation sequence')
            if len(observations)==2:
                reasons=confirmation_issues(*observations)
                if reasons:raise RuntimeError('Policy revalidation did not independently confirm improvement: '+'; '.join(reasons))
            return value
        result=engine.trial(lambda fresh:decide(fresh,saved['selected'])['changes'],'automatic-policy-reapply',30,root,True,observer=observe,restore_position=False,replace_current=old)
        with engine.locked(root):
            if result['status']=='committed':
                if len(observations)!=2:raise RuntimeError('Two actual final observations required')
                journal=engine.load(root/'current.json')
                if journal.get('benchmark')!={'before':observations[0],'after':observations[1]}:raise RuntimeError('Committed policy evidence differs from its fresh observations')
            elif result['status']=='no-change':
                if len(observations)!=1 or result.get('baseline')!=observations[0]:raise RuntimeError('Unchanged policy lacks its actual complete observation')
                if engine.load(root/'current.json')!=old or engine.load(root/'transaction.json')!=old:raise RuntimeError('Configuration changed during observation')
                return {'status':'already-current-observed','audio_started':False,'settings_written':False,
                        'new_committed_policy_created':False,'playlist_write_commands':0}
            else:raise RuntimeError('Revalidation did not commit')
            # Retain original undo values for surviving controls and the player
            # buffer, while new thread controls carry only fresh identities.
            fresh={control_key(c):c for c in journal['changes']}
            for c in old['changes']:
                if c.get('tid') and (old.get('boot_id')!=engine.boot_id() or engine.start_ticks(c['tid'])!=c.get('start')):continue
                key=control_key(c)
                if key in fresh:fresh[key]['before']=c['before']
                else:fresh[key]=c
            journal.update(changes=list(fresh.values()),profile='automatic',session_owner={'kind':'automatic','id':automatic['id']},selected_policy=saved['selected'],revalidated_at=time.time(),previous_profile=old.get('previous_profile'))
            for name in ('transaction.json','current.json','last-known-good.json'):engine.atomic(root/name,journal)
        return {'status':'reapplied-verified','transaction_id':journal['id'],'audio_started':False}
    finally:
        if lease:lease.finish()


def status(root):
    import engine
    if _v2(root):
        from autotune_v2.persistence import status as current
        return current(root)
    root=Path(root);path=root/'policy-restore-pid.json'
    record=engine.load(path) if path.exists() else {}
    running=bool(record.get('boot_id')==engine.boot_id() and record.get('start') and engine.start_ticks(record.get('pid'))==record['start'])
    latest=engine.load(root/'policy-restore.json') if (root/'policy-restore.json').exists() else {'status':'inactive'}
    return dict(running=running,latest=latest)


def start(root):
    import engine
    if _v2(root):
        from autotune_v2.persistence import start as current
        return current(root)
    root=Path(root)
    if not (root/'automatic-policy.json').exists() or not engine.load(root/'automatic-policy.json').get('persistent'):return {'status':'disabled'}
    if status(root)['running']:return {'status':'already-running'}
    with (root/'policy-restore.log').open('ab') as log:
        p=subprocess.Popen([sys.executable,str(Path(__file__)),str(root),'serve'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    return {'status':'starting','pid':p.pid}


def stop(root):
    import engine
    if _v2(root):
        from autotune_v2.persistence import stop as current
        return current(root)
    if not status(root)['running']:return {'status':'inactive'}
    record=engine.load(Path(root)/'policy-restore-pid.json')
    # pidfd prevents a process exit/reuse race between checking and signalling.
    try:fd=os.pidfd_open(record['pid'])
    except ProcessLookupError:return {'status':'inactive'}
    try:
        if engine.start_ticks(record['pid'])!=record['start']:raise RuntimeError('Monitor identity changed')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        if not select.select([fd],[],[],60)[0]:raise RuntimeError('Monitor recovery still active; stop not confirmed')
    finally:os.close(fd)
    return {'status':'stopped'}


def serve(root):
    import engine,fcntl
    if _v2(root):
        from autotune_v2.persistence import serve as current
        return current(root)
    from detection import scan
    root=Path(root);running=True;attempted=None;pending=None;seen=0
    def end(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,end);signal.signal(signal.SIGINT,end)
    with (root/'policy-restore.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        engine.atomic(root/'policy-restore-pid.json',{'pid':os.getpid(),'start':engine.start_ticks(os.getpid()),'boot_id':engine.boot_id()})
        while running:
            try:
                a=engine.load(root/'automatic.json');saved=engine.load(root/'automatic-policy.json')
                if a.get('status')!='configured' or not saved.get('persistent'):break
                h=scan();record={'status':'waiting-for-active-audio','time':time.time(),'audio_started':False}
                if h['dac'].get('status')=='verified-active':
                    current=engine.load(root/'current.json');reasons=issues(saved,h,a,current,native_digest())
                    if reasons:record.update(status='invalidated',reasons=reasons);running=False
                    else:
                        fingerprint=(engine.boot_id(),h['dac'].get('owner_pid'),engine.start_ticks(h['dac'].get('owner_pid')))
                        departed=current.get('boot_id')!=engine.boot_id() or any(c.get('tid') and engine.start_ticks(c['tid'])!=c.get('start') for c in current['changes'])
                        seen=seen+1 if pending==fingerprint else 1;pending=fingerprint
                        if not departed:record['status']='policy-current'
                        elif seen>=3 and attempted!=fingerprint:
                            # One attempt per stable audio identity; a failed safety
                            # gate never causes repeated trials in the background.
                            attempted=fingerprint;record.update(reapply(root,h))
                        else:record['status']='awaiting-revalidation'
                engine.atomic(root/'policy-restore.json',record)
            except (RuntimeError,OSError,ValueError,KeyError) as exc:
                engine.atomic(root/'policy-restore.json',{'status':'deferred','reason':str(exc),'time':time.time(),'audio_started':False})
            for _ in range(10):
                if not running:break
                time.sleep(1)


def _v2(root):
    path=Path(root)/'automatic.json'
    if not path.exists():return False
    if path.is_symlink():raise RuntimeError('Indirect automatic policy refused')
    return json.loads(path.read_text()).get('kind')=='automatic-configuration-v2'


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[2] not in ('serve','stop'):raise SystemExit('Owned state path and serve/stop required')
    result=serve(Path(sys.argv[1])) if sys.argv[2]=='serve' else stop(Path(sys.argv[1]))
    if result:print(json.dumps(result))

__release_signature__ = 'Tino Zappala'
