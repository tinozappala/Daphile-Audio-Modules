PLAN={'profiles': [{'slot': 4, 'name': 'Denon AH-D9200', 'fingerprint': '3befc301fa1d791edac49a001f86c068bcb68e95c8d0f7c59da3f6a3315c871c', 'bands': [{'kind': 'low', 'frequency': 35, 'gain': 2.5, 'q': 0.5}, {'kind': 'peak', 'frequency': 184, 'gain': -4.0, 'q': 1.3}, {'kind': 'peak', 'frequency': 1098, 'gain': -3.0, 'q': 1.5}, {'kind': 'peak', 'frequency': 2180, 'gain': 6.5, 'q': 1.5}, {'kind': 'peak', 'frequency': 5320, 'gain': -4.0, 'q': 5.0}]}, {'slot': 5, 'name': 'HiFiMAN Ed XS', 'fingerprint': '05b7c07b30b29efe73c06d8fbd2d31a22b99867227d3dd3ecf62f1af07709641', 'bands': [{'kind': 'low', 'frequency': 62, 'gain': 5.5, 'q': 0.5}, {'kind': 'peak', 'frequency': 204, 'gain': -2.0, 'q': 0.5}, {'kind': 'peak', 'frequency': 1804, 'gain': 4.0, 'q': 1.6}, {'kind': 'peak', 'frequency': 2910, 'gain': -4.0, 'q': 3.2}, {'kind': 'high', 'frequency': 12550, 'gain': -7.0, 'q': 0.8}]}]}
from pathlib import Path
import copy,hashlib,http.client,json,sys,time
host=Path('/mnt/hdd/system/daphile-audio-host')
module=host/'modules/DaphileRME'
current=json.loads((module/'current.json').read_text())
assert current['version']=='0.5.22'
release=module/'releases'/current['release']
sys.path.insert(0,str(release/'rme/lib'))
import rme_peq as peq, rme_memory as memory, rme_control as control
state=Path(json.loads((host/'config.json').read_text())['rme_state'])
cookie=None
def api(path,body=None):
    global cookie
    conn=http.client.HTTPConnection('127.0.0.1',80,timeout=45)
    headers={'Content-Type':'application/json'}
    if cookie:headers['Cookie']=cookie
    try:
        conn.request('GET' if body is None else 'POST','/cgi-bin/RMEPlugin/api/'+path,
                     None if body is None else json.dumps(body),headers)
        reply=conn.getresponse()
        if reply.getheader('Set-Cookie'):cookie=reply.getheader('Set-Cookie').split(';')[0]
        data=json.loads(reply.read())
        if reply.status!=200:raise RuntimeError((path,reply.status,data.get('error')))
        return data
    finally:conn.close()
def snap():return api('rme/snapshot',{})
def same_config(a,b):
    def stable_device(s):
        return {k:v for k,v in s['device'].items() if k not in ('clock_source','sample_rate')}
    return a['outputs']==b['outputs'] and a['current_eq']==b['current_eq'] and stable_device(a)==stable_device(b)
def recall(slot,baseline):
    response=api('rme/preset/apply',{'confirm':True,'patch':{'output':'line','slot':slot,'expected':baseline['revision']}})
    if response.get('status') not in ('applied-verified','no-change') or response.get('readback_verified') is not True:
        raise RuntimeError('Richiamo non confermato: '+str(response.get('status')))
    return response['state']
def readbanks():
    return {n:memory.read(n) for n in (4,5)}
def frames(profile,bank):
    payload={}
    for name,value in bank['raw_parameters'].items():
        a,i=map(int,name.split(':'));payload[a,i]=value
    for number,row in enumerate(profile['bands'],1):
        fields={'frequency':row['frequency'],'gain':row['gain'],'q':row['q']}
        if number in (1,5):
            fields['type']=int(row['kind'] in ('low','high'))
        for key,value in fields.items():
            for address in (13,14):
                payload[address,peq.BANDS[number][key]]=peq.encode_value(number,key,value)
    return payload
