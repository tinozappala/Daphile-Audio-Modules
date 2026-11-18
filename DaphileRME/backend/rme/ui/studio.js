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
  cancelTimer(){clearTimeout(this.timer);this.timer=null;}
}
globalThis.RMEAutoSync=RMEAutoSync;

const $=id=>document.getElementById(id),clone=v=>structuredClone(v),equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const OUTS=['line','phones','iem'],NAMES={line:'Line',phones:'Phones',iem:'IEM'};
const PAGES={eq:['Equalizzatore','Cinque bande, controllo preciso e curve interattive.'],levels:['Uscite e volume','Controlla il livello e configura l’uscita giusta.'],tones:['Toni e loudness','Il timbro e la compensazione per l’ascolto a basso volume.'],signal:['Stereo e percorso audio','Sorgenti, immagine stereo e conversione.'],memory:['Memorie DAC e backup','Venti preset EQ hardware, nove Setup e backup dei quattro slot software.'],display:['Display e telecomando','Personalizza l’aspetto e le assegnazioni del DAC.'],device:['Dispositivo e stato','Il comportamento del DAC e le informazioni realmente lette.']};
const AUDIO=['tone_enabled','bass_gain','bass_frequency','bass_q','treble_gain','treble_frequency','treble_q','loudness_enabled','loudness_bass','loudness_treble','loudness_reference','crossfeed','balance','stereo_width','mono','polarity'];
let state=null,work=null,side='stereo',busy=false,slots={revision:0,slots:[null,null,null,null]},editStore=null,cursorBusy=null,graph=null;
let autoSync=null;
let currentPage='eq',bank=null,bankNames={},pollTimer=null,dragging=false;
let stateGeneration=0,pollInFlight=false;
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
 else{work=fresh;bank=null;bankNames={};editStore=new EqEditStore('daphile:rme:studio:v2:'+next.identity);const saved=preserve?editStore.read('workspace'):null;if(saved?.identity===next.identity&&saved.work&&saved.baseline)work=mergeEdits(saved.work,saved.baseline,fresh,'',conflicts);}
 state=clone(next);stateGeneration++;remember();if(paint)render();return conflicts;
}
async function api(path,body){
 for(let attempt=0;attempt<12;attempt++){
  const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  let value;try{value=await r.json();}catch{throw Error('Il controller non risponde ancora. Le impostazioni sono conservate: rileggi il DAC per verificare l’esito.');}
  if(r.status===409)throw Error('Il DAC è occupato. Rileggi il dispositivo o riprova quando è disponibile.');
  if(!r.ok){const error=Error(value.error||'Operazione non disponibile.');if(r.status===412&&value.settings_write_commands_sent===0&&validState(value.state))error.freshState=value.state;throw error;}return value;
 }
}
function gate(){for(const n of document.querySelectorAll('button,input,select'))if(!n.matches('[data-view],#zoom,#scale,#show-bands'))n.disabled=busy||!state||n.dataset.unavailable==='true'||Boolean(autoSync?.running&&n.id==='read')||Boolean(autoSync?.active&&n.matches('button,#output,[data-side],#chip,[id^="include-"]')&&!n.matches('#retry-sync,#read'));}
async function run(action){if(busy||autoSync?.running)return;return cursorBusy.run(async()=>{busy=true;stateGeneration++;gate();try{return await action();}catch(e){if(e.freshState)adopt(e.freshState);tell(e.message||'Operazione non riuscita.',true);}finally{busy=false;gate();draw();}});}
async function refresh(preserve=true){const next=await api('api/rme/snapshot',{}),conflicts=adopt(next,preserve);$('connection').textContent='DAC letto · '+new Date(next.read_time*1000).toLocaleTimeString('it-IT');return conflicts;}
async function fresh(){const conflicts=await refresh();if(conflicts.length)throw Error('Alcuni parametri sono cambiati dal DAC o da un altro controller. Rileggi il DAC prima di riprovare.');}
function requireResult(result){if(!['applied-verified','restored-verified','no-change'].includes(result.status)||result.readback_verified!==true||!validState(result.state))throw Error(result.status==='failed-restored'?'Applicazione non riuscita. I valori precedenti sono stati ripristinati; le tue regolazioni restano disponibili.':result.status==='rollback-incomplete'?'Operazione non verificata. Usa Recupera operazione interrotta prima di applicare altre modifiche.':'Il DAC non ha confermato la modifica. Rileggi il dispositivo prima di riprovare.');return result.state;}
function validInputs(){return !busy&&Boolean(state)&&!Array.from(document.querySelectorAll('input[type="number"]')).some(n=>!n.disabled&&(n.value===''||!n.checkValidity()));}
function syncPaint(){
 if(!autoSync)return;
 $('retry-sync').hidden=!autoSync.blocked;
 $('sync-state').textContent=autoSync.blocked?'Modifiche non confermate':autoSync.active?'Aggiornamento DAC…':'';
 gate();
 if(state&&!autoSync.active&&!dragging&&!document.activeElement?.matches('input,select'))render();
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
 return cursorBusy.run(async()=>{
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
  }else if(request.kind==='manual'){
   if(state.outputs[request.output].eq_preset===0)return;
   path='api/rme/preset/apply';patch.slot='manual';
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
  renderStatus();renderDisplay();draw();
 });
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
const HARDWARE_LABELS={reference_level:'Ref Level · riferimento analogico',auto_reference:'Auto Ref Level · automatico',mid_side:'M/S Proc · Mid/Side',stereo_width:'Width · ampiezza stereo'};
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
 if(input.type==='number')input.oninput=()=>{if(input.value!==''&&input.checkValidity())queueValue(key,Number(input.value));else autoSync.cancelTimer?.();};
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
function bandEditable(){if(!state)return false;const eq=state.current_eq[out()];if(state.outputs[out()].eq_preset!==0)return false;if(side!=='stereo'&&!eq.dual_eq)return false;if(side==='stereo'&&eq.dual_eq&&!equal(work.eq[out()].left,work.eq[out()].right))return false;return true;}
function bandChange(id,patch,final=true){
 if(busy||!bandEditable())return;const channels=side==='stereo'?['left','right']:[side];
 for(const s of channels){const b=work.eq[out()][s].find(b=>b.band===id);if(patch.frequency!==undefined){let f=Math.max(id<=3?20:200,Math.min(20000,patch.frequency));b.frequency=f>2040?Math.round(f/10)*10:Math.round(f);}if(patch.gain!==undefined)b.gain=Math.max(-12,Math.min(12,Math.round(patch.gain*2)/2));if(patch.q!==undefined)b.q=Number(Math.max(.5,Math.min(id<=3?9.9:5,patch.q)).toFixed(1));if(patch.type!==undefined)b.type=patch.type;}
 for(const input of $('bands').querySelectorAll(`[data-band="${id}"] [data-field]`)){input.value=work.eq[out()][bandSide()].find(b=>b.band===id)[input.dataset.field];if(input.dataset.field==='frequency')input.step=Number(input.value)>2040?10:1;}remember();draw();queueEq(final);
}
function filterType(b){if(!b.type)return 'Peaking';if(b.type===1)return b.band===1?'Lowshelf':'Highshelf';return b.band===1&&b.type===2?'Highpass':'Lowpass';}
function curve(rows,enabled){
 const each=rows.map(b=>{const key=JSON.stringify(b);if(!cache.has(key)){const coeff=eqCoefficients(filterType(b),b.frequency,b.gain,b.q,48000);cache.set(key,FREQUENCIES.map(f=>eqResponse(coeff,f,48000)));if(cache.size>70)cache.delete(cache.keys().next().value);}return cache.get(key);});
 return {each,total:FREQUENCIES.map((_,i)=>enabled?each.reduce((sum,c)=>sum+c[i],0):0)};
}
function draw(){
 if(!state||!graph)return;const o=out(),s=bandSide(),pending=work.eq[o],actual=state.current_eq[o],curves=[];
 for(const [name,color]of [['left','#70dfca'],['right','#be9bff']]){
  const current=curve(pending[name],Boolean(name==='right'?pending.eq_enabled_right:pending.eq_enabled)),readback=curve(actual[name],Boolean(name==='right'?actual.eq_enabled_right:actual.eq_enabled));
  if(name===s&&$('show-bands').checked)current.each.forEach((values,i)=>curves.push({frequency:FREQUENCIES,values,color:EqGraph.colors[i],opacity:.4,width:1.2}));
  curves.push({frequency:FREQUENCIES,values:readback.total,color:'#a4b3c8',dashed:true,opacity:.5,width:1.2},{frequency:FREQUENCIES,values:current.total,color,width:name===s?2.8:1.8,opacity:name===s?1:.7});
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
   const td=node('td'),input=node('input',{type:'number',value:b[key],min,max,step});input.dataset.field=key;input.dataset.unavailable=String(!bandEditable()||(key==='gain'&&filterType(b).endsWith('pass')));input.setAttribute('aria-label',key+' banda '+b.band);input.oninput=input.onchange=()=>{if(key==='frequency')input.step=Number(input.value)>2040?10:1;if(input.value!==''&&input.checkValidity())bandChange(b.band,{[key]:Number(input.value)});};td.append(input);row.append(td);
  }box.append(row);
 }
 const actual=state.current_eq[out()],split=actual.eq_enabled!==actual.eq_enabled_right;
 $('eq-enabled').checked=Boolean(actual.eq_enabled);$('eq-enabled').dataset.unavailable=String(split);
 $('select-manual').dataset.unavailable=String(state.outputs[out()].eq_preset===0);
 $('eligibility').textContent=state.outputs[out()].eq_preset!==0?'Preset hardware in ascolto. Usa Manual nel DAC per tornare ai filtri modificabili; il DAC richiama la configurazione Manual conservata.':!bandEditable()?'Per modificare un solo lato attiva EQ separato. Per collegare curve diverse usa esplicitamente Copia L→R o Copia R→L.':(side==='stereo'?'Regolazione collegata dei due canali.':'Regolazione del solo canale '+(side==='left'?'sinistro.':'destro.'));
 document.querySelectorAll('[data-side]').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.side===side)));
}
async function restore(){return run(async()=>{await fresh();const next=requireResult(await api('api/rme/control/restore',{confirm:true,output:out(),expected:state.revision}));adopt(next,false);tell('Ultima modifica annullata e ripristino verificato.');});}
async function recover(){return run(async()=>{await api('api/rme/control/recover',{confirm:true});await refresh();tell('Esito del recupero riletto dal DAC.');});}

