'use strict';
const $=id=>document.getElementById(id), uuid=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),b=>b.toString(16).padStart(2,'0')).join('');
const kinds=['Peaking','Lowshelf','Highshelf','Lowpass','Highpass','Notch'];
const labels=['Bell / Peaking','Low Shelf','High Shelf','Low Pass · 12 dB/oct','High Pass · 12 dB/oct','Notch'];
const gainTypes=kinds.slice(0,3);
let snapshot={revision:0,presets:[]}, profile, channel='L', chart, abProfile=null, abChart=null, rewPreview=null, previewSequence=0;
const clone=o=>structuredClone(o);
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
 failed:error=>{remoteConflict=error.status===409;message(remoteConflict?'Impostazioni cambiate altrove. Premi Rileggi.':'Modifiche non applicate. Premi Riprova.',true);},paint:()=>paintSync()});
function validFields(){let valid=Boolean(profile?.name?.trim());for(const e of document.querySelectorAll('input[type="number"]')){
 const v=Number(e.value),ok=e.value.trim()!==''&&Number.isFinite(v)&&(!e.min||v>=Number(e.min))&&(!e.max||v<=Number(e.max));
 e.setAttribute('aria-invalid',String(!ok));valid=valid&&ok;}return valid;}
function paintSync(){if(!profile)return;const retry=syncQueue.blocked||recoveredEdits;
 for(const id of ['apply','applyBands']){$(id).hidden=!retry;$(id).textContent=remoteConflict?'Rileggi':syncQueue.blocked?'Riprova':'Applica modifiche';$(id).disabled=audioBusy||Boolean(syncQueue.running)||!audioState;}
 $('peqToggle').disabled=audioBusy||Boolean(syncQueue.running)||!audioState||!profile;
 $('studio').classList.toggle('sync-pending',Boolean(syncQueue.running));}
function changed(final=true){rememberEdits();syncQueue.changed(final);}
function undoable(action){undoAction=action;$('undo').hidden=false;}
function previewRate(){return $('rate').value==='auto'?engineRate:Number($('rate').value);}
function numericValue(e){const v=Number(e.value);return e.value.trim()!==''&&Number.isFinite(v)&&v>=Number(e.min)&&v<=Number(e.max)?v:null;}
function rememberEdits(){if(profile){editStore.write(profile.id,{profile,anchor:editAnchor});editStore.write('@selected',profile.id);updatePresetStatus();}}
function headphoneDefault(){return (snapshot.protected_ids||[]).includes(profile?.id);}
function updatePresetStatus(){const saved=snapshot.presets.find(p=>p.id===profile?.id),base=saved&&clone(saved);if(base){base.enabled=true;base.bypass=false;base.levels.auto_headroom=false;}
 $('presetStatus').textContent=headphoneDefault()?'Preset cuffie predefinito. Regola le bande per l’ascolto. Usa Duplica per salvare una copia personale.':profile?.id==='flat'?'Flat è la base iniziale. Modifica una banda o crea un nuovo preset.':base&&JSON.stringify(base)===JSON.stringify(profile)?'Preset salvato sul dispositivo.':'Modifiche non salvate nel preset.';}
