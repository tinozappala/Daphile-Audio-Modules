'use strict';
const $=id=>document.getElementById(id), uuid=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
const kinds=['Peaking','Lowshelf','Highshelf','Lowpass','Highpass','Notch'];
const labels=['Campana','Regola i bassi','Regola gli alti','Taglia gli alti','Taglia i bassi','Tacca stretta'];
const gainTypes=kinds.slice(0,3);
let snapshot={revision:0,presets:[]}, profile, channel='L', chart, previewSequence=0;
let marginAdvice=null;
const clone=copyEqData;
const editStore=new EqEditStore('daphile:camilla:editing:v1');
const localPreview=new EqPreview();
const cursorBusy=new EqBusy(document.documentElement);
let paintFrame=null,editAnchor='';
let audioBusy=false;
let audioState=null;
let statusBusy=false, audioEpoch=0, statusTimer=null;
let recoveredEdits=false,engineRate=48000,previewRunning=false,previewWanted=false;
let undoAction=null,initialStatus=Promise.resolve(),profileRevision=null,remoteConflict=false;
const syncQueue=new EqSyncQueue({read:()=>profile,valid:()=>!audioBusy&&validFields(),
 write:async p=>{await initialStatus;if(!audioState)throw Error('Stato audio non disponibile');
   if(!profileRevision)throw Error('Profilo non verificato');
   return cursorBusy.run(()=>api(audioState.enabled?'/api/apply':'/api/profile/prepare',{profile:p,expected:profileRevision}));},
 confirmed:(p,result)=>{profileRevision=result.profile_revision||profileRevision;remoteConflict=false;recoveredEdits=false;if(profile.id===p.id){editAnchor=JSON.stringify(p);rememberEdits();}message('');},
 failed:error=>{remoteConflict=error.status===409||!error.status;message(remoteConflict?'Rileggi le impostazioni del lettore prima di continuare.':'Modifiche non applicate. Premi Riprova.',true);},paint:()=>paintSync()});
function validFields(){let valid=Boolean(profile?.name?.trim());for(const e of document.querySelectorAll('input[type="number"]')){
 const v=Number(e.value),ok=e.value.trim()!==''&&Number.isFinite(v)&&(!e.min||v>=Number(e.min))&&(!e.max||v<=Number(e.max));
 e.setAttribute('aria-invalid',String(!ok));valid=valid&&ok;}return valid;}
function paintSync(){if(!profile)return;const retry=syncQueue.blocked||recoveredEdits;
 for(const id of ['apply','applyBands']){$(id).hidden=id==='applyBands'||!retry;$(id).textContent=remoteConflict?'Rileggi':syncQueue.blocked?'Riprova':'Applica modifiche';$(id).disabled=audioBusy||Boolean(syncQueue.running)||!audioState;}
 $('peqToggle').disabled=audioBusy||Boolean(syncQueue.running)||!audioState||!profile;
 $('listenPresets').disabled=audioBusy||!profile||Boolean(syncQueue.running)||remoteConflict;
 $('openCompare').disabled=!profile||!audioState||audioBusy||syncQueue.blocked||recoveredEdits||syncQueue.ack!==syncQueue.version||Boolean(syncQueue.running);
 updatePresetStatus();
 $('studio').classList.toggle('sync-pending',Boolean(syncQueue.running));}
function changed(final=true){marginAdvice=null;$('useMargin').hidden=true;rememberEdits();syncQueue.changed(final);}
function undoable(action){undoAction=action;$('undo').hidden=false;}
function previewRate(){return $('rate').value==='auto'?engineRate:Number($('rate').value);}
function numericValue(e){const v=Number(e.value);return e.value.trim()!==''&&Number.isFinite(v)&&v>=Number(e.min)&&v<=Number(e.max)?v:null;}
function rememberEdits(){if(profile){editStore.write(profile.id,{profile,anchor:editAnchor});editStore.write('@selected',profile.id);updatePresetStatus();}}
function displayName(p){return p?.id==='flat'?'Nessuna correzione':p?.name||'';}
function editingProfile(p){const out=clone(p);out.enabled=true;out.bypass=false;out.levels.auto_headroom=false;return out;}
function updatePresetStatus(){
 if(!profile)return;
 const saved=snapshot.presets.find(p=>p.id===profile.id);
 const stored=saved&&JSON.stringify(editingProfile(saved))===JSON.stringify(profile);
 $('presetStatus').textContent=profile.id==='flat'?'Aggiungi un filtro per creare il tuo profilo.':stored?'Profilo salvato.':'Premi Salva profilo per conservare queste modifiche.';
 $('listenHint').textContent=syncQueue.blocked?'Il lettore richiede una verifica.':syncQueue.running||syncQueue.version!==syncQueue.ack?'Invio della regolazione al lettore…':recoveredEdits?'Queste modifiche sono recuperate in Taratura.':profile.id==='flat'?'Per creare una regolazione, apri Taratura.':stored?'Puoi modificare questo profilo in Taratura.':'Regolazione in uso. Puoi salvarla in Taratura.';
}
async function api(path,data){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='/api/apply'||path.startsWith('/api/audio/')?185000:15000);
 try{const r=await fetch('.'+path,{method:data===undefined?'GET':'POST',signal:controller.signal,headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});let v;try{v=await r.json();}catch{throw Error('Equalizzatore momentaneamente non disponibile.');}if(!r.ok){const error=Error(v.error||'Richiesta fallita');error.status=r.status;throw error;}return v;}
 finally{clearTimeout(timer);}}
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('operation-error',error);}
function guard(fn,showBusy=true){return async(...args)=>{const action=async()=>{try{return await fn(...args);}catch(e){message(e.message,true);}};return showBusy?cursorBusy.run(action):action();};}
function syncLinked(){if(profile.mode==='linked'){const other=channel==='L'?'R':'L', old=profile.peq[other]; profile.peq[other]=profile.peq[channel].map((b,i)=>({...clone(b),id:old[i]?.id||uuid()}));}}
function editable(){prepareBandDraft();return true;}
function prepareBandDraft(){if(profile.id==='flat'){profile=clone(profile);profile.id=uuid();profile.name='La mia regolazione';profile.enabled=true;renderPresets();$('presetName').value=profile.name;$('presetName').disabled=false;$('save').disabled=false;}}
function setProfile(p,restore=false){rememberEdits();const base=editingProfile(p);
 const cached=restore?editStore.read(p.id):null,recover=cached?.profile&&cached.anchor===JSON.stringify(base)&&JSON.stringify(cached.profile)!==cached.anchor;
 if(cached?.profile&&!recover&&JSON.stringify(cached.profile)!==cached.anchor){editStore.write('@recovery',cached.profile);$('recoverEdits').hidden=false;}
 profile=recover?clone(cached.profile):base;editAnchor=JSON.stringify(base);recoveredEdits=Boolean(recover);rememberEdits();render();paintSync();}
