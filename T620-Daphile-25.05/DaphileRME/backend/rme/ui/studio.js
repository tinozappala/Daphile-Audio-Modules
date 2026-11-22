'use strict';

// Only deliberate edits enter this queue. Readback, polling and recovered
// browser work never enqueue a DAC write. Each request owns its output/side.
class RMEAutoSync {
  constructor({write,valid=()=>true,failed=()=>{},paint=()=>{},delay=380}) {
    Object.assign(this,{write,valid,failed,paint,delay});
    this.pending=new Map();this.held=new Set();this.running=null;
    this.blocked=false;this.timer=null;this.retryKey=null;
  }
  get active(){return Boolean(this.running||this.pending.size);}
  edit(key,command,final=true,delay=this.delay){
    // A failed prerequisite stays ahead of dependent commands, including
    // when its newest edit is still being dragged after an explicit retry.
    if(key!==this.retryKey)this.pending.delete(key);
    this.pending.set(key,structuredClone(command));
    if(final)this.held.delete(key);else this.held.add(key);
    clearTimeout(this.timer);this.timer=null;this.paint();
    if(final&&!this.blocked)this.timer=setTimeout(()=>this.flush(),delay);
  }
  async flush(retry=false){
    clearTimeout(this.timer);this.timer=null;
    if(this.running)return this.running;
    if(retry)this.blocked=false;
    if(this.blocked||this.held.size||!this.valid()||!this.pending.size)return;
    const [key,command]=this.pending.entries().next().value;
    this.pending.delete(key);
    this.running=Promise.resolve().then(()=>this.write(structuredClone(command))).then(result=>{
      if(this.retryKey===key)this.retryKey=null;
      return result;
    }).catch(error=>{
      // Keep a newer edit if one arrived during the request. No automatic
      // retry, including an edit of another parameter after uncertain I/O.
      const retry=this.pending.has(key)?this.pending.get(key):command;
      this.pending=new Map([[key,retry],...Array.from(this.pending).filter(([pendingKey])=>pendingKey!==key)]);
      this.retryKey=key;
      this.blocked=true;this.failed(error);
    }).finally(()=>{
      this.running=null;this.paint();
      if(!this.blocked&&!this.held.size&&this.pending.size&&this.valid())
        this.timer=setTimeout(()=>this.flush(),0);
    });
    this.paint();return this.running;
  }
  clear(){
    if(this.running)throw Error('Attendi la verifica del DAC prima di rileggere.');
    clearTimeout(this.timer);this.timer=null;this.pending.clear();
    this.held.clear();this.blocked=false;this.retryKey=null;this.paint();
  }
  incomplete(key){
    // An unfinished number supersedes only its own unsent request.
    if(key===this.retryKey)return;
    this.pending.delete(key);this.held.delete(key);
    clearTimeout(this.timer);this.timer=null;
    if(this.pending.size&&!this.running&&!this.blocked&&!this.held.size)
      this.timer=setTimeout(()=>this.flush(),this.delay);
  }
}
globalThis.RMEAutoSync=RMEAutoSync;

const $=id=>document.getElementById(id),clone=v=>structuredClone(v),equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const OUTS=['line','phones','iem'],NAMES={line:'Line',phones:'Phones',iem:'IEM'};
const PAGES={eq:['Equalizzatore','Regola il suono e salva la curva nel DAC.'],levels:['Uscite e volume','Controlla il livello e configura l’uscita giusta.'],tones:['Toni e loudness','Il timbro e la compensazione per l’ascolto a basso volume.'],signal:['Stereo e percorso audio','Sorgenti, immagine stereo e conversione.'],setups:['Setup del DAC','Nove memorie per la configurazione completa del DAC.'],display:['Display e telecomando','Personalizza l’aspetto e le assegnazioni del DAC.'],device:['Dispositivo e stato','Il comportamento del DAC e le informazioni realmente lette.']};
let state=null,work=null,side='stereo',busy=false,editStore=null,graph=null;
let autoSync=null;
let currentPage='eq',dragging=false;
let stateGeneration=0,pollInFlight=false,sessionReady=false,verifyRequired=false,recoveryRequired=false,slowOperation=false;
const cache=new Map(),FREQUENCIES=Array.from({length:500},(_,i)=>20*1000**(i/499));
const out=()=>$('output').value;