function renderStatus(){
 const o=out(),eq=state.current_eq[o];$('actual-eq').textContent=NAMES[o]+' · EQ '+(eq.eq_enabled?'ON':'OFF')+(eq.eq_enabled!==eq.eq_enabled_right?' · L/R differenti':'');
 $('output-cards').replaceChildren(...OUTS.map(output=>{const b=node('button',{class:'output-card'+(output===o?' selected':'')});b.dataset.localView='true';b.append(node('small',{},NAMES[output]),node('strong',{},state.outputs[output].volume===undefined?'Non disponibile':state.outputs[output].volume.toFixed(1)+' dB'),node('span',{},output==='iem'?'IEM · riferimento fisso −3 dBu':state.outputs[output].mute?'Mute attivo':state.outputs[output].dim?'Dim attivo':'Livello letto dal DAC'));b.onclick=()=>{if(busy||autoSync?.active)return;$('output').value=output;render();};return b;}));
 const vol=state.outputs[o].volume,ref=work.outputs[o].loudness_reference;
 $('loudness-state').textContent=typeof vol==='number'&&typeof ref==='number'?'Volume letto '+vol.toFixed(1)+' dB · riferimento '+ref.toFixed(1)+' dB. '+(vol>=ref+20?'A questo volume la compensazione loudness è nulla.':vol<=ref?'A questo volume la compensazione è massima.':'La compensazione diminuisce aumentando il volume.'):'Relazione loudness / volume non disponibile.';
 $('signal-path').replaceChildren();for(const [i,[title,value]]of [['INGRESSO',capFor('source').options[state.outputs[o].source]||'Non disponibile'],['ELABORAZIONE','EQ '+(eq.eq_enabled?'ON':'OFF')+' · toni '+(state.outputs[o].tone_enabled?'ON':'OFF')],['USCITA',NAMES[o]]].entries()){if(i)$('signal-path').append(node('span',{class:'path-arrow','aria-hidden':'true'},'→'));const n=node('div',{class:'path-node'});n.append(node('small',{},title),node('strong',{},value));$('signal-path').append(n);}
 const status=$('device-status');status.replaceChildren();
 for(const [label,value]of [['Connessione','DAC letto e identificato'],['Clock',state.capabilities.device.clock_source.options[state.device.clock_source]||'Non disponibile'],['Frequenza',state.capabilities.device.sample_rate.options[state.device.sample_rate]||'Non disponibile'],['Formato','Non disponibile'],['Uscita visualizzata',NAMES[o]],['Ultima lettura',new Date(state.read_time*1000).toLocaleTimeString('it-IT')]]){const n=node('div',{class:'status-item'});n.append(node('small',{},label),node('strong',{},value));status.append(n);}
 const st=state.status_messages?.[0];$('diagnostics').textContent=st?'Revisione protocollo letta: '+st.protocol_revision+'. Codice revisione hardware: '+st.device_revision_bits+'. I campi IOStatus non documentati non vengono interpretati.':'Stato dettagliato non disponibile.';
 $('chip').value=state.context.chip;
}
function renderDisplay(){const p=$('display-preview');p.classList.toggle('light',work.device.display_mode===0);p.style.setProperty('--meter',['#70dfca','#70dfff','#f5ba70','#b6c3cf','#fa8f9f','#f4a25e'][work.device.meter_color]||'#70dfca');}
function render(){if(!state){gate();return;}renderBands();renderFields();renderStatus();renderDisplay();draw();gate();}
function switchPage(page){if(!PAGES[page])return;currentPage=page;document.querySelectorAll('[data-page]').forEach(n=>n.hidden=n.dataset.page!==page);document.querySelectorAll('[data-view]').forEach(n=>{if(n.dataset.view===page)n.setAttribute('aria-current','page');else n.removeAttribute('aria-current');});$('page-title').textContent=PAGES[page][0];$('page-subtitle').textContent=PAGES[page][1];if(page==='eq')draw();}

