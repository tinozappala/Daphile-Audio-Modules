"""Bounded RME PEQ transactions. No volume, Dual EQ or preset SET commands."""
import contextlib,hashlib,json,math,os,re,signal,subprocess,sys,time,uuid
from decimal import Decimal
from pathlib import Path
import rme
from engine import STATE,atomic,locked,load,start_ticks

OUTPUTS={'line':(4,5,3),'phones':(7,8,6),'iem':(10,11,9)}
BANDS={1:{'type':3,'gain':4,'frequency':5,'q':6},2:{'gain':7,'frequency':8,'q':9},
       3:{'gain':10,'frequency':11,'q':12},4:{'gain':13,'frequency':14,'q':15},
       5:{'type':16,'gain':17,'frequency':18,'q':19}}
INDICES=tuple(range(3,20))
GLOBAL_LOCK=Path('/run/daphile-rme-peq.lock')

def indexes(reply):
    result={}
    for p in reply['parameters']:
        key=(p['address'],p['index'])
        if key in result and result[key]!=p['raw']:raise RuntimeError('RME state changed during read; fresh stable read required')
        result[key]=p['raw']
    return result

def output_name(value):
    if not isinstance(value,str) or value not in OUTPUTS:raise ValueError('Choose Line, Phones or IEM')
    return value

def stepped(value,lo,hi,step):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('Finite numeric PEQ value required')
    v=Decimal(str(value));unit=Decimal(str(step))
    if not Decimal(str(lo))<=v<=Decimal(str(hi)) or v%unit:raise ValueError('PEQ value outside range or step')
    return int(v/unit)

def encode_value(band,key,value):
    if isinstance(band,bool) or not isinstance(band,int) or band not in BANDS or key not in BANDS[band]:raise ValueError('Unknown PEQ band or parameter')
    if key=='gain':return stepped(value,-12,12,.5)&4095
    if key=='q':return stepped(value,.5,9.9 if band<=3 else 5,.1)
    if key=='type':return stepped(value,0,3 if band==1 else 2,1)
    hz=stepped(value,20 if band<=3 else 200,20000,1)
    if hz>2040:
        if hz%10:raise ValueError('Frequencies above 2040 Hz require 10 Hz steps')
        return 2048+hz//10
    return hz

def validate_patch(patch):
    if not isinstance(patch,dict) or set(patch)-{'output','bands','enabled','expected'}:raise ValueError('Invalid PEQ request keys')
    if not {'output','expected'}<=set(patch):raise ValueError('Output and fresh read revision required')
    output=output_name(patch['output']);expected=patch['expected']
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('Invalid PEQ read revision')
    bands=patch.get('bands',[])
    if not isinstance(bands,list) or len(bands)>5:raise ValueError('At most five PEQ bands')
    values={};seen=set()
    for row in bands:
        if not isinstance(row,dict) or 'band' not in row:raise ValueError('Invalid PEQ band')
        band=row['band']
        if isinstance(band,bool) or not isinstance(band,int) or band not in BANDS or band in seen:raise ValueError('Invalid or duplicate PEQ band')
        seen.add(band)
        if not set(row)-{'band'} or set(row)-({'band'}|set(BANDS[band])):raise ValueError('Invalid PEQ band fields')
        for key,value in row.items():
            if key!='band':values[BANDS[band][key]]=encode_value(band,key,value)
    if 'enabled' in patch:
        if not isinstance(patch['enabled'],bool):raise ValueError('EQ enabled must be boolean')
        values[2]=int(patch['enabled'])
    if not values:raise ValueError('Empty PEQ request')
    return output,expected,values

def revision(reply):
    values=indexes(reply)
    body=[reply['usb_serial'],[[a,i,v] for (a,i),v in sorted(values.items())]]
    return hashlib.sha256(json.dumps(body,separators=(',',':')).encode()).hexdigest()