function node(tag,attrs={},text){const n=document.createElement(tag);for(const [k,v]of Object.entries(attrs)){if(k==='class')n.className=v;else if(k.startsWith('aria-'))n.setAttribute(k,v);else n[k]=v;}if(text!==undefined)n.textContent=text;return n;}
function tell(message,error=false){$('message').textContent=message;$('message').classList.toggle('error',error);}
function validBands(rows){return Array.isArray(rows)&&rows.length===5&&rows.every((r,i)=>{const keys=['band','gain','frequency','q',...([0,4].includes(i)?['type']:[])];return r&&r.band===i+1&&Object.keys(r).length===keys.length&&keys.every(k=>typeof r[k]==='number'&&Number.isFinite(r[k]))&&r.gain>=-12&&r.gain<=12&&Number.isInteger(r.gain*2)&&r.frequency>=(i<3?20:200)&&r.frequency<=20000&&Number.isInteger(r.frequency)&& (r.frequency<=2040||r.frequency%10===0)&&r.q>=.5&&r.q<=(i<3?9.9:5)&&Math.abs(r.q*10-Math.round(r.q*10))<1e-9&&(!('type'in r)||(Number.isInteger(r.type)&&r.type>=0&&r.type<=(i===0?3:2)));});}
function validState(value){return Boolean(value&&typeof value.revision==='string'&&/^[0-9a-f]{64}$/.test(value.revision)&&typeof value.identity==='string'&&/^[0-9a-f]{64}$/.test(value.identity)&&value.capabilities?.outputs&&value.capabilities?.device&&OUTS.every(o=>value.outputs?.[o]&&value.current_eq?.[o]&&['left','right'].every(s=>validBands(value.current_eq[o][s]))));}
function editable(value){return {outputs:clone(value.outputs),device:clone(value.device),eq:clone(value.current_eq)};}
function mergeEdits(saved,prior,fresh,path='',conflicts=[]){
 if(saved===undefined||prior===undefined)return clone(fresh);
 if(Array.isArray(fresh))return fresh.map((v,i)=>mergeEdits(saved[i],prior[i],v,path+'.'+i,conflicts));
 if(fresh&&typeof fresh==='object')return Object.fromEntries(Object.entries(fresh).map(([k,v])=>[k,mergeEdits(saved?.[k],prior?.[k],v,path+'.'+k,conflicts)]));
 if(!equal(saved,prior)){if(!equal(fresh,prior)&&!equal(fresh,saved))conflicts.push(path);return clone(saved);}
 return clone(fresh);
}
function remember(){if(!state||!editStore)return;if(equal(work,editable(state)))editStore.remove('workspace');else editStore.write('workspace',{identity:state.identity,work,baseline:editable(state)});}
function adopt(next,preserve=true,paint=true){
 if(!validState(next))throw Error('La lettura del DAC è incompleta. Nessuna impostazione viene applicata.');
 const fresh=editable(next),conflicts=[];
 if(state&&state.identity===next.identity&&preserve)work=mergeEdits(work,editable(state),fresh,'',conflicts);
 else{work=fresh;editStore=new EqEditStore('daphile:rme:studio:v2:'+next.identity);const saved=preserve?editStore.read('workspace'):null;if(saved?.identity===next.identity&&saved.work&&saved.baseline)work=mergeEdits(saved.work,saved.baseline,fresh,'',conflicts);}
 state=clone(next);recoveryRequired=Boolean(next.pending);stateGeneration++;remember();if(paint)render();return conflicts;
}
async function api(path,body){
 const reading=body===undefined||['api/rme/snapshot','api/rme/memory/read'].includes(path);
 const controller=new AbortController();
 const timer=setTimeout(()=>controller.abort(),body===undefined?5000:reading?20000:30000);
 let r,value;
 try{
  r=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
  if(r.status===401)sessionReady=false;
  if(r.status===409)throw Error('busy');
  value=await r.json();
 }catch(error){
  if(r?.status===409)throw Error('Il DAC è occupato. Riprova tra poco.');
  if(!reading){
   verifyRequired=true;
   throw Error('Risposta interrotta. Usa Rileggi DAC per verificare l’esito prima di riprovare.');
  }
  throw Error('La lettura non è riuscita. Puoi riprovare con Rileggi DAC.');
 }finally{clearTimeout(timer);}
 if(!r.ok){
  const error=Error(value.error||'Operazione non disponibile.');
  if(r.status===412&&value.settings_write_commands_sent===0&&validState(value.state))error.freshState=value.state;
  throw error;
 }
 return value;
}
async function ensureSession(){
 if(sessionReady)return;
 const session=await api('api/session');
 if(!session.authenticated)throw Error('Controller RME non disponibile.');
 sessionReady=true;
}
function gate(){
 for(const n of document.querySelectorAll('button,input,select')){
  if(n.matches('[data-view],#zoom,#scale,#show-bands'))continue;
  if(n.id==='read'||n.matches('#recover-main,#recover-peq,#recover-audio')){
   n.disabled=busy||Boolean(autoSync?.running)||(n.id!=='read'&&!recoveryRequired);continue;
  }
  n.disabled=busy||!state||verifyRequired||recoveryRequired||n.dataset.unavailable==='true'||
    Boolean(autoSync?.active&&n.matches('button,#output,#hardware-slot,#store-slot,[data-side],#chip')&&n.id!=='retry-sync');
 }
}
function paintStatus(){
 $('retry-sync').hidden=verifyRequired||recoveryRequired||!autoSync?.blocked;
 $('recover-main').hidden=!recoveryRequired;
 let message='';
 if(busy)message=slowOperation?'In attesa del DAC. Puoi continuare a navigare.':'Comunicazione con il DAC…';
 else if(recoveryRequired)message='Operazione RME in attesa di recupero';
 else if(verifyRequired)message='Usa Rileggi DAC per verificare l’esito.';
 else if(autoSync?.blocked)message='Modifiche non confermate';
 else if(autoSync?.active)message='Aggiornamento DAC…';
 $('sync-state').textContent=message;
}
async function run(action){
 if(busy||autoSync?.running)return;
 busy=true;stateGeneration++;gate();paintStatus();
 const slow=setTimeout(()=>{slowOperation=true;paintStatus();},4000);
 try{return await action();}
 catch(e){if(e.freshState)adopt(e.freshState);tell(e.message||'Operazione non riuscita.',true);}
 finally{clearTimeout(slow);busy=false;slowOperation=false;gate();paintStatus();draw();}
}
async function refresh(preserve=true){const next=await api('api/rme/snapshot',{}),conflicts=adopt(next,preserve);$('connection').textContent='DAC letto · '+new Date(next.read_time*1000).toLocaleTimeString('it-IT');return conflicts;}
async function fresh(){const conflicts=await refresh();if(conflicts.length)throw Error('Alcuni parametri sono cambiati dal DAC o da un altro controller. Rileggi il DAC prima di riprovare.');}
function requireResult(result){if(!['applied-verified','restored-verified','no-change'].includes(result.status)||result.readback_verified!==true||!validState(result.state)){if(result.status==='rollback-incomplete')recoveryRequired=true;throw Error(result.status==='failed-restored'?'Applicazione non riuscita. I valori precedenti sono stati ripristinati; le tue regolazioni restano disponibili.':result.status==='rollback-incomplete'?'Operazione non verificata. Usa Recupera operazione interrotta prima di applicare altre modifiche.':'Il DAC non ha confermato la modifica. Rileggi il dispositivo prima di riprovare.');}return result.state;}
function validInputs(){return !busy&&!verifyRequired&&!recoveryRequired&&Boolean(state);}
function syncPaint(){
 if(!autoSync)return;
 paintStatus();gate();
 if(state&&!autoSync.active&&!dragging&&!document.activeElement?.matches('input[type=number],input[type=range],input[type=text]'))render();
}
function queueValue(key,value,final=true,delay=380){
 if(!autoSync.blocked)tell('');
 const output=out();setValue(key,value,output);
 autoSync.edit('value:'+output+':'+key,{kind:'value',identity:state.identity,output,key,value},final,delay);
}
function queueEq(final=true,enabled){
 if(!autoSync.blocked)tell('');
 const output=out(),request={kind:'eq',identity:state.identity,output,side:enabled===undefined?side:'stereo'};
 if(enabled===undefined)request.bands=clone(work.eq[output][bandSide()]);else request.enabled=enabled;
 autoSync.edit('eq:'+output+':'+(enabled===undefined?side:'enabled'),request,final,enabled===undefined?380:0);
}
async function writeAutomatic(request){
 if(!state||request.identity!==state.identity)throw Error('Il DAC è cambiato. Rileggi il dispositivo.');
 if(!validInputs())throw Error('Completa i valori numerici prima di proseguire.');
 // A read already in transit predates this operation, even if the write
 // fails without returning a new state or finishes before that read arrives.
 stateGeneration++;
  const patch={output:request.output,expected:state.revision};let path;
  if(request.kind==='value'){
   if(valueFor(request.key,request.output,editable(state))===request.value)return;
   path='api/rme/control/apply';patch.values={[request.key]:request.value};
  }else if(request.kind==='eq'){
   const actual=state.current_eq[request.output],channels=request.side==='stereo'?['left','right']:[request.side];
   if(request.enabled!==undefined&&Boolean(actual.eq_enabled)===request.enabled&&Boolean(actual.eq_enabled_right)===request.enabled)return;
   if(request.bands&&channels.every(s=>equal(actual[s],request.bands)))return;
   path='api/rme/eq/apply';patch.side=request.side;patch.bands=clone(request.bands||[]);
   if(request.enabled!==undefined)patch.enabled=request.enabled;
  }else throw Error('Modifica non riconosciuta.');
  const workAtSend=clone(work);
  const next=requireResult(await api(path,{confirm:true,patch}));
  // Acknowledgement belongs only to this submitted value. Preserve edits
  // made while the DAC was being read or written, including a later drag.
  if(request.kind==='value'&&equal(valueFor(request.key,request.output),request.value)){
   const target=Object.hasOwn(next.device,request.key)?work.device:work.outputs[request.output];
   target[request.key]=valueFor(request.key,request.output,editable(next));
  }
  if(request.kind==='eq'){
   for(const s of request.side==='stereo'?['left','right']:[request.side])
    if(request.bands&&equal(work.eq[request.output][s],request.bands))work.eq[request.output][s]=clone(next.current_eq[request.output][s]);
   if(request.enabled!==undefined&&Boolean(work.eq[request.output].eq_enabled)===request.enabled&&Boolean(work.eq[request.output].eq_enabled_right)===request.enabled){work.eq[request.output].eq_enabled=next.current_eq[request.output].eq_enabled;work.eq[request.output].eq_enabled_right=next.current_eq[request.output].eq_enabled_right;}
  }
  const workAtReply=clone(work);
  adopt(next,true,false);
  // A newer edit may deliberately return to the old DAC baseline. A merge
  // against that baseline alone would mistake it for an untouched field.
  work=mergeEdits(workAtReply,workAtSend,work);remember();
  renderHardware();renderStatus();renderDisplay();draw();
}
function capFor(key,o=out()){return state.capabilities.outputs[o][key]||state.capabilities.device[key];}
function valueFor(key,o=out(),target=work){return Object.hasOwn(target.device,key)?target.device[key]:target.outputs[o][key];}
// Nominal level for a full-scale PCM sine, derived from the verified MIDI
// volume and analog reference. Never a meter, a gain command, or an estimate
// of the playing track. Dim is already reflected in the returned volume.
function levelReadout(output,values,device={}){
 if(!OUTS.includes(output)||!values||device.dsd_direct!==false||!Number.isFinite(values.volume)||values.volume<-114.5||values.volume>6)return null;
 const refs=output==='line'?[-5,1,7,13]:output==='phones'?[7,22]:[-3];
 if(output!=='iem'&&(!Number.isInteger(values.reference_level)||values.reference_level<0||values.reference_level>=refs.length))return null;
 const reference=refs[output==='iem'?0:values.reference_level],maximum=refs[refs.length-1],round=v=>Math.round(v*10)/10;
 return {digital:values.volume,relative:round(values.volume+reference-maximum),reference,
  outputs:(output==='line'?[['RCA',reference],['XLR',reference+6]]:[[NAMES[output],reference]])
   .map(([name,ref])=>({name,reference:ref,dbu:round(ref+values.volume)}))};
}
const LEVEL_HELP={
 volume:['Volume','Regolazione digitale in dB. Il livello analogico dipende anche da Ref Level.'],
 reference_level:['Ref Level','Riferimento analogico a 0 dBFS. Per sceglierlo manualmente disattiva Auto Ref Level.'],
 auto_reference:['Auto Ref Level','Il DAC sceglie il riferimento analogico e compensa il volume digitale. Non è un volume aggiuntivo.'],
 volume_lock:['Lock Volume','Blocca la manopola fisica. Il plugin rispetta il blocco e richiede di disattivarlo prima di regolare il volume.'],
 mute:['Mute','Silenzia l’uscita. Il plugin non toglie Mute implicitamente regolando il volume.'],
 dim:['Dim','Riduzione di 20 dB gestita dal DAC; il valore restituito comprende già la variazione.']
};
const HARDWARE_LABELS={reference_level:'Ref Level · riferimento analogico',auto_reference:'Auto Ref Level · automatico',mid_side:'M/S Proc · Mid/Side',stereo_width:'Width · ampiezza stereo',load_volume:'Carica anche il volume'};
function setValue(key,value,o=out()){const target=Object.hasOwn(work.device,key)?work.device:work.outputs[o];target[key]=value;remember();}
async function immediate(key,value){queueValue(key,value,true,0);return autoSync.flush();}
function fieldControl(key,cap){
  const row=node('div',{class:'field-control'+(!cap.writable?' unavailable':'')}),id='field-'+key,labelText=HARDWARE_LABELS[key]||cap.label,label=node('label',{htmlFor:id},labelText+(cap.unit?' · '+cap.unit:''));row.append(label);
 const value=valueFor(key);let input;
 if(cap.kind==='boolean'){input=node('input',{id,type:'checkbox',checked:Boolean(value)});}
 else if(cap.options){input=node('select',{id});cap.options.forEach((name,i)=>input.add(new Option(name,String(i))));if(value===undefined)input.add(new Option('Non disponibile','unknown'));input.value=value===undefined?'unknown':String(value);}
 else input=node('input',{id,type:'number',value:value===undefined?'':value,min:cap.minimum,max:cap.maximum,step:cap.step});
  input.dataset.unavailable=String(!cap.writable);input.setAttribute('aria-label',labelText);input.disabled=!cap.writable||busy;row.append(input);
  if(key==='reference_level'&&work.outputs[out()].auto_reference)input.dataset.unavailable='true';
 input.onchange=()=>{if(input.type==='checkbox'){immediate(key,input.checked);return;}if(input.value===''||!input.reportValidity())return;queueValue(key,Number(input.value));if(key==='display_mode'||key==='meter_color')renderDisplay();};
 if(input.type==='number')input.oninput=()=>{if(input.value!==''&&input.checkValidity())queueValue(key,Number(input.value));else autoSync.incomplete('value:'+out()+':'+key);};
 if(['volume','balance','stereo_width'].includes(key)&&value!==undefined){
  const range=node('input',{type:'range',min:cap.minimum,max:cap.maximum,step:cap.step,value}),container=node('div',{class:'range-control'}),number=node('span',{},Number(value).toFixed(key==='volume'?1:2));
  range.dataset.unavailable=String(!cap.writable||(key==='volume'&&(work.outputs[out()].volume_lock||work.outputs[out()].mute||work.outputs[out()].dim)));
  if(key==='volume'&&range.dataset.unavailable==='true')input.dataset.unavailable='true';
  if(key==='volume'&&range.dataset.unavailable==='true')row.append(node('small',{},'Per regolare il volume disattiva esplicitamente Blocco volume, Mute o Dim.'));
  range.setAttribute('aria-label',cap.label+' · cursore');range.oninput=()=>{input.value=range.value;number.textContent=Number(range.value).toFixed(key==='volume'?1:2);queueValue(key,Number(range.value),false);};
  range.onchange=()=>queueValue(key,Number(range.value),true,0);
  if(key==='volume')range.onpointerdown=event=>{const bounds=range.getBoundingClientRect(),ratio=(Number(range.value)-cap.minimum)/(cap.maximum-cap.minimum),thumb=bounds.left+ratio*bounds.width;if(Math.abs(event.clientX-thumb)>22){event.preventDefault();tell('Per una regolazione graduale trascina il cursore del volume oppure usa il campo numerico.');}};
  container.append(range,number);row.append(container);
 }
 if(!cap.writable)row.append(node('small',{},cap.reason||(cap.read_only?'Informazione in sola lettura.':'Controllo non disponibile.')));
 const actual=valueFor(key,out(),editable(state));if(actual!==undefined&&value!==actual)row.append(node('span',{class:'field-readback'},'Letto dal DAC: '+(cap.options?cap.options[actual]??actual:typeof actual==='boolean'?(actual?'ON':'OFF'):actual)));
  if(LEVEL_HELP[key])row.append(node('small',{class:'hardware-field-help'},LEVEL_HELP[key][0]+' · '+LEVEL_HELP[key][1]));
  if(key==='mid_side')row.append(node('small',{},'M/S Proc separa il contenuto centrale (Mid, a sinistra) da quello laterale (Side, a destra). Non è un controllo di volume.'));
  if(key==='stereo_width')row.append(node('small',{},'Width: 1 = stereo originale; 0 = mono; −1 = canali scambiati.'));
  return row;
}
function renderLevelFields(){
 const box=$('levels-fields');box.replaceChildren();
 for(const [title,kind,keys]of [['Volume digitale','digital',['volume']],['Riferimento analogico','reference',['auto_reference','reference_level']],['Blocco e silenziamento','protection',['volume_lock','mute','dim']]]){
  const group=node('section',{class:'level-group level-group-'+kind});group.append(node('h3',{},title));
  for(const key of keys){const cap=capFor(key);if(cap)group.append(fieldControl(key,cap));}
  box.append(group);
 }
 const summary=$('level-readout');summary.replaceChildren();
 const ref=state.outputs[out()].loudness_reference;
 $('level-loudness-reference').textContent=Number.isFinite(ref)?ref.toFixed(1)+' dB':'Non disponibile';
 const value=levelReadout(out(),state.outputs[out()],state.device);
 if(!value){summary.append(node('p',{class:'model-note'},state.device.dsd_direct?'DSD Direct: il volume digitale non descrive il livello analogico.':'Riferimento o volume non disponibili: livelli derivati non mostrati.'));return;}
 const signed=v=>(v>0?'+':'')+v.toFixed(1),items=[['Volume relativo',signed(value.relative)+' dBr'],...value.outputs.map(v=>[v.name+' · nominale a 0 dBFS',signed(v.dbu)+' dBu'])];
 for(const [label,text]of items){const card=node('div',{class:'level-reading'});card.append(node('small',{},label),node('strong',{},text));summary.append(card);}
}
function renderFields(){
 for(const section of ['levels','tones','loudness','stereo','signal','memory','display','remote','device','setup']){
  if(section==='levels'){renderLevelFields();continue;}
  const box=$(section+'-fields');box.replaceChildren();
  for(const [key,cap]of Object.entries({...state.capabilities.outputs[out()],...state.capabilities.device}))if(cap.section===section&&key!=='eq_preset')box.append(fieldControl(key,cap));
 }
 $('dual-eq').checked=Boolean(state.outputs[out()].dual_eq);$('dual-eq').dataset.unavailable=String(!capFor('dual_eq').writable);
}
function bandSide(){return side==='right'?'right':'left';}
function bandEditable(){if(!state||verifyRequired||recoveryRequired)return false;const eq=state.current_eq[out()],preset=state.outputs[out()].eq_preset;if(!Number.isInteger(preset)||preset<0||preset>21)return false;if(side!=='stereo'&&!eq.dual_eq)return false;if(side==='stereo'&&eq.dual_eq&&!equal(work.eq[out()].left,work.eq[out()].right))return false;return true;}
function bandChange(id,patch,final=true){
 if(busy||!bandEditable())return;const channels=side==='stereo'?['left','right']:[side];
 for(const s of channels){const b=work.eq[out()][s].find(b=>b.band===id);if(patch.frequency!==undefined){let f=Math.max(id<=3?20:200,Math.min(20000,patch.frequency));b.frequency=f>2040?Math.round(f/10)*10:Math.round(f);}if(patch.gain!==undefined)b.gain=Math.max(-12,Math.min(12,Math.round(patch.gain*2)/2));if(patch.q!==undefined)b.q=Number(Math.max(.5,Math.min(id<=3?9.9:5,patch.q)).toFixed(1));if(patch.type!==undefined)b.type=patch.type;}
 for(const input of $('bands').querySelectorAll(`[data-band="${id}"] [data-field]`)){input.value=work.eq[out()][bandSide()].find(b=>b.band===id)[input.dataset.field];if(input.dataset.field==='frequency')input.step=Number(input.value)>2040?10:1;}remember();draw();queueEq(final);
}
function filterType(b){if(!b.type)return 'Peaking';if(b.type===1)return b.band===1?'Lowshelf':'Highshelf';return b.band===1&&b.type===2?'Highpass':'Lowpass';}
function curve(rows){
 const each=rows.map(b=>{const key=JSON.stringify(b);if(!cache.has(key)){const coeff=eqCoefficients(filterType(b),b.frequency,b.gain,b.q,48000);cache.set(key,FREQUENCIES.map(f=>eqResponse(coeff,f,48000)));if(cache.size>70)cache.delete(cache.keys().next().value);}return cache.get(key);});
 return {each,total:FREQUENCIES.map((_,i)=>each.reduce((sum,c)=>sum+c[i],0))};
}
function draw(){
 if(!state||!graph)return;const o=out(),s=bandSide(),pending=work.eq[o],actual=state.current_eq[o],curves=[];
 for(const [name,color]of [['left','#70dfca'],['right','#be9bff']]){
  const enabled=Boolean(name==='right'?actual.eq_enabled_right:actual.eq_enabled);
  const current=curve(pending[name]),readback=curve(actual[name]);
  if(name===s&&$('show-bands').checked)current.each.forEach((values,i)=>curves.push({frequency:FREQUENCIES,values,color:EqGraph.colors[i],opacity:.4,width:1.2}));
  curves.push({frequency:FREQUENCIES,values:readback.total,color:'#a4b3c8',dashed:true,opacity:.5,width:1.2},{frequency:FREQUENCIES,values:current.total,color,dashed:!enabled,width:name===s?2.8:1.8,opacity:name===s?1:.7});
 }
 const [lo,hi]=$('zoom').value.split(',').map(Number),[minDb,maxDb]=$('scale').value.split(',').map(Number);
 graph.set({lo,hi,minDb,maxDb,curves,bands:pending[s].map((b,i)=>({id:b.band,label:String(b.band),frequency:b.frequency,gain:filterType(b).endsWith('pass')?0:b.gain,q:b.q,color:EqGraph.colors[i],editable:!busy&&bandEditable(),gainEditable:!filterType(b).endsWith('pass'),gainStep:.5,qStep:.1}))});
}
function renderBands(){
 const box=$('bands');box.replaceChildren();
 for(const b of work.eq[out()][bandSide()]){
  const row=node('tr');row.dataset.band=b.band;row.append(node('td',{},b.band));const cell=node('td');
  if(b.band===1||b.band===5){const select=node('select');(b.band===1?['Peak','Low Shelf','High Pass','Low Pass']:['Peak','High Shelf','Low Pass']).forEach((name,i)=>select.add(new Option(name,String(i))));select.value=b.type;select.dataset.field='type';select.dataset.unavailable=String(!bandEditable());select.onchange=()=>{bandChange(b.band,{type:Number(select.value)});renderBands();gate();};cell.append(select);}else cell.textContent='Peak';row.append(cell);
  for(const [key,min,max,step]of [['frequency',b.band<=3?20:200,20000,b.frequency>2040?10:1],['gain',-12,12,.5],['q',.5,b.band<=3?9.9:5,.1]]){
   const td=node('td'),input=node('input',{type:'number',value:b[key],min,max,step});input.dataset.field=key;input.dataset.unavailable=String(!bandEditable()||(key==='gain'&&filterType(b).endsWith('pass')));input.setAttribute('aria-label',key+' banda '+b.band);input.oninput=input.onchange=()=>{if(key==='frequency')input.step=Number(input.value)>2040?10:1;if(input.value!==''&&input.checkValidity())bandChange(b.band,{[key]:Number(input.value)});else autoSync.incomplete('eq:'+out()+':'+side);};td.append(input);row.append(td);
  }box.append(row);
 }
 const actual=state.current_eq[out()],split=actual.eq_enabled!==actual.eq_enabled_right;
 const power=$('eq-enabled'), enabled=Boolean(actual.eq_enabled);
 power.setAttribute('aria-checked',String(enabled&&!split));
 power.dataset.unavailable=String(split);
 power.textContent=split?'EQ diverso sui due canali':enabled?'Disattiva EQ':'Attiva EQ';
 $('eq-power-status').textContent=split?'Sinistra e destra hanno stati diversi.':enabled?'EQ attivo su '+NAMES[out()]+'.':'EQ spento su '+NAMES[out()]+'.';
 $('eligibility').textContent=!bandEditable()?'Per modificare un solo lato attiva EQ separato. Per collegare curve diverse usa esplicitamente Copia L→R o Copia R→L.':state.outputs[out()].eq_preset!==0?'Modifica la curva in ascolto. Per conservarla scegli Salva EQ nel DAC.':(side==='stereo'?'Regolazione collegata dei due canali.':'Regolazione del solo canale '+(side==='left'?'sinistro.':'destro.'));
 document.querySelectorAll('[data-side]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.side===side)));
}
async function restore(){return run(async()=>{await fresh();const next=requireResult(await api('api/rme/control/restore',{confirm:true,output:out(),expected:state.revision}));adopt(next,false);tell('Ultima modifica annullata e ripristino verificato.');});}
async function recover(){return run(async()=>{await ensureSession();await api('api/rme/control/recover',{confirm:true});await refresh(false);verifyRequired=false;autoSync.clear();render();renderHardware();tell('Esito del recupero riletto dal DAC.');});}

function renderStatus(){
 const o=out(),eq=state.current_eq[o];$('actual-eq').textContent=NAMES[o]+' · EQ '+(eq.eq_enabled?'ON':'OFF')+(eq.eq_enabled!==eq.eq_enabled_right?' · L/R differenti':'');
 $('output-cards').replaceChildren(...OUTS.map(output=>{const b=node('button',{class:'output-card'+(output===o?' selected':'')});b.dataset.localView='true';b.dataset.output=output;b.append(node('span',{class:'rme-icon','aria-hidden':'true'},{line:'🔊',phones:'🎧',iem:'🎵'}[output]),node('small',{},NAMES[output]),node('strong',{},state.outputs[output].volume===undefined?'Non disponibile':state.outputs[output].volume.toFixed(1)+' dB'),node('span',{},output==='iem'?'IEM · riferimento fisso −3 dBu':state.outputs[output].mute?'Mute attivo':state.outputs[output].dim?'Dim attivo':'Livello letto dal DAC'));b.onclick=()=>{if(busy||autoSync?.active)return;$('output').value=output;render();};return b;}));
 const vol=state.outputs[o].volume,ref=work.outputs[o].loudness_reference;
 $('loudness-state').textContent=typeof vol==='number'&&typeof ref==='number'?'Volume letto '+vol.toFixed(1)+' dB · riferimento '+ref.toFixed(1)+' dB. '+(vol>=ref+20?'A questo volume la compensazione loudness è nulla.':vol<=ref?'A questo volume la compensazione è massima.':'La compensazione diminuisce aumentando il volume.'):'Relazione loudness / volume non disponibile.';
 $('signal-path').replaceChildren();for(const [i,[title,value]]of [['INGRESSO',capFor('source').options[state.outputs[o].source]||'Non disponibile'],['ELABORAZIONE','EQ '+(eq.eq_enabled?'ON':'OFF')+' · toni '+(state.outputs[o].tone_enabled?'ON':'OFF')],['USCITA',NAMES[o]]].entries()){if(i)$('signal-path').append(node('span',{class:'path-arrow','aria-hidden':'true'},'→'));const n=node('div',{class:'path-node'});n.append(node('span',{class:'rme-icon','aria-hidden':'true'},['🔌','∿','🔊'][i]),node('small',{},title),node('strong',{},value));$('signal-path').append(n);}
 const status=$('device-status');status.replaceChildren();
 for(const [label,value]of [['Connessione','DAC letto e identificato'],['Clock',state.capabilities.device.clock_source.options[state.device.clock_source]||'Non disponibile'],['Frequenza',state.capabilities.device.sample_rate.options[state.device.sample_rate]||'Non disponibile'],['Formato','Non disponibile'],['Uscita visualizzata',NAMES[o]],['Ultima lettura',new Date(state.read_time*1000).toLocaleTimeString('it-IT')]]){const n=node('div',{class:'status-item'});n.append(node('small',{},label),node('strong',{},value));status.append(n);}
 const st=state.status_messages?.[0],diagnostics=$('diagnostics');
 if(st)diagnostics.textContent='Revisione protocollo letta: '+st.protocol_revision+'. Codice revisione hardware: '+st.device_revision_bits+'. I campi IOStatus non documentati non vengono interpretati.';
 else if(diagnostics.dataset.identity!==state.identity)diagnostics.textContent='Stato dettagliato non disponibile.';
 diagnostics.dataset.identity=state.identity;
 $('chip').value=state.context.chip;
}
function renderDisplay(){const p=$('display-preview');p.classList.toggle('light',work.device.display_mode===0);p.style.setProperty('--meter',['#70dfca','#70dfff','#f5ba70','#b6c3cf','#fa8f9f','#f4a25e'][work.device.meter_color]||'#70dfca');}
function render(){if(!state){gate();return;}renderBands();renderFields();renderStatus();renderDisplay();draw();gate();paintStatus();}
function switchPage(page){if(!PAGES[page])return;currentPage=page;document.documentElement.dataset.rmePage=page;document.querySelectorAll('[data-page]').forEach(n=>n.hidden=n.dataset.page!==page);document.querySelectorAll('[data-view]').forEach(n=>{if(n.dataset.view===page)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});$('page-title').textContent=PAGES[page][0];$('page-subtitle').textContent=PAGES[page][1];if(page==='eq')draw();}

function presetName(slot){
 return state.hardware_names?.find(entry=>entry.identifier===slot)?.name||'Preset '+slot;
}

function presetOptions(){
 return Array.from({length:20},(_,index)=>{
  const slot=index+1,name=presetName(slot);
  return new Option(name==='Preset '+slot?name:slot+' · '+name,String(slot));
 });
}

function renderHardware(){
 const recall=$('hardware-slot'),destination=$('store-slot'),oldDestination=destination.value;
 const raw=state.outputs[out()].eq_preset;
 const special=({1:'Temp',22:'Clear'})[raw];
 recall.replaceChildren(new Option('Manual','manual'),...presetOptions());
 if(special||raw<0||raw>22){
  const current=new Option(special||'Preset in uso','');
  current.disabled=true;
  recall.prepend(current);
 }
 recall.value=raw===0?'manual':raw>=2&&raw<=21?String(raw-1):'';
 const destinations=presetOptions();
 destinations.forEach((option,index)=>{
  if((state.protected_eq_slots||[]).includes(index+1)){option.disabled=true;option.textContent+=' · protetto';}
 });
 destination.replaceChildren(new Option('Scegli memoria',''),...destinations);
 destination.value=oldDestination;
 const active=raw===0?'Manual':special||presetName(raw-1);
 $('active-hardware').textContent=NAMES[out()]+' · '+active;
 const names=new Map((state.hardware_names||[]).map(row=>[row.identifier,row.name]));
 $('setup-names').replaceChildren(...Array.from({length:9},(_,index)=>{
  const card=node('div');
  const slot=index+1, name=names.get(33+index)||'Setup '+slot;
  const load=node('button',{type:'button'},'Richiama'), save=node('button',{type:'button'},'Salva');
  load.onclick=()=>setupAction(slot,'load',name);
  save.onclick=()=>setupAction(slot,'save',name);
  const rename=node('button',{type:'button'},'Rinomina');
  rename.onclick=()=>renameSetup(slot,name);
  card.append(node('small',{},'Setup '+slot),node('strong',{},name),load,save,rename);
  return card;
 }));
 paintStore();
}

async function renameSetup(slot,previous){
 const name=prompt('Nome del Setup (1–14 caratteri, senza accenti)',previous);
 if(name===null||name.trim()===previous)return;
 await run(async()=>{
  const result=await api('api/rme/setup/rename',{confirm:true,slot,name:name.trim(),previous,expected:state.revision});
  adopt(result.state,false);
  renderHardware();
  tell('Nome «'+result.name+'» verificato sul DAC.');
 });
}

async function setupAction(slot,action,name){
 if(!confirm(action==='save'?'Sostituire il contenuto di «'+name+'» con le impostazioni attuali?':'Richiamare «'+name+'» sul DAC?'))return;
 await run(async()=>{
  const result=await api('api/rme/setup/apply',{confirm:true,slot,action,expected:state.revision});
  adopt(result.state,false);
  renderHardware();
  tell(action==='save'?'Comando di salvataggio inviato al DAC.':'Setup richiamato e DAC riletto.');
 });
}

function paintStore(){
 $('store-hardware').dataset.unavailable=String(!$('store-slot').value||!$('preset-name').value.trim());
 gate();
}

async function activateBank(){
 const selected=$('hardware-slot').value;
 if(!selected)return;
 const slot=selected==='manual'?selected:Number(selected),output=out();
 await run(async()=>{
  tell('Applicazione sul DAC…');
  const result=await api('api/rme/preset/apply',{confirm:true,patch:{output,expected:state.revision,slot}});
  const next=requireResult(result);
  const previous=editable(state);
  work.eq[output]=previous.eq[output];
  for(const [key,value] of Object.entries(next.outputs[output]))
   if(!equal(value,state.outputs[output][key]))work.outputs[output][key]=state.outputs[output][key];
  adopt(next);
  if(!next.current_eq[output].dual_eq&&side!=='stereo'){
   side='stereo';
   render();
  }
  tell('EQ applicato e verificato sul DAC.');
 });
 renderHardware();
}

async function storeBank(){
 return run(async()=>{
  const slot=Number($('store-slot').value),output=out(),expected=state.revision;
  const name=$('preset-name').value.trim();
  if(!Number.isInteger(slot)||slot<1||slot>20||!name)return;
  const target=await api('api/rme/memory/read',{slot});
  if(target.slot!==slot||target.read_only!==true||!/^[0-9a-f]{64}$/.test(target.fingerprint))
   throw Error('Impossibile verificare la memoria scelta.');
  const replace=!target.empty;
  if(replace&&!confirm('Sostituire «'+target.name+'» nella memoria '+slot+'?'))return;
  tell('Salvataggio nel DAC…');
  const result=await api('api/rme/memory/store',{confirm:true,patch:{output,expected,slot,fingerprint:target.fingerprint,replace,name}});
  if(result.readback_verified!==true||result.memory?.slot!==slot||result.memory.hardware_name!==name||
     !['stored-verified','no-change'].includes(result.status))
   throw Error('Il DAC non ha confermato il nome e la curva.');
  state.hardware_names=[...(state.hardware_names||[]).filter(entry=>entry.identifier!==slot),{identifier:slot,name}];
  renderHardware();
  tell('«'+name+'» verificato nella memoria '+slot+'.');
 });
}

function pollingAllowed(){
 return !busy&&!verifyRequired&&!autoSync?.active&&!dragging&&document.visibilityState!=='hidden'&&
        !document.activeElement?.matches('input,select');
}
async function poll(){
 if(pollInFlight||!pollingAllowed())return;
 const generation=stateGeneration;pollInFlight=true;
 try{
  await ensureSession();
  if(generation!==stateGeneration||!pollingAllowed())return;
  const value=await api('api/rme/state');
  // Never repaint from an older request after a read/write, or while the
  // user has started interacting with controls since this request began.
  if(generation!==stateGeneration||!pollingAllowed())return;
  if(validState(value.state)){
   const next=value.state;
   if(!state||next.revision!==state.revision||!equal(next.pending,state.pending)||!equal(next.hardware_names,state.hardware_names)||
      (!state.status_messages?.length&&next.status_messages?.length)){
    adopt(next);renderHardware();
   }
   $('connection').textContent=(value.error?'Ultima lettura DAC · ':'DAC letto · ')+new Date(next.read_time*1000).toLocaleTimeString('it-IT');
  }else if(!state){
   $('connection').textContent=value.error||value.state?'DAC da rileggere':'Prima lettura del DAC…';
  }
 }catch{
  if(generation===stateGeneration&&!state)$('connection').textContent='Connessione RME da verificare';
 }
 finally{pollInFlight=false;}
}
async function watch(){
 await poll();
 setTimeout(watch,state?5000:1000);
}
async function boot(){
 autoSync=new RMEAutoSync({write:writeAutomatic,valid:validInputs,paint:syncPaint,failed:error=>{if(error.freshState)adopt(error.freshState,true,false);tell(error.message||'Il DAC non ha confermato la modifica. Rileggi o riprova.',true);}});
 graph=new EqGraph($('graph'),{change:bandChange,select:id=>{$('bands').querySelectorAll('tr').forEach(n=>n.classList.toggle('selected-band',Number(n.dataset.band)===id));},inspect:p=>$('graph-inspect').textContent=Math.round(p.frequency)+' Hz · '+p.gain.toFixed(1)+' dB | Trascina · rotella per Q'});
 $('graph').addEventListener('pointerdown',()=>{dragging=true;});for(const event of ['pointerup','pointercancel'])window.addEventListener(event,()=>{dragging=false;});
 document.querySelectorAll('[data-view]').forEach(n=>n.onclick=()=>switchPage(n.dataset.view));
 document.querySelectorAll('[data-side]').forEach(n=>n.onclick=()=>{side=n.dataset.side;renderBands();draw();gate();});
 $('output').onchange=()=>{render();renderHardware();};
 $('read').onclick=()=>run(async()=>{
  await ensureSession();
  await refresh(false);
  verifyRequired=false;
  autoSync.clear();
  render();renderHardware();
  tell('Lettura verificata. Nessuna modifica inviata.');
 });
 $('open-volume-loudness').onclick=()=>switchPage('tones');
 $('open-volume-stereo').onclick=()=>switchPage('signal');
 $('open-volume-setup').onclick=()=>switchPage('setups');
 $('retry-sync').onclick=()=>{tell('');autoSync.flush(true);};
 $('recover-main').onclick=recover;
 for(const id of ['zoom','scale','show-bands'])$(id).onchange=draw;
 $('eq-enabled').onclick=()=>{const enabled=!Boolean(state.current_eq[out()].eq_enabled);work.eq[out()].eq_enabled=Number(enabled);work.eq[out()].eq_enabled_right=Number(enabled);remember();queueEq(true,enabled);};$('dual-eq').onchange=()=>immediate('dual_eq',$('dual-eq').checked);
 $('copy-lr').onclick=()=>{work.eq[out()].right=clone(work.eq[out()].left);remember();render();const saved=side;side=state.current_eq[out()].dual_eq?'right':'stereo';queueEq();side=saved;tell('');};$('copy-rl').onclick=()=>{work.eq[out()].left=clone(work.eq[out()].right);remember();render();const saved=side;side=state.current_eq[out()].dual_eq?'left':'stereo';queueEq();side=saved;tell('');};
 for(const id of ['peq','audio']){$('restore-'+id).onclick=restore;$('recover-'+id).onclick=recover;}
 $('hardware-slot').onchange=activateBank;
 $('store-slot').onchange=paintStore;
 $('preset-name').oninput=paintStore;
 $('store-hardware').onclick=storeBank;
 $('chip').onchange=()=>run(async()=>{await api('api/rme/context',{chip:$('chip').value,expected_identity:state.identity});await refresh();tell('Revisione dichiarata salvata nel plugin. DAC invariato.');});
 window.addEventListener('pagehide',()=>{remember();editStore?.flush();});
 gate();
 watch();
}
globalThis.RMEStudio={validState,validBands,mergeEdits,requireResult,levelReadout,boot};
if(!globalThis.RME_TEST_MODE)boot().catch(()=>tell('Controller temporaneamente non disponibile. Ricarica la pagina.',true));