function renderPresets(){
 for(const id of ['presets','listenPresets']){
   const select=$(id);setEqChildren(select);
   if(profile&&!snapshot.presets.some(p=>p.id===profile.id))select.add(new Option(displayName(profile)+' · in modifica',profile.id));
   for(const p of snapshot.presets)select.add(new Option(displayName(p),p.id));
   select.value=profile?.id||'flat';
 }
}
function render(){renderPresets();updatePresetStatus();$('presetName').value=displayName(profile);const flat=profile.id==='flat';for(const id of ['mode','pregain','auto','channelBypass','channelGain','copyChannel','openRew','addBand'])$(id).disabled=false;for(const id of ['presetName','save'])$(id).disabled=flat;$('deletePreset').disabled=flat||!snapshot.presets.some(p=>p.id===profile.id);
 $('enabled').checked=profile.enabled;$('bypass').checked=profile.bypass;$('mode').checked=profile.mode==='linked';$('linkState').textContent=profile.mode==='linked'?'Filtri Dx + Sx collegati':'Filtri Dx e Sx indipendenti';$('pregain').value=profile.levels.pre_gain;$('auto').checked=profile.levels.auto_headroom;$('channelBypass').checked=profile.channel_bypass[channel];$('channelGain').value=profile.levels[channel];$('copyChannel').textContent=`Copia ${channel} → ${channel==='L'?'R':'L'}`;$('bandCount').textContent=`${profile.peq.L.length+profile.peq.R.length} / 48`;
 document.querySelectorAll('[data-channel]').forEach(b=>b.classList.toggle('active',b.dataset.channel===channel));renderBands();refreshChart();}
function control(tag,value,options={}){const e=document.createElement(tag);Object.assign(e,options);if(tag==='input'&&e.type==='checkbox')e.checked=value;else e.value=value;return e;}
function renderBands(){const body=$('bands');setEqChildren(body);$('empty').hidden=profile.peq[channel].length>0;
 profile.peq[channel].forEach((b,i)=>{const tr=document.createElement('tr');tr.dataset.id=b.id;const cell=e=>{const td=document.createElement('td');td.append(e);tr.append(td);};
 const field=(key,tag='input',opts={})=>{const el=control(tag,b[key],opts);el.setAttribute('aria-label',`${key} banda ${i+1} canale ${channel}`);const commit=()=>{if(!editable())return;const value=el.type==='checkbox'?el.checked:el.type==='number'?numericValue(el):el.value;
 if(value===null){validFields();syncQueue.cancelTimer();return;}if(b[key]===value)return;b[key]=value;if(key==='type'&&!gainTypes.includes(b.type))b.gain=0;syncLinked();changed();if(key==='type')render();else {scheduleChart();}};el.addEventListener('input',commit);el.addEventListener('change',commit);return el;};
 cell(field('enabled','input',{type:'checkbox'}));cell(field('label','input',{type:'text',maxLength:80,placeholder:`Banda ${i+1}`}));
 const type=field('type','select');kinds.forEach((k,j)=>{const o=document.createElement('option');o.value=k;o.textContent=labels[j];type.append(o);});type.value=b.type;cell(type);
 cell(field('freq','input',{type:'number',min:20,max:20000,step:.1}));cell(field('gain','input',{type:'number',min:-18,max:12,step:.1,disabled:!gainTypes.includes(b.type)}));cell(field('q','input',{type:'number',min:.1,max:20,step:.01}));
 const order=document.createElement('span');for(const [text,delta] of [['↑',-1],['↓',1]]){const x=document.createElement('button');x.textContent=text;x.disabled=i+delta<0||i+delta>=profile.peq[channel].length;x.title=delta<0?'Sposta banda in alto':'Sposta banda in basso';x.onclick=()=>{const bands=profile.peq[channel];[bands[i],bands[i+delta]]=[bands[i+delta],bands[i]];syncLinked();changed();render();};order.append(x);}cell(order);
 const actions=document.createElement('span');for(const [text,fn] of [['⧉',()=>{if(profile.peq[channel].length>=24)throw Error('Limite di 24 bande per canale');profile.peq[channel].splice(i+1,0,{...clone(b),id:uuid()});}],['×',()=>profile.peq[channel].splice(i,1)]]){const x=document.createElement('button');x.textContent=text;x.title=text==='×'?'Elimina banda':'Duplica banda';x.setAttribute('aria-label',x.title);x.onclick=guard(()=>{const old=clone(profile);fn();undoable(()=>{setProfile(old);changed();});syncLinked();changed();render();});actions.append(x);}cell(actions);body.append(tr);
 });}
function refreshChart(){
 if(!profile)return; marginAdvice=null;$('useMargin').hidden=true;rememberEdits();chart=localPreview.calculate(profile,previewRate());drawGraph();
 $('effectiveL').textContent=chart.effective_gain_db.L.toFixed(2)+' dB';$('effectiveR').textContent=chart.effective_gain_db.R.toFixed(2)+' dB';
 ++previewSequence;clearTimeout(chartTimer);previewWanted=true;
 chartTimer=setTimeout(runLevelPreview,180);
}
async function runLevelPreview(){if(previewRunning)return;previewRunning=true;previewWanted=false;const seq=previewSequence;
 try{
   const result=await api('/api/levels',{profile:clone(profile),rate:previewRate()});if(seq!==previewSequence)return;
   const h=Math.min(result.headroom_db.L,result.headroom_db.R);
   const suggested=Math.floor((profile.levels.pre_gain+h-0.5)*10)/10;
   if(h<-.01 && suggested>=-120){marginAdvice={gain:suggested,revision:seq};$('useMargin').textContent='Riduci il livello a '+suggested.toLocaleString('it-IT')+' dB';$('useMargin').hidden=false;}
   else{marginAdvice=null;$('useMargin').hidden=true;}setEqChildren($('headroom'),document.createTextNode(h.toFixed(1)),Object.assign(document.createElement('small'),{textContent:'dB'}));
   $('headroom').style.color=h<0?'#ffb0b0':'#68dcb8';$('headroomNote').textContent=h<0?'Con questi filtri alcuni passaggi potrebbero distorcere.':'Margine calcolato sui filtri del profilo.';$('headroomMeter').style.width=Math.max(0,Math.min(100,h/12*100))+'%';
 }catch{if(seq===previewSequence){$('headroom').textContent='—';$('headroomNote').textContent='Stima non disponibile';}}
 finally{previewRunning=false;if(previewWanted)chartTimer=setTimeout(runLevelPreview,180);}
}
function scheduleChart(final=false){
 previewSequence++;clearTimeout(chartTimer);rememberEdits();
 if(final){if(paintFrame!==null)cancelAnimationFrame(paintFrame);paintFrame=null;refreshChart();editStore.flush();}
 else if(paintFrame===null)paintFrame=requestAnimationFrame(()=>{paintFrame=null;refreshChart();});
}
let chartTimer=null;
const editor=new EqGraph($('graph'),{change:(id,patch,final)=>{
 if(!editable())return;const band=profile.peq[channel].find(b=>b.id===id);if(!band)return;
 if(patch.frequency!==undefined)band.freq=Number(Math.max(20,Math.min(20000,patch.frequency)).toFixed(1));
 if(patch.gain!==undefined&&gainTypes.includes(band.type))band.gain=Number(Math.max(-18,Math.min(12,patch.gain)).toFixed(1));
 if(patch.q!==undefined)band.q=Number(Math.max(.1,Math.min(20,patch.q)).toFixed(2));
 syncLinked();changed(final);
 if(final)renderBands();else {const row=$('bands').querySelector(`[data-id="${id}"]`);if(row)for(const key of ['freq','gain','q'])row.querySelector(`[aria-label^="${key} "]`).value=band[key];}
 scheduleChart(final);
 },select:id=>{for(const row of $('bands').children)row.classList.toggle('selected-band',row.dataset.id===id);},inspect:p=>{$('graphInspect').textContent=`${Math.round(p.frequency)} Hz · ${p.gain.toFixed(1)} dB | Trascina · rotella per Q`;}});