def verified_read():
    reply=rme.query()
    if reply['status']!='read-response-received':raise RuntimeError('RME state read is incomplete; no SET allowed')
    values=indexes(reply)
    for left,right,channel in OUTPUTS.values():
        required=[(a,i) for a in (left,right) for i in INDICES]+[(left,2),(right,2),(left,28),(channel,11),(channel,12)]
        if any(k not in values for k in required):raise RuntimeError('RME full PEQ state is incomplete; no SET allowed')
    return reply

def eligibility(reply,output):
    left,right,channel=OUTPUTS[output];v=indexes(reply);reasons=[]
    if v.get((left,2)) not in [0,1]:reasons.append('Unknown EQ enable state; editing refused')
    if v.get((channel,11))!=0:reasons.append('Dual EQ must be OFF for coupled stereo editing')
    if v.get((left,28))!=0:reasons.append('Select EQ preset Manual on the DAC before editing')
    if any(v.get((left,i))!=v.get((right,i)) for i in (2,*INDICES)):reasons.append('Left/right PEQ values must match in coupled stereo mode')
    for row in reply['current_eq'].get(output,{}).get('left',[]):
        try:
            for key,value in row.items():
                if key!='band':encode_value(row['band'],key,value)
        except ValueError:reasons.append('Current PEQ values cannot be safely restored within supported ranges');break
    return {'eligible':not reasons,'reasons':reasons,'mode':'Manual, coupled stereo','bands':5,'filter_types':{'1':['Peak','Shelf','Hi Pass','Hi Cut'],'5':['Peak','Shelf','Hi Cut']}}

