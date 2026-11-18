"""V2 policy continuation: abstract controls, fresh identities, no music replay.

Only a repeatedly accepted, nonempty policy can arm this monitor. Startup is
passive until actual compatible PCM exists. Revalidation uses a new native
transaction and independent lease, never the previous playlist or thread IDs.
A failed revalidation is not retried automatically.
"""
import copy,hashlib,json,os,select,signal,subprocess,sys,time,uuid,urllib.parse
from pathlib import Path
from .acceptance import proposal
from .accounting import issues as accounting_issues
try:
    from measurement_contract import optimization_issues,confirmation_issues
except ImportError:
    from ..measurement_contract import optimization_issues,confirmation_issues

KIND='autotune-v2-policy-v1'


def digest(value):return hashlib.sha256(value).hexdigest()


def binding(h,target):
    usb=h['dac'].get('usb') or next((u for u in h.get('usb',[]) if u.get('path')==target['identity'].get('path')),None)
    if not usb or not usb.get('controller') or not usb.get('path') or '-' not in usb['path']:raise RuntimeError('Physical USB controller and port required')
    topology=[dict(cpu=c['cpu'],topology=c['topology']) for c in h['cpu']['cores']]
    return dict(host_uuid=h['system']['product_uuid'],kernel=h['kernel']['release'],topology=topology,
        online=h['cpu']['online'],isolated=h['cpu']['isolated'],alsa_id=target['alsa_id'],player=target['mac'],
        usb=dict(controller=usb['controller'],port=usb['path'].split('-',1)[1],vendor=usb['idVendor'],product=usb['idProduct'],serial=usb.get('serial')))


def configuration(saved,current,boot_started,observed,writer,init):
    """Raw equality by default; one source-proved boot timestamp exception."""
    old=bytes.fromhex(saved['native_configuration_hex'])
    if digest(old)!=saved['native_configuration_sha256']:raise RuntimeError('Saved native configuration differs')
    if old==current:return dict(status='byte-identical')
    if saved['origin']['boot_id']==observed['boot_id']:raise RuntimeError('Native Settings changed during this boot')
    from .native_config_status import online_timestamp_difference
    return online_timestamp_difference(old,current,writer,init,boot_started,observed['epoch'])


def native_api():
    import engine,player,search,measure,session
    from detection import scan
    from native_coordination import Lease
    from .workflow import owner_lock
    def native_config():return Path('/var/daphile/conf/config.sys').read_bytes()
    def boot_time():return time.time()-float(Path('/proc/uptime').read_text().split()[0])
    def config_proof(saved):
        return configuration(saved,native_config(),boot_time(),dict(boot_id=engine.boot_id(),epoch=time.time()),
            digest(Path('/opt/netmgr/netmount').read_bytes()),digest(Path('/etc/init.d/netdrives').read_bytes()))
    return type('NativePolicyAPI',(),dict(engine=engine,player=player,search=search,measure=measure,Lease=session.Lease,
        coordinate=Lease,scan=staticmethod(scan),owner_lock=staticmethod(owner_lock),native_config=staticmethod(native_config),config_proof=staticmethod(config_proof)))()


def load(root,name,api):
    path=Path(root)/name
    if path.is_symlink():raise RuntimeError('Indirect V2 policy record refused')
    return api.engine.load(path) if path.exists() else {}


def activity(root,ident,api):
    current=load(root,'activity-session.json',api)
    if current.get('id')==ident:return current
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise RuntimeError('Exact archived activity required')
    return load(root,'v2-policy-activities/'+ident+'.json',api)


def current_journal(root,record,api,allow_running_origin=False,*,for_restore=False):
    journals=[load(root,n,api) for n in ('transaction.json','current.json','last-known-good.json')]
    if not journals[0] or any(j!=journals[0] for j in journals):
        if for_restore:
            from .policy_custody import failed_reapply_journal
            return failed_reapply_journal(root,record,api)
        raise RuntimeError('Three identical nonempty V2 journals required')
    try:
        return validate_owned_policy_journal(root,record,api,load(root,'v2-policy.json',api),journals[0],
                                            allow_running_origin,for_restore=for_restore)
    except RuntimeError:
        if not for_restore:raise
        from .policy_custody import failed_reapply_journal
        return failed_reapply_journal(root,record,api)