let graphWidth=0;
function resizeGraph(){
 const width=$('graph').getBoundingClientRect().width;
 if(width&&Math.abs(width-graphWidth)>1){graphWidth=width;drawGraph();}
}
new ResizeObserver(resizeGraph).observe($('graph').parentElement);
window.addEventListener('resize',resizeGraph);
function drawGraph(){
 if(!chart||!profile)return;const [lo,hi]=$('zoom').value.split(',').map(Number),[minDb,maxDb]=$('scale').value.split(',').map(Number),curves=[],rate=previewRate();
 for(const ch of ['L','R']){if(!$(ch==='L'?'showL':'showR').checked)continue;const color=ch==='L'?'#b7a3ff':'#68dfc4';

 if($('showBands').checked&&ch===channel&&profile.enabled&&!profile.bypass&&!profile.channel_bypass[ch])for(const [i,b] of profile.peq[ch].entries()){if(!b.enabled)continue;curves.push({frequency:chart.frequency,values:chart.band_curves[ch][b.id].map(v=>chart.effective_gain_db[ch]+v),color:EqGraph.colors[i%EqGraph.colors.length],width:1,opacity:.4});}
 curves.push({frequency:chart.frequency,values:chart.curves[ch],color,width:2.8});}
 const active=profile.enabled&&!profile.bypass&&!profile.channel_bypass[channel]&&$(channel==='L'?'showL':'showR').checked;
 const bands=active?profile.peq[channel].filter(b=>b.enabled).map(b=>({id:b.id,type:b.type,label:String(profile.peq[channel].indexOf(b)+1),frequency:b.freq,gain:(gainTypes.includes(b.type)?b.gain:0)+chart.effective_gain_db[channel],q:b.q,color:EqGraph.colors[profile.peq[channel].indexOf(b)%EqGraph.colors.length],editable:profile.id!=='flat',gainEditable:gainTypes.includes(b.type),gainStep:.1,qStep:.01,gainOffset:chart.effective_gain_db[channel]})):[];
 editor.set({lo,hi,minDb,maxDb,curves,bands,rate});
}
function markDraft(){changed();}
async function load(){snapshot=await api('/api/presets');const current=await api('/api/profile/state'),selected=current.profile;profileRevision=current.profile_revision;
 const savedId=editStore.read('@selected');
 for(const id of new Set([selected.id,...(typeof savedId==='string'?[savedId]:[])])){const saved=editStore.read(id);
  if(saved?.profile&&JSON.stringify(saved.profile)!==saved.anchor){try{await api('/api/levels',{profile:saved.profile,rate:previewRate()});if(id!==selected.id)editStore.write('@recovery',saved.profile);}catch{editStore.remove(id);}}}
 setProfile(selected,true);$('recoverEdits').hidden=!editStore.read('@recovery');}
function renderAudio(audio,engine){
 if(engine!==undefined)renderAudio.engine=engine;engine=renderAudio.engine;
 audioState=audio;const enabled=Boolean(audio?.enabled),dsd=audio?.mode==='dsd-bypass',fault=audio?.mode==='fault-paused'||Boolean(engine?.fault)||(audio?.active&&engine?.mute===true),unknown=!audio||(audio.active&&engine&&!engine.connected);
 const active=enabled&&audio?.processing===true&&engine?.connected===true&&engine.state==='Running'&&!fault&&!unknown;
 const comparing=active&&engine?.comparison_active===true;
 const title=unknown?'Stato del lettore non disponibile':fault?'Controlla il lettore':comparing?'Confronto in ascolto':active?'Correzione in ascolto':enabled&&dsd?'Il brano DSD segue il percorso originale':enabled&&audio.mode==='armed-unsupported'?'Il brano segue il percorso originale':enabled?'In attesa della musica':'Correzione spenta';
 $('peqState').textContent=unknown?'Non disponibile':fault?'Da verificare':comparing?'Confronto':active?'In ascolto':enabled?'Pronta':'Spenta';
 $('peqDot').classList.toggle('idle',!active);$('peqDot').classList.toggle('armed',enabled&&!active&&!fault&&!unknown);
 $('clipCount').textContent=active&&Number.isFinite(engine?.clipped_samples)?String(engine.clipped_samples):'—';
 $('audioPath').textContent=title;
 $('audioPathNote').textContent=unknown?'Premi Aggiorna stato per riprovare.':fault?'Apri Daphile per controllare la riproduzione.':comparing?'La prova è temporanea.':active?'La musica passa attraverso il correttore.':enabled&&dsd?'La correzione riprenderà con il prossimo brano compatibile.':enabled&&audio.mode==='armed-unsupported'?'La correzione riprenderà con un formato compatibile.':enabled?'La correzione è pronta per il prossimo brano compatibile.':'Puoi preparare un profilo anche a musica ferma.';
 $('peqToggle').disabled=audioBusy||Boolean(syncQueue.running)||!audio||!profile;$('peqToggle').textContent=audioBusy?'Attendi…':audio?.recovery_required?'Ripristina audio':enabled?'Spegni correzione':'Accendi correzione';$('peqToggle').setAttribute('aria-pressed',String(enabled));
 paintSync();document.dispatchEvent(new Event('daphile-audio-status'));
}
async function status(){if(audioBusy||statusBusy||syncQueue.running)return;statusBusy=true;const epoch=audioEpoch;try{const s=await api('/api/status');if(audioBusy||epoch!==audioEpoch)return;renderAudio(s.audio,s);$('moduleVersion').textContent='CamillaDSP '+s.module_version;if(s.connected&&s.sample_rate&&engineRate!==s.sample_rate){engineRate=s.sample_rate;if($('rate').value==='auto'){refreshChart();}}}
 catch{if(!audioBusy&&epoch===audioEpoch)renderAudio(null,{connected:false});}finally{statusBusy=false;}}
