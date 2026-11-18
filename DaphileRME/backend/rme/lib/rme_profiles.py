"""Versioned software presets. Legacy slots are validated without rewriting."""
import json
import rme_peq as peq
import rme_audio as audio
import rme_registry as registry

def bands(rows):
    if not isinstance(rows,list) or len(rows)!=5 or {r.get('band') for r in rows if isinstance(r,dict)}!={1,2,3,4,5}:
        raise ValueError('Sono richieste cinque bande complete')
    for row in rows:
        if type(row['band'])!=int or set(row)!={'band',*peq.BANDS[row['band']]}:raise ValueError('Banda RME incompleta')
        for key,value in row.items():
            if key!='band':peq.encode_value(row['band'],key,value)

def validate(profile):
    if not isinstance(profile,dict):raise ValueError('Preset RME non valido')
    if 'schema' not in profile:
        if set(profile)!={'output','bands','enabled','audio'}:raise ValueError('Preset legacy non valido')
        peq.output_name(profile['output']);bands(profile['bands'])
        if type(profile['enabled'])!=bool:raise ValueError('Stato EQ non valido')
        values=profile['audio']
        if not isinstance(values,dict) or set(values)-audio.FIELDS.keys():raise ValueError('Controlli legacy non validi')
        for key,value in values.items():audio.encode(key,value)
    else:
        required={'schema','output','enabled','channels','audio','settings','device'}
        if not required<=set(profile) or set(profile)-required-{'context'} or type(profile['schema'])!=int or profile['schema']!=2:
            raise ValueError('Versione o struttura del preset non supportata')
        ctx=profile.get('context',{'chip':'unknown'})
        if not isinstance(ctx,dict) or set(ctx)!={'chip'} or ctx['chip'] not in registry.CHIPS:raise ValueError('Compatibilità hardware del preset non valida')
        output=peq.output_name(profile['output'])
        if type(profile['enabled'])!=bool or not isinstance(profile['channels'],dict) or set(profile['channels'])!={'left','right'}:
            raise ValueError('Canali o stato EQ non validi')
        for rows in profile['channels'].values():bands(rows)
        for group,expected_scope in [('audio',None),('settings',None),('device','device')]:
            values=profile[group]
            if not isinstance(values,dict) or len(values)>len(registry.FIELDS):raise ValueError('Ambito del preset non valido')
            for key,value in values.items():
                if key not in registry.FIELDS:raise ValueError('Parametro del preset sconosciuto')
                item=registry.FIELDS[key]
                if group=='audio' and key not in audio.FIELDS:raise ValueError('Controllo audio non valido')
                if group=='settings' and (item['scope']=='device' or key in audio.FIELDS):raise ValueError('Parametro nell’ambito errato o duplicato')
                if expected_scope and item['scope']!=expected_scope:raise ValueError('Parametro nell’ambito errato')
                if item.get('memory_action'):raise ValueError('Le azioni sulle memorie non fanno parte dei preset software')
                # Variant-specific filter selection remains explicit, outside
                # portable presets until it can carry a qualified model ID.
                registry.encode(key,value,output,ctx)
    return json.loads(json.dumps(profile,allow_nan=False))

def migrate(profile):
    profile=validate(profile)
    if profile.get('schema')==2:return profile
    return dict(schema=2,output=profile['output'],enabled=profile['enabled'],
        channels={'left':profile['bands'],'right':json.loads(json.dumps(profile['bands']))},
        audio=profile['audio'],settings={},device={})

def backup(slots,identity):
    return {'format':'daphile-rme-software-presets','version':2,'device_identity':identity,
            'slots':slots,'scope':'Software presets only. No hardware memory is read or written by export.'}

def import_preview(document):
    if not isinstance(document,dict) or set(document)!={'format','version','device_identity','slots','scope'} or document['format']!='daphile-rme-software-presets' or type(document['version'])!=int or document['version']!=2:
        raise ValueError('Formato di backup RME non supportato')
    data=document['slots']
    if not isinstance(data,dict) or set(data)!={'revision','slots'} or type(data['revision'])!=int or data['revision']<0 or not isinstance(data['slots'],list) or len(data['slots'])!=4:
        raise ValueError('Il backup deve contenere quattro slot software')
    rows=[]
    for row in data['slots']:
        if row is None:rows.append(None);continue
        if not isinstance(row,dict) or set(row)!={'name','profile'} or not isinstance(row['name'],str) or not 1<=len(row['name'].strip())<=48:
            raise ValueError('Nome o struttura dello slot non validi')
        rows.append({'name':row['name'],'profile':migrate(row['profile'])})
    return {'slots':rows,'settings_write_commands_sent':0,'status':'import-preview'}