def validate_owned_policy_journal(root,record,api,saved,journal,allow_running_origin=False,*,for_restore=False):
    # Quality can invalidate promotion without invalidating an exact owned
    # undo. Only workflow's explicit undo and its read-only availability check
    # request this exception; all identities, three journals and the completed
    # lease remain mandatory below. It never qualifies promotion/revalidation.
    if type(for_restore) is not bool or (for_restore and allow_running_origin):
        raise ValueError('Owned restore requires an already completed policy')
    if saved.get('kind')!=KIND or saved.get('enabled') is not True or saved.get('run_id')!=record.get('id') or record.get('status')!='configured':
        raise RuntimeError('Enabled measured V2 policy required')
    selected=copy.deepcopy(record.get('selected_policy')) if for_restore else proposal(record['search'])
    if not isinstance(selected,dict) or selected.get('name')!='combined' or selected.get('stage')!='combined' or len(selected)<=2:
        raise RuntimeError('Exact nonempty combined policy identity required')
    if selected!=saved.get('selected') or selected!=record.get('selected_policy'):raise RuntimeError('Policy differs from repeated measured selection')
    if not journal or journal.get('status')!='committed' or journal.get('profile')!='automatic' or not journal.get('changes'):
        raise RuntimeError('Exact nonempty original committed V2 journal required')
    if journal.get('id')!=saved.get('transaction_id') or journal.get('selected_policy')!=selected:raise RuntimeError('Policy receipt differs from journal')
    lease=activity(root,saved.get('activity_id'),api)
    if lease.get('id')!=saved.get('activity_id') or journal.get('session_owner')!={'kind':'activity','id':lease.get('id')}:
        raise RuntimeError('Current policy recovery owner differs')
    link=journal.get('workflow_v2',{})
    if link.get('run_id')!=record['id'] or link.get('activity_id')!=lease.get('id'):raise RuntimeError('Explicit V2 policy link required')
    if journal.get('boot_id')!=lease.get('boot_id') or lease.get('kind') not in ('automatic-v2','policy-revalidation-v2'):
        raise RuntimeError('V2 policy activity identity differs')
    if journal.get('parent_pid')!=lease.get('owner') or journal.get('parent_start')!=lease.get('start') or not lease.get('start'):
        raise RuntimeError('Transaction writer differs from its activity')
    if not for_restore:
        pair=journal.get('benchmark',{})
        if not isinstance(pair,dict):pair={}
        before,after=(pair.get(k) for k in ('before','after'))
        final_issues=confirmation_issues(before,after)
        if any(accounting_issues(m) for m in (before,after)) or final_issues:
            raise RuntimeError('Complete safe final measurements with independent benefit required: '+'; '.join(final_issues))
    if lease.get('kind')=='automatic-v2':
        if link.get('protocol')!='activity-completion-v1' or lease.get('parent_session_owner')!={'kind':'automatic','id':record['id']}:
            raise RuntimeError('Original policy acceptance link differs')
        if any(lease.get(a)!=record.get(b) for a,b in (('owner','owner_pid'),('start','owner_start'),('boot_id','boot_id'))):raise RuntimeError('Original V2 process differs')
    else:
        if link.get('protocol')!='policy-revalidation-v1' or link.get('policy_id')!=saved.get('id') or not link.get('previous_transaction_id'):
            raise RuntimeError('Fresh revalidation receipt required')
        origin=saved['origin']
        if origin.get('run_id')!=record['id'] or origin.get('activity_id')!=record.get('activity_session_id') or origin.get('transaction_id')!=record.get('transaction_id'):
            raise RuntimeError('Original measured policy receipt differs')
        original=activity(root,origin['activity_id'],api)
        if original.get('status')!='completed' or original.get('kind')!='automatic-v2' or original.get('parent_session_owner')!={'kind':'automatic','id':record['id']}:
            raise RuntimeError('Completed original measured activity required')
        if any(original.get(a)!=record.get(b) for a,b in (('owner','owner_pid'),('start','owner_start'),('boot_id','boot_id'))):raise RuntimeError('Original archived process identity differs')
    if lease.get('status')=='completed':return journal
    if allow_running_origin and lease.get('kind')=='automatic-v2' and lease.get('status')=='running' and lease.get('boot_id')==api.engine.boot_id() and api.engine.start_ticks(lease.get('owner'))==lease.get('start'):
        return journal
    raise RuntimeError('Independent policy acceptance has not completed')