async function api(path,data){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==='/api/apply'||path.startsWith('/api/audio/')?185000:15000);
 try{const r=await fetch('.'+path,{method:data===undefined?'GET':'POST',signal:controller.signal,headers:data===undefined?{}:{'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});let v;try{v=await r.json();}catch{throw Error('Equalizzatore momentaneamente non disponibile.');}if(!r.ok){const error=Error(v.error||'Richiesta fallita');error.status=r.status;throw error;}return v;}
 finally{clearTimeout(timer);}}
function message(text,error=false){$('message').textContent=error?text:'';$('message').classList.toggle('operation-error',error);}
function guard(fn,showBusy=true){return async(...args)=>{const action=async()=>{try{return await fn(...args);}catch(e){message(e.message,true);}};return showBusy?cursorBusy.run(action):action();};}
function syncLinked(){if(profile.mode==='linked'){const other=channel==='L'?'R':'L', old=profile.peq[other]; profile.peq[other]=profile.peq[channel].map((b,i)=>({...clone(b),id:old[i]?.id||uuid()}));}}
function editable(){prepareBandDraft();return true;}
function prepareBandDraft(){if(profile.id==='flat'){profile=clone(profile);profile.id=uuid();profile.name='Il mio PEQ';profile.enabled=true;renderPresets();$('presetName').value=profile.name;}}
function setProfile(p,restore=false){rememberEdits();const base=clone(p);base.enabled=true;base.bypass=false;base.levels.auto_headroom=false;
 const cached=restore?editStore.read(p.id):null,recover=cached?.profile&&cached.anchor===JSON.stringify(base)&&JSON.stringify(cached.profile)!==cached.anchor;
 if(cached?.profile&&!recover&&JSON.stringify(cached.profile)!==cached.anchor){editStore.write('@recovery',cached.profile);$('recoverEdits').hidden=false;}
 profile=recover?clone(cached.profile):base;editAnchor=JSON.stringify(base);recoveredEdits=Boolean(recover);rememberEdits();render();paintSync();}
function renderPresets(){const s=$('presets');s.replaceChildren();if(profile&&!snapshot.presets.some(p=>p.id===profile.id)){const o=new Option(profile.name+' · non salvato',profile.id);s.append(o);}for(const p of snapshot.presets){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;s.append(o);}s.value=profile?.id||'flat';}
function render(){renderPresets();updatePresetStatus();$('presetName').value=profile.name;const flat=profile.id==='flat';for(const id of ['mode','pregain','auto','channelBypass','channelGain','copyChannel','rewfile','addBand'])$(id).disabled=false;for(const id of ['presetName','save'])$(id).disabled=flat||headphoneDefault();$('deletePreset').disabled=flat||(snapshot.protected_ids||[]).includes(profile.id)||!snapshot.presets.some(p=>p.id===profile.id);$('deletePreset').title=(snapshot.protected_ids||[]).includes(profile.id)?'Preset cuffie protetto dalla cancellazione':'';
 $('enabled').checked=profile.enabled;$('bypass').checked=profile.bypass;$('mode').checked=profile.mode==='linked';$('linkState').textContent=profile.mode==='linked'?'Filtri Dx + Sx collegati':'Filtri Dx e Sx indipendenti';$('pregain').value=profile.levels.pre_gain;$('auto').checked=profile.levels.auto_headroom;$('channelBypass').checked=profile.channel_bypass[channel];$('channelGain').value=profile.levels[channel];$('copyChannel').textContent=`Copia ${channel} → ${channel==='L'?'R':'L'}`;$('bandCount').textContent=`${profile.peq.L.length+profile.peq.R.length} / 48`;
 document.querySelectorAll('[data-channel]').forEach(b=>b.classList.toggle('active',b.dataset.channel===channel));renderBands();refreshChart();}
function control(tag,value,options={}){const e=document.createElement(tag);Object.assign(e,options);if(tag==='input'&&e.type==='checkbox')e.checked=value;else e.value=value;return e;}
function renderBands(){const body=$('bands');body.replaceChildren();$('empty').hidden=profile.peq[channel].length>0;
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
 if(!profile)return;rememberEdits();chart=localPreview.calculate(profile,previewRate());drawGraph();
 $('effectiveL').textContent=chart.effective_gain_db.L.toFixed(2)+' dB';$('effectiveR').textContent=chart.effective_gain_db.R.toFixed(2)+' dB';
 ++previewSequence;clearTimeout(chartTimer);previewWanted=true;
 chartTimer=setTimeout(runLevelPreview,180);
}
async function runLevelPreview(){if(previewRunning)return;previewRunning=true;previewWanted=false;const seq=previewSequence;
 try{
   const result=await api('/api/levels',{profile:clone(profile),rate:previewRate()});if(seq!==previewSequence)return;
   const h=Math.min(result.headroom_db.L,result.headroom_db.R);$('headroom').replaceChildren(document.createTextNode(h.toFixed(1)),Object.assign(document.createElement('small'),{textContent:'dB'}));
   $('headroom').style.color=h<0?'#ffb0b0':'#68dcb8';$('headroomNote').textContent=h<0?'Rischio di clipping a regime':'Margine conservativo a regime';$('headroomMeter').style.width=Math.max(0,Math.min(100,h/12*100))+'%';
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
new ResizeObserver(entries=>{const width=entries[0]?.contentRect.width;if(width&&Math.abs(width-graphWidth)>1){graphWidth=width;drawGraph();}}).observe($('graph'));
function drawGraph(){
 if(!chart||!profile)return;const [lo,hi]=$('zoom').value.split(',').map(Number),[minDb,maxDb]=$('scale').value.split(',').map(Number),curves=[],rate=previewRate();
 for(const ch of ['L','R']){if(!$(ch==='L'?'showL':'showR').checked)continue;const color=ch==='L'?'#b7a3ff':'#68dfc4';
 if(abChart)curves.push({frequency:abChart.frequency,values:abChart.curves[ch],color,dashed:true,opacity:.45,width:1.5});
 if($('showBands').checked&&ch===channel&&profile.enabled&&!profile.bypass&&!profile.channel_bypass[ch])for(const [i,b] of profile.peq[ch].entries()){if(!b.enabled)continue;curves.push({frequency:chart.frequency,values:chart.band_curves[ch][b.id].map(v=>chart.effective_gain_db[ch]+v),color:EqGraph.colors[i%EqGraph.colors.length],width:1,opacity:.4});}
 curves.push({frequency:chart.frequency,values:chart.curves[ch],color,width:2.8});}
 const active=profile.enabled&&!profile.bypass&&!profile.channel_bypass[channel]&&$(channel==='L'?'showL':'showR').checked;
 const bands=active?profile.peq[channel].filter(b=>b.enabled).map(b=>({id:b.id,label:String(profile.peq[channel].indexOf(b)+1),frequency:b.freq,gain:(gainTypes.includes(b.type)?b.gain:0)+chart.effective_gain_db[channel],q:b.q,color:EqGraph.colors[profile.peq[channel].indexOf(b)%EqGraph.colors.length],editable:profile.id!=='flat',gainEditable:gainTypes.includes(b.type),gainStep:.1,qStep:.01,gainOffset:chart.effective_gain_db[channel]})):[];
 editor.set({lo,hi,minDb,maxDb,curves,bands});
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
 const active=enabled&&Boolean(audio?.active)&&!fault&&!unknown;
 const title=unknown?'Verifica non disponibile':fault?'Intervento necessario':active?'Equalizzatore attivo':enabled&&dsd?'Equalizzatore escluso per DSD':enabled&&audio.mode==='armed-unsupported'?'Escluso per questo formato':enabled?'Equalizzatore pronto':'Equalizzatore disattivato';
 $('peqState').textContent=unknown?'Non verificato':fault?'Da verificare':active?'Attivo':enabled&&dsd?'Escluso per DSD':enabled?'Pronto':'Disattivato';$('peqDot').classList.toggle('idle',!active);$('peqDot').classList.toggle('armed',enabled&&!active);
 $('audioPath').textContent=title;$('audioPathNote').textContent=fault?(audio?.message||'Verifica il recupero del percorso audio da Daphile.'):enabled&&dsd?'La riproduzione DSD rimane nativa. I tuoi filtri si riattivano automaticamente con audio compatibile.':enabled?'I tuoi filtri sono pronti. Puoi modificare le bande anche senza musica.':'Puoi attivarlo anche senza musica. Si esclude automaticamente per DSD e si riattiva con audio compatibile.';
 $('peqToggle').disabled=audioBusy||Boolean(syncQueue.running)||!audio||!profile;$('peqToggle').textContent=audioBusy?'Attendi…':audio?.recovery_required?'Ripristina audio':enabled?'Disattiva equalizzatore':'Attiva equalizzatore';$('peqToggle').setAttribute('aria-pressed',String(enabled));
 paintSync();
}
async function status(){if(audioBusy||statusBusy||syncQueue.running)return;statusBusy=true;const epoch=audioEpoch;try{const s=await api('/api/status');if(audioBusy||epoch!==audioEpoch)return;renderAudio(s.audio,s);$('moduleVersion').textContent='CamillaDSP '+s.module_version;if(s.connected&&s.sample_rate&&engineRate!==s.sample_rate){engineRate=s.sample_rate;if($('rate').value==='auto'){rebuildAB();refreshChart();}}}
 catch{renderAudio(null,{connected:false});}finally{statusBusy=false;}}
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
$('presets').onchange=()=>{const p=snapshot.presets.find(p=>p.id===$('presets').value);if(p){setProfile(p);changed();}};
$('new').onclick=()=>{const p=clone(snapshot.presets[0]);p.id=uuid();p.name='Nuovo preset';setProfile(p);changed();};
$('duplicatePreset').onclick=()=>{const p=clone(profile);p.id=uuid();p.name=profile.name.slice(0,72)+' · copia';for(const ch of ['L','R'])p.peq[ch].forEach(b=>b.id=uuid());setProfile(p);changed();};
$('save').onclick=guard(async()=>{if(headphoneDefault())return;if(!profile||!validFields()||!$('presetName').value.trim())throw Error('Controlla nome e valori prima di salvare.');if(!editable())return;profile.name=$('presetName').value.trim();snapshot=await api('/api/presets/save',{revision:snapshot.revision,profile:clone(profile)});rememberEdits();render();message('');});
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
$('addBand').onclick=guard(()=>{prepareBandDraft();if(profile.peq[channel].length>=24)throw Error('Limite di 24 bande per canale');profile.peq[channel].push({id:uuid(),type:'Peaking',freq:1000,gain:0,q:1,enabled:true,label:''});syncLinked();markDraft();render();});
$('flatBands').onclick=()=>{
 prepareBandDraft();const old=clone(profile);undoable(()=>{setProfile(old);changed();});
 for(const ch of ['L','R'])for(const band of profile.peq[ch]){
  band.gain=0;
  if(!gainTypes.includes(band.type))band.enabled=false;
 }
 markDraft();render();
 message('Curve Dx e Sx rese flat. Le attenuazioni manuali restano invariate. Premi Applica modifiche per ascoltarle.');
};
for(const id of ['rate','zoom','showL','showR','scale','showBands'])$(id).onchange=()=>{if(id==='rate'){rebuildAB();refreshChart();}else drawGraph();};
function rebuildAB(){abChart=abProfile?clone(localPreview.calculate(abProfile,previewRate())):null;}
$('abA').onclick=()=>{abProfile=clone(profile);abChart=clone(chart);$('abB').disabled=false;$('abLabel').textContent='· A tratteggiato';drawGraph();message('A memorizzato per il confronto');};
$('abB').onclick=()=>{const tmp=clone(profile);setProfile(abProfile);abProfile=tmp;rebuildAB();drawGraph();changed();};
$('apply').onclick=$('applyBands').onclick=guard(async()=>{
 if(audioBusy)return;
 if(remoteConflict){editStore.write('@recovery',clone(profile));await load();remoteConflict=false;syncQueue.acknowledge();await status();message('');return;}
 if(!validFields())return;if(recoveredEdits&&syncQueue.version===syncQueue.ack)syncQueue.version++;
 await status();if(audioState)await syncQueue.flush(true);
});
$('refresh').onclick=guard(async()=>{await Promise.allSettled([!profile?load():Promise.resolve(),status()]);});
$('export').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(profile,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='peq-'+profile.id+'.json';a.click();URL.revokeObjectURL(url);};
$('import').onchange=guard(async()=>{const f=$('import').files[0];if(!f)return;try{if(f.size>131072||!f.name.toLowerCase().endsWith('.json'))throw Error('Importa un file JSON entro 128 KiB');let p;try{p=JSON.parse(await f.text());}catch{throw Error('Il file JSON non è leggibile.');}await api('/api/preview',{profile:p,rate:previewRate()});p.id=uuid();setProfile(p);changed();}finally{$('import').value='';}});
$('rewfile').onchange=guard(async()=>{const f=$('rewfile').files[0];if(!f)return;try{const name=f.name.toLowerCase();if(name.endsWith('.frd'))throw Error('Il formato FRD descrive una misura, non i filtri PEQ. In REW scegli Equaliser: Generic, poi Export filter settings as text e importa il TXT.');if(!name.endsWith('.txt'))throw Error('Seleziona il TXT dei filtri: in REW scegli Equaliser: Generic, poi Export filter settings as text. I file MDAT e REQ non sono supportati.');if(f.size>65536)throw Error('Il TXT dei filtri deve essere entro 64 KiB. Non usare il file della misura.');rewPreview=await api('/api/rew/preview',{text:await f.text(),channel,rate:previewRate()});$('rewSummary').textContent=`${rewPreview.bands.length} bande riconosciute per ${rewPreview.channel}. La conferma sostituirà ${profile.mode==='linked'?'le bande di entrambi i canali collegati':'le bande di questo canale'}.`;$('rewIssues').textContent=rewPreview.issues.map(x=>`Riga ${x.line}: ${x.reason}`).join('\n')||'Tutti i filtri sono supportati.';$('rewConfirm').disabled=!rewPreview.can_apply;$('rewDialog').showModal();}finally{$('rewfile').value='';}});
$('rewCancel').onclick=()=>$('rewDialog').close();$('rewConfirm').onclick=()=>{if(!rewPreview?.can_apply||!editable())return;const old=clone(profile);undoable(()=>{setProfile(old);changed();});channel=rewPreview.channel;profile.peq[channel]=clone(rewPreview.bands);syncLinked();$('rewDialog').close();changed();render();};
cursorBusy.run(async()=>{const s=await api('/api/session');if(s.authenticated)await start();}).catch(e=>{$('studio').hidden=false;message('Controller non disponibile: '+e.message,true);});

if(window.parent!==window){
 document.body.classList.add('embedded');let pending=false,last=0;
 const resize=()=>{if(pending)return;pending=true;requestAnimationFrame(()=>{pending=false;const height=Math.ceil(document.body.getBoundingClientRect().height)+24;if(height!==last&&height>0&&height<60000){last=height;parent.postMessage({type:'dat-settings-height',height},location.origin);}});};
 new ResizeObserver(resize).observe(document.body);window.addEventListener('load',resize);resize();
}

$('undo').onclick=guard(async()=>{const action=undoAction;undoAction=null;$('undo').hidden=true;if(action)await action();});
$('recoverEdits').onclick=guard(async()=>{const p=editStore.read('@recovery');if(!p)return;await api('/api/levels',{profile:p,rate:previewRate()});setProfile(p);editStore.remove('@recovery');$('recoverEdits').hidden=true;changed();});

window.addEventListener('pagehide',()=>{rememberEdits();editStore.flush();});
