'use strict';
(()=>{
const $=id=>document.getElementById(id);
let token='',active=null,activeAction='',automaticId=null,followingAutomatic=false,busy=false,pending=false,cancellable=false,lastResultId='',analyzed=false,online=false,restoreAvailable=null,uncertain=false;
const hardwareFacts=new Map();
const labels={'automatic-analyze':'Analisi','audio-setup':'Audio attuale','automatic-run':'AutoTune','automatic-restore':'Ripristino','power-review':'POWER','search-run':'Confronto esteso','stability':'Stabilità'};
const readActions=new Set(['automatic-analyze','audio-setup','power-review']);
const automaticFinal=new Set(['configured','retained-current','failed-restored','inconclusive-restored','restored','cancelled-restored','cancelled-external-intent-preserved','cancelled-before-activity-no-effects','no-change','blocked']);
function text(id,value){$(id).textContent=value??'';}
function controls(){
  for(const id of ['analyze','setup','power-read','runtime-read'])$(id).disabled=busy||!online;
  for(const id of ['restore','stability','configure'])$(id).disabled=busy||uncertain||!online;
  $('search').disabled=true;
  $('search').title='Usa AutoTune per confrontare le impostazioni, entro 30 minuti complessivi.';
  if(restoreAvailable===false)$('restore').disabled=true;
  $('cancel').disabled=pending||!busy||!active||!online||!cancellable;
}
async function api(action,extra={}){
  const response=await fetch('/cgi-bin/AutoTune',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra,...(token?{token}:{})}),signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('Risposta HTTP '+response.status);
  const result=await response.json();
  if(result.status==='failed')throw Error(result.reason||'Operazione non completata');
  return result;
}
function facts(result){
  const has=key=>Object.prototype.hasOwnProperty.call(result,key),unknown='Non rilevato';
  // A fresh snapshot replaces its own fields, including explicitly unknown
  // values. Unrelated POWER/job responses do not erase the last audio reading.
  if(has('hardware')){
    const h=result.hardware||{};hardwareFacts.set('Computer',String(h.model??unknown));hardwareFacts.set('Kernel',String(h.kernel??unknown));
  }
  if(has('audio_setup')||has('selected')||has('effective')||has('dac_status')){
    const a=(has('audio_setup')?result.audio_setup:result)||{},buffer=a.effective?.buffer_ms;
    hardwareFacts.set('DAC',String(a.selected?.product||a.selected?.name||a.selected?.alsa_id||unknown));
    hardwareFacts.set('Buffer effettivo',Number.isFinite(buffer)&&buffer>0?buffer+' ms':unknown);
  }
  $('hardware').replaceChildren();
  for(const [label,value]of hardwareFacts){const box=document.createElement('div');box.className='fact';const small=document.createElement('small');small.textContent=label;box.append(small,document.createTextNode(value));$('hardware').append(box);}
  if(result.runtime_summary)runtimeView(result.runtime_summary);
}
function runtimeView(r){
  if(r.read_only!==true)return;
  if(typeof r.usb_diagnostic_hold==='boolean')diagnosticNote(r.usb_diagnostic_hold);
  const unknown='Non rilevato',cpuText=v=>Array.isArray(v)?(v.length?v.map(n=>'CPU '+n).join(', '):'Nessuna'):unknown;
  const number=v=>Number.isFinite(v)?v.toLocaleString('it-IT'):unknown;
  const cpu=r.cpu||{},irq=(r.interrupts||[])[0],threads=r.threads||[];
  $('runtime-summary').hidden=false;
  const stamp=new Date(r.observed_at);
  text('runtime-at',Number.isFinite(stamp.getTime())?'Lettura delle '+stamp.toLocaleTimeString('it-IT'):'Ora della lettura non disponibile');
  text('runtime-cpu-main',Array.isArray(cpu.online)?cpu.online.length+' CPU disponibili':unknown);
  const governors=[...new Set((cpu.cores||[]).map(c=>c.governor).filter(v=>typeof v==='string'&&v))];
  text('runtime-governor',governors.length?'Governatore: '+governors.join(' / '):'Governatore non rilevato');
  $('runtime-cpus').replaceChildren();
  for(const n of cpu.online||[]){
    const c=(cpu.cores||[]).find(v=>v.cpu===n),chip=document.createElement('span');chip.className='cpu-chip';
    chip.textContent='CPU '+n+(Number.isFinite(c?.current_khz)?' · '+number(c.current_khz/1000)+' MHz':'');
    $('runtime-cpus').append(chip);
  }
  text('runtime-isolation','CPU isolate: '+cpuText(cpu.isolated));
  text('runtime-irq-main',irq?'IRQ '+irq.number:unknown);
  text('runtime-irq-placement',irq?'Riceve su '+cpuText(irq.effective_cpus):'Nessun interrupt del DAC osservato');
  $('runtime-irq-counters').replaceChildren();
  if(Array.isArray(irq?.counts_since_boot)){
    const peak=Math.max(1,...irq.counts_since_boot);
    irq.counts_since_boot.forEach((count,n)=>{
      const row=document.createElement('div'),label=document.createElement('span'),track=document.createElement('div'),bar=document.createElement('i'),value=document.createElement('span');
      row.className='irq-counter';track.className='irq-track';label.textContent='CPU '+n;value.textContent=number(count);
      bar.style.width=(100*count/peak)+'%';track.append(bar);row.append(label,track,value);$('runtime-irq-counters').append(row);
    });
  }
  $('runtime-priorities').replaceChildren();$('runtime-threads').replaceChildren();
  for(const t of threads){
    const role=t.role==='usb-irq'?'IRQ USB':'Uscita audio',row=document.createElement('div'),name=document.createElement('span'),value=document.createElement('strong');
    row.className='priority-row';name.textContent=role;value.textContent=(t.scheduler??unknown)+(Number.isFinite(t.priority)?' · '+t.priority:'');row.append(name,value);$('runtime-priorities').append(row);
    const tr=document.createElement('tr');
    for(const item of [role,(t.name||unknown)+' · TID '+t.tid,cpuText(t.allowed_cpus),t.scheduler??unknown,t.priority??unknown]){
      const td=document.createElement('td');td.textContent=String(item);tr.append(td);
    }
    $('runtime-threads').append(tr);
  }
  if(!threads.length)text('runtime-priorities','Nessun thread audio o USB osservato in questa lettura.');
}
function diagnosticNote(active){
  $('usb-diagnostic-note').hidden=!active;
  text('usb-diagnostic-note','USB: disattivazione delle porte temporaneamente sospesa per diagnosi. La scelta POWER salvata è conservata.');
}
function power(result){
  const r=result.power_inventory||result;$('power-rows').replaceChildren();
  for(const item of r.settings||[]){const tr=document.createElement('tr');for(const value of [item.label,item.saved_value??'Non disponibile',item.decision,item.criterion]){const td=document.createElement('td');td.textContent=String(value);tr.append(td);}$('power-rows').append(tr);}
  const usb=(r.settings||[]).find(v=>v.key==='hw_audio_usb_empty_ports');
  if(typeof usb?.runtime_evidence?.diagnostic_hold==='boolean')diagnosticNote(usb.runtime_evidence.diagnostic_hold);
}
function outcomeText(result){
  const reason=[result.message,result.reason,result.error].filter(Boolean).join(' ');
  const restored=result.status==='failed-restored'&&!result.recovery_errors?.length;
  if(restored){
    const intro='AutoTune non ha applicato una nuova configurazione. I parametri e la playlist precedenti sono stati ripristinati.';
    if(reason.includes('Hardware or material changed during baseline'))return intro+'\n\nDurante la misurazione iniziale, AutoTune ha rilevato una variazione dell’identità hardware oppure del brano in riproduzione. Il controllo non permette di distinguere quale dei due sia cambiato. Le misure iniziali non sono quindi confrontabili con quelle delle impostazioni alternative: per evitare una scelta inaffidabile, AutoTune ha interrotto il confronto prima di provarle.\n\nPer riprovare, attendi che il lettore sia pronto e che la scansione della libreria sia terminata. Durante AutoTune evita di cambiare brano, DAC o impostazioni e di avviare altri test audio. Se il problema si ripete senza interventi, conserva i dettagli nei Controlli avanzati: occorre verificare perché le letture cambiano.';
    if(reason.includes('Reference window refused'))return intro+'\n\nLa misurazione iniziale non ha superato i controlli di completezza e stabilità. Senza un riferimento affidabile, AutoTune non può stabilire se le impostazioni alternative siano migliori e non le ha provate.\n\nAttendi che il sistema sia pronto e riprova senza altre attività o modifiche audio in corso. Se il problema si ripete, i Controlli avanzati contengono il motivo tecnico da verificare.';
    return intro+'\n\nLa prova si è interrotta prima di verificare una configurazione migliore. Consulta i Controlli avanzati per il motivo tecnico; riprova quando il sistema è pronto e non sono in corso altre modifiche o prove audio.';
  }
  return result.message||result.reason||result.error;
}
function finished(job){
  const r=job.result||{};text('diagnostic',JSON.stringify(r,null,2));facts(r);if(r.settings||r.power_inventory)power(r);
  const ok=['configured','retained-current','no-change','current-verified','analyzed','restored','rolled-back','cancelled-restored','pass-observed-windows','finished-restored','observed','reviewed'].includes(r.status);
  $('result').className=ok?'success':'error';
  const unchanged=r.optimization_outcome==='retained-current';
  text('result',unchanged?'Configurazione attuale verificata e conservata. Nessun miglioramento dimostrato dalle alternative.':outcomeText(r)||((labels[job.action]||'Operazione')+': '+(r.status||'Esito da verificare')));
  if(job.action==='automatic-analyze')text('summary',uncertain?'Lettura aggiornata. Lo stato della prova precedente non è ancora verificato: nuove prove e modifiche restano sospese.':r.eligible?'Sistema riconosciuto. Puoi avviare AutoTune.':r.message||'Completa la preparazione indicata prima delle prove.');
}
function progressView(result,fallback){
  $('progress-box').hidden=false;const percent=result.status==='running'?result.percent:null;
  if(Number.isFinite(percent))$('progress').value=Math.min(100,Math.max(0,percent));else $('progress').removeAttribute('value');
  const timing=result.timing,elapsed=timing?.elapsed_seconds,remaining=timing?.remaining_seconds;
  const seconds=value=>Math.floor(value/60)+' min '+Math.floor(value%60)+' s';
  const timeText=timing?.deadline_valid===true&&Number.isFinite(elapsed)&&Number.isFinite(remaining)?
    ' Tempo trascorso: '+seconds(elapsed)+'. Tempo massimo restante, compreso il recupero: '+seconds(remaining)+'.':'';
  text('progress-text',(result.message||fallback)+timeText);
}
function unknownJob(){
  uncertain=true;busy=false;active=null;cancellable=false;$('progress-box').hidden=true;
  text('summary','Il servizio è stato riavviato. Lo stato della prova precedente non è verificato: sono disponibili le letture, mentre nuove prove e modifiche restano sospese.');
}
async function poll(){
  try{
    const state=await api('status'),brokerChanged=Boolean(token&&state.token&&token!==state.token);
    token=state.token||token;online=state.enabled!==false&&Boolean(token);
    if(state.automatic?.kind==='automatic-configuration-v2')restoreAvailable=state.automatic.restore_available===true;
    text('connection',online?'Servizio disponibile':'Backend disabilitato');
    const jobs=state.jobs||[],automatic=state.automatic||{},v2=automatic.kind==='automatic-configuration-v2';
    const job=(active&&jobs.find(j=>j.id===active))||jobs.find(j=>j.running||j.recovering);
    if(job&&(job.running||job.recovering)){
      active=job.id;activeAction=job.action;busy=true;
      cancellable=job.running===true&&!job.recovering&&!(job.action==='automatic-run'&&['recovering','recovery-required','timed-out-recovery-required'].includes(automatic.status));
      if(job.action==='automatic-run'&&v2&&['running','recovering'].includes(automatic.status))automaticId=automatic.id;
      progressView(automatic.status==='running'?automatic:{},(labels[job.action]||'Operazione')+' in corso…');
    }else if(v2&&['running','recovering','recovery-required','timed-out-recovery-required'].includes(automatic.status)){
      // Broker jobs are volatile. The durable workflow remains authoritative
      // across broker restarts and page reloads; recovery is never cancelled.
      automaticId=automatic.id;followingAutomatic=true;activeAction='automatic-run';busy=true;
      active=automatic.status==='running'&&automatic.id?'automatic:'+automatic.id:null;
      cancellable=Boolean(active);
      progressView(automatic,'Recupero della prova precedente: attendi la verifica.');
    }else if(!pending&&v2&&automaticId&&automatic.id===automaticId&&automaticFinal.has(automatic.status)&&
             (brokerChanged||uncertain||followingAutomatic)){
      const id='automatic:'+automaticId+':'+automatic.status;
      busy=false;uncertain=false;active=null;automaticId=null;followingAutomatic=false;$('progress-box').hidden=true;
      if(id!==lastResultId){lastResultId=id;finished({id,action:'automatic-run',result:automatic});}
    }else if(!pending&&brokerChanged&&active&&!job){
      if(readActions.has(activeAction)){
        busy=false;active=null;$('progress-box').hidden=true;
        text('summary','Servizio riavviato: il risultato della lettura precedente non è disponibile. Puoi aggiornarla.');
      }else unknownJob();
    }else if(!pending&&(!active||job)){
      busy=false;$('progress-box').hidden=true;
      if(job&&job.id!==lastResultId){lastResultId=job.id;finished(job);}active=null;
    }
    if(!online)text('summary','Abilita Audio Setup/AutoTune nella sezione POWER di Settings, salva e riapri questa pagina.');
    controls();
    if(online&&!busy&&!pending&&!uncertain&&!analyzed){analyzed=true;await start('automatic-analyze');}
  }catch(error){
    online=false;text('connection','Collegamento interrotto');
    text('summary',(busy?'La prova potrebbe essere ancora in corso. Attendo il collegamento per verificarne lo stato. ':'Impossibile leggere il servizio. ')+error.message);controls();
  }finally{setTimeout(poll,2000);}
}
async function start(action,extra={}){
  if(pending||busy&&action!=='cancel'||uncertain&&!readActions.has(action)&&action!=='cancel'||action==='cancel'&&!cancellable)return;
  const requestToken=token;
  try{
    pending=true;busy=true;controls();text('result','');$('thermal-result').hidden=true;
    if(action!=='cancel')activeAction=action;
    if(action==='automatic-run'){automaticId=null;followingAutomatic=false;}
    const r=await api(action,extra);pending=false;
    if(r.id&&token!==requestToken){if(!active?.startsWith('automatic:'))unknownJob();controls();return;}
    active=r.id||active;
    if(r.id&&action!=='cancel')cancellable=true;
    if(action==='cancel'){text('result','Interruzione richiesta. Attendo la conferma del recupero.');}
    else if(!r.id){finished({action,result:r});busy=false;active=null;}
    if(r.id)text('summary',(labels[action]||'Operazione')+' richiesta.');controls();
  }catch(error){pending=false;busy=Boolean(active);controls();$('result').className='error';text('result',error.message);}
}
$('analyze').onclick=()=>start('automatic-analyze');$('setup').onclick=()=>start('audio-setup');
$('runtime-read').onclick=()=>start('automatic-analyze');
$('configure').onclick=()=>{if(!busy&&online&&confirm('AutoTune può interrompere e ripetere la riproduzione per le prove. Al termine recupera la playlist. Durata massima totale: 30 minuti, compreso il recupero. Avviare?'))start('automatic-run',{confirm_audio:true});};
$('restore').onclick=()=>{if(confirm('Ripristinare la configurazione precedente? La riproduzione può interrompersi.'))start('automatic-restore');};
$('power-read').onclick=()=>start('power-review');$('stability').onclick=()=>start('stability',{duration:60});
$('search').onclick=()=>text('result','Il confronto esteso non è disponibile. Usa AutoTune, con durata massima totale di 30 minuti.');
$('cancel').onclick=()=>active&&start('cancel',{job:active});
window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data?.type==='dat-settings-theme'&&['light','dark'].includes(event.data.theme))document.documentElement.dataset.theme=event.data.theme;});
new ResizeObserver(()=>{if(parent!==window)parent.postMessage({type:'dat-settings-height',height:document.documentElement.scrollHeight},location.origin);}).observe(document.body);
controls();poll();
})();