def prepare(root,record,api=None):
    api=api or native_api();root=Path(root)
    from .workflow import commit_state
    from .native_target import resolve
    journal=commit_state(root,record,api,completed=False);h=api.scan();target=resolve(h,player=api.player)
    if proposal(record['search'])!=journal['selected_policy']:raise RuntimeError('Repeated proposal differs')
    raw=api.native_config();saved=dict(kind=KIND,id=uuid.uuid4().hex,enabled=True,run_id=record['id'],
        transaction_id=journal['id'],activity_id=record['activity_session_id'],selected=copy.deepcopy(journal['selected_policy']),
        origin=dict(run_id=record['id'],transaction_id=journal['id'],activity_id=record['activity_session_id'],boot_id=record['boot_id']),
        binding=binding(h,target),player_preferences_sha256=digest(Path(target['path']).read_bytes()),
        native_configuration_hex=raw.hex(),native_configuration_sha256=digest(raw),created=time.time())
    with api.engine.locked(root):
        latest=load(root,'automatic.json',api)
        if latest!=record:raise RuntimeError('Automatic result changed before policy save')
        api.engine.atomic(root/'v2-policy.json',saved)
        current_journal(root,record,api,allow_running_origin=True)
    return saved


def disable(root,run_id,api=None):
    api=api or native_api();root=Path(root)
    with api.engine.locked(root):
        saved=load(root,'v2-policy.json',api)
        if not saved:return
        if saved.get('run_id')!=run_id:raise RuntimeError('Newer persistent policy preserved')
        saved['enabled']=False;api.engine.atomic(root/'v2-policy.json',saved)


def status(root,api=None):
    api=api or native_api();r=load(root,'v2-policy-monitor.json',api)
    alive=bool(r.get('boot_id')==api.engine.boot_id() and r.get('start') and api.engine.start_ticks(r.get('pid'))==r['start'])
    latest=load(root,'v2-policy-status.json',api)
    return dict(running=alive,latest=latest or dict(status='inactive'),audio_started=False)