async function audioAction(enabled){
 if(audioBusy||syncQueue.running||(enabled&&!validFields()))return;syncQueue.cancelTimer();audioBusy=true;audioEpoch++;renderAudio(audioState);
 const submitted=clone(profile),version=syncQueue.version;let succeeded=false;
 try{const data=enabled?{enabled:true,profile:submitted,expected:profileRevision}:{enabled:false};const result=await api('/api/audio/enable',data);profileRevision=result.profile_revision||profileRevision;renderAudio.engine=undefined;renderAudio(result);if(enabled&&!result.already_enabled){editAnchor=JSON.stringify(submitted);recoveredEdits=false;syncQueue.acknowledge(version);rememberEdits();}succeeded=true;if(result.paused_source_stopped)message('Riproduzione ferma. Puoi riprenderla da Daphile.',true);else message('');}
 catch(error){syncQueue.blocked=true;remoteConflict=error.status===409;throw error;}
 finally{audioBusy=false;renderAudio(audioState);if(succeeded){syncQueue.resume();setTimeout(()=>status(),80);}}
}
$('peqToggle').onclick=guard(()=>audioAction(!audioState?.enabled&&!audioState?.recovery_required));
function startPolling(){clearInterval(statusTimer);statusTimer=setInterval(()=>{if(!document.hidden)status();},2000);}
async function start(){$('studio').hidden=false;initialStatus=status();const results=await Promise.allSettled([load(),initialStatus]);if(results[0].status==='rejected')message('Profilo non disponibile. Premi Aggiorna stato.',true);if(audioState)renderAudio(audioState);startPolling();}
document.addEventListener('visibilitychange',()=>{if(statusTimer&&!document.hidden)guard(status,false)();});
window.addEventListener('pagehide',()=>{clearInterval(statusTimer);statusTimer=null;});
window.addEventListener('pageshow',e=>{if(e.persisted){startPolling();status();}});
$('listenPresets').onchange=$('presets').onchange=e=>{const p=snapshot.presets.find(p=>p.id===e.target.value);if(p){setProfile(p);changed();}};
$('new').onclick=()=>{const p=clone(snapshot.presets[0]);p.id=uuid();p.name='La mia regolazione';setProfile(p);changed();};
$('duplicatePreset').onclick=()=>{const p=clone(profile);p.id=uuid();p.name=profile.name.slice(0,72)+' · copia';for(const ch of ['L','R'])p.peq[ch].forEach(b=>b.id=uuid());setProfile(p);changed();};
$('save').onclick=guard(async()=>{if(!profile||!validFields()||!$('presetName').value.trim())throw Error('Controlla nome e valori prima di salvare.');if(!editable())return;profile.name=$('presetName').value.trim();snapshot=await api('/api/presets/save',{revision:snapshot.revision,profile:clone(profile)});rememberEdits();render();message('');});
$('deletePreset').onclick=guard(async()=>{if(profile.id==='flat')return;const id=profile.id;snapshot=await api('/api/presets/delete',{revision:snapshot.revision,id});undoable(async()=>{snapshot=await api('/api/presets/restore',{revision:snapshot.revision});render();});editStore.remove(id);render();});
$('restore').onclick=guard(async()=>{snapshot=await api('/api/presets/restore',{revision:snapshot.revision});render();});
$('presetName').oninput=()=>{if(editable()){const value=$('presetName').value;$('presetName').setAttribute('aria-invalid',String(!value.trim()));if(value.trim()){profile.name=value;changed();}}};
$('presetName').onblur=()=>{if(!$('presetName').value.trim()){$('presetName').value=profile.name;$('presetName').setAttribute('aria-invalid','false');}};
for(const [id,fn] of [['enabled',v=>profile.enabled=v],['bypass',v=>profile.bypass=v],['auto',v=>profile.levels.auto_headroom=v]])$(id).onchange=()=>{if(editable()){fn($(id).checked);render();}};
$('pregain').oninput=$('pregain').onchange=()=>{if(editable()){const v=numericValue($('pregain'));if(v===null){validFields();syncQueue.cancelTimer();return;}profile.levels.pre_gain=v;changed();refreshChart();}};
$('channelGain').oninput=$('channelGain').onchange=()=>{if(editable()){const v=numericValue($('channelGain'));if(v===null){validFields();syncQueue.cancelTimer();return;}profile.levels[channel]=v;changed();refreshChart();}};
$('channelBypass').onchange=()=>{if(editable()){profile.channel_bypass[channel]=$('channelBypass').checked;changed();refreshChart();}};
$('mode').onchange=()=>{if(!editable())return;const old=clone(profile);undoable(()=>{setProfile(old);changed();});profile.mode=$('mode').checked?'linked':'independent';syncLinked();markDraft();render();};
$('copyChannel').onclick=()=>{if(!editable())return;const old=clone(profile);undoable(()=>{setProfile(old);changed();});const target=channel==='L'?'R':'L';profile.peq[target]=profile.peq[channel].map(b=>({...clone(b),id:uuid()}));changed();render();};
for(const b of document.querySelectorAll('[data-channel]'))b.onclick=()=>{channel=b.dataset.channel;render();};
$('flatBands').onclick=()=>{
 prepareBandDraft();const old=clone(profile);undoable(()=>{setProfile(old);changed();});
 for(const ch of ['L','R'])for(const band of profile.peq[ch]){
  band.gain=0;
  if(!gainTypes.includes(band.type))band.enabled=false;
 }
 markDraft();render();
 message('Filtri azzerati. Puoi annullare la modifica.');
};
for(const id of ['rate','zoom','showL','showR','scale','showBands'])$(id).onchange=()=>{if(id==='rate'){refreshChart();}else drawGraph();};
$('apply').onclick=$('applyBands').onclick=guard(async()=>{
 if(audioBusy)return;
 if(remoteConflict){editStore.write('@recovery',clone(profile));await load();remoteConflict=false;syncQueue.acknowledge();await status();message('');return;}
 if(!validFields())return;if(recoveredEdits&&syncQueue.version===syncQueue.ack)syncQueue.version++;
 await status();if(audioState)await syncQueue.flush(true);
});
$('refresh').onclick=guard(async()=>{await Promise.allSettled([!profile?load():Promise.resolve(),status()]);});
$('export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(profile,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='peq-'+profile.id+'.json';a.click();URL.revokeObjectURL(url);};
$('import').onchange=guard(async()=>{const f=$('import').files[0];if(!f)return;try{if(f.size>131072||!f.name.toLowerCase().endsWith('.json'))throw Error('Importa un file JSON entro 128 KiB');let p;try{p=JSON.parse(await f.text());}catch{throw Error('Il file JSON non è leggibile.');}await api('/api/preview',{profile:p,rate:previewRate()});p.id=uuid();setProfile(p);changed();}finally{$('import').value='';}});
cursorBusy.run(async()=>{const s=await api('/api/session');if(s.authenticated)await start();}).catch(e=>{$('studio').hidden=false;message('Controller non disponibile: '+e.message,true);});

if(window.parent!==window){
 document.body.classList.add('embedded');let pending=false,last=0;
 const resize=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;const height=Math.ceil(document.body.getBoundingClientRect().height)+24;if(height!==last&&height>0&&height<60000){last=height;parent.postMessage({type:'dat-settings-height',height},location.origin);}});};
 new ResizeObserver(resize).observe(document.body);window.addEventListener('load',resize);resize();
}

