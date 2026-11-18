"""ADI-2 DAC audio controls from RME MIDI table 0.2, Channels rows45..100.

No opaque MIDI, volume, reference level, routing or preset commands. A fresh
whole-device revision, shared PEQ lock and independent recovery guard cover
each transaction. Bass/treble belong to the LEFT address only (row93).
"""
import contextlib,math,os,re,signal,subprocess,sys,time,uuid
from pathlib import Path
import rme,rme_peq as peq
from engine import STATE,atomic,load,start_ticks

# Loudness reference uses the table's half-dB increments on the ADI-2 DAC:
# raw-60 is its documented-30dB default. The special0.1dB encoding of
# Volume(index12) must not be copied to Loudness(index23). Verified on device.
# key: address scope, index, min, max, increment, numeric transmission scale.
FIELDS={
 'tone_enabled':('eq',20,0,1,1,1),
 'bass_gain':('eq',21,-12,12,.5,2),'bass_frequency':('eq',22,20,150,1,1),'bass_q':('eq',23,.5,1.5,.1,10),
 'treble_gain':('eq',24,-12,12,.5,2),'treble_frequency':('eq',25,3000,10000,10,None),'treble_q':('eq',26,.5,1.5,.1,10),
 'loudness_enabled':('channel',20,0,1,1,1),
 'loudness_bass':('channel',21,1,10,.5,2),'loudness_treble':('channel',22,1,10,.5,2),'loudness_reference':('channel',23,-90,-20,.5,2),
 'crossfeed':('channel',8,0,5,1,1),'balance':('channel',14,-1,1,.01,100),
 'stereo_width':('channel',5,-1,1,.01,100),'mono':('channel',4,0,2,1,1),'polarity':('channel',7,0,3,1,1),
}
BOOLS={'tone_enabled','loudness_enabled'}
FINAL={'committed','restored','rolled-back','no-change'}

def location(output,key):
 left,_,channel=peq.OUTPUTS[peq.output_name(output)]
 return (left if FIELDS[key][0]=='eq' else channel,FIELDS[key][1])

def encode(key,value):
 if key not in FIELDS:raise ValueError('Unknown RME audio control')
 _,_,lo,hi,step,scale=FIELDS[key]
 if key in BOOLS:
  if type(value)!=bool:raise ValueError('Boolean RME switch required')
  return int(value)
 units=peq.stepped(value,lo,hi,step)
 if key=='treble_frequency':return 2048+int(units*step)//10
 return int(round(units*step*scale))&4095

def decode(key,raw):
 if key not in FIELDS:raise ValueError('Unknown RME audio control')
 if type(raw)!=int or not 0<=raw<4096:raise ValueError('Invalid RME audio read value')
 if key in BOOLS:
  if raw not in [0,1]:raise ValueError('Unknown RME switch value')
  return bool(raw)
 if key=='treble_frequency':value=(raw&2047)*(10 if raw&2048 else 1)
 else:value=(raw-4096 if raw&2048 else raw)/FIELDS[key][5]
 encode(key,value) # Refuse values we cannot restore safely.
 return value

def equivalent(key,a,b):
 try:return decode(key,a)==decode(key,b)
 except (ValueError,TypeError):return False

def validate(patch):
 if not isinstance(patch,dict) or set(patch)!={'output','expected','values'}:raise ValueError('Output, fresh revision and audio values required')
 output=peq.output_name(patch['output']);expected=patch['expected'];values=patch['values']
 if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('Fresh RME read revision required')
 if not isinstance(values,dict) or not values or set(values)-FIELDS.keys():raise ValueError('Unknown or empty RME audio values')
 return output,expected,{key:encode(key,value) for key,value in values.items()}

def packet(output,key,raw):
 # Revalidate raw at the transport boundary; no volume or arbitrary index.
 decode(key,raw);address,index=location(output,key)
 return bytes.fromhex('F0 00 20 0D 71 02')+bytes([(address<<3)|(index>>2),((index&3)<<5)|(raw>>7),raw&127,247])

def send(output,key,raw,serial):
 payload=packet(output,key,raw)
 tool=rme.shutil.which('amidi')
 if not tool:raise RuntimeError('amidi unavailable')
 with rme.transport():
  card,usb=rme.select(rme.alsa_cards(),rme.usb_devices())
  if usb['serial']!=serial:raise RuntimeError('Original RME serial absent; SET refused')
  subprocess.run([tool,'--port=hw:'+str(card['card'])+',0,0','--send-hex='+payload.hex(' ')],stdin=subprocess.DEVNULL,capture_output=True,check=True,timeout=5,preexec_fn=rme.child_cleanup(os.getpid()))
  time.sleep(.04)

def verified_read():return peq.verified_read()