def start(root,api=None):
    api=api or native_api();root=Path(root)
    saved=load(root,'v2-policy.json',api)
    if saved.get('kind')!=KIND or saved.get('enabled') is not True:return dict(status='disabled',audio_started=False)
    if status(root,api)['running']:return dict(status='already-running',audio_started=False)
    with (root/'v2-policy-monitor.log').open('ab') as log:
        child=subprocess.Popen([sys.executable,'-B',str(Path(__file__).resolve().parents[2]/'bin/dat'),'--state',str(root),'policy-restore','serve'],
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
    deadline=time.monotonic()+10
    while child.poll() is None and time.monotonic()<deadline:
        r=load(root,'v2-policy-monitor.json',api)
        if r.get('pid')==child.pid and r.get('start')==api.engine.start_ticks(child.pid) and r.get('policy_id')==saved['id']:
            return dict(status='armed-independent-policy-monitor',pid=child.pid,audio_started=False)
        time.sleep(.05)
    raise RuntimeError('Independent V2 policy monitor did not arm')


def stop(root,api=None):
    api=api or native_api()
    if not status(root,api)['running']:return dict(status='inactive')
    record=load(root,'v2-policy-monitor.json',api)
    try:fd=os.pidfd_open(record['pid'])
    except ProcessLookupError:return dict(status='inactive')
    try:
        if api.engine.start_ticks(record['pid'])!=record['start']:raise RuntimeError('Policy monitor PID changed')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        if not select.select([fd],[],[],210)[0]:raise RuntimeError('Policy recovery still active')
    finally:os.close(fd)
    return dict(status='stopped')


def compatible(root,saved,h,api):
    target=api.player.target(h)
    if binding(h,target)!=saved['binding']:raise RuntimeError('Host, kernel, CPU topology or physical DAC route changed')
    if digest(Path(target['path']).read_bytes())!=saved['player_preferences_sha256']:raise RuntimeError('Native audio preferences changed')
    api.config_proof(saved)
    if h['dac'].get('owner_contract') and any(k in saved['selected'] for k in ('output','irq','policy','priority')):
        raise RuntimeError('External processor owns thread placement; complete policy deferred')
    return target


def pcm_observable(h,target,api):
    """Reapply only over actually playing, recognizable PCM, never DSD/DoP."""
    hw=h.get('dac',{}).get('pcm',{}).get('hw',{})
    if hw.get('format') not in ('S16_LE','S24_LE','S24_3LE','S32_LE') or str(hw.get('channels'))!='2':return False
    state=api.player.rpc(target['mac'],['status','-','1','tags:uoTI'])
    rows=state.get('playlist_loop',[])
    if state.get('mode')!='play' or not isinstance(rows,list) or len(rows)!=1:return False
    current=rows[0]
    if not isinstance(current,dict) or not isinstance(current.get('url'),str):return False
    extension=Path(urllib.parse.urlsplit(current['url']).path).suffix.lower().lstrip('.')
    formats={str(current.get(k,'')).strip().lower() for k in ('type','content_type','format','audioformat','filetype')}
    if extension in ('dsf','dff','dsd','dop') or any(any(term in v for term in ('dsd','dsf','dff','dop')) for v in formats):return False
    known={'flac','flc','wav','wave','aif','aiff','pcm','mp3','aac','m4a','alac','ogg','vorbis','opus','wma'}
    return extension in known or bool(formats&known)


def reapply(root,saved,record,h,api=None):
    api=api or native_api();root=Path(root);lease=None;claim=None
    with api.owner_lock(root):
        with api.engine.locked(root):
            old=current_journal(root,record,api);target=compatible(root,saved,h,api)
            api.engine.ensure_session_owner(root);api.player.assert_library_idle(h)
            for change in old['changes']:
                if change.get('tid') and (old['boot_id']!=api.engine.boot_id() or api.engine.start_ticks(change['tid'])!=change.get('start')):continue
                api.engine.guard(change,h,restoring=True)
                if api.engine.setting_value(change) not in (change['before'],change['after']):raise RuntimeError('Manual runtime control preserved')
            previous_activity=load(root,'activity-session.json',api)
            history=root/'v2-policy-activities';history.mkdir(mode=0o700,exist_ok=True)
            if history.is_symlink():raise RuntimeError('Indirect policy history refused')
            destination=history/(previous_activity['id']+'.json')
            if destination.exists() and api.engine.load(destination)!=previous_activity:raise RuntimeError('Policy activity archive changed')
            api.engine.atomic(destination,previous_activity)
        try:
            lease=api.Lease(root,api.player.playback_snapshot(target['mac']),180,'policy-revalidation-v2',restore_playback=False)
            claim=api.coordinate(h,target,'verified-policy');claim.renew(h)
            observations=[]
            def observe(seconds):
                claim.renew(api.scan());m=api.measure.observe(seconds);claim.renew(api.scan())
                if optimization_issues(m)+accounting_issues(m):raise RuntimeError('Incomplete V2 revalidation observation')
                observations.append(copy.deepcopy(m))
                if len(observations)>2:raise RuntimeError('Unexpected policy revalidation observation sequence')
                if len(observations)==2:
                    issues=confirmation_issues(*observations)
                    if issues:raise RuntimeError('Policy revalidation did not independently confirm improvement: '+'; '.join(issues))
                return m
            def changes(fresh):
                compatible(root,saved,fresh,api)
                return api.search.changes(fresh,copy.deepcopy(saved['selected']))
            result=api.engine.trial(changes,'automatic-policy-reapply',30,root,True,observer=observe,restore_position=False,replace_current=old)
            if result.get('status')=='no-change':
                if len(observations)!=1 or result.get('baseline')!=observations[0]:raise RuntimeError('Unchanged policy lacks its actual complete observation')
                if load(root,'current.json',api)!=old or load(root,'transaction.json',api)!=old:raise RuntimeError('Concurrent policy change during no-write verification')
                lease.finish();lease=None
                return dict(status='already-current-observed',audio_started=False,playlist_write_commands=0,settings_written=False,new_committed_policy_created=False)
            if result.get('status')!='committed' or len(observations)!=2:raise RuntimeError('Fresh policy did not commit with two actual observations; no empty replacement created')
            with api.engine.locked(root):
                journal=load(root,'current.json',api)
                if journal.get('session_owner')!={'kind':'activity','id':lease.record['id']} or journal.get('status')!='committed':raise RuntimeError('Fresh policy owner differs')
                if journal.get('benchmark')!={'before':observations[0],'after':observations[1]}:raise RuntimeError('Committed policy evidence differs from its fresh observations')
                def key(c):return tuple((k,c.get(k)) for k in ('parameter','path','policy','tid','service','irq'))
                fresh={key(c):c for c in journal['changes']}
                for change in old['changes']:
                    if change.get('tid') and (old['boot_id']!=api.engine.boot_id() or api.engine.start_ticks(change['tid'])!=change.get('start')):continue
                    k=key(change)
                    if k in fresh:fresh[k]['before']=change['before']
                    elif api.engine.setting_value(change)==change['after']:fresh[k]=change
                journal.update(profile='automatic',changes=list(fresh.values()),selected_policy=saved['selected'],
                    workflow_v2=dict(run_id=record['id'],activity_id=lease.record['id'],protocol='policy-revalidation-v1',policy_id=saved['id'],previous_transaction_id=old['id']))
                for name in ('transaction.json','current.json','last-known-good.json'):api.engine.atomic(root/name,journal)
                latest=load(root,'v2-policy.json',api)
                if latest!=saved:raise RuntimeError('Newer saved policy preserved')
                updated=dict(saved,transaction_id=journal['id'],activity_id=lease.record['id'])
                api.engine.atomic(root/'v2-policy.json',updated)
            lease.finish();lease=None
            current_journal(root,record,api)
            return dict(status='reapplied-verified',transaction_id=journal['id'],audio_started=False,playlist_write_commands=0)
        except BaseException:
            if lease:
                with api.engine.locked(root):
                    recovered=api.engine.recover_owned_transaction(root,{'kind':'activity','id':lease.record['id']})
                    if recovered.get('status') not in ('rolled-back','no-change'):raise RuntimeError('Independent policy recovery still required')
                lease.finish();lease=None
            raise
        finally:
            if claim:claim.close()


def serve(root,api=None):
    import fcntl
    api=api or native_api();root=Path(root);running=True;claim=None;attempted=set();pending=None;seen=0
    def end(*_):
        nonlocal running
        running=False
    signal.signal(signal.SIGTERM,end);signal.signal(signal.SIGINT,end)
    with (root/'v2-policy-monitor.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        saved=load(root,'v2-policy.json',api);record=load(root,'automatic.json',api)
        current_journal(root,record,api,allow_running_origin=True)
        api.engine.atomic(root/'v2-policy-monitor.json',dict(pid=os.getpid(),start=api.engine.start_ticks(os.getpid()),boot_id=api.engine.boot_id(),policy_id=saved['id']))
        try:
            while running:
                try:
                    saved=load(root,'v2-policy.json',api);record=load(root,'automatic.json',api)
                    journal=current_journal(root,record,api,allow_running_origin=True)
                    h=api.scan();report=dict(status='waiting-for-compatible-pcm',audio_started=False,playlist_write_commands=0)
                    if h['dac'].get('status')=='verified-active':
                        target=compatible(root,saved,h,api)
                        owner=(api.engine.boot_id(),h['dac']['owner_pid'],api.engine.start_ticks(h['dac']['owner_pid']))
                        seen=seen+1 if pending==owner else 1;pending=owner
                        departed=journal['boot_id']!=api.engine.boot_id() or any(c.get('tid') and api.engine.start_ticks(c['tid'])!=c.get('start') for c in journal['changes'])
                        if not departed:
                            for c in journal['changes']:
                                api.engine.guard(c,h)
                                if api.engine.setting_value(c)!=c['after']:raise RuntimeError('Manual runtime setting preserved')
                            if claim is None:claim=api.coordinate(h,target,'verified-policy')
                            claim.renew(h);report['status']='policy-current'
                        elif activity(root,saved['activity_id'],api).get('status')!='completed':
                            report['status']='awaiting-original-acceptance'
                        elif not pcm_observable(h,target,api):
                            if claim:claim.close();claim=None
                            report['status']='waiting-for-playing-pcm'
                        elif seen>=3 and owner not in attempted:
                            if claim:claim.close();claim=None
                            attempted.add(owner);report.update(reapply(root,saved,record,h,api))
                            if report['status']=='already-current-observed':running=False
                        else:report['status']='awaiting-fresh-identity-verification'
                    elif claim:claim.close();claim=None
                    api.engine.atomic(root/'v2-policy-status.json',dict(report,time=time.time(),boot_id=api.engine.boot_id()))
                except (OSError,RuntimeError,ValueError,KeyError,TypeError) as error:
                    api.engine.atomic(root/'v2-policy-status.json',dict(status='invalidated-no-retry',reason=type(error).__name__+': '+str(error),audio_started=False,playlist_write_commands=0,time=time.time()))
                    running=False
                for _ in range(5):
                    if not running:break
                    time.sleep(1)
        finally:
            if claim:claim.close()
            (root/'v2-policy-monitor.json').unlink(missing_ok=True)

__release_signature__ = 'Tino Zappala'


_policy_reapply_without_common = reapply
_policy_stop_without_common = stop

def reapply(root,saved,record,h,api=None):
    from . import common_activity,policy_custody
    return common_activity.run_owned(root,lambda:policy_custody.reapply_owned(root,saved,record,h,api or native_api()))

def stop(root,api=None):
    from . import common_activity
    return common_activity.run_owned(root,lambda:_policy_stop_without_common(root,api))


# B4: original algorithms/current-journal/reapply remain unchanged. Monitor
# writers and lifecycle use physical per-cycle custody; idle grants nothing.
_policy_prepare_before_b4 = prepare
_policy_disable_before_b4 = disable

def prepare(root,record,api=None):
    from . import common_activity
    common_activity.require_exclusive()
    return _policy_prepare_before_b4(root,record,api)

def disable(root,run_id,api=None):
    from . import common_activity
    common_activity.require_exclusive()
    return _policy_disable_before_b4(root,run_id,api)

def start(root,api=None):
    from . import common_activity,policy_custody
    api=api or native_api()
    return policy_custody.start_owned(root,api)

def drain(root,api=None):
    from . import policy_custody
    return policy_custody.drain(root,api or native_api())

def stop(root,api=None):
    # PIDFD drain is intentionally before a NEW EX. If the original caller
    # already has EX, drain independently proves the monitor has no common FD.
    return drain(root,api)

def serve(root,api=None):
    from . import policy_custody
    return policy_custody.serve(root,api or native_api())