$('undo').onclick=guard(async()=>{const action=undoAction;undoAction=null;$('undo').hidden=true;if(action)await action();});
$('recoverEdits').onclick=guard(async()=>{const p=editStore.read('@recovery');if(!p)return;await api('/api/levels',{profile:p,rate:previewRate()});setProfile(p);editStore.remove('@recovery');$('recoverEdits').hidden=true;changed();});

window.addEventListener('pagehide',()=>{rememberEdits();editStore.flush();});

/* Measure the two stereo EQ profiles on the same excerpt, on the browser only.
   eqCoefficients is shared with the plot and matches the backend compiler. */
const measureEqComparison = (() => {
  const supportedRates = [44100, 48000, 88200, 96000, 176400, 192000, 384000];
  const formatError = 'Scegli un file WAV stereo PCM a 16, 24 o 32 bit oppure WAV float a 32 bit.';
  const badFile = 'Il file WAV è incompleto o danneggiato. Scegli un altro file.';

  function tag(bytes, offset) {
    return String.fromCharCode(...new Uint8Array(bytes.buffer, bytes.byteOffset + offset, 4));
  }

  async function read(file, offset, length) {
    const bytes = await file.slice(offset, offset + length).arrayBuffer();
    if (bytes.byteLength !== length) throw Error(badFile);
    return new DataView(bytes);
  }

  function wavFormat(bytes) {
    if (bytes.byteLength < 16 || bytes.getUint16(2, true) !== 2) throw Error(formatError);
    let codec = bytes.getUint16(0, true);
    const rate = bytes.getUint32(4, true);
    const block = bytes.getUint16(12, true);
    const bits = bytes.getUint16(14, true);
    if (codec === 0xfffe) {
      // PCM and IEEE float use the same twelve trailing bytes in their WAVE subtype GUID.
      const tail = [0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113];
      if (bytes.byteLength < 40 || bytes.getUint16(16, true) < 22 ||
          !tail.every((byte, i) => bytes.getUint8(28 + i) === byte)) throw Error(formatError);
      const channelMask = bytes.getUint32(20, true);
      if (channelMask !== 0 && channelMask !== 3) throw Error(formatError);
      codec = bytes.getUint32(24, true);
      const validBits = bytes.getUint16(18, true);
      if (validBits > bits || validBits === 1 || (codec === 3 && validBits && validBits !== bits)) throw Error(formatError);
    }
    if (!supportedRates.includes(rate)) throw Error('La frequenza di questo WAV non è supportata dal pannello.');
    if (block !== 2 * bits / 8 || bytes.getUint32(8, true) !== rate * block) throw Error(badFile);
    if (codec === 3 && bits === 32) return {rate, block, sample: (data, pos) => data.getFloat32(pos, true)};
    if (codec !== 1) throw Error(formatError);
    if (bits === 16) return {rate, block, sample: (data, pos) => data.getInt16(pos, true) / 32768};
    if (bits === 24) return {rate, block, markers: [2], sample: (data, pos) =>
      (data.getUint8(pos) + data.getInt16(pos + 1, true) * 256) / 8388608};
    if (bits === 32) return {rate, block, markers: [2, 3], sample: (data, pos) => data.getInt32(pos, true) / 2147483648};
    throw Error(formatError);
  }

  async function readWav(file) {
    const header = await read(file, 0, 12);
    if (tag(header, 0) !== 'RIFF' || tag(header, 8) !== 'WAVE') throw Error(formatError);
    const end = header.getUint32(4, true) + 8;
    if (end > file.size) throw Error(badFile);
    let offset = 12, format, data;
    // Seek over artwork and metadata instead of reading the whole track.
    for (let chunks = 0; offset + 8 <= end && chunks < 256; chunks++) {
      const chunk = await read(file, offset, 8);
      const name = tag(chunk, 0);
      const size = chunk.getUint32(4, true);
      offset += 8;
      if (offset + size > end) throw Error(badFile);
      if (name === 'fmt ') format = wavFormat(await read(file, offset, Math.min(size, 40)));
      if (name === 'data') data = {offset, size};
      if (format && data) {
        if (data.size % format.block !== 0) throw Error(badFile);
        return {...format, offset: data.offset, frames: data.size / format.block};
      }
      offset += size + size % 2;
    }
    throw Error(badFile);
  }

  function makePath(profile, rate) {
    if (profile.schema_version !== 1 || profile.routing?.format !== 'PCM' ||
        profile.drc?.enabled !== false || profile.drc.fir_refs.length ||
        !profile.compatibility.rates.includes(rate)) {
      throw Error('Queste impostazioni non possono essere confrontate alla frequenza del file scelto.');
    }
    const channels = ['L', 'R'].map(ch => {
      const enabled = profile.enabled && !profile.bypass && !profile.channel_bypass[ch];
      const bands = enabled ? profile.peq[ch].filter(b => b.enabled) : [];
      const filters = bands.map(b => ({
        sos: eqCoefficients(b.type, b.freq, b.gain, b.q, rate),
        z1: 0,
        z2: 0
      }));
      return {gain: 10 ** ((profile.levels.pre_gain + profile.levels[ch]) / 20), filters};
    });
    return {channels, energy: 0, peak: 0};
  }

  function checkPcm(bytes, block, markers) {
    // Match the RC6 audio gate: 16 alternating marker bytes in BOTH channels.
    for (const marker of markers) {
      for (let at = marker.offset; at < bytes.byteLength; at += block) {
        const value = bytes.getUint8(at);
        if ((value === 5 || value === 250) && bytes.getUint8(at + block / 2) === value) {
          marker.count = marker.last === (value ^ 255) ? marker.count + 1 : 1;
          marker.last = value;
          if (marker.count === 16) throw Error('Questo file contiene audio DSD. Scegli un brano WAV PCM.');
        } else {
          marker.count = 0;
          marker.last = null;
        }
      }
    }
  }

  function process(channel, samples) {
    for (let i = 0; i < samples.length; i++) samples[i] *= channel.gain;
    for (const filter of channel.filters) {
      const [b0, b1, b2, , a1, a2] = filter.sos;
      let {z1, z2} = filter;
      for (let i = 0; i < samples.length; i++) {
        const x = samples[i];
        const y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        samples[i] = y;
      }
      filter.z1 = z1;
      filter.z2 = z2;
    }
  }

  return async function measure(file, profileA, profileB, {expectedRate = null, signal} = {}) {
    const audio = await readWav(file);
    if (expectedRate != null && audio.rate !== expectedRate) {
      throw Error('Scegli un WAV alla stessa frequenza del brano in ascolto (' + (expectedRate / 1000).toLocaleString('it-IT') + ' kHz).');
    }
    if (audio.frames < audio.rate) throw Error('Scegli un passaggio musicale lungo almeno un secondo.');
    const frames = Math.min(audio.frames, audio.rate * 6);
    const start = Math.floor((audio.frames - frames) / 2);
    // Warm up the filters before the measured section when preceding samples exist.
    const first = Math.max(0, start - audio.rate * 2);
    const paths = [makePath(profileA, audio.rate), makePath(profileB, audio.rate)];
    const markers = (audio.markers || []).map(offset => ({offset, last: null, count: 0}));
    let lastYield = performance.now();
    for (let pos = first; pos < start + frames; pos += 8192) {
      if (signal?.aborted) throw new DOMException('Calcolo annullato.', 'AbortError');
      const count = Math.min(8192, start + frames - pos);
      const bytes = await read(file, audio.offset + pos * audio.block, count * audio.block);
      checkPcm(bytes, audio.block, markers);
      for (let ch = 0; ch < 2; ch++) {
        const source = new Float64Array(count);
        for (let i = 0; i < count; i++) {
          source[i] = audio.sample(bytes, i * audio.block + ch * audio.block / 2);
          if (!Number.isFinite(source[i])) throw Error(badFile);
        }
        for (const [index, path] of paths.entries()) {
          const output = index === 0 ? source.slice() : source;
          process(path.channels[ch], output);
          for (let i = Math.max(0, start - pos); i < count; i++) {
            path.energy += output[i] * output[i];
            path.peak = Math.max(path.peak, Math.abs(output[i]));
          }
        }
      }
      if (performance.now() - lastYield > 25) {
        await new Promise(resolve => setTimeout(resolve, 0));
        lastYield = performance.now();
      }
    }
    const [a, b] = paths.map(path => 10 * Math.log10(path.energy / (frames * 2)));
    if (!Number.isFinite(a) || !Number.isFinite(b) || Math.min(a, b) < -85) {
      throw Error('Il passaggio è troppo silenzioso per confrontare i livelli. Scegli un altro WAV.');
    }
    const rmsDb = {A: a, B: b};
    const peakDb = {A: 20 * Math.log10(paths[0].peak), B: 20 * Math.log10(paths[1].peak)};
    const target = Math.min(a, b);
    const trimDb = {A: target - a, B: target - b};
    const commonTrimDb = Math.min(0, -Math.max(peakDb.A + trimDb.A, peakDb.B + trimDb.B));
    trimDb.A += commonTrimDb;
    trimDb.B += commonTrimDb;
    if (profileA.levels.pre_gain + trimDb.A < -120 || profileB.levels.pre_gain + trimDb.B < -120) {
      throw Error('La differenza di volume è troppo grande. Scegli due impostazioni con livelli più vicini.');
    }
    return {trimDb, rmsDb, peakDb, rate: audio.rate, durationSeconds: frames / audio.rate,
      startSeconds: start / audio.rate, commonTrimDb};
  };
})();