def view(reply):
 v=peq.indexes(reply);outputs={};caps={}
 for output in peq.OUTPUTS:
  values={};fields={}
  for key in FIELDS:
   try:
    values[key]=decode(key,v.get(location(output,key)));fields[key]={'available':True,'minimum':FIELDS[key][2],'maximum':FIELDS[key][3],'step':FIELDS[key][4]}
    if FIELDS[key][0]=='eq' and v.get((peq.OUTPUTS[output][0],28))!=0:fields[key]={'available':False,'reason':'Seleziona EQ Manual sul DAC: modifica dei toni su preset caricati non qualificata.'}
   except (ValueError,TypeError):fields[key]={'available':False,'reason':'Valore assente o non ripristinabile in questa revisione DAC.'}
  outputs[output]=values;caps[output]={'fields':fields,'eligible':bool(values)}
 return outputs,caps

@contextlib.contextmanager
def operation(state):
 # Same global lock as the PEQ: two pages/modules cannot write concurrently.
 with peq.operation(state):
  from engine import locked
  with locked(Path(state)/'rme-audio') as private:
   old=Path(state)/'rme-peq/transaction.json'
   if old.exists() and load(old).get('status') not in FINAL:raise RuntimeError('Recover pending PEQ transaction first')
   extended=Path(state)/'rme-control/transaction.json'
   if extended.exists() and load(extended).get('status') not in FINAL:raise RuntimeError('Recover pending extended RME transaction first')
   yield private

def check(before,after,output,desired):
 if before['usb_serial']!=after['usb_serial']:raise RuntimeError('Readback belongs to another RME')
 original=peq.indexes(before);actual=peq.indexes(after);expected=dict(original)
 changed={location(output,key):key for key in desired}
 for key,value in desired.items():expected[location(output,key)]=value
 if set(actual)!=set(original):raise RuntimeError('RME parameter inventory changed')
 differences=[{'address':a,'index':i,'expected':value,'actual':actual.get((a,i))} for (a,i),value in expected.items() if not (equivalent(changed[(a,i)],value,actual.get((a,i))) if (a,i) in changed else actual.get((a,i))==value)]
 if differences:raise RuntimeError('RME audio readback differs: '+str(differences))

def guard_values(reply,output,desired):
 values,caps=view(reply)
 if any(not caps[output]['fields'][key]['available'] for key in desired):raise RuntimeError('Selected RME control is unavailable or not safely restorable')
 if any(FIELDS[key][0]=='eq' for key in desired) and peq.indexes(reply).get((peq.OUTPUTS[output][0],28))!=0:raise RuntimeError('Select EQ Manual before editing tones; loaded preset writes are not qualified')

def result(reply,status,state,**extra):
 values,caps=view(reply);private=Path(state)/'rme-audio';j=load(private/'transaction.json') if (private/'transaction.json').exists() else {}
 return dict(status=status,current_audio=values,audio_capabilities=caps,revision=peq.revision(reply),
  pending_audio=None if not j or j.get('status') in FINAL else {'id':j['id'],'status':j['status'],'output':j['output']},
  last_audio_snapshot={o:(private/('last-'+o+'.json')).is_file() for o in peq.OUTPUTS},
  dsp_note='Toni, PEQ, Loudness e Crossfeed agiscono sul PCM. In DSD/DoP il DSP del DAC non elabora questi effetti.',**extra)

def read(state=STATE):return result(verified_read(),'rme-audio-read',state)

def pending(private):
 if (private/'transaction.json').exists() and load(private/'transaction.json').get('status') not in FINAL:raise RuntimeError('Recover pending RME audio transaction first')

def rollback(private,j):
 before=j['before'];current=verified_read();original=peq.indexes(before);actual=peq.indexes(current);output=j['output']
 if before['usb_serial']!=current['usb_serial']:raise RuntimeError('Original RME absent; restore deferred')
 desired={key:original[location(output,key)] for key in j['desired']}
 for key in desired:
  v=actual.get(location(output,key))
  if not any(equivalent(key,v,candidate) for candidate in [desired[key],j['desired'][key]]):raise RuntimeError('Concurrent audio edit detected; restore refused')
 # Preserve every unrelated parameter changed by the user since the snapshot.
 for key,raw in desired.items():
  if not equivalent(key,actual.get(location(output,key)),raw):send(output,key,raw,before['usb_serial'])
 after=verified_read();check(current,after,output,desired)
 j.update(status='rolled-back',restored_verified=True);atomic(private/('after-rollback-'+j['id']+'.json'),after);atomic(private/'transaction.json',j)
 return after

def watchdog(state,ident):
 if not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('Invalid audio transaction id')
 private=Path(state)/'rme-audio';path=private/'transaction.json';first=load(path)
 if first.get('id')!=ident:return
 atomic(private/('watchdog-ready-'+ident+'.json'),{'id':ident,'pid':os.getpid(),'start':start_ticks(os.getpid())})
 while True:
  j=load(path)
  if j.get('id')!=ident or j.get('status') in FINAL:return
  alive=peq.parent_alive(j);expired=time.time()>=j['deadline']
  if expired and alive:__import__('rme_guard').terminate_expired(path,ident);time.sleep(.2)
  if not alive or expired or j.get('status')=='rollback-incomplete':
   try:
    with operation(state) as directory:
     fresh=load(path)
     if fresh.get('id')!=ident or fresh.get('status') in FINAL:return
     try:rollback(directory,fresh)
     except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError) as error:
      pending=load(path)
      if pending.get('id')==ident and pending.get('status') not in FINAL:
       pending.update(status='rollback-incomplete',rollback_error=str(error));atomic(path,pending)
    return
   except (OSError,RuntimeError,ValueError,KeyError,subprocess.SubprocessError):return
  else:time.sleep(.5)

