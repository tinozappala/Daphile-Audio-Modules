"""Typed extended RME transactions; shares the qualified legacy global lock.

No player control. No arbitrary MIDI endpoint. Recovery never overwrites a
third-party value and keeps incomplete transactions available for inspection.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import rme
import rme_peq as peq
import rme_registry as registry
from engine import atomic, load, locked, start_ticks

FINAL={'committed','restored','rolled-back','no-change'}

class RevisionConflict(RuntimeError):
    def __init__(self,current):
        super().__init__('I parametri sono cambiati sul DAC o da un altro controller. Verifica i valori aggiornati prima di applicare nuovamente.')
        self.current=current

def key(location):return f'{location[0]}:{location[1]}'
def location(value):return tuple(map(int,value.split(':')))
def values(reply):return {key(k):v for k,v in peq.indexes(reply).items()}

@contextlib.contextmanager
def operation(state, recovery=False):
    with peq.operation(state):
        for name in ('rme-peq','rme-audio'):
            path=Path(state)/name/'transaction.json'
            if path.exists() and load(path).get('status') not in FINAL:
                raise RuntimeError('Recupera prima la precedente operazione RME')
        with locked(Path(state)/'rme-control') as private:
            path=private/'transaction.json'
            if not recovery and path.exists() and load(path).get('status') not in FINAL:
                raise RuntimeError('Recupera prima l’operazione RME estesa interrotta')
            yield private

def context(state,serial=None):
    path=Path(state)/'rme-context.json'
    if not path.exists():return {'chip':'unknown'}
    item=load(path)
    if set(item)!={'chip','identity'} or item['chip'] not in registry.CHIPS:raise RuntimeError('Contesto DAC non valido')
    identity=hashlib.sha256(serial.encode()).hexdigest() if serial else None
    return {'chip':item['chip']} if identity==item['identity'] else {'chip':'unknown'}

def set_context(state,item):
    if not isinstance(item,dict) or set(item)!={'chip','expected_identity'} or item['chip'] not in registry.CHIPS:
        raise ValueError('Seleziona una revisione DAC valida')
    with operation(state):
        reply=peq.verified_read();identity=hashlib.sha256(reply['usb_serial'].encode()).hexdigest()
        if item['expected_identity']!=identity:raise RuntimeError('Identità DAC cambiata; dichiarazione non salvata')
        atomic(Path(state)/'rme-context.json',{'chip':item['chip'],'identity':identity})
    return {'status':'context-saved','context':{'chip':item['chip']},'settings_write_commands_sent':0}

def from_reply(reply,state):
    view=registry.view(reply,context(state,reply['usb_serial']))
    view.update(revision=peq.revision(reply),current_eq=reply['current_eq'],settings_write_commands_sent=0,
                status_messages=reply.get('status_messages',[])[-1:],hardware_names=reply.get('hardware_names',[]),
                read_time=time.time(),identity=hashlib.sha256(reply['usb_serial'].encode()).hexdigest())
    view['pending']=None
    path=Path(state)/'rme-control/transaction.json'
    if path.exists():
        j=load(path)
        if j.get('status') not in FINAL:view['pending']={'id':j['id'],'status':j['status']}
    return view

def snapshot(state):return from_reply(peq.verified_read(),state)

def send(payload,serial):
    # Only internally produced, bounded single-parameter SET frames.
    if len(payload)!=10 or payload[:6]!=bytes.fromhex('F0 00 20 0D 71 02') or payload[-1]!=247 or any(v>127 for v in payload[6:-1]):
        raise ValueError('Comando RME fuori dal formato consentito')
    tool=rme.shutil.which('amidi')
    if not tool:raise RuntimeError('Trasporto MIDI non disponibile')
    with rme.transport():
        card,usb=rme.select(rme.alsa_cards(),rme.usb_devices())
        if usb['serial']!=serial:raise RuntimeError('Identità del DAC cambiata; scrittura rifiutata')
        subprocess.run([tool,'--port=hw:'+str(card['card'])+',0,0','--send-hex='+payload.hex(' ')],
                       stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=5,preexec_fn=rme.child_cleanup(os.getpid()))
        time.sleep(.04)

def raw_frame(loc,raw,ctx=None):
    address,index=location(loc)
    allowed_device={f['index'] for f in registry.FIELDS.values() if f['scope']=='device' and not f.get('read_only') and not f.get('blocked')}
    allowed_channel={f['index'] for f in registry.FIELDS.values() if f['scope']=='channel'}
    if not ((address==12 and index in allowed_device)
            or (address in (3,6,9) and index in allowed_channel)
            or (address in (4,7,10) and index in range(2,29))
            or (address in (5,8,11) and index in range(3,20))):
        raise ValueError('Parametro fuori dall’allowlist RME')
    if type(raw)!=int or not 0<=raw<=(2047 if address==12 else 4095):raise ValueError('Codifica RME fuori intervallo')
    output=next((o for o,(left,right,ch) in registry.OUTPUTS.items() if address in (left,right,ch)),'line')
    if address in (4,5,7,8,10,11) and index<=19:
        peq.packet(output,index,raw)  # Reuse the independent wire-boundary checks.
    elif address in (4,7,10) and index==28:
        if not 0<=raw<=21:raise ValueError('Preset speciale non qualificato')
    else:
        parameter=next((name for name,item in registry.FIELDS.items() if registry.location(name,output)==(address,index)),None)
        if parameter is None:raise ValueError('Parametro RME non tipizzato')
        registry.decode(parameter,raw,output,ctx)
    triple=[(address<<3)|(index>>(3 if address==12 else 2)),
            ((index&(7 if address==12 else 3))<<(4 if address==12 else 5))|(raw>>7),raw&127]
    return bytes.fromhex('F0 00 20 0D 71 02')+bytes(triple)+b'\xf7'

def validate_expected(expected):
    if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('È richiesta una lettura aggiornata del DAC')

def restorable_eq(before,output):
    left,right,channel=registry.OUTPUTS[output];indexed=peq.indexes(before)
    if indexed.get((channel,11)) not in (0,1) or any(indexed.get((a,2)) not in (0,1) for a in (left,right)):
        raise ValueError('Stato EQ non supportato; nessuna scrittura consentita')
    for side in ('left','right'):
        rows=before['current_eq'].get(output,{}).get(side,[])
        if len(rows)!=5:raise ValueError('Lettura delle bande incompleta')
        for number,row in enumerate(rows,1):
            if not isinstance(row,dict) or row.get('band')!=number or set(row)!={'band',*peq.BANDS[number]}:
                raise ValueError('Lettura delle bande incompleta o non ordinata')
            for name,value in row.items():
                if name!='band':peq.encode_value(row['band'],name,value)

def settings_plan(before,patch,ctx):
    if not isinstance(patch,dict) or set(patch)!={'output','expected','values'}:raise ValueError('Richiesta RME non valida')
    output=peq.output_name(patch['output']);validate_expected(patch['expected']);v=peq.indexes(before)
    requested=patch['values']
    if not isinstance(requested,dict) or not requested or len(requested)>len(registry.FIELDS):raise ValueError('Seleziona almeno un parametro')
    if 'volume' in requested and ('reference_level' in requested or 'auto_reference' in requested):
        raise ValueError('Applica separatamente volume e riferimento per mantenere una variazione di livello controllabile.')
    if 'dim' in requested and any(k in requested for k in ('volume','reference_level','auto_reference')):
        raise ValueError('Applica Dim separatamente dal volume e dal riferimento: il DAC modifica automaticamente il livello di 20 dB.')
    if requested.get('load_tones_with_preset') is True and requested.get('tone_enabled') is False:
        raise ValueError('Carica i toni con il preset attiva i toni sul DAC. Applica separatamente una loro successiva disattivazione.')
    current=registry.view(before,ctx);desired={};commands={}
    for name,value in requested.items():
        item=registry.spec(name,output,ctx)
        cap=current['capabilities']['device' if item['scope']=='device' else 'outputs']
        if item['scope']!='device':cap=cap[output]
        if not cap.get(name,{}).get('writable'):raise ValueError(cap.get(name,{}).get('reason','Parametro non disponibile'))
        if item.get('memory_action'):raise ValueError('Usa l’azione dedicata per selezionare un preset hardware')
        if name=='volume' and current['outputs'][output].get('volume_lock') and requested.get('volume_lock') is not False:
            raise ValueError('Il volume è bloccato. Disattiva esplicitamente Blocco volume prima di regolarlo.')
        if name=='volume' and (current['outputs'][output].get('mute') or current['outputs'][output].get('dim')):
            raise ValueError('Disattiva esplicitamente Mute o Dim prima di regolare il volume.')
        if name=='reference_level' and current['outputs'][output].get('auto_reference'):
            raise ValueError('Disattiva Riferimento automatico prima di scegliere il livello manuale.')
        loc=registry.location(name,output);raw=registry.encode(name,value,output,ctx)
        desired[key(loc)]=raw;commands[key(loc)]=registry.packet(name,value,output,ctx)
        if name=='load_tones_with_preset':
            tone=key(registry.location('tone_enabled',output))
            # Turning the load policy on also activates B/T. Turning it off
            # must preserve the user's current B/T state explicitly.
            desired[tone]=1 if value else int(requested.get('tone_enabled',bool(v[registry.location('tone_enabled',output)])))
            commands[tone]=raw_frame(tone,desired[tone],ctx)
        if name=='auto_reference' and value is False and current['outputs'][output].get('auto_reference'):
            for extra in ('reference_level','volume'):
                identifier=key(registry.location(extra,output));desired[identifier]=v[registry.location(extra,output)]
                commands[identifier]=raw_frame(identifier,desired[identifier],ctx)
        if item.get('synchronized'):
            for o in registry.OUTPUTS:desired[key(registry.location(name,o))]=raw
        if name=='dual_eq' and value is False:
            left,right,_=registry.OUTPUTS[output]
            if any(v[(left,i)]!=v[(right,i)] for i in (2,*peq.INDICES)):
                raise ValueError('I canali hanno filtri diversi: usa esplicitamente Copia L→R o Copia R→L prima di disattivare EQ separato.')
            for i in peq.INDICES:desired[key((right,i))]=v[(left,i)]
            desired[key((right,2))]=v[(left,2)]
    return output,desired,commands

def eq_plan(before,patch):
    if not isinstance(patch,dict) or set(patch)-{'output','expected','side','bands','enabled'} or not {'output','expected','side'}<=set(patch):
        raise ValueError('Richiesta dei filtri non valida')
    output=peq.output_name(patch['output']);validate_expected(patch['expected'])
    restorable_eq(before,output)
    side=patch['side'];left,right,channel=registry.OUTPUTS[output];v=peq.indexes(before)
    if side not in ('stereo','left','right'):raise ValueError('Seleziona Stereo, Sinistro o Destro')
    if patch.get('bands') and v.get((left,28))!=0:raise ValueError('Seleziona Manual per modificare i filtri; i preset hardware vengono gestiti separatamente.')
    if side!='stereo' and v[(channel,11)]!=1:raise ValueError('Attiva EQ separato sinistro / destro prima di modificare un solo canale')
    if 'enabled' in patch and (side!='stereo' or v[(left,2)]!=v[(right,2)]):
        raise ValueError('Abilitazione separata L/R non qualificata; usa lo stato globale soltanto con interruttori coincidenti')
    # Reuse all qualified band range/step checks, independently of output target.
    _,_,encoded=peq.validate_patch({'output':output,'expected':patch['expected'],
        **({'enabled':patch['enabled']} if 'enabled' in patch else {}),'bands':patch.get('bands',[])})
    desired={};commands={}
    targets=[left,right] if side=='stereo' else [left if side=='left' else right]
    for index,raw in encoded.items():
        for address in targets:
            loc=key((address,index));desired[loc]=raw
            # Coupled mode writes only left; hardware mirrors the second side.
            if address==right and (v[(channel,11)]==0 or index==2):continue
            commands[loc]=raw_frame(loc,raw)
    return output,desired,commands

def preset_plan(before,patch):
    if not isinstance(patch,dict) or set(patch)!={'output','expected','slot'}:raise ValueError('Richiesta preset hardware non valida')
    output=peq.output_name(patch['output']);validate_expected(patch['expected']);slot=patch['slot']
    restorable_eq(before,output)
    left,right,channel=registry.OUTPUTS[output];v=peq.indexes(before)
    if not 0<=v[(left,28)]<=21:raise ValueError('La selezione corrente usa un preset speciale non qualificato')
    if slot=='manual':
        if v[(left,28)]==0:return output,{},{}
        loc=key((left,28));return output,{loc:0},{loc:raw_frame(loc,0)}
    import rme_memory
    bank=rme_memory.read(slot)
    if bank['empty']:raise ValueError('Il preset hardware selezionato è vuoto')
    desired={key((left,28)):slot+1,key((channel,11)):int(bank['dual_eq'])}
    for side,address in [('left',left),('right',right)]:
        for row in bank['channels'][side]:
            for label,value in row.items():
                if label!='band':desired[key((address,peq.BANDS[row['band']][label]))]=peq.encode_value(row['band'],label,value)
    if v[(left,27)]:
        for name,value in bank['tones'].items():desired[key(registry.location(name,output))]=registry.encode(name,value,output)
        desired[key((left,20))]=1  # RME manual: loading B/T also activates it.
    loc=key((left,28));return output,desired,{loc:raw_frame(loc,slot+1)}

def spawn(state,ident):
    executable=Path(__file__).resolve().parents[1]/'bin/dat'
    worker=subprocess.Popen([rme.shutil.which('python3') or '/usr/bin/python3',str(executable),
        '--state',str(state),'rme-control','watchdog',ident],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,start_new_session=True)
    ready=Path(state)/'rme-control'/('watchdog-ready-'+ident+'.json');end=time.monotonic()+5
    while worker.poll() is None and time.monotonic()<end:
        if ready.exists():
            value=load(ready)
            if value.get('id')==ident and value.get('pid')==worker.pid and value.get('start')==start_ticks(worker.pid):return worker
        time.sleep(.02)
    if worker.poll() is None:
        worker.terminate()
        try:worker.wait(timeout=2)
        except subprocess.TimeoutExpired:worker.kill();worker.wait()
    raise RuntimeError('Il recupero automatico non si è avviato: nessun comando SET è stato inviato.')

def analogue_level(indexed,channel):
    refs={3:[-5,1,7,13],6:[7,22]}
    ref=indexed.get(f'{channel}:2');volume=indexed.get(f'{channel}:12')
    if channel not in refs or type(ref)!=int or not 0<=ref<len(refs[channel]) or type(volume)!=int:
        raise RuntimeError('Livello analogico non verificabile per questa uscita')
    signed=volume-4096 if volume&2048 else volume
    return refs[channel][ref]*10+signed

def dependencies(before,after,desired):
    """Only explicit, validated automatic transitions; never all differences."""
    original=values(before);actual=values(after);accepted={}
    for left in (4,7,10):
        policy=f'{left}:27';tone=f'{left}:20'
        if policy in desired and tone not in desired and desired[policy]!=original[policy] and actual[policy]==desired[policy]:
            if actual[tone] not in (0,1):raise RuntimeError('Stato dei toni non valido dopo la modifica del caricamento preset')
            # Used for recovery between the policy SET and the explicit B/T
            # SET, as well as verification. No other tone parameter is owned.
            if actual[tone]!=original[tone]:accepted[tone]=actual[tone]
    for channel in (3,6,9):
        dim=f'{channel}:16';vol=f'{channel}:12';ref=f'{channel}:2'
        if dim in desired and desired[dim]!=original[dim] and actual[dim]==desired[dim]:
            delta=-200 if desired[dim] else 200
            output={3:'line',6:'phones',9:'iem'}[channel]
            registry.decode('volume',actual[vol],output)
            if channel in (3,6) and actual[f'{channel}:3']==1:
                registry.decode('reference_level',actual[ref],output)
                if analogue_level(actual,channel) not in (analogue_level(original,channel),analogue_level(original,channel)+delta):
                    raise RuntimeError('Dim ha prodotto una variazione analogica diversa dai due stati qualificati: uscita inattiva o variazione di 20 dB')
                if actual[ref]!=original[ref]:accepted[ref]=actual[ref]
            else:
                signed=lambda raw:raw-4096 if raw&2048 else raw
                if signed(actual[vol]) not in (signed(original[vol]),signed(original[vol])+delta):
                    raise RuntimeError('Dim ha prodotto una variazione diversa dai due stati qualificati: uscita inattiva o variazione di 20 dB')
            if actual[vol]!=original[vol]:accepted[vol]=actual[vol]
    for output,(left,right,channel) in registry.OUTPUTS.items():
        select=key((left,28))
        if desired.get(select)==0 and original.get(select)!=0 and actual.get(select)==0:
            # Manual is a dynamic hardware bank with no independent GET.
            # Verify selection plus the documented, bounded EQ-only effects;
            # undo uses the saved full snapshot and stricter equality.
            restorable_eq(after,output)
            related=[(a,i) for a in (left,right) for i in peq.INDICES]+[(channel,11)]
            if original.get(key((left,27)))==1:related.extend((left,i) for i in range(20,27))
            for loc in related:
                identifier=key(loc)
                if actual[identifier]!=original[identifier]:accepted[identifier]=actual[identifier]
    if any(k in desired and desired[k]!=original[k] for k in ('3:1','6:1','9:1')):
        # Clock follows the selected source on the DAC and is never written.
        for name in ('clock_source','sample_rate'):
            loc=key(registry.location(name));registry.decode(name,actual[loc]);accepted[loc]=actual[loc]
    for channel in (3,6):
        auto=f'{channel}:3';vol=f'{channel}:12';ref=f'{channel}:2'
        if ((auto in desired and desired[auto]==1) or (vol in desired and actual.get(auto)==1)) and actual.get(auto)==1:
            expected=dict(original)
            if vol in desired:expected[vol]=desired[vol]
            conserved=analogue_level(actual,channel)==analogue_level(expected,channel)
            # Physically qualified on this DAC: inactive Phones pre-applies
            # the exact Lo->Hi 15 dB compensation while reporting Lo until
            # the output becomes active. Never permit a level increase here.
            signed=lambda raw:raw-4096 if raw&2048 else raw
            pending_phones=(channel==6 and desired.get(auto)==1 and original[auto]==0 and vol not in desired
                and original[ref]==actual[ref]==0 and signed(actual[vol])==signed(original[vol])-150)
            if not conserved and not pending_phones:
                raise RuntimeError('Auto Ref ha prodotto una variazione di livello non richiesta')
            output='line' if channel==3 else 'phones'
            registry.decode('volume',actual[vol],output);registry.decode('reference_level',actual[ref],output)
            for loc in (vol,ref):
                if actual[loc]!=original[loc]:accepted[loc]=actual[loc]
            if pending_phones:accepted[ref]=original[ref]  # Explicit Lo reassertion on undo.
    return accepted

def ordered_commands(before,desired,commands):
    original=values(before)
    def priority(item):
        loc,_=item;address,index=location(loc)
        if address in (3,6,9):
            if index==13:return -2 if desired[loc]==0 else 20
            if index==3:return -1 if desired[loc]==0 else 15
            if index==16:return 0  # Restore Dim before an explicit volume/ref undo.
            if index in (2,12):
                target_ref=desired.get(f'{address}:2',original.get(f'{address}:2'))
                rises=target_ref>original.get(f'{address}:2',target_ref)
                return 1 if (index==12 and rises) or (index==2 and not rises) else 2
        if address in (4,7,10) and index==27:return 4
        if address in (4,7,10) and index==28:return 3
        return 5
    return sorted(commands.items(),key=priority)

def check(before,after,desired,allow_dependencies=True):
    if before['usb_serial']!=after['usb_serial']:raise RuntimeError('Il DAC è cambiato durante l’operazione')
    original=values(before);actual=values(after)
    if set(original)!=set(actual):raise RuntimeError('Inventario del DAC cambiato durante l’operazione')
    expected=dict(original);expected.update(desired)
    if allow_dependencies:expected.update(dependencies(before,after,desired))
    differences={k:{'expected':value,'actual':actual[k]} for k,value in expected.items() if actual[k]!=value}
    if differences:raise RuntimeError('Rilettura RME non coincidente: '+json.dumps(differences,sort_keys=True))

def rollback(private,j):
    current=peq.verified_read();actual=values(current);original=values(j['before'])
    if current['usb_serial']!=j['before']['usb_serial'] or set(actual)!=set(original):
        raise RuntimeError('Identità o inventario cambiati; ripristino rifiutato')
    desired=j['desired'];owned=set(desired)
    attempted={loc:desired[loc] for loc in j['writes_attempted']}
    # A SET may have reached the DAC even if its transport acknowledgement was
    # lost. Accept only independently checked documented automatic effects.
    derived=dependencies(j['before'],current,attempted)
    owned_expected=dict(desired);owned_expected.update(derived)
    for loc,value in actual.items():
        if value not in {original[loc],owned_expected.get(loc,original[loc])}:
            raise RuntimeError('Modifica esterna rilevata; ripristino conservato senza sovrascriverla')
    j['desired'].update(derived);j.setdefault('derived',{}).update(derived)
    j['status']='writing';atomic(private/'transaction.json',j)
    restorable=set(j['writes_attempted'])|(set() if j.get('selection') else set(j.get('derived',{})))
    restorable.update(f'{a}:20' for a in (4,7,10) if f'{a}:27' in j['writes_attempted'])
    if j.get('selection'):
        for a in (4,7,10):
            if f'{a}:28' in j['writes_attempted'] and original[f'{a}:28'] in (0,1):
                restorable.update(loc for loc in j['desired'] if location(loc)[0]==a and 20<=location(loc)[1]<=26)
    # Read-only clock dependencies restore themselves when Source is restored.
    restorable-={'12:15','12:16'}
    desired_restore={loc:original[loc] for loc in restorable}
    restore_commands={loc:raw_frame(loc,original[loc],j.get('context')) for loc in restorable}
    for loc,packet in ordered_commands(current,desired_restore,restore_commands):
        send(packet,current['usb_serial'])
    final=peq.verified_read();check(j['before'],final,{})
    j.update(status='rolled-back',restored_verified=True);atomic(private/'transaction.json',j)
    return final

def execute(state,private,before,output,desired,commands,restoring=False):
    original=values(before)
    policy_tones={f'{a}:20' for a in (4,7,10) if f'{a}:27' in commands and desired[f'{a}:27']!=original[f'{a}:27']}
    auto_levels={f'{a}:{i}' for a in (3,6) for i in (2,12) if f'{a}:3' in commands and desired[f'{a}:3']==0 and original[f'{a}:3']==1}
    selection_tones={loc for loc in commands if any(f'{a}:28' in commands and desired[f'{a}:28'] in (0,1) and location(loc)[0]==a and 20<=location(loc)[1]<=26 for a in (4,7,10))}
    commands={loc:packet for loc,packet in commands.items() if desired[loc]!=original[loc] or loc in policy_tones or loc in auto_levels or loc in selection_tones}
    if not commands:return {'status':'no-change','revision':peq.revision(before),'readback_verified':True,'state':from_reply(before,state)}
    ident=uuid.uuid4().hex
    j=dict(id=ident,status='armed',output=output,before=before,desired=dict(desired),
        commands={k:v.hex() for k,v in commands.items()},writes_attempted=[],parent_pid=os.getpid(),
        parent_start=start_ticks(os.getpid()),boot_id=peq.current_boot(),deadline=time.time()+120,
        selection=any(location(loc)[0] in (4,7,10) and location(loc)[1]==28 for loc in commands))
    j['context']=context(state,before['usb_serial'])
    atomic(private/('before-'+ident+'.json'),before);atomic(private/'transaction.json',j)
    worker=None
    try:
        worker=spawn(state,ident);j['watchdog_armed']=True;j['status']='writing';atomic(private/'transaction.json',j)
        for loc,packet in ordered_commands(before,desired,commands):
            j['writes_attempted'].append(loc);atomic(private/'transaction.json',j);send(packet,before['usb_serial'])
        j['status']='verifying';atomic(private/'transaction.json',j)
        after=peq.verified_read()
        derived={} if restoring else dependencies(before,after,desired)
        j['derived']=derived;j['desired'].update(derived);atomic(private/'transaction.json',j)
        check(before,after,desired,allow_dependencies=not restoring)
        j.update(status='restored' if restoring else 'committed',readback_verified=True)
        atomic(private/'transaction.json',j);atomic(private/('after-'+ident+'.json'),after)
        if not restoring:atomic(private/('last-'+output+'.json'),{'journal':j,'after':after})
        return {'status':'restored-verified' if restoring else 'applied-verified','revision':peq.revision(after),
                'readback_verified':True,'other_configurable_parameters_unchanged':True,'dependent_parameters':derived,
                'other_parameters_unchanged':not bool(derived),'snapshot':ident,'state':from_reply(after,state)}
    except BaseException as exc:
        j['failure']=str(exc)
        if not j['writes_attempted']:
            j.update(status='rolled-back',no_set_sent=True,restored_verified=True);atomic(private/'transaction.json',j);raise
        try:
            restored=rollback(private,j)
            return {'status':'failed-restored','reason':str(exc),'original_restored':True,'revision':peq.revision(restored)}
        except BaseException as recovery:
            j.update(status='rollback-incomplete',rollback_error=str(recovery));atomic(private/'transaction.json',j)
            return {'status':'rollback-incomplete','reason':str(exc),'original_restored':False}
    finally:
        if worker:
            with contextlib.suppress(subprocess.TimeoutExpired):worker.wait(timeout=2)

def apply(state,patch,kind='settings'):
    if kind not in ('settings','eq','preset') or not isinstance(patch,dict):
        raise ValueError('Richiesta RME non valida')
    validate_expected(patch.get('expected'))
    with operation(state) as private:
        before=peq.verified_read()
        if peq.revision(before)!=patch.get('expected'):raise RevisionConflict(from_reply(before,state))
        if kind=='eq':output,desired,commands=eq_plan(before,patch)
        elif kind=='preset':output,desired,commands=preset_plan(before,patch)
        else:output,desired,commands=settings_plan(before,patch,context(state,before['usb_serial']))
        return execute(state,private,before,output,desired,commands)

def restore(state,output,expected):
    peq.output_name(output);validate_expected(expected)
    with operation(state) as private:
        saved=load(private/('last-'+output+'.json'));before=peq.verified_read();j=saved['journal']
        if peq.revision(before)!=expected or before['usb_serial']!=j['before']['usb_serial']:
            raise RuntimeError('Stato o DAC cambiato; ripristino rifiutato')
        # Undo only while our complete last result is still the current state.
        if values(before)!=values(saved['after']):raise RuntimeError('Modifiche successive rilevate; annullamento rifiutato')
        original=values(j['before']);desired={loc:original[loc] for loc in j['desired']}
        restore_locations=set(j['commands'])|(set() if j.get('selection') else set(j.get('derived',{})))
        if j.get('selection'):
            for a in (4,7,10):
                if f'{a}:28' in j['commands'] and original[f'{a}:28'] in (0,1):
                    restore_locations.update(loc for loc in desired if location(loc)[0]==a and 20<=location(loc)[1]<=26)
        restore_locations-={'12:15','12:16'}
        commands={loc:raw_frame(loc,original[loc],j.get('context')) for loc in restore_locations}
        return execute(state,private,before,output,desired,commands,True)

def recover(state):
    import rme_audio
    for module,directory in ((peq,'rme-peq'),(rme_audio,'rme-audio')):
        previous=Path(state)/directory/'transaction.json'
        if previous.exists() and load(previous).get('status') not in FINAL:module.recover(state)
    with operation(state,True) as private:
        if not (private/'transaction.json').exists():return {'status':'no-pending-transaction'}
        j=load(private/'transaction.json')
        if j.get('status') in FINAL:return {'status':'no-pending-transaction'}
        if peq.parent_alive(j):raise RuntimeError('L’operazione RME è ancora in corso')
        after=rollback(private,j)
        return {'status':'recovered-verified','revision':peq.revision(after)}

def watchdog(state,ident):
    if not isinstance(ident,str) or not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Identificativo operazione non valido')
    path=Path(state)/'rme-control/transaction.json'
    if not path.exists():return
    initial=load(path)
    if initial.get('id')!=ident or initial.get('status') in FINAL:return
    atomic(path.parent/('watchdog-ready-'+ident+'.json'),{'id':ident,'pid':os.getpid(),'start':start_ticks(os.getpid())})
    while path.exists():
        j=load(path)
        if j.get('id')!=ident or j.get('status') in FINAL:return
        if not peq.parent_alive(j):
            with operation(state,True) as private:
                fresh=load(path)
                if fresh.get('id')!=ident or fresh.get('status') in FINAL:return
                try:rollback(private,fresh)
                except Exception as exc:
                    fresh.update(status='rollback-incomplete',rollback_error=str(exc));atomic(path,fresh)
            return
        if time.time()>j['deadline']:
            __import__('rme_guard').terminate_expired(path,ident)
        time.sleep(.5)