// The listening page and tuning editor operate on the same native profile.
function showView(tuning) {
  $('listenView').hidden = tuning;
  $('tuneView').hidden = !tuning;
  $('viewTune').setAttribute('aria-pressed', String(tuning));
  $('viewListen').setAttribute('aria-pressed', String(!tuning));
  if (tuning && profile) requestAnimationFrame(drawGraph);
}
$('viewListen').onclick = () => showView(false);
$('viewTune').onclick = () => showView(true);

$('useMargin').onclick = () => {
  if (!marginAdvice || marginAdvice.revision !== previewSequence) return;
  const gain = marginAdvice.gain;
  const old = clone(profile);
  prepareBandDraft();
  profile.levels.pre_gain = gain;
  undoable(() => { setProfile(old); changed(); });
  changed();
  render();
};

// Shapes are adapted from CamillaEQ's FilterIcons.svelte (see licenses).
const filterShapes = {
  Peaking:   {path:'M3 12 C7 12 8 6 12 6 C16 6 17 12 21 12', freq:1000},
  Lowshelf:  {path:'M3 16 H8 C10 16 11 14 11 12 C11 10 12 8 14 8 H21', freq:120},
  Highshelf: {path:'M3 8 H10 C12 8 13 10 13 12 C13 14 14 16 16 16 H21', freq:6000},
  Lowpass:   {path:'M3 9 H12 C14 9 15 10 15 12 V20', freq:12000},
  Highpass:  {path:'M3 20 V12 C3 10 4 9 6 9 H21', freq:40},
  Notch:     {path:'M3 9 H8 C10 9 10 20 12 20 C14 20 14 9 16 9 H21', freq:1000}
};
$('addBand').onclick = guard(() => {
  if (!profile || !validFields()) throw Error('Completa i valori del filtro prima di continuare.');
  if (profile.peq[channel].length >= 24) throw Error('Questo canale contiene già 24 filtri.');
  $('shapeDialog').showModal();
});
$('shapeCancel').onclick = () => $('shapeDialog').close();
for (const [index, kind] of kinds.entries()) {
  const button = document.createElement('button');
  button.type='button'; button.dataset.shape=kind;
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('aria-hidden','true');
  svg.append(editor.node('path',{d:filterShapes[kind].path,fill:'none',stroke:'currentColor','stroke-width':2,'stroke-linecap':'round','stroke-linejoin':'round'}));
  button.append(svg,document.createTextNode(labels[index]));
  button.onclick = () => {
    const previous = clone(profile);
    prepareBandDraft();
    const band = {id:uuid(), type:kind, freq:filterShapes[kind].freq, gain:0, q:kind==='Notch'?4:1, enabled:true, label:''};
    profile.peq[channel].push(band);
    editor.selected=band.id;
    syncLinked();
    undoable(() => {setProfile(previous); changed();});
    changed();
    render();
    $('shapeDialog').close();
  };
  $('shapeChoices').append(button);
}

