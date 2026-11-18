'use strict';
(()=>{
const $=id=>document.getElementById(id);
let token='',active=null,activeAction='',automaticId=null,followingAutomatic=false,busy=false,pending=false,cancellable=false,lastResultId='',analyzed=false,online=false,restoreAvailable=null,uncertain=false,ready=false;
const hardwareFacts=new Map();
const labels={'automatic-analyze':'Controllo del lettore','automatic-prepare-player':'Preparazione del lettore','audio-setup':'Lettura delle impostazioni audio','automatic-run':'Prova AutoTune','automatic-restore':'Ripristino','power-review':'Controllo delle impostazioni POWER','stability':'Controllo della stabilità'};
const readActions=new Set(['automatic-analyze','audio-setup','power-review']);
const automaticFinal=new Set(['configured','retained-current','failed-restored','inconclusive-restored','restored','cancelled-restored','cancelled-external-intent-preserved','cancelled-before-activity-no-effects','no-change','blocked']);
function text(id,value){$(id).textContent=value??'';}
function controls(){
  for(const id of ['analyze','setup','power-read','runtime-read','recheck'])$(id).disabled=busy||!online;
  for(const id of ['restore','stability','configure'])$(id).disabled=busy||uncertain||!online;
  $('configure').disabled ||= !ready;
  $('search').disabled=true;
  $('search').title='Usa AutoTune per confrontare le impostazioni, entro 30 minuti complessivi.';
  if(restoreAvailable===false)$('restore').disabled=true;
  $('cancel').disabled=pending||!busy||!active||!online||!cancellable;
}
async function api(action,extra={}){
  const response=await fetch('/cgi-bin/AutoTune',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra,...(token?{token}:{})}),signal:AbortSignal.timeout(15000)});
  if(!response.ok)throw Error('HTTP '+response.status);
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
const recheck='Torna qui e premi Ricontrolla.';
const support='Se il problema continua, apri Controlli avanzati e conserva i dettagli per l’assistenza.';
const retryTrial='Premi Ricontrolla. Quando il lettore è pronto, premi Avvia AutoTune.';
const pendingRecovery=['recovering','recovery-required','timed-out-recovery-required','interrupted-recovery-unverified','rollback-incomplete'];
const preparationNotices={
  'pause-first':['Il lettore sta riproducendo un brano.','Premi Ricontrolla per aggiornare lo stato del lettore.'],
  'prepare-player':['Il lettore conserva il punto in cui hai interrotto il brano.','Premi Ricontrolla per aggiornare lo stato del lettore.'],
  'player-off':['Il lettore è spento.','Apri Audio Player e accendi il lettore.',recheck],
  'shuffle-on':['La riproduzione casuale è attiva.','Apri Audio Player e disattiva la riproduzione casuale.',recheck],
  'player-group':['Il lettore è in un gruppo.','Apri Audio Player e separa il lettore dagli altri.',recheck],
  'player-group-check':['AutoTune non riesce a controllare il gruppo del lettore.','Apri Audio Player e verifica che il lettore sia usato da solo.',recheck,support],
  'queue-full':['La coda è piena per questa prova.','In Audio Player salva la coda come playlist se vuoi conservarla. Libera spazio nella coda.',recheck],
  'library-scan':['La libreria musicale si sta aggiornando.','Attendi la fine dell’aggiornamento.',recheck],
  'library-check':['Lo stato della libreria non è disponibile.','Apri Audio Player e verifica che la libreria si apra.',recheck,support],
  'audio-output':['AutoTune non riesce a identificare l’uscita audio.','In Settings, controlla che sia selezionata una sola uscita audio. Se usi un convertitore esterno, controlla che sia acceso e collegato.',recheck],
  'audio-format':['AutoTune non trova un formato di prova per l’uscita audio.','Controlla l’uscita audio in Settings e salva le impostazioni.',recheck,support],
  'player-unreadable':['AutoTune non riesce a leggere lo stato del lettore.','Apri Audio Player e verifica che il lettore e la coda siano visibili.',recheck,support],
  'queue-check':['AutoTune non riesce a controllare la coda.','Apri Audio Player e verifica che la coda si apra.',recheck,support],
  'ram-on':['La riproduzione in RAM è attiva.','Apri RAM e disattiva la riproduzione in RAM. Attendi la conferma.',recheck],
  'audio-busy':['È in corso un’altra operazione audio.','Attendi che finisca.',recheck],
  'recovery-pending':['Il ripristino della prova precedente è ancora da verificare.','Attendi che lo stato si aggiorni.',support],
  'player-changed':['Il lettore o la coda sono cambiati durante il controllo.','Verifica in Audio Player che la coda sia quella che vuoi usare.',recheck],
  'stop-unverified':['AutoTune non è riuscito a verificare che il brano sia all’inizio.','Apri Audio Player e verifica che il lettore sia fermo.',recheck,support]
};
function preparationCode(result){
  // Older reports remain readable without showing their diagnostic text.
  if(result.reason_code)return result.reason_code;
  const old=String(result.eligibility_reason||result.reason||'');
  return old.includes('requires an already stopped, powered player')?'prepare-player':'';
}
function showNotice(notice,kind=''){
  const [title,...steps]=notice,box=$('result');
  box.className=kind;
  const heading=document.createElement('strong');heading.textContent=title;
  box.replaceChildren(heading);
  if(!steps.length)return;
  const list=document.createElement('ol');
  for(const step of steps){const item=document.createElement('li');item.textContent=step;list.append(item);}
  box.append(list);
}
function outcome(result,action){
  const status=result.status,code=preparationCode(result);
  if(status==='player-prepared')return ['Lettore pronto. La coda è conservata.','Premi Avvia AutoTune per iniziare la prova.'];
  if(status==='analyzed'&&(result.eligible||result.can_prepare))return ['Puoi avviare la prova.','Premi Avvia AutoTune. Il pannello ti dirà quando il lettore è pronto e la prova inizia.'];
  if(status==='blocked'||status==='analyzed')return preparationNotices[code]||['AutoTune non è ancora pronto.','Apri Audio Player e controlla che il lettore sia acceso e la coda visibile.',recheck,support];
  if(pendingRecovery.includes(status))return preparationNotices['recovery-pending'];
  if(status==='configured')return ['Prova completata. AutoTune ha applicato le impostazioni migliori tra quelle provate e ha ripristinato la coda.','Puoi tornare in Audio Player e riprendere l’ascolto.'];
  if(status==='retained-current')return [result.optimization_outcome==='no-applicable-alternative'?'AutoTune ha conservato le impostazioni perché non c’erano alternative adatte.':'AutoTune ha conservato le impostazioni perché le prove non hanno mostrato un vantaggio ripetibile.','Puoi tornare in Audio Player e riprendere l’ascolto.'];
  if(status==='inconclusive-restored')return ['Il confronto non è arrivato a un risultato. Impostazioni e coda sono state ripristinate.',retryTrial];
  if(status==='failed-restored'){
    if(result.recovery_errors?.length)return preparationNotices['recovery-pending'];
    return [result.interruption_reason?'Le impostazioni audio sono cambiate durante la prova. La coda è stata recuperata.':'La prova si è interrotta. Impostazioni e coda sono state ripristinate.',
      'Attendi la fine delle altre attività e lascia ferme le impostazioni audio.',retryTrial,support];
  }
  if(['restored','rolled-back'].includes(status))return ['Ripristino completato.','Puoi tornare in Audio Player.'];
  if(status==='no-change')return ['Le impostazioni sono già al loro posto.'];
  if(status==='cancelled-before-activity-no-effects')return ['Prova annullata prima di usare il lettore.',retryTrial];
  if(status==='cancelled-restored')return ['Prova interrotta. Impostazioni e coda sono state recuperate.','Puoi tornare in Audio Player.'];
  if(status==='cancelled-external-intent-preserved')return ['Prova annullata. Il lettore mantiene la riproduzione scelta dopo l’avvio.',retryTrial];
  if(['current-verified','pass-observed-windows','finished-restored','observed','reviewed'].includes(status))return ['Controllo completato.'];
  if(action==='audio-setup'&&result.audio_setup)return ['Impostazioni audio lette.'];
  if(action==='power-review'&&(result.settings||result.power_inventory))return ['Impostazioni POWER lette.','Apri Parametri POWER per vedere il risultato.'];
  return ['AutoTune non ha potuto completare l’operazione.',recheck,support];
}
function finished(job){
  const r=job.result||{};text('diagnostic',JSON.stringify(r,null,2));facts(r);if(r.settings||r.power_inventory)power(r);
  if(['automatic-analyze','automatic-prepare-player','automatic-run'].includes(job.action)){
    ready=r.eligible===true||(r.status==='analyzed'&&r.can_prepare===true);
    text('summary',ready?'Puoi avviare AutoTune.':'Segui i passaggi nella sezione AutoTune qui sotto.');
  }
  const ok=['player-prepared','configured','retained-current','no-change','current-verified','restored','rolled-back','cancelled-restored','pass-observed-windows','finished-restored','observed','reviewed'].includes(r.status)||r.eligible===true||r.can_prepare===true;
  showNotice(uncertain?preparationNotices['recovery-pending']:outcome(r,job.action),ok?'success':'');
}
function progressView(result,fallback){
  $('progress-box').hidden=false;const percent=result.status==='running'?result.percent:null;
  if(Number.isFinite(percent))$('progress').value=Math.min(100,Math.max(0,percent));else $('progress').removeAttribute('value');
  const timing=result.timing,elapsed=timing?.elapsed_seconds,remaining=timing?.remaining_seconds;
  const seconds=value=>Math.floor(value/60)+' min '+Math.floor(value%60)+' s';
  const timeText=timing?.deadline_valid===true&&Number.isFinite(elapsed)&&Number.isFinite(remaining)?
    ' Tempo trascorso: '+seconds(elapsed)+'. Tempo massimo restante, compreso il recupero: '+seconds(remaining)+'.':'';
  const phases={preflight:'Il lettore è pronto. Preparo la prova.',setup:'Preparo la prova. Lascia il lettore e le impostazioni come sono.',search:'Confronto le impostazioni. Attendi la fine della prova.',apply:'Verifico le impostazioni selezionate. Attendi il risultato.',recovery:'Ripristino la coda e controllo le impostazioni. Attendi la conferma.'};
  const notice=pendingRecovery.includes(result.status)?'Sto verificando il ripristino. Attendi che la pagina si aggiorni.':phases[result.phase]||fallback;
  text('progress-text',notice+timeText);
}
function unknownJob(){
  uncertain=true;busy=false;active=null;cancellable=false;$('progress-box').hidden=true;
  text('summary','La pagina sta verificando la prova precedente.');
  showNotice(preparationNotices['recovery-pending']);
}
async function poll(){
  try{
    const state=await api('status'),brokerChanged=Boolean(token&&state.token&&token!==state.token);
    token=state.token||token;online=state.enabled!==false&&Boolean(token);
    if(state.automatic?.kind==='automatic-configuration-v2')restoreAvailable=state.automatic.restore_available===true;
    text('connection',online?'AutoTune disponibile':'AutoTune disattivato');
    const jobs=state.jobs||[],automatic=state.automatic||{},v2=automatic.kind==='automatic-configuration-v2';
    const job=(active&&jobs.find(j=>j.id===active))||jobs.find(j=>j.running||j.recovering);
    if(job&&(job.running||job.recovering)){
      active=job.id;activeAction=job.action;busy=true;
      cancellable=job.running===true&&!job.recovering&&job.action!=='automatic-prepare-player'&&!(job.action==='automatic-run'&&pendingRecovery.includes(automatic.status));
      if(job.action==='automatic-run'&&v2&&['running','recovering'].includes(automatic.status))automaticId=automatic.id;
      const measuring=job.action==='automatic-run'&&['running','recovering'].includes(automatic.status);
      const waiting=job.action==='automatic-run'?'Preparo il lettore. Attendi l’inizio della prova.':(labels[job.action]||'Operazione')+' in corso. Attendi il risultato.';
      progressView(measuring?automatic:{},waiting);
    }else if(v2&&['running','recovering','recovery-required','timed-out-recovery-required'].includes(automatic.status)){
      // Broker jobs are volatile. The durable workflow remains authoritative
      // across broker restarts and page reloads; recovery is never cancelled.
      automaticId=automatic.id;followingAutomatic=true;activeAction='automatic-run';busy=true;
      active=automatic.status==='running'&&automatic.id?'automatic:'+automatic.id:null;
      cancellable=Boolean(active);
      progressView(automatic,'Attendi la fine del ripristino.');
      if(automatic.status!=='running'&&automatic.status!=='recovering')showNotice(preparationNotices['recovery-pending']);
    }else if(!pending&&v2&&automaticId&&automatic.id===automaticId&&automaticFinal.has(automatic.status)&&
             (brokerChanged||uncertain||followingAutomatic)){
      const id='automatic:'+automaticId+':'+automatic.status;
      busy=false;uncertain=false;active=null;automaticId=null;followingAutomatic=false;$('progress-box').hidden=true;
      if(id!==lastResultId){lastResultId=id;finished({id,action:'automatic-run',result:automatic});}
    }else if(!pending&&brokerChanged&&active&&!job){
      if(readActions.has(activeAction)){
        busy=false;active=null;$('progress-box').hidden=true;
        text('summary','Il collegamento è tornato. Premi Ricontrolla per aggiornare lo stato del lettore.');
      }else unknownJob();
    }else if(!pending&&(!active||job)){
      busy=false;$('progress-box').hidden=true;
      if(job&&job.id!==lastResultId){lastResultId=job.id;finished(job);}active=null;
    }
    if(!online)text('summary','In Settings, apri POWER e attiva Audio Setup/AutoTune. Salva e riapri questa pagina.');
    controls();
    if(online&&!busy&&!pending&&!uncertain&&!analyzed){analyzed=true;await start('automatic-analyze');}
  }catch(error){
    online=false;text('connection','Collegamento interrotto');
    text('summary',busy?'Il collegamento si è interrotto durante un’operazione. Attendi che la pagina verifichi lo stato.':'Il collegamento si è interrotto. Verifica che la pagina Audio Player si apra, poi ricarica Settings.');controls();
  }finally{setTimeout(poll,2000);}
}
async function start(action,extra={}){
  if(pending||busy&&action!=='cancel'||uncertain&&!readActions.has(action)&&action!=='cancel'||action==='cancel'&&!cancellable)return;
  const requestToken=token;
  try{
    pending=true;busy=true;controls();text('result','');$('thermal-result').hidden=true;
    if(action!=='cancel')activeAction=action;
    if(action==='automatic-run'){automaticId=null;followingAutomatic=false;showNotice(['Preparo il lettore. Attendi l’inizio della prova.']);}
    const r=await api(action,extra);pending=false;
    if(r.id&&token!==requestToken){if(!active?.startsWith('automatic:'))unknownJob();controls();return;}
    active=r.id||active;
    if(r.id&&action!=='cancel'&&action!=='automatic-prepare-player')cancellable=true;
    if(action==='cancel'){showNotice(['Interruzione richiesta. Attendi la conferma del ripristino.']);}
    else if(!r.id){finished({action,result:r});busy=false;active=null;}
    if(r.id)text('summary',(labels[action]||'Operazione')+' in corso. Attendi il risultato.');controls();
  }catch(error){
    pending=false;busy=Boolean(active);controls();
    text('diagnostic',error.message);
    showNotice(['Non è arrivata la conferma dell’operazione.','Attendi che la pagina aggiorni lo stato prima di riprovare.',support],'error');
  }
}
$('analyze').onclick=()=>start('automatic-analyze');$('setup').onclick=()=>start('audio-setup');
$('runtime-read').onclick=()=>start('automatic-analyze');
$('recheck').onclick=()=>start('automatic-analyze');
$('configure').onclick=()=>{if(!busy&&online&&ready)start('automatic-run',{confirm_audio:true,prepare_player:true});};
$('restore').onclick=()=>{if(confirm('Ripristinare la configurazione precedente? La riproduzione può interrompersi.'))start('automatic-restore');};
$('power-read').onclick=()=>start('power-review');$('stability').onclick=()=>start('stability',{duration:60});
$('search').onclick=()=>showNotice(['Il confronto esteso non è disponibile.','Usa Avvia AutoTune per confrontare le impostazioni.']);
$('cancel').onclick=()=>active&&start('cancel',{job:active});
window.addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data?.type==='dat-settings-theme'&&['light','dark'].includes(event.data.theme))document.documentElement.dataset.theme=event.data.theme;});
new ResizeObserver(()=>{if(parent!==window)parent.postMessage({type:'dat-settings-height',height:document.documentElement.scrollHeight},location.origin);}).observe(document.body);
controls();poll();
})();
