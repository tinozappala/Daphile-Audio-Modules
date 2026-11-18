"""ADI-2 DAC registry. Every writable field has a typed, bounded codec.

Public reference: RME MIDI table 0.2, 2023-09-30. Device-specific unknown
values remain unavailable; a returned register is not permission to write it.
"""
from decimal import Decimal
import math

OUTPUTS = {'line': (4, 5, 3), 'phones': (7, 8, 6), 'iem': (10, 11, 9)}
CHIPS = {'unknown', 'ak4490', 'ak4493', 'ess'}
FIELDS = {}

def field(key, label, scope, index, section, lo=0, hi=1, step=1, scale=1,
          options=None, kind='number', source='', **extra):
    FIELDS[key] = dict(key=key, label=label, scope=scope, index=index, section=section,
                      minimum=lo, maximum=hi, step=step, scale=scale, kind=kind,
                      options=options, source=source, **extra)

def switch(key,label,scope,index,section,source='',**extra):
    field(key,label,scope,index,section,kind='boolean',source=source,**extra)

field('source','Sorgente','channel',1,'signal',0,5,options=['Automatico','SPDIF coassiale','Ottico','USB','USB · registra coassiale','USB · registra ottico'],source='Channels!39',synchronized=True)
field('reference_level','Livello di riferimento','channel',2,'levels',0,3,options=['−5 dBu RCA / +1 dBu XLR','+1 dBu RCA / +7 dBu XLR','+7 dBu RCA / +13 dBu XLR','+13 dBu RCA / +19 dBu XLR'],source='Channels!40/43')
switch('auto_reference','Riferimento automatico','channel',3,'levels','Channels!44')
field('mono','Mono','channel',4,'stereo',0,2,options=['Stereo','Mono su entrambe','Mono solo a sinistra'],source='Channels!45')
field('stereo_width','Ampiezza stereo','channel',5,'stereo',-1,1,.01,100,source='Channels!46')
switch('mid_side','Elaborazione Mid / Side','channel',6,'stereo','Channels!47')
field('polarity','Polarità','channel',7,'stereo',0,3,options=['Normale','Inverti entrambi','Inverti sinistro','Inverti destro'],source='Channels!48')
field('crossfeed','Crossfeed','channel',8,'stereo',0,5,options=['Disattivato','Livello 1','Livello 2','Livello 3','Livello 4','Livello 5'],source='Channels!49')
field('dac_filter','Filtro D/A','channel',9,'signal',0,4,options=['SD Sharp','SD Slow','Sharp','Slow','NOS'],source='Channels!52',variant=True)
field('de_emphasis','De-enfasi','channel',10,'signal',0,2,options=['Automatico','Attiva','Disattiva'],source='Channels!53')
switch('dual_eq','EQ separato sinistro / destro','channel',11,'eq','Channels!54')
field('volume','Volume','channel',12,'levels',-114.5,6,.1,10,source='Channels!55',unit='dB',changes_level=True)
switch('volume_lock','Blocco volume','channel',13,'levels','Channels!56')
field('balance','Bilanciamento','channel',14,'stereo',-1,1,.01,100,source='Channels!57')
switch('mute','Mute','channel',15,'levels','Channels!58')
switch('dim','Dim · −20 dB','channel',16,'levels','Channels!59')
field('loopback','Ritorno audio USB · comune alle uscite','channel',17,'signal',0,3,options=['Disattivato','Prima degli effetti','Dopo gli effetti','Dopo gli effetti · ritorno −6 dB'],source='Channels!60; actual DAC alias readback',synchronized=True)
field('dc_protection','Protezione DC','channel',18,'signal',0,2,options=['Solo avviso','Rileva e silenzia','Filtro DC e infrasuoni'],source='Channels!62')
switch('loudness_enabled','Loudness attivo sul DAC','channel',20,'loudness','Channels!66')
field('loudness_bass','Loudness · bassi','channel',21,'loudness',1,10,.5,2,source='Channels!67',unit='dB')
field('loudness_treble','Loudness · alti','channel',22,'loudness',1,10,.5,2,source='Channels!68',unit='dB')
# Official example and captured DAC agree on half-dB scaling, unlike prose C15.
field('loudness_reference','Riferimento loudness','channel',23,'loudness',-90,-20,.5,2,source='Channels!69; Examples!A9; captured DAC',unit='dB')
switch('tone_enabled','Toni attivi sul DAC','eq',20,'tones','Channels!94')
field('bass_gain','Bassi · guadagno','eq',21,'tones',-12,12,.5,2,source='Channels!95',unit='dB')
field('bass_frequency','Bassi · frequenza','eq',22,'tones',20,150,1,1,source='Channels!96',unit='Hz',kind='frequency')
field('bass_q','Bassi · Q','eq',23,'tones',.5,1.5,.1,10,source='Channels!97')
field('treble_gain','Alti · guadagno','eq',24,'tones',-12,12,.5,2,source='Channels!98',unit='dB')
field('treble_frequency','Alti · frequenza','eq',25,'tones',3000,10000,10,1,source='Channels!99',unit='Hz',kind='frequency')
field('treble_q','Alti · Q','eq',26,'tones',.5,1.5,.1,10,source='Channels!100')
switch('load_tones_with_preset','Carica i toni con il preset EQ','eq',27,'memory','Channels!102')
# Manual/Temp/20 slots are unambiguous; special Clear mapping is not.
field('eq_preset','Preset EQ hardware','eq',28,'memory',0,21,options=['Manual','Temp']+[f'Preset {i}' for i in range(1,21)],source='Channels!103',memory_action=True)
field('output_policy','Commutazione delle uscite','device',1,'device',0,3,options=['Uscite simultanee','Silenzia Line con cuffie inserite','Alterna cuffie / Line','Alterna le uscite collegate'],source='Device!17')
field('auto_standby','Standby automatico','device',2,'device',0,4,options=['Disattivato','30 minuti','1 ora','2 ore','4 ore'],source='Device!18')
switch('dsd_detection','Riconoscimento DSD','device',3,'device','Device!19')
field('dsd_filter','Filtro DSD','device',4,'device',0,1,options=['50 kHz','Da identificare per il modello'],source='Device!20',variant=True,blocked='La frequenza della seconda opzione diverge fra tabella e manuale; comando non qualificato.')
switch('dsd_direct','DSD Direct · uscita Line','device',5,'device','Device!21',blocked='DSD Direct bypassa il volume digitale. Attivazione non qualificata e mai automatica.')
field('clock_source','Clock attuale','device',15,'status',0,3,options=['Automatico','Interno','SPDIF coassiale','Ottico'],source='Device!32; manuale DAC 14.1.2',read_only=True)
field('sample_rate','Frequenza attuale','device',16,'status',0,9,options=['44,1 kHz','48 kHz','88,2 kHz','96 kHz','176,4 kHz','192 kHz','352,8 kHz','384 kHz','705,6 kHz','768 kHz'],source='Device!33',read_only=True)
field('remap_mode','Assegnazione tasti','device',25,'remote',0,2,options=['Disattivata','Dispositivo e telecomando','Solo telecomando'],source='Device!35')
REMAP = ['Predefinito']+[f'Setup {i}' for i in range(1,10)]+[
    'Mono','Mono a sinistra','Mute','Loudness','EQ','Toni','EQ + Toni + Loudness',
    'Alterna cuffie / Line','Polarità','AutoDark','Dim','Cambia schermata']+[
    f'Crossfeed {i}' for i in range(1,6)]+['Filtro SD Sharp','Filtro SD Slow','Filtro Sharp',
    'Filtro Slow','Filtro NOS','Filtro specifico AKM / ESS']+[f'Preset EQ {i}' for i in range(1,10)]
