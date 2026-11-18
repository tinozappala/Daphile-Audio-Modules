"""Read hardware EQ banks without loading them. Known selection is separate.

Flag index 1 is established by official examples AND all 20 captured DAC
replies. The incompatible commit description is not used for memory writes.
"""
import rme
import rme_peq as peq

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
    result={'slot':slot,'name':names.get(slot,f'Preset {slot}'),'empty':flag['raw']&15==15,
            'dual_eq':bool(flag['raw']&1),'read_only':True,'settings_write_commands_sent':0}
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
    import rme_registry as registry
    tones={}
    for name,item in registry.FIELDS.items():
        if item['scope']=='eq' and 21<=item['index']<=26:
            param=indexed.get((13,item['index']))
            if param is None:raise RuntimeError('Toni del preset hardware incompleti')
            tones[name]=registry.decode(name,param['raw'])
    result.update(channels=channels,tones=tones,
        raw_parameters={f'{a}:{i}':p['raw'] for (a,i),p in indexed.items() if i!=1})
    return result

def read(slot):
    validate_slot(slot)
    request=bytes.fromhex('F0 00 20 0D 71 03')+bytes([slot+9,247])
    return decode(rme.query(request),slot)

LIMITS={
 'eq_save':'Il commit della memoria EQ è descritto con indici discordanti. Scrittura disabilitata fino a una specifica verificabile.',
 'eq_rename':'La scrittura dei nomi non ha ancora una codifica e una verifica hardware qualificate; i nomi letti sono disponibili.',
 'setup_load':'Load Volume è regolabile separatamente. Il richiamo completo dei Setup non è ancora qualificato per verificare e ripristinare tutte le impostazioni: i nomi restano consultabili senza caricarli.',
 'setup_save':'La specifica non offre una lettura non invasiva del contenuto di un Setup per verificarne salvataggio e rollback; nessuna memoria esistente viene sovrascritta.',
 'setup_rename':'Scrittura dei nomi Setup non qualificata sul DAC.',
 'factory':'Ripristino di fabbrica escluso dal collaudo e dalle normali operazioni: richiede backup e autorizzazione specifica.',
 'preset_clear':'Il valore speciale Clear non ha una mappa univoca nella tabella pubblica.',
}