function softwareProfile(){
 const o=out(),profile={schema:2,output:o,enabled:Boolean(work.eq[o].eq_enabled),channels:{left:clone(work.eq[o].left),right:clone(work.eq[o].right)},audio:Object.fromEntries(AUDIO.filter(k=>work.outputs[o][k]!==undefined).map(k=>[k,work.outputs[o][k]])),settings:{},device:{}};
 if($('include-levels').checked)for(const [k,c]of Object.entries(state.capabilities.outputs[o]))if(c.section==='levels'&&c.writable)profile.settings[k]=work.outputs[o][k];
 if($('include-signal').checked)for(const [k,c]of Object.entries(state.capabilities.outputs[o]))if(!AUDIO.includes(k)&&c.section!=='levels'&&c.writable&&!c.memory_action)profile.settings[k]=work.outputs[o][k];
 if($('include-device').checked)for(const [k,c]of Object.entries(state.capabilities.device))if(c.writable)profile.device[k]=work.device[k];
 if(profile.settings.dac_filter>=5)profile.context={chip:state.context.chip};
 return profile;
}
function migrateProfile(p){if(p.schema===2)return clone(p);return {schema:2,output:p.output,enabled:p.enabled,channels:{left:clone(p.bands),right:clone(p.bands)},audio:clone(p.audio),settings:{},device:{}};}
function presetCommands(p,current){
 const output=p.output,identity=current.identity,now=current.outputs[output],values={...p.audio,...p.settings,...p.device};
 const writable=key=>{const cap=current.capabilities.outputs[output][key]||current.capabilities.device[key];return Boolean(cap&&(cap.writable||now.eq_preset!==0&&cap.available&&!cap.blocked&&!cap.read_only&&(cap.scope==='eq'&&cap.index>=20&&cap.index<=27||key==='dual_eq')));};
 const desired=k=>Object.hasOwn(values,k)?values[k]:now[k],diff=k=>Object.hasOwn(values,k)&&!equal(values[k],now[k]);
 if(Object.hasOwn(values,'volume')){
  if(diff('dim')||diff('auto_reference')||diff('reference_level'))throw Error('Questo preset include volume e un cambiamento di Dim o riferimento. Regola prima Dim e riferimento, poi richiama il preset: nessuna modifica è stata inviata.');
  if(diff('volume')&&(now.mute||now.dim||desired('mute')||desired('dim')||now.volume_lock&&desired('volume_lock')!==false))throw Error('Per richiamare il volume del preset disattiva prima Mute, Dim e Blocco volume. Nessuna modifica è stata inviata.');
 }
 if(diff('reference_level')&&now.auto_reference&&desired('auto_reference')!==false)throw Error('Il riferimento manuale del preset richiede Auto Ref disattivato. Nessuna modifica è stata inviata.');
 for(const [key,value]of Object.entries(values)){
  if(!writable(key)&&!equal(value,Object.hasOwn(current.device,key)?current.device[key]:now[key]))throw Error('Il preset contiene un parametro non disponibile su questo DAC: '+key+'. Nessuna modifica è stata inviata.');
 }
 const commands=[],split=!equal(p.channels.left,p.channels.right);
 if(now.eq_preset!==0)commands.push({kind:'manual',identity,output});
 const value=(key,v)=>({kind:'value',identity,output,key,value:v});
 if(split)commands.push(value('dual_eq',true));
 // A preset may explicitly unlock volume or switch off Auto Ref. Their
 // prerequisites precede the dependent field; tone-load policy precedes B/T.
 const rank=(key,v)=>key==='volume_lock'&&!v?0:key==='auto_reference'&&!v?1:key==='load_tones_with_preset'?2:key==='volume'?5:['mute','dim','volume_lock'].includes(key)?6:3;
 for(const [key,v]of Object.entries(values).filter(([k])=>k!=='dual_eq').sort((a,b)=>rank(...a)-rank(...b)))
  if(writable(key))commands.push(value(key,v));
 for(const side of split?['left','right']:['stereo'])commands.push({kind:'eq',identity,output,side,bands:clone(p.channels[side==='stereo'?'left':side])});
 commands.push({kind:'eq',identity,output,side:'stereo',enabled:Boolean(p.enabled)});
 // Both channel curves AND enable flags must agree before Dual EQ goes OFF.
 if(!split)commands.push(value('dual_eq',false));
 return commands;
}
function loadProfile(profile){
 const p=migrateProfile(profile),o=p.output;
 if(busy||autoSync.active)return;
 if(!OUTS.includes(o)||!validBands(p.channels?.left)||!validBands(p.channels?.right))throw Error('Preset non valido. Nessuna modifica inviata.');
 if(p.context?.chip&&p.context.chip!=='unknown'&&p.context.chip!==state.context.chip)throw Error('Questo preset include un filtro specifico di un altro chip DAC. Verifica la revisione prima di caricarlo.');
 const commands=presetCommands(p,state);
 work.eq[o].left=clone(p.channels.left);work.eq[o].right=clone(p.channels.right);work.eq[o].eq_enabled=Number(p.enabled);work.eq[o].eq_enabled_right=Number(p.enabled);
 Object.assign(work.outputs[o],p.audio,p.settings);Object.assign(work.device,p.device);$('output').value=o;
 side=equal(p.channels.left,p.channels.right)?'stereo':'left';remember();render();switchPage('eq');
 // Loading a software slot is an explicit recall. Hardware-bank preview
 // remains read-only and is intentionally a separate action below.
 for(const command of commands){const key=command.kind==='value'?'value:'+o+':'+command.key:command.kind==='eq'?'eq:'+o+':'+(command.enabled===undefined?command.side:'enabled'):'manual:'+o;autoSync.edit(key,command);}
 tell('');
}
function renderSlots(){
 $('slots').replaceChildren(...slots.slots.map((s,i)=>{const b=node('button',{class:'preset-slot'});b.append(node('small',{},'PRESET 0'+(i+1)),node('strong',{},s?.name||'Slot libero'),node('span',{},s?NAMES[s.profile.output]+' · Richiama sul DAC':'Salva una configurazione'));b.onclick=()=>{if(busy||autoSync.active)return;$('slot').value=String(i+1);$('slot-name').value=s?.name||'';if(s)loadProfile(s.profile);};return b;}));gate();
}
async function loadSlots(){slots=await api('api/slots');renderSlots();}
async function saveSlot(profile=softwareProfile(),name=$('slot-name').value,slot=Number($('slot').value)){
 if(slots.slots[slot-1]&&!confirm('Sostituire il preset software nello slot '+slot+'? Il DAC resta invariato.'))return;
 slots=await api('api/slots/save',{revision:slots.revision,slot,name:name||'Preset '+slot,profile});renderSlots();tell('Preset software salvato. Nessuna modifica inviata al DAC.');
}
function renderHardware(){
 $('hardware-slots').replaceChildren(...Array.from({length:20},(_,i)=>{const n=i+1,b=node('button',{class:'hardware-slot'+(bank?.slot===n?' selected':'')+(bankNames[n]?.empty?' empty':'')});b.append(node('span',{},String(n).padStart(2,'0')),node('strong',{},bankNames[n]?.name||'Preset '+n));b.onclick=()=>{$('hardware-slot').value=String(n);run(readBank);};return b;}));
 const known=Object.fromEntries((state.hardware_names||[]).filter(n=>n.identifier>=33).map(n=>[n.identifier-32,n.name]));
 $('setup-names').replaceChildren(...Array.from({length:9},(_,i)=>{const n=node('div');n.append(node('small',{},'SETUP '+(i+1)),node('strong',{},known[i+1]||'Nome non letto'));return n;}));
 $('hardware-info').textContent=bank?bank.name+(bank.empty?' · memoria vuota':' · '+(bank.dual_eq?'EQ separato L/R':'stereo'))+' · lettura senza applicazione':'La lettura non cambia il preset in ascolto.';
 $('preview-hardware').dataset.unavailable=String(!bank||bank.empty);$('activate-hardware').dataset.unavailable=String(!bank||bank.empty);gate();
}
async function readBank(){bank=await api('api/rme/memory/read',{slot:Number($('hardware-slot').value)});bankNames[bank.slot]={name:bank.name,empty:bank.empty};renderHardware();tell('Preset hardware letto. Il suono non è stato modificato.');}
function previewBank(){if(!bank||bank.empty)return;const o=out();work.eq[o].left=clone(bank.channels.left);work.eq[o].right=clone(bank.channels.right);Object.assign(work.outputs[o],bank.tones);side=bank.dual_eq?'left':'stereo';remember();render();switchPage('eq');tell('Curve del preset caricate nell’editor. Nessuna modifica inviata al DAC.');}
async function activateBank(){return run(async()=>{if(!bank||bank.empty)return;const output=out();await fresh();const next=requireResult(await api('api/rme/preset/apply',{confirm:true,patch:{output,expected:state.revision,slot:bank.slot}}));adopt(next,false);tell('Preset hardware attivato e verificato sul DAC.');});}
async function previewBackup(file){if(!file)return;if(file.size>65536)throw Error('Il backup supera 64 KB.');const preview=await api('api/rme/backup/preview',{text:await file.text()});$('import-rows').replaceChildren(...preview.slots.map((row,i)=>{const n=node('div',{class:'import-row'});n.append(node('strong',{},'Slot '+(i+1)+' · '+(row?.name||'libero')));if(row){const load=node('button',{},'Richiama sul DAC'),save=node('button',{},'Salva nello slot '+(i+1));load.onclick=()=>{$('import-dialog').close();loadProfile(row.profile);};save.onclick=()=>run(async()=>{await saveSlot(row.profile,row.name,i+1);$('import-dialog').close();});n.append(load,save);}return n;}));$('import-dialog').showModal();}
function download(value){const a=node('a',{href:URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),download:'RME-preset-backup.json'});a.click();setTimeout(()=>URL.revokeObjectURL(a.href),500);}