assert len(REMAP)==42
for i,label in enumerate(['VOL / 1','I/O / 2','EQ / 3','SETUP / 4'],26):
    field(f'remap_{i-25}',f'Tasto {label}','device',i,'remote',0,41,options=REMAP,source='Device!36:39; Remap Keys!4:8')
field('display_mode','Tema display DAC','device',32,'display',0,1,options=['Chiaro','Scuro'],source='Device!49')
field('meter_color','Colore meter DAC','device',33,'display',0,5,options=['Verde','Ciano','Ambra','Monocromatico','Rosso','Arancione'],source='Device!50')
field('horizontal_meter','Meter orizzontale DAC','device',34,'display',0,3,options=['Dopo gli effetti','Prima degli effetti','Entrambi','Dopo gli effetti · dBu'],source='Device!51')
switch('auto_dark','Oscuramento automatico','device',35,'display','Device!52')
switch('volume_screen','Mostra schermata volume','device',36,'display','Device!53')
field('ui_lock','Blocco comandi fisici','device',37,'display',0,3,options=['Nessun blocco','Telecomando','Tasti','Tasti e telecomando'],source='Device!54')
# Not guessed from a spare register: full GET captures of a user-operated
# menu transition Disabled -> with Setup -> Disabled change ONLY 12:62,
# 1 -> 0 -> 1, with all220 values restored. See the immutable empirical
# mapping report rme55_load_volume_mapping.json and official Remote manual21.
field('load_volume','Load Volume · volume con Setup','device',62,'setup',0,1,
      options=['with Setup · carica il volume','Disabled · conserva il volume'],
      source='ADI-2 Remote2.2 p21; user menu differential GET1/0/1, reports/rme55_load_volume_mapping.json',
      empirical_mapping=True)