def spawn(state,ident):
 proc=subprocess.Popen([sys.executable,str(Path(__file__).parent.parent/'bin/dat'),'--state',str(state),'rme-audio','watchdog',ident],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,close_fds=True)
 ready=Path(state)/'rme-audio'/('watchdog-ready-'+ident+'.json');end=time.monotonic()+5
 while time.monotonic()<end and proc.poll() is None:
  if ready.exists():
   r=load(ready)
   if r.get('id')==ident and r.get('pid')==proc.pid and r.get('start')==start_ticks(proc.pid):return proc
  time.sleep(.05)
 proc.terminate()
 try:proc.wait(timeout=3)
 except subprocess.TimeoutExpired:proc.kill();proc.wait()
 raise RuntimeError('RME audio recovery guard did not arm; no SET sent')

def execute(private,state,before,output,values,restoring=False):
 guard_values(before,output,values);v=peq.indexes(before)
 desired={key:value for key,value in values.items() if not equivalent(key,value,v[location(output,key)])}
 if not desired:return result(before,'no-change',state,output=output)
 ident=uuid.uuid4().hex;j=dict(id=ident,status='armed',output=output,before=before,desired=desired,parent_pid=os.getpid(),parent_start=start_ticks(os.getpid()),boot_id=peq.current_boot(),deadline=time.time()+120,writes_attempted=[])
 atomic(private/('before-'+ident+'.json'),before);atomic(private/'transaction.json',j)
 try:worker=spawn(state,ident)
 except BaseException:
  j.update(status='rolled-back',no_set_sent=True,restored_verified=True);atomic(private/'transaction.json',j);raise
 try:
  j['status']='writing';atomic(private/'transaction.json',j)
  for key,raw in desired.items():
   j['writes_attempted'].append(key);atomic(private/'transaction.json',j);send(output,key,raw,before['usb_serial'])
  j['status']='verifying';atomic(private/'transaction.json',j)
  from rme_verify import verify
  expected=peq.indexes(before);owned={location(output,key):key for key in desired}
  for key,value in desired.items():expected[location(output,key)]=value
  def repeat(loc,value):
   key=owned[loc];j['writes_attempted'].append(key);atomic(private/'transaction.json',j);send(output,key,value,before['usb_serial'])
  after,verification=verify(before,expected,set(owned),verified_read,peq.indexes,lambda loc,a,b:equivalent(owned[loc],a,b),lambda reply:check(before,reply,output,desired),repeat)
  j['verification']=verification
  atomic(private/('after-'+ident+'.json'),after)
  if not restoring:atomic(private/('last-'+output+'.json'),dict(before=before,after=after,desired=desired,id=ident))
  j['status']='restored' if restoring else 'committed';atomic(private/'transaction.json',j)
  with contextlib.suppress(subprocess.TimeoutExpired):worker.wait(timeout=3)
  return result(after,'audio-restored-verified' if restoring else 'audio-applied-verified',state,output=output,readback_verified=True,other_parameters_unchanged=True,snapshot=ident,verification=verification)
 except BaseException as e:
  j['failure']=str(e)
  try:
   after=rollback(private,j)
   with contextlib.suppress(subprocess.TimeoutExpired):worker.wait(timeout=3)
   return result(after,'audio-failed-restored',state,reason=str(e),original_audio_restored=True)
  except BaseException as recovery:
   j.update(status='rollback-incomplete',rollback_error=str(recovery));atomic(private/'transaction.json',j)
   return dict(status='audio-rollback-incomplete',reason=str(e),recovery_error=str(recovery),original_audio_restored=False,pending_audio={'id':ident,'output':output,'status':'rollback-incomplete'})

def apply(patch,state=STATE):
 output,expected,values=validate(patch)
 with operation(state) as private:
  pending(private);before=verified_read()
  if peq.revision(before)!=expected:raise RuntimeError('RME changed since read; refresh before applying')
  return execute(private,state,before,output,values)

def restore(output,expected,state=STATE):
 peq.output_name(output)
 with operation(state) as private:
  pending(private);saved=load(private/('last-'+output+'.json'));before=verified_read()
  if saved['before']['usb_serial']!=before['usb_serial']:raise RuntimeError('Snapshot belongs to another RME')
  if peq.revision(before)!=expected:raise RuntimeError('RME changed since read; refresh before restoring')
  original=peq.indexes(saved['before']);return execute(private,state,before,output,{key:original[location(output,key)] for key in saved['desired']},True)

def recover(state=STATE):
 with operation(state) as private:
  j=load(private/'transaction.json')
  if j.get('status') in FINAL:return {'status':'no-pending-transaction'}
  if peq.parent_alive(j):raise RuntimeError('Audio transaction is still active')
  return result(rollback(private,j),'audio-recovered-verified',state,output=j['output'])