@contextlib.contextmanager
def operation(state):
    if os.geteuid()!=0:raise RuntimeError('Root required for RME PEQ transaction')
    fd=os.open(GLOBAL_LOCK,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as guard:
        rme.wait_lock(guard)
        with locked(state) as parent:
            with locked(parent/'rme-peq') as private:yield private

def packet(output,index,raw):
    output_name(output)
    if isinstance(index,bool) or not isinstance(index,int) or index not in (2,*INDICES) or isinstance(raw,bool) or not isinstance(raw,int) or not 0<=raw<4096:raise ValueError('Outside PEQ SET allowlist')
    if index==2 and raw not in [0,1]:raise ValueError('Invalid EQ enable')
    if index in [3,16] and raw> (3 if index==3 else 2):raise ValueError('Invalid filter type')
    if index in [4,7,10,13,17] and not (raw<=24 or raw>=4072):raise ValueError('Invalid gain')
    if index in [6,9,12,15,19] and not 5<=raw<=(99 if index in [6,9,12] else 50):raise ValueError('Invalid Q')
    if index in [5,8,11,14,18]:
        hz=(raw&2047)*(10 if raw&2048 else 1)
        if not (20 if index in [5,8,11] else 200)<=hz<=20000:raise ValueError('Invalid frequency')
        if 2040<raw<2048:raise ValueError('Frequency would be rounded by the DAC; use 10 Hz steps')
    address=OUTPUTS[output][0]
    return bytes.fromhex('F0 00 20 0D 71 02')+bytes([(address<<3)|(index>>2),((index&3)<<5)|(raw>>7),raw&127,247])

def send(output,index,raw,serial):
    payload=packet(output,index,raw)
    tool=rme.shutil.which('amidi')
    if not tool:raise RuntimeError('amidi unavailable')
    with rme.transport():
        card,usb=rme.select(rme.alsa_cards(),rme.usb_devices())
        if usb['serial']!=serial:raise RuntimeError('Original RME serial changed; SET refused')
        subprocess.run([tool,'--port=hw:'+str(card['card'])+',0,0','--send-hex='+payload.hex(' ')],stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=5,preexec_fn=rme.child_cleanup(os.getpid()))
        time.sleep(.04)

def target(reply,output):
    left,_,_=OUTPUTS[output];v=indexes(reply)
    return {i:v[(left,i)] for i in (2,*INDICES)}

def equivalent(index,a,b):
    if a==b:return True
    if index in [5,8,11,14,18] and isinstance(a,int) and isinstance(b,int):
        return (a&2047)*(10 if a&2048 else 1)==(b&2047)*(10 if b&2048 else 1)
    return False

def rollback_matches(index,actual,candidate,journal):
    if equivalent(index,actual,candidate):return True
    # Legacy journals may contain the native DAC's observed 2041..2047 -> 2040
    # normalization. Only an actually attempted frequency SET permits recovery;
    # normal verification still rejects rounding and new SETs reject this range.
    attempted=any(w.get('index')==index and w.get('raw')==candidate for w in journal.get('writes_attempted',[]))
    return index in [5,8,11,14,18] and isinstance(candidate,int) and 2040<candidate<2048 and attempted and equivalent(index,actual,2040)

def check_result(before,after,output,desired):
    if after['usb_serial']!=before['usb_serial']:raise RuntimeError('Readback came from another RME')
    a=indexes(before);b=indexes(after);left,right,_=OUTPUTS[output]
    expected=dict(a)
    for i,value in desired.items():
        expected[(left,i)]=value
        expected[(right,i)]=value
    differences=[{'address':k[0],'index':k[1],'expected':v,'actual':b.get(k)} for k,v in expected.items() if not (equivalent(k[1],b.get(k),v) if k[0] in [4,5,7,8] else b.get(k)==v)]
    if set(a)!=set(b):raise RuntimeError('Parameter inventory changed during PEQ transaction')
    if differences:raise RuntimeError('RME readback differs: '+json.dumps(differences))

def rollback(private,journal):
    before=journal['before'];output=output_name(journal['output']);left,right,channel=OUTPUTS[output]
    current=verified_read();v=indexes(current);original=indexes(before)
    if current['usb_serial']!=before['usb_serial']:raise RuntimeError('Original RME absent; restore deferred')
    if v[(channel,11)]!=original[(channel,11)] or v[(left,28)]!=original[(left,28)]:raise RuntimeError('Dual EQ or Manual selection changed externally; restore refused')
    for address in (left,right):
        for i in INDICES:
            allowed={original[(address,i)],journal['desired'].get(str(i),original[(address,i)])}
            if not any(rollback_matches(i,v[(address,i)],candidate,journal) for candidate in allowed):raise RuntimeError('Concurrent PEQ edit detected; restore refused')
    for address in (left,right):
        if v[(address,2)] not in {0,original[(address,2)],journal['desired'].get('2',original[(address,2)])}:raise RuntimeError('Concurrent EQ enable change detected')
    send(output,2,0,before['usb_serial'])
    for i in INDICES:send(output,i,original[(left,i)],before['usb_serial'])
    send(output,2,original[(left,2)],before['usb_serial'])
    after=verified_read()
    # Preserve changes outside this PEQ made during a cancellation/recovery.
    check_result(current,after,output,target(before,output))
    atomic(private/('after-rollback-'+journal['id']+'.json'),after)
    journal['status']='rolled-back';journal['restored_verified']=True
    atomic(private/'transaction.json',journal)
    return after

def current_boot():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

def spawn_watchdog(state,ident):
    cli=Path(__file__).resolve().parent.parent/'bin/dat'
    proc=subprocess.Popen([sys.executable,str(cli),'--state',str(state),'rme-peq','watchdog',ident],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
    ready=Path(state)/'rme-peq'/('watchdog-ready-'+ident+'.json');end=time.monotonic()+5
    while time.monotonic()<end and proc.poll() is None:
        if ready.exists():
            record=load(ready)
            if record.get('id')==ident and record.get('pid')==proc.pid and record.get('start')==start_ticks(proc.pid):return proc
        time.sleep(.05)
    if proc.poll() is None:
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill();proc.wait()
    raise RuntimeError('PEQ watchdog did not arm; no SET sent')

def parent_alive(journal):
    if journal.get('boot_id') and journal['boot_id']!=current_boot():return False
    if start_ticks(journal['parent_pid'])!=journal['parent_start']:return False
    try:
        stat=Path('/proc',str(journal['parent_pid']),'stat').read_text()
        return stat[stat.rfind(')')+2:].split()[0] not in ['Z','X']
    except OSError:return False

def watchdog(state,ident):
    if not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Invalid PEQ transaction id')
    path=Path(state)/'rme-peq/transaction.json'
    initial=load(path)
    if initial.get('id')!=ident:return
    atomic(path.parent/('watchdog-ready-'+ident+'.json'),dict(id=ident,pid=os.getpid(),start=start_ticks(os.getpid())))
    while True:
        try:j=load(path)
        except (OSError,ValueError):return
        if j.get('id')!=ident or j.get('status') in ['committed','restored','rolled-back','no-change']:return
        alive=parent_alive(j)
        expired=time.time()>j['deadline']
        if expired and alive:
            __import__('rme_guard').terminate_expired(path,ident);time.sleep(.2)
        if not alive or expired:
            try:
                with operation(state) as private:
                    fresh=load(private/'transaction.json')
                    if fresh.get('id')!=ident or fresh.get('status') in ['committed','restored','rolled-back','no-change']:return
                    try:rollback(private,fresh)
                    except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as error:
                        pending=load(path)
                        if pending.get('id')==ident and pending.get('status') not in ['committed','restored','rolled-back','no-change']:
                            pending.update(status='rollback-incomplete',rollback_error=str(error));atomic(path,pending)
                return
            except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError):return
        else:time.sleep(.5)

def read(state=STATE):
    reply=verified_read();private=Path(state)/'rme-peq'
    pending=None
    if (private/'transaction.json').is_file():
        j=load(private/'transaction.json')
        if j['status'] not in ['committed','restored','rolled-back','no-change']:pending={'id':j['id'],'output':j['output'],'status':j['status']}
    return dict(status='peq-read',current_eq=reply['current_eq'],revision=revision(reply),capabilities={o:eligibility(reply,o) for o in OUTPUTS},pending=pending,
                last_snapshot={o:(private/('last-'+o+'.json')).is_file() for o in OUTPUTS},settings_write_commands_sent=0)

def execute(private,state,before,output,values,restore=False):
    desired=target(before,output);desired.update(values)
    changes={i:v for i,v in desired.items() if v!=target(before,output)[i]}
    if not changes:return dict(status='no-change',current_eq=before['current_eq'],revision=revision(before),output=output)
    ident=uuid.uuid4().hex
    journal=dict(id=ident,status='armed',output=output,before=before,desired={str(k):v for k,v in desired.items()},parent_pid=os.getpid(),parent_start=start_ticks(os.getpid()),deadline=time.time()+120,writes_attempted=[],boot_id=current_boot())
    atomic(private/('before-'+ident+'.json'),before);atomic(private/'transaction.json',journal)
    try:worker=spawn_watchdog(state,ident)
    except Exception:
        journal['status']='rolled-back';journal['restored_verified']=True;journal['no_set_sent']=True;atomic(private/'transaction.json',journal);raise
    journal['watchdog_armed']=True;atomic(private/'transaction.json',journal)
    try:
        journal['status']='writing';atomic(private/'transaction.json',journal)
        # Separate SETs. Never select Manual or load/save a preset during restore.
        def attempt(index,value):
            journal['writes_attempted'].append({'index':index,'raw':value});atomic(private/'transaction.json',journal)
            send(output,index,value,before['usb_serial'])
        if any(i!=2 for i in changes):attempt(2,0)
        for i,value in changes.items():
            if i!=2:attempt(i,value)
        if any(i!=2 for i in changes) or 2 in changes:attempt(2,desired[2])
        journal['status']='verifying';atomic(private/'transaction.json',journal)
        from rme_verify import verify
        expected=indexes(before);left,right,_=OUTPUTS[output];owned=set()
        for index,value in desired.items():
            for address in [left,right]:expected[(address,index)]=value;owned.add((address,index))
        intermediate={(address,2):[0] for address in [left,right]} if any(i!=2 for i in changes) else None
        after,verification=verify(before,expected,owned,verified_read,indexes,lambda loc,a,b:equivalent(loc[1],a,b),lambda reply:check_result(before,reply,output,desired),lambda loc,value:attempt(loc[1],value),accepted_intermediate=intermediate)
        journal['verification']=verification
        atomic(private/('after-'+ident+'.json'),after)
        if not restore:atomic(private/('last-'+output+'.json'),dict(id=ident,output=output,before=before,after=after))
        journal['status']='restored' if restore else 'committed';atomic(private/'transaction.json',journal)
        
        with contextlib.suppress(subprocess.TimeoutExpired):worker.wait(timeout=3)
        return dict(status='restored-verified' if restore else 'applied-verified',output=output,snapshot=ident,current_eq=after['current_eq'],revision=revision(after),readback_verified=True,watchdog_armed=True,non_peq_parameters_unchanged=True,volume_write_commands_sent=0,preset_write_commands_sent=0,verification=verification)
    except Exception as e:
        journal['failure']=str(e)
        try:
            after=rollback(private,journal)
            with contextlib.suppress(subprocess.TimeoutExpired):worker.wait(timeout=3)
            return dict(status='failed-restored',reason=str(e),original_peq_restored=True,current_eq=after['current_eq'],revision=revision(after))
        except Exception as recovery:
            journal['status']='rollback-incomplete';journal['rollback_error']=str(recovery);atomic(private/'transaction.json',journal)
            return dict(status='rollback-incomplete',output=output,snapshot=ident,reason=str(e),recovery_error=str(recovery),original_peq_restored=False)

def audio_pending_guard(state):
    path=Path(state)/'rme-audio/transaction.json'
    if path.exists() and load(path).get('status') not in ['committed','restored','rolled-back','no-change']:raise RuntimeError('Recover pending RME audio transaction first')
    extended=Path(state)/'rme-control/transaction.json'
    if extended.exists() and load(extended).get('status') not in ['committed','restored','rolled-back','no-change']:raise RuntimeError('Recover pending extended RME transaction first')

def apply(patch,state=STATE):
    output,expected,values=validate_patch(patch)
    with operation(state) as private:
        audio_pending_guard(state)
        pending=load(private/'transaction.json') if (private/'transaction.json').exists() else None
        if pending and pending['status'] not in ['committed','restored','rolled-back','no-change']:raise RuntimeError('Recover the pending PEQ transaction first')
        before=verified_read()
        if revision(before)!=expected:raise RuntimeError('RME settings changed since read; refresh before applying')
        guard=eligibility(before,output)
        if not guard['eligible']:raise RuntimeError('; '.join(guard['reasons']))
        return execute(private,state,before,output,values)

def restore(output,expected,state=STATE):
    output_name(output)
    with operation(state) as private:
        audio_pending_guard(state)
        pending=load(private/'transaction.json') if (private/'transaction.json').exists() else None
        if pending and pending['status'] not in ['committed','restored','rolled-back','no-change']:raise RuntimeError('Recover pending PEQ transaction first')
        saved=load(private/('last-'+output+'.json'));before=verified_read()
        if before['usb_serial']!=saved['before']['usb_serial']:raise RuntimeError('Snapshot belongs to another RME')
        if revision(before)!=expected:raise RuntimeError('RME settings changed since read; refresh before restoring')
        guard=eligibility(before,output)
        if not guard['eligible']:raise RuntimeError('; '.join(guard['reasons']))
        return execute(private,state,before,output,target(saved['before'],output),restore=True)

def recover(state=STATE):
    with operation(state) as private:
        j=load(private/'transaction.json')
        if j['status'] in ['committed','restored','rolled-back','no-change']:return dict(status='no-pending-transaction')
        if parent_alive(j):raise RuntimeError('PEQ transaction process is still active')
        after=rollback(private,j)
        return dict(status='recovered-verified',output=j['output'],current_eq=after['current_eq'],revision=revision(after))