(() => {
  const dialog=$('compareDialog');
  const selections={A:$('compareA'),B:$('compareB')};
  let options, session, expected, heard, match, occupied=false, possiblyActive=false;
  let heartbeatTimer=null, matchGeneration=0;

  function available() {
    return Boolean(audioState?.processing && renderAudio.engine?.connected && renderAudio.engine.state==='Running' && !renderAudio.engine?.fault && renderAudio.engine?.mute===false);
  }
  function raw(side){return options.get(selections[side].value);}
  function soundKey(p){
    return JSON.stringify(['L','R'].map(ch=>[
      p.levels.pre_gain+p.levels[ch],
      p.enabled&&!p.bypass&&!p.channel_bypass[ch] ? p.peq[ch].filter(b=>b.enabled).map(b=>[b.type,b.freq,b.gain,b.q]) : []
    ]));
  }
  function pairDiffers(){return options && soundKey(raw('A'))!==soundKey(raw('B'));}
  function controls() {
    const playable=Boolean(session && available() && pairDiffers());
    for(const side of ['A','B']){
      selections[side].disabled=occupied;
      $('hear'+side).disabled=occupied||!playable;
      $('hear'+side).setAttribute('aria-pressed',String(heard===side));
    }
    $('keepCompare').disabled=occupied||!heard||!available();
    $('matchFile').disabled=occupied||!pairDiffers();
    $('compareClose').disabled=occupied;
  }
  function initialHint(){
    if (!pairDiffers()) return 'Scegli due regolazioni diverse. Puoi crearne una in Taratura.';
    if (!available()) return 'Per ascoltare il confronto accendi la correzione e avvia un brano compatibile in Daphile.';
    return 'Scegli Ascolta A oppure Ascolta B.';
  }
  const tell=text=>{$('compareState').textContent=text;};
  function args(action){return {comparison:action,expected,session};}
  function resetMatch(){
    matchGeneration++; match=null;
    $('matchFile').value=''; $('matchSummary').textContent='';
  }
  function stopHeartbeat(){clearInterval(heartbeatTimer);heartbeatTimer=null;}
  function startHeartbeat(){
    stopHeartbeat();
    heartbeatTimer=setInterval(async()=>{
      if (occupied || !heard || !dialog.open) return;
      occupied=true;
      try {
        const r=await api('/api/apply',args('heartbeat'));
        if(r.active!==true) throw Error('Il confronto è terminato.');
      }catch(e){
        heard=null; stopHeartbeat(); resetMatch();
        tell(e.status===409?'Il brano o le impostazioni sono cambiati. Chiudi il confronto e riaprilo.':'Contatto con il lettore perso. Premi Torna all’originale.');
      }finally{occupied=false;controls();}
    },8000);
  }
  async function endTrial(){
    stopHeartbeat();
    if (!possiblyActive) return;
    const r=await api('/api/apply',args('end'));
    if (r.active!==false) throw Error('Il ritorno alla regolazione di partenza non è confermato.');
    possiblyActive=false; heard=null;
  }
  function exclusive(fn) {
    return async () => {
      if (occupied || !dialog.open) return;
      occupied=true;controls();
      try {await fn();}
      catch(e){tell(e.message);}
      finally{occupied=false;if(dialog.open)controls();}
    };
  }
  $('openCompare').onclick=guard(async()=>{
    if (syncQueue.ack!==syncQueue.version || syncQueue.blocked || !profile) return;
    const current=await api('/api/profile/state');
    if(current.profile_revision!==profileRevision) throw Error('La regolazione è cambiata su un altro dispositivo. Riapri il pannello per confrontarla.');
    options=new Map([['current',editingProfile(profile)]]);
    for(const p of snapshot.presets)options.set(p.id,editingProfile(p));
    for(const select of Object.values(selections)){
      setEqChildren(select);
      for(const [key,p] of options)select.add(new Option(key==='current'?displayName(p)+' · in uso':displayName(p),key));
    }
    selections.B.value='current';
    const baseKey=snapshot.presets.find(p=>p.id===profile.id && soundKey(options.get(p.id))!==soundKey(options.get('current')))?.id;
    selections.A.value=baseKey||[...options.keys()].find(k=>soundKey(options.get(k))!==soundKey(options.get('current')))||'flat';
    session=uuid(); expected=current.profile_revision; heard=null; possiblyActive=false;
    resetMatch();$('matchTools').open=false;dialog.showModal();controls();tell(initialHint());
  });
  for(const select of Object.values(selections))select.onchange=exclusive(async()=>{
    await endTrial();resetMatch();tell(initialHint());
  });
  for(const side of ['A','B'])$('hear'+side).onclick=exclusive(async()=>{
    if(!available()) throw Error(initialHint());
    const choice=clone(raw(side));
    if(match){
      const gain=choice.levels.pre_gain+match.trimDb[side];
      if(gain < -120 || gain>0) throw Error('Il livello richiesto supera quello supportato dal profilo.');
      choice.levels.pre_gain=gain;
    }
    possiblyActive=true;
    tell('Il lettore sta cambiando regolazione…');
    try {
      const r=await api('/api/apply',{...args('listen'),profile:choice,...(match?{rate:match.rate}:{})});
      if(r.applied!==true||r.active!==true)throw Error('Ascolto non confermato dal lettore.');
    }catch(e){
      heard=null;stopHeartbeat();
      // End also reconciles a lost reply; never repeat an uncertain listen.
      try{await endTrial();}catch{throw Error('Ascolto non confermato. Premi Torna all’originale per verificare lo stato.');}
      throw e;
    }
    heard=side;startHeartbeat();
    tell('Stai ascoltando '+side+' · '+displayName(raw(side))+(match?' · volume allineato sul brano scelto.':'.'));
  });
  $('matchFile').onchange=exclusive(async()=>{
    const file=$('matchFile').files[0];
    if(!file)return;
    await endTrial();match=null;
    const generation=++matchGeneration;
    $('matchSummary').textContent='Misuro lo stesso passaggio per A e B…';tell('');
    try{
      const rate=available()?renderAudio.engine?.sample_rate:null;
      const result=await measureEqComparison(file,raw('A'),raw('B'),{expectedRate:rate||undefined});
      if(generation===matchGeneration){
        match=result;
        $('matchSummary').textContent='Pronto · '+result.durationSeconds.toLocaleString('it-IT',{maximumFractionDigits:1})+' s misurati. A '+result.trimDb.A.toFixed(1)+' dB · B '+result.trimDb.B.toFixed(1)+' dB.';
        tell(initialHint());
      }
    }catch(e){$('matchSummary').textContent=e.message;tell('Puoi scegliere un altro WAV o ascoltare con i volumi dei profili.');}
  });
  $('compareClose').onclick=exclusive(async()=>{
    await endTrial();dialog.close();status();
  });
  $('keepCompare').onclick=exclusive(async()=>{
    const chosen=clone(raw(heard));
    await endTrial();
    const current=await api('/api/profile/state');
    if(current.profile_revision!==expected)throw Error('La regolazione di partenza è cambiata. Riapri il pannello.');
    const r=await api('/api/apply',{profile:chosen,expected});
    if(!(r.applied||r.pending)||!r.profile_revision)throw Error('Applicazione della scelta non confermata.');
    profileRevision=r.profile_revision;setProfile(chosen);syncQueue.acknowledge();dialog.close();
    message('Regolazione scelta: '+displayName(chosen)+'.');status();
  });
  dialog.addEventListener('cancel',e=>{e.preventDefault();$('compareClose').click();});
  dialog.addEventListener('close',()=>{stopHeartbeat();resetMatch();session=null;heard=null;});
  dialog.addEventListener('focusin',()=>{if(session)controls();});
  window.addEventListener('pagehide',()=>{
    stopHeartbeat();
    if(possiblyActive&&session)fetch('./api/apply',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json'},body:JSON.stringify(args('end'))}).catch(()=>{});
  });
  window.addEventListener('pageshow',e=>{if(e.persisted&&dialog.open){heard=null;tell('Verifica lo stato del confronto prima di continuare.');controls();}});
  // A track may start, pause or change format while the selector is open.
  document.addEventListener('daphile-audio-status',()=>{
    if(!dialog.open||!options)return;
    if(!occupied && !heard && !possiblyActive)tell(initialHint());
    controls();
  });
})();

// Preview both speakers first. The existing profile writer commits them together.
(() => {
  const dialog = $('rewDialog');
  const target = $('rewTarget');
  const confirm = $('rewConfirm');
  const names = {L: 'Sinistro', R: 'Destro', both: 'Entrambi'};
  const graph = new EqGraph($('rewGraph'), {});
  let original, candidate, rate, sequence = 0;

  function resetPreview() {
    candidate = null;
    confirm.disabled = true;
    $('rewEffect').hidden = true;
    $('rewCurve').hidden = true;
    setEqChildren($('rewIssues'));
    $('rewIssues').hidden = true;
    $('rewSummary').textContent = '';
  }

  function chooseTarget() {
    sequence++;
    resetPreview();
    $('rewFirst').value = '';
    $('rewSecond').value = '';
    const pair = target.value === 'pair';
    $('rewFirstLabel').textContent = pair || target.value === 'L' ? 'File del diffusore sinistro'
      : target.value === 'R' ? 'File del diffusore destro' : 'File per entrambi i diffusori';
    $('rewSecondField').hidden = !pair;
  }

  $('openRew').onclick = () => {
    if (!profile || audioBusy || syncQueue.blocked || !validFields()) {
      message('Completa o correggi le modifiche in corso prima di importare.', true);
      return;
    }
    original = JSON.stringify(profile);
    target.value = profile.mode === 'linked' ? 'both' : channel;
    $('rewCurve').open = false;
    chooseTarget();
    dialog.showModal();
  };
  target.onchange = chooseTarget;
  $('rewCancel').onclick = () => dialog.close();
  dialog.addEventListener('close', () => {
    if (!dialog.open) { sequence++; resetPreview(); }
  });

  function explain(issue) {
    const reasons = {
      frequency: 'La frequenza deve essere compresa tra 20 e 20.000 Hz.',
      gain: 'Il guadagno deve essere compreso tra −18 e +12 dB.',
      Q: 'Il valore Q deve essere compreso tra 0,1 e 20.'
    };
    const key = issue.reason.split(':')[0];
    return (issue.line ? 'Riga ' + issue.line + ' · ' : '') + (Object.prototype.hasOwnProperty.call(reasons, key) ? reasons[key] : issue.reason);
  }

  async function readFilters(file, speaker, sampleRate) {
    if (!/\.txt$/i.test(file.name)) throw Error('Scegli il file .txt delle impostazioni dei filtri.');
    if (file.size > 65536) throw Error('Il file supera 64 KiB. Scegli l’esportazione dei filtri.');
    const bytes = new Uint8Array(await file.arrayBuffer());
    let encoding = 'utf-8';
    if (bytes[0] === 255 && bytes[1] === 254) encoding = 'utf-16le';
    if (bytes[0] === 254 && bytes[1] === 255) encoding = 'utf-16be';
    let text;
    try { text = new TextDecoder(encoding, {fatal: true}).decode(bytes); }
    catch { throw Error('Il testo non è leggibile. Esporta di nuovo i filtri da REW.'); }
    const result = await api('/api/rew/preview', {text, channel: speaker, rate: sampleRate});
    return {bands: result.bands, errors: result.issues.map(explain), valid: result.can_apply};
  }

  function drawPreview() {
    if (!candidate || !$('rewCurve').open) return;
    const response = localPreview.calculate(candidate, rate);
    const curves = ['L', 'R'].map((ch, i) => ({
      frequency: response.frequency, values: response.curves[ch], color: i ? '#68dfc4' : '#b7a3ff',
      dashed: ch === 'R', width: 2.8
    }));
    graph.set({lo: 20, hi: 20000, minDb: -36, maxDb: 18, curves, bands: []});
  }
  $('rewCurve').addEventListener('toggle', drawPreview);

  async function preview() {
    const current = ++sequence, mode = target.value;
    resetPreview();
    const inputs = mode === 'pair'
      ? [['L', $('rewFirst').files[0]], ['R', $('rewSecond').files[0]]]
      : [[mode, $('rewFirst').files[0]]];
    if (inputs.some(([, file]) => !file)) {
      $('rewSummary').textContent = mode === 'pair' ? 'Scegli un file per ciascun diffusore.' : 'Scegli il file dei filtri.';
      return;
    }
    rate = previewRate();
    $('rewSummary').textContent = 'Lettura dei filtri…';
    const results = await Promise.all(inputs.map(async ([side, file]) => {
      try { return {side, ...await readFilters(file, side === 'R' ? 'R' : 'L', rate)}; }
      catch (e) { return {side, valid: false, errors: [e.message]}; }
    }));
    if (current !== sequence || !dialog.open) return;
    if (results.some(result => !result.valid)) {
      $('rewSummary').textContent = 'Controlla i file indicati prima di continuare.';
      const list = $('rewIssues');
      for (const {side, errors} of results) for (const error of errors) {
        const li = document.createElement('li');
        li.textContent = names[side] + ' · ' + error;
        list.append(li);
      }
      list.hidden = false;
      return;
    }
    candidate = JSON.parse(original);
    if (candidate.id === 'flat') { candidate.id = uuid(); candidate.name = 'Correzione REW'; }
    const unlink = mode !== 'both' && candidate.mode === 'linked';
    if (unlink) candidate.mode = 'independent';
    for (const {side, bands} of results) {
      for (const ch of side === 'both' ? ['L', 'R'] : [side]) {
        candidate.peq[ch] = bands.map((band, index) => {
          const old = candidate.peq[ch][index];
          if (old && ['type', 'freq', 'gain', 'q', 'enabled'].every(key => old[key] === band[key])) return old;
          return side === 'both' && ch === 'R' ? {...band, id: uuid()} : band;
        });
      }
    }
    $('rewSummary').textContent = results.map(({side, bands}) => {
      const count = bands.length, off = bands.filter(b => !b.enabled).length;
      return names[side] + ' · ' + count + (count === 1 ? ' filtro' : ' filtri')
        + (off ? ' (' + off + (off === 1 ? ' spento)' : ' spenti)') : '');
    }).join('  /  ');
    const which = mode === 'L' ? 'del diffusore sinistro' : mode === 'R' ? 'del diffusore destro' : 'di entrambi i diffusori';
    const unchanged = JSON.stringify(candidate) === original;
    $('rewEffect').textContent = unchanged ? 'Questi filtri sono già presenti.' :
      'I nuovi filtri sostituiranno quelli ' + which + '.'
      + (unlink ? ' Sinistro e destro diventeranno regolabili separatamente.' : '');
    $('rewEffect').hidden = false;
    $('rewCurve').hidden = false;
    drawPreview();
    confirm.disabled = unchanged;
  }
  $('rewFirst').onchange = $('rewSecond').onchange = preview;

  confirm.onclick = async () => {
    if (!candidate || confirm.disabled) return;
    confirm.disabled = true;
    const current = sequence;
    try {
      const state = await api('/api/profile/state');
      if (current !== sequence || !dialog.open) return;
      if (JSON.stringify(profile) !== original || state.profile_revision !== profileRevision ||
          syncQueue.blocked || audioBusy || previewRate() !== rate) {
        resetPreview();
        $('rewSummary').textContent = 'Le impostazioni sono cambiate. Annulla, ricarica la pagina e ripeti l’importazione.';
        return;
      }
      const previous = clone(profile);
      if (target.value === 'R' || target.value === 'L') channel = target.value;
      setProfile(candidate);
      undoable(() => { setProfile(previous); changed(); });
      changed();
      dialog.close();
    } catch {
      if (current === sequence && dialog.open) {
        $('rewSummary').textContent = 'Non riesco a rileggere le impostazioni del lettore. Riprova.';
        confirm.disabled = false;
      }
    }
  };
})();
