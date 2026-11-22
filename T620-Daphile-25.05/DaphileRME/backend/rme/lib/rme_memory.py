"""Read and store the DAC's numbered EQ memories over the native MIDI port."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import rme
import rme_peq as peq
import rme_registry as registry
from engine import atomic, load

EQ_INDICES = tuple(range(3, 20))
TONE_INDICES = tuple(range(21, 27))
PRESET_HEADER = bytes.fromhex('F0 00 20 0D 71 02')
NAME_HEADER = bytes.fromhex('F0 00 20 0D 71 06')
NAME_PATTERN = re.compile(r"[A-Za-z0-9 +/()*:;.,!#$&<>='|@-]{1,14}")
BANK_FIELDS = ({(13,index) for index in (*EQ_INDICES,*TONE_INDICES)} |
               {(14,index) for index in EQ_INDICES})

def validate_slot(slot):
    if type(slot)!=int or not 1<=slot<=20:raise ValueError('Seleziona un preset hardware da 1 a 20')

def decode(reply,slot):
    validate_slot(slot)
    if reply.get('status')!='read-response-received' or reply.get('parse_issues'):
        raise RuntimeError('Lettura del preset hardware incompleta')
    indexed={(p['address'],p['index']):p for p in reply['parameters'] if p['address'] in (13,14)}
    flag=indexed.get((13,1))
    if flag is None or (flag['raw']>>4)!=(slot-1):raise RuntimeError('Identità del preset hardware non coincidente')
    names={n['identifier']:n['name'] for n in reply.get('hardware_names',[])}
    fingerprint = json.dumps([names.get(slot), sorted((a,i,p['raw']) for (a,i),p in indexed.items())])
    result={'slot':slot,'name':names.get(slot) or f'Preset {slot}','empty':flag['raw']&15==15,
            'hardware_name':names.get(slot),
            'fingerprint':hashlib.sha256(fingerprint.encode()).hexdigest(),
            'dual_eq':bool(flag['raw']&1),'read_only':True,'settings_write_commands_sent':0}
    if reply.get('usb_serial'):
        result['identity'] = hashlib.sha256(reply['usb_serial'].encode()).hexdigest()
    if result['empty']:return result
    channels={}
    for side,address in [('left',13),('right',14)]:
        bands=[]
        for band,fields in peq.BANDS.items():
            row={'band':band}
            for field,index in fields.items():
                item=indexed.get((address,index))
                if item is None:raise RuntimeError('Dati del preset hardware incompleti')
                raw=item['raw'];value=((raw-4096 if raw&2048 else raw)/2 if field=='gain' else raw/10 if field=='q'
                    else (raw&2047)*(10 if raw&2048 else 1) if field=='frequency' else raw)
                peq.encode_value(band,field,value);row[field]=value
            bands.append(row)
        channels[side]=bands
    tones={}
    for name,item in registry.FIELDS.items():
        if item['scope']=='eq' and 21<=item['index']<=26:
            param=indexed.get((13,item['index']))
            if param is None:raise RuntimeError('Toni del preset hardware incompleti')
            tones[name]=registry.decode(name,param['raw'])
    result.update(channels=channels,tones=tones,
        raw_parameters={f'{a}:{i}':p['raw'] for (a,i),p in indexed.items() if i!=1})
    return result

def complete_bank(reply, slot):
    if not any(item['identifier']==slot for item in reply['hardware_names']):
        return False
    indexed = {(item['address'],item['index']):item['raw'] for item in reply['parameters']}
    flag = indexed.get((13,1))
    return (flag is not None and flag>>4==slot-1 and
            (flag&15==15 or BANK_FIELDS.issubset(indexed)))


def read(slot):
    validate_slot(slot)
    request=bytes.fromhex('F0 00 20 0D 71 03')+bytes([slot+9,247])
    return decode(rme.query(request,complete=lambda reply:complete_bank(reply,slot)),slot)


def projection(reply, output):
    """Use the DAC's exact parameter values, including both channels and tones."""
    left, right, channel = registry.OUTPUTS[output]
    values = peq.indexes(reply)
    payload = {(13,index): values[left,index] for index in (*EQ_INDICES,*TONE_INDICES)}
    payload.update({(14,index): values[right,index] for index in EQ_INDICES})
    # The DAC stores treble frequency at its native 100 Hz resolution.
    treble = registry.decode('treble_frequency',payload[13,25],output)
    payload[13,25] = registry.encode('treble_frequency',treble//100*100,output)
    return payload, bool(values[channel,11])


def matches(bank, payload, dual):
    return (not bank['empty'] and bank['dual_eq'] == dual and
            all(bank['raw_parameters'].get(f'{a}:{i}') == raw for (a,i),raw in payload.items()))


def frame(parameters):
    data = bytearray()
    for (address,index),raw in parameters.items():
        if address not in (13,14) or not 0 <= index <= 26 or not 0 <= raw <= 4095:
            raise ValueError('Dati della memoria EQ fuori intervallo')
        data.extend(((address << 3) | (index >> 2), ((index & 3) << 5) | (raw >> 7), raw & 127))
    return PRESET_HEADER + data + b'\xf7'


def send_packets(serial, packets):
    tool = rme.shutil.which('amidi')
    if not tool:
        raise RuntimeError('Collegamento MIDI al DAC non disponibile')
    with rme.transport():
        card, usb = rme.select(rme.alsa_cards(), rme.usb_devices())
        if usb['serial'] != serial:
            raise RuntimeError('Il DAC è cambiato. Rileggilo prima di salvare.')
        port = '--port=hw:'+str(card['card'])+',0,0'
        for packet in packets:
            subprocess.run([tool,port,'--send-hex='+packet.hex(' ')],
                           stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=5)
            time.sleep(.05)


def write_bank(slot, payload, dual, serial, name=None):
    # The device receives the commit flag at index 2 and reports it at index 1.
    data = frame(payload)
    commit = frame({(13,2): ((slot - 1) << 4) | int(dual)})
    packets = (data,commit)
    if name is not None:
        label = NAME_HEADER + bytes([slot]) + name.encode('ascii').ljust(16,b'\x00') + b'\xf7'
        packets = (data,label,commit)
    send_packets(serial,packets)


def validate_name(value):
    if not isinstance(value,str) or not NAME_PATTERN.fullmatch(value.strip()):
        raise ValueError('Inserisci un nome di 1–14 caratteri, senza lettere accentate.')
    return value.strip()


def verified_memory(slot, payload, dual, name=None):
    for _ in range(2):
        try:
            after = read(slot)
        except (OSError,RuntimeError,subprocess.SubprocessError):
            continue
        if matches(after,payload,dual) and (name is None or after['hardware_name']==name):
            return after
    return None


def known_names(state, identity):
    path = Path(state)/'rme-memory-names.json'
    try:
        data = load(path) if path.exists() else {}
        if data.get('identity') == identity:
            return {key:value for key,value in data['names'].items()
                    if key.isdigit() and 1<=int(key)<=20 and isinstance(value,str)}
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        pass
    return {}


def remember_name(state, bank):
    identity = bank.get('identity')
    if not identity:
        return False
    key, name = str(bank['slot']), bank['hardware_name']
    names = known_names(state,identity)
    if names.get(key)==(name or None):
        return False
    if name:
        names[key] = name
    else:
        names.pop(key,None)
    try:
        atomic(Path(state)/'rme-memory-names.json',{'identity':identity,'names':names})
    except OSError:
        return False
    return True


def protected_slots(state):
    path = Path(state) / 'rme-protected-eq.json'
    if not path.exists():
        return []
    data = load(path)
    slots = data.get('slots')
    if not isinstance(slots, list) or any(type(slot) is not int or not 1 <= slot <= 20 for slot in slots):
        raise RuntimeError('Elenco dei preset protetti non valido')
    return slots


def store(state, patch):
    import rme_control as control
    required = {'output','expected','slot','fingerprint','replace'}
    if (not isinstance(patch,dict) or set(patch)-{'name'} != required
            or type(patch['replace']) is not bool):
        raise ValueError('Scegli l’uscita e la memoria EQ da salvare')
    name = validate_name(patch['name']) if 'name' in patch else None
    output, slot = peq.output_name(patch['output']), patch['slot']
    validate_slot(slot)
    control.validate_expected(patch['expected'])
    control.validate_expected(patch['fingerprint'])
    with control.operation(state):
        if slot in protected_slots(state):
            raise ValueError('Preset protetto: questa memoria non può essere sovrascritta dal modulo.')
        target = read(slot)
        before = peq.verified_read()
        if peq.revision(before) != patch['expected']:
            raise control.RevisionConflict(control.from_reply(before,state))
        if target['fingerprint'] != patch['fingerprint']:
            raise RuntimeError('La memoria è cambiata. Rileggila prima di salvare.')
        payload, dual = projection(before,output)
        same_eq = matches(target,payload,dual)
        same_name = name is None or target['hardware_name']==name
        if same_eq and same_name:
            remember_name(state,target)
            return {'status':'no-change','readback_verified':True,'memory':target}
        if not target['empty'] and not patch['replace']:
            raise ValueError('Conferma la sostituzione della memoria occupata')
        values = peq.indexes(before)
        if any(values[left,28] == slot+1 for left,_,_ in registry.OUTPUTS.values()):
            raise ValueError('Questa memoria è in uso. Scegli un’altra memoria.')
        name = name or target.get('hardware_name') or None
        try:
            write_bank(slot,payload,dual,before['usb_serial'],name)
        except (OSError,subprocess.SubprocessError):
            # A lost response may follow a completed commit.
            pass
        target = verified_memory(slot,payload,dual,name)
        if target is None:
            raise RuntimeError('Il DAC non ha confermato la curva e il nome. Rileggi il DAC prima di riprovare.')
        remember_name(state,target)
        return {'status':'stored-verified','readback_verified':True,'memory':target}


LIMITS={
 'eq_save':'Le memorie EQ si salvano dall’Equalizzatore e si verificano rileggendole dal DAC.',
 'eq_rename':'Il nome si imposta durante il salvataggio e si verifica rileggendolo dal DAC.',
 'setup_load':'Load Volume è regolabile separatamente. Il richiamo completo dei Setup non è ancora qualificato per verificare e ripristinare tutte le impostazioni: i nomi restano consultabili senza caricarli.',
 'setup_save':'La specifica non offre una lettura non invasiva del contenuto di un Setup per verificarne salvataggio e rollback; nessuna memoria esistente viene sovrascritta.',
 'setup_rename':'Scrittura dei nomi Setup non qualificata sul DAC.',
 'factory':'Ripristino di fabbrica escluso dal collaudo e dalle normali operazioni: richiede backup e autorizzazione specifica.',
 'preset_clear':'Il valore speciale Clear non ha una mappa univoca nella tabella pubblica.',
}


def setup_action(state, slot, action, expected):
    import rme_control as control
    if type(slot) is not int or not 1 <= slot <= 9 or action not in ('load', 'save'):
        raise ValueError('Seleziona un Setup da 1 a 9')
    control.validate_expected(expected)
    with control.operation(state):
        before = peq.verified_read()
        if peq.revision(before) != expected:
            raise control.RevisionConflict(control.from_reply(before, state))
        command = slot if action == 'load' else slot + 9
        send_packets(before['usb_serial'], [bytes.fromhex('F0 00 20 0D 71 04') + bytes([command, 0xF7])])
        time.sleep(.5)
        after = peq.verified_read()
        if after['usb_serial'] != before['usb_serial']:
            raise RuntimeError('Il DAC è cambiato durante il comando')
        return {'state': control.from_reply(after, state), 'action': action, 'slot': slot}


def rename_setup(state, slot, name, previous, expected):
    import rme_control as control
    if type(slot) is not int or not 1 <= slot <= 9:
        raise ValueError('Seleziona un Setup da 1 a 9')
    name = validate_name(name)
    if not isinstance(previous, str):
        raise ValueError('Rileggi il nome del Setup')
    control.validate_expected(expected)
    identifier = 32 + slot
    with control.operation(state):
        before = peq.verified_read()
        if peq.revision(before) != expected:
            raise control.RevisionConflict(control.from_reply(before, state))
        names = {entry['identifier']: entry['name'] for entry in before.get('hardware_names', [])}
        if names.get(identifier) != previous:
            raise ValueError('Il nome del Setup è cambiato. Rileggi il DAC.')
        if previous != name:
            packet = NAME_HEADER + bytes([identifier]) + name.encode('ascii').ljust(16, b'\x00') + b'\xf7'
            send_packets(before['usb_serial'], [packet])
        for attempt in range(3):
            after = peq.verified_read()
            names = {entry['identifier']: entry['name'] for entry in after.get('hardware_names', [])}
            if after['usb_serial'] == before['usb_serial'] and names.get(identifier) == name:
                return {'state': control.from_reply(after, state), 'slot': slot, 'name': name}
        raise RuntimeError('Nome inviato ma non confermato dal DAC. Rileggi prima di riprovare.')