def spec(key,output='line',context=None):
    if key not in FIELDS:raise ValueError('Parametro RME sconosciuto')
    if output not in OUTPUTS:raise ValueError('Uscita RME sconosciuta')
    result=dict(FIELDS[key]);chip=(context or {}).get('chip','unknown')
    if chip not in CHIPS:raise ValueError('Revisione DAC non valida')
    if key=='reference_level' and output=='phones':
        result.update(maximum=1,options=['Lo Power · +7 dBu','Hi Power · +22 dBu'])
    if key in ('reference_level','auto_reference') and output=='iem':
        result['blocked']='IEM ha riferimento fisso a −3 dBu; questo controllo non si applica.'
    if key=='dac_filter' and chip in ('ak4493','ess'):
        result['maximum']=5;result['options']=[*result['options'],'SD LD' if chip=='ak4493' else 'Brickwall']
    return result

def location(key,output='line'):
    item=spec(key,output);left,_,channel=OUTPUTS[output]
    return (12 if item['scope']=='device' else left if item['scope']=='eq' else channel,item['index'])

def numeric(value,lo,hi,step):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('È richiesto un numero finito')
    v=Decimal(str(value));unit=Decimal(str(step))
    if not Decimal(str(lo))<=v<=Decimal(str(hi)) or v%unit:
        raise ValueError('Valore fuori intervallo o passo non valido')
    return v

def encode(key,value,output='line',context=None):
    item=spec(key,output,context)
    if item.get('read_only') or item.get('blocked'):raise ValueError(item.get('blocked','Parametro in sola lettura'))
    if item['kind']=='boolean':
        if type(value)!=bool:raise ValueError('È richiesto un interruttore ON/OFF')
        return int(value)
    v=numeric(value,item['minimum'],item['maximum'],item['step'])
    if item['kind']=='frequency':
        return int(v) if v<=2040 else 2048+int(v)//10
    return int(v*Decimal(str(item['scale']))) & (2047 if item['scope']=='device' else 4095)

def decode(key,raw,output='line',context=None):
    item=spec(key,output,context);maximum=2047 if item['scope']=='device' else 4095
    if type(raw)!=int or not 0<=raw<=maximum:raise ValueError('Valore assente o non valido')
    if key in ('reference_level','auto_reference') and output=='iem':raise ValueError('Non applicabile a IEM')
    if item['kind']=='boolean':
        if raw not in (0,1):raise ValueError('Interruttore non disponibile')
        return bool(raw)
    if item['kind']=='frequency':value=(raw&2047)*(10 if raw&2048 else 1)
    else:value=(raw-4096 if item['scope']!='device' and raw&2048 else raw)/item['scale']
    numeric(value,item['minimum'],item['maximum'],item['step'])
    return int(value) if item['step']==1 else value

def packet(key,value,output='line',context=None):
    raw=encode(key,value,output,context);address,index=location(key,output)
    if address==12:triple=[(address<<3)|(index>>3),((index&7)<<4)|(raw>>7),raw&127]
    else:triple=[(address<<3)|(index>>2),((index&3)<<5)|(raw>>7),raw&127]
    return bytes.fromhex('F0 00 20 0D 71 02')+bytes(triple)+b'\xf7'

def view(reply,context=None):
    indexed={(p['address'],p['index']):p['raw'] for p in reply['parameters']}
    result={'outputs':{},'device':{},'capabilities':{'outputs':{},'device':{}},'context':context or {'chip':'unknown'}}
    for output in OUTPUTS:
        values={};caps={}
        for key,base in FIELDS.items():
            if base['scope']=='device' and output!='line':continue
            item=spec(key,output,context);loc=location(key,output)
            cap={k:v for k,v in item.items() if k not in ('scale',)}
            try:value=decode(key,indexed.get(loc),output,context)
            except ValueError as exc:cap.update(available=False,writable=False,reason=str(exc))
            else:
                values[key]=value;cap.update(available=True,writable=not item.get('read_only') and not item.get('blocked'))
                if key=='dim' and value is False and values.get('volume',-114.5)<-94.5:
                    cap.update(writable=False,reason='A questo volume Dim richiederebbe un livello inferiore all’intervallo MIDI qualificato. Abbassa il volume con il controllo dedicato.')
                if item.get('blocked'):cap['reason']=item['blocked']
                if (item['scope']=='eq' and 20<=item['index']<=27 or key=='dual_eq') and indexed.get((OUTPUTS[output][0],28))!=0:
                    cap.update(writable=False,reason='Usa Manual per modificare toni o modalità L/R: la scrittura su un preset hardware caricato non è qualificata.')
            if base['scope']=='device':result['capabilities']['device'][key]=cap
            else:caps[key]=cap
        result['outputs'][output]={k:v for k,v in values.items() if FIELDS[k]['scope']!='device'}
        result['capabilities']['outputs'][output]=caps
        if output=='line':result['device']={k:v for k,v in values.items() if FIELDS[k]['scope']=='device'}
    result['unknown_parameters']=[{'address':a,'index':i,'raw':v} for (a,i),v in indexed.items()
        if a==12 and i not in {f['index'] for f in FIELDS.values() if f['scope']=='device'}]
    return result