def update(profile,expected,configuration):
    slot=profile['slot']
    with control.operation(state):
        before=peq.verified_read()
        current_view=control.from_reply(before,state)
        if not same_config(current_view,configuration):
            raise RuntimeError('Impostazioni del DAC cambiate durante la preparazione')
        indexed=peq.indexes(before)
        if any(indexed[(a,28)]==slot+1 for a in (4,7,10)):
            raise RuntimeError('La memoria è ancora in ascolto su una uscita')
        bank=memory.read(slot)
        if bank['fingerprint']!=expected['fingerprint']:
            raise RuntimeError('La memoria è cambiata prima del salvataggio')
        payload=frames(profile,bank)
        memory.write_bank(slot,payload,bank['dual_eq'],before['usb_serial'],profile['name'])
        after=memory.verified_memory(slot,payload,bank['dual_eq'],profile['name'])
        if after is None:raise RuntimeError('Scrittura non confermata: riconciliare prima di ritentare')
        memory.remember_name(state,after)
        print('MEMORIA_VERIFICATA',slot,after['name'],after['fingerprint'],flush=True)
        return after
assert peq.OUTPUTS and set(memory.protected_slots(state)) >= {4,5}
api('session')
start=snap()
assert not start['pending'] and start['outputs']['line']['eq_preset']==5
assert start['outputs']['line']['load_tones_with_preset'] is False
assert all(start['outputs'][x]['eq_preset'] not in (5,6) for x in ('phones','iem'))
banks=readbanks()
for profile in PLAN['profiles']:
    bank=banks[profile['slot']]
    assert bank['fingerprint']==profile['fingerprint'] and bank['hardware_name']==profile['name']
assert all(start['current_eq']['line'][side]==banks[4]['channels'][side] for side in ('left','right'))
folder=host/'recovery'/('rme-cuffie-pre-revisione-'+str(int(time.time())))
folder.mkdir(mode=0o700)
control.atomic(folder/'banks.json',banks)
control.atomic(folder/'state.json',start)
print('COPIA_DI_RIPRISTINO',str(folder),flush=True)
verified={}
verified[5]=update(PLAN['profiles'][1],banks[5],start)
cur=snap()
if not same_config(cur,start):raise RuntimeError('Stato cambiato: Denon non aggiornato')
manual=recall('manual',cur)
print('LINE_TEMPORANEAMENTE_MANUAL',flush=True)
try:
    verified[4]=update(PLAN['profiles'][0],banks[4],manual)
finally:
    latest=snap()
    if not same_config(latest,manual) or latest['pending']:
        print('RIPRISTINO_LINE_NON_ESEGUITO: stato modificato nel frattempo',flush=True)
    else:
        restored=recall(4,latest)
        print('LINE_RIPRISTINATA_PRESET',restored['outputs']['line']['eq_preset'],flush=True)
if 4 not in verified:raise RuntimeError('Profilo Denon non confermato')
end=snap()
assert end['outputs']['line']['eq_preset']==5 and not end['pending']
for side in ('left','right'):
    assert end['current_eq']['line'][side]==verified[4]['channels'][side]
for key in ('tone_enabled','bass_gain','bass_frequency','bass_q','treble_gain','treble_frequency',
            'treble_q','load_tones_with_preset','loudness_enabled','loudness_bass',
            'loudness_treble','loudness_reference'):
    assert end['outputs']['line'][key]==start['outputs']['line'][key],key
assert all(end['outputs'][o]==start['outputs'][o] and end['current_eq'][o]==start['current_eq'][o] for o in ('phones','iem'))
assert set(end.get('protected_eq_slots',[])) >= {4,5}
for bank in verified.values():bank.pop('identity',None)
control.atomic(folder/'banks-new.json',verified)
print('VERIFICA_FINALE',json.dumps({'profiles':verified,'protected':end['protected_eq_slots'],'line_preset':end['outputs']['line']['eq_preset']}),flush=True)