function pollingAllowed(){return !busy&&!autoSync?.active&&!dragging&&document.visibilityState!=='hidden'&&!document.activeElement?.matches('input,select')&&Boolean(state);}
async function poll(){
 if(pollInFlight||!pollingAllowed())return;
 const generation=stateGeneration;pollInFlight=true;
 try{
  const value=await api('api/rme/state');
  // Never repaint from an older request after a read/write, or while the
  // user has started interacting with controls since this request began.
  if(generation!==stateGeneration||!pollingAllowed())return;
  if(value.state&&validState(value.state)&&value.state.revision!==state.revision){adopt(value.state);renderHardware();$('connection').textContent='DAC aggiornato · '+new Date(value.state.read_time*1000).toLocaleTimeString('it-IT');}
 }catch{/* Quiet background failures do not erase controls or edits. */}
 finally{pollInFlight=false;}
}
async function boot(){
 cursorBusy=new EqBusy(document.documentElement);
 autoSync=new RMEAutoSync({write:writeAutomatic,valid:validInputs,paint:syncPaint,failed:error=>{if(error.freshState)adopt(error.freshState,true,false);tell(error.message||'Il DAC non ha confermato la modifica. Rileggi o riprova.',true);}});
 graph=new EqGraph($('graph'),{change:bandChange,select:id=>{$('bands').querySelectorAll('tr').forEach(n=>n.classList.toggle('selected-band',Number(n.dataset.band)===id));},inspect:p=>$('graph-inspect').textContent=Math.round(p.frequency)+' Hz · '+p.gain.toFixed(1)+' dB | Trascina · rotella per Q'});
 $('graph').addEventListener('pointerdown',()=>{dragging=true;});for(const event of ['pointerup','pointercancel'])window.addEventListener(event,()=>{dragging=false;});
 document.querySelectorAll('[data-view]').forEach(n=>n.onclick=()=>switchPage(n.dataset.view));
 document.querySelectorAll('[data-side]').forEach(n=>n.onclick=()=>{side=n.dataset.side;renderBands();draw();gate();});
 $('output').onchange=()=>{bank=null;render();renderHardware();};$('read').onclick=()=>run(async()=>{autoSync.clear();await refresh(false);renderHardware();tell('Lettura verificata. Nessuna modifica inviata.');});
 $('open-volume-loudness').onclick=()=>switchPage('tones');
 $('open-volume-stereo').onclick=()=>switchPage('signal');
 $('open-volume-setup').onclick=()=>switchPage('memory');
 $('retry-sync').onclick=()=>{tell('');autoSync.flush(true);};
 for(const id of ['zoom','scale','show-bands'])$(id).onchange=draw;
 $('eq-enabled').onchange=()=>{const enabled=$('eq-enabled').checked;work.eq[out()].eq_enabled=Number(enabled);work.eq[out()].eq_enabled_right=Number(enabled);remember();queueEq(true,enabled);};$('dual-eq').onchange=()=>immediate('dual_eq',$('dual-eq').checked);
 $('select-manual').onclick=()=>run(async()=>{const output=out();await fresh();const next=requireResult(await api('api/rme/preset/apply',{confirm:true,patch:{output,expected:state.revision,slot:'manual'}}));adopt(next);tell('Manual richiamato e verificato nel DAC. Le regolazioni dell’editor restano disponibili.');});
 $('copy-lr').onclick=()=>{work.eq[out()].right=clone(work.eq[out()].left);remember();render();const saved=side;side=state.current_eq[out()].dual_eq?'right':'stereo';queueEq();side=saved;tell('');};$('copy-rl').onclick=()=>{work.eq[out()].left=clone(work.eq[out()].right);remember();render();const saved=side;side=state.current_eq[out()].dual_eq?'left':'stereo';queueEq();side=saved;tell('');};
 for(const id of ['peq','audio']){$('restore-'+id).onclick=restore;$('recover-'+id).onclick=recover;}
 $('save-slot').onclick=()=>run(()=>saveSlot());$('delete-slot').onclick=()=>run(async()=>{const slot=Number($('slot').value);if(!confirm('Svuotare lo slot software '+slot+'? Il DAC resta invariato.'))return;slots=await api('api/slots/delete',{revision:slots.revision,slot});renderSlots();tell('Slot software svuotato. DAC invariato.');});
 for(let i=1;i<=20;i++)$('hardware-slot').add(new Option('Preset '+i,String(i)));
 $('read-hardware').onclick=()=>run(readBank);$('preview-hardware').onclick=previewBank;$('activate-hardware').onclick=activateBank;
 $('chip').onchange=()=>run(async()=>{await api('api/rme/context',{chip:$('chip').value,expected_identity:state.identity});await refresh();tell('Revisione dichiarata salvata nel plugin. DAC invariato.');});
 $('export-backup').onclick=()=>run(async()=>download(await api('api/rme/backup')));
 $('import-backup').onchange=()=>run(async()=>{try{await previewBackup($('import-backup').files[0]);}finally{$('import-backup').value='';}});$('cancel-import').onclick=()=>$('import-dialog').close();
 window.addEventListener('pagehide',()=>{remember();editStore?.flush();});
 gate();await run(async()=>{const session=await api('api/session');if(!session.authenticated)throw Error('Controller non disponibile.');await refresh();await loadSlots();const limits=await api('api/rme/memory');$('hardware-write-limit').textContent=limits.limits.eq_save+' '+limits.limits.eq_rename;$('setup-limit').textContent=limits.limits.setup_load+' '+limits.limits.setup_save;$('factory-limit').textContent=limits.limits.factory;renderHardware();tell('Parametri letti automaticamente. Nessuna modifica inviata.');});
 pollTimer=setInterval(poll,5000);
}
globalThis.RMEStudio={validState,validBands,mergeEdits,migrateProfile,presetCommands,requireResult,levelReadout,boot};
if(!globalThis.RME_TEST_MODE)boot().catch(()=>tell('Controller temporaneamente non disponibile. Ricarica la pagina.',true));
