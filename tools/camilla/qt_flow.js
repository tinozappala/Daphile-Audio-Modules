// Automated navigation of the module served through its real same-origin bridge.
// This test never injects application code, substitutes browser APIs or mocks the DSP.
(async () => {
  const e = id => document.getElementById(id);
  const check = (ok, msg) => { if (!ok) throw Error(msg); };
  const until = async (fn, msg) => {
    const end = Date.now()+8500;
    do {
      if (await fn()) return;
      await new Promise(resolve=>setTimeout(resolve,35));
    } while (Date.now()<end);
    throw Error(msg + ' · ' + ['message','compareState','matchSummary','rewSummary','listenHint'].map(id=>e(id)?.textContent||'').join(' / '));
  };
  const click = id => {
    check(!e(id).disabled, 'Controllo disattivato: '+id);
    e(id).click();
  };
  const select = (id,value) => {
    const field = e(id); field.value=value;
    check(field.value===value, 'Scelta assente '+id);
    field.dispatchEvent(new Event('change',{bubbles:true}));
  };
  const file = (id, contents, name, type) => {
    const transfer=new DataTransfer();
    transfer.items.add(new File([contents],name,{type}));
    e(id).files=transfer.files;
    e(id).dispatchEvent(new Event('change',{bubbles:true}));
  };
  const api = async p => {const r=await fetch('./api/'+p);check(r.ok,'HTTP '+p+' '+r.status);return r.json();};
  const shown = id => { const rect=e(id).getBoundingClientRect();return rect.width>0&&rect.height>0; };
  const setup = async () => {
    await until(()=>e('listenPresets')?.value==='sample-peq' && !e('openCompare').disabled,'Il profilo Salotto non è pronto');
    check(shown('listenView')&&!shown('tuneView'),'Apertura Ascolto');
  };
  const report = () => ({
    viewport:[innerWidth,innerHeight], docWidth:document.documentElement.scrollWidth,
    listen:shown('listenView'), tune:shown('tuneView'), text:e('audioPath').textContent,
    profile:e('listenPresets').value, userAgent:navigator.userAgent,
    graphViewBox:shown('graph')?e('graph').getAttribute('viewBox'):null
  });
  const snap = (label,rms=null) => new Promise((resolve,reject) => {
    window.qaResume = resolve;
    window.qaState={kind:'observation',label,rms,metrics:report()};
    const limit=setTimeout(()=>reject(Error('Rilevazione non completata: '+label)),8500);
    window.qaResume=()=>{clearTimeout(limit); window.qaState={kind:'waiting'}; resolve();};
  });
  try {
    await setup();
    const original = await api('profile/state'), catalog = await api('presets');
    await snap('01-ascolto-desktop',[-6,-2]);
    await snap('02-ascolto-mobile');
    check(document.documentElement.scrollWidth<=innerWidth+1,'Ascolto scorre in orizzontale');
    click('viewTune');
    await until(()=>shown('tuneView')&&!shown('listenView'),'Taratura');
    check(document.documentElement.scrollWidth<=innerWidth+1,'Taratura scorre in orizzontale');
    await snap('03-taratura-mobile');
    await until(()=>innerWidth>=1000 && e('graph').viewBox.baseVal.height===410,
                'Il grafico deve riprendere la scala desktop dopo la rotazione');
    await snap('04-taratura-desktop');
    // Confirm a real pointer focus on an SVG marker and its specific zone.
    const marker=e('graph').querySelector('[data-band]');
    check(marker,'Punto curva sinistra assente'); marker.focus();
    check(e('graph').querySelector('.bandwidth-markers[aria-label]'),'Zona di intervento assente');
    click('viewListen');
    click('openCompare');
    await until(()=>e('compareDialog').open&&!e('hearA').disabled,'Due profili pronti');
    select('compareA','flat');
    await until(()=>!e('hearA').disabled,'Flat disponibile');
    click('hearA');await until(()=>e('hearA').getAttribute('aria-pressed')==='true','Ascolto A');
    await snap('05-confronto-A',[0,0]);
    click('hearB');await until(()=>e('hearB').getAttribute('aria-pressed')==='true','Ascolto B');
    await snap('06-confronto-B',[-6,-2]);
    // The same 1 kHz stereo PCM used by the external gain oracle, here in a WAV.
    const n=144000, b=new ArrayBuffer(44+n*4), v=new DataView(b);
    const tag=(o,t)=>{for(let i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i));};
    tag(0,'RIFF');v.setUint32(4,36+n*4,true);tag(8,'WAVE');tag(12,'fmt ');
    v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,2,true);
    v.setUint32(24,48000,true);v.setUint32(28,192000,true);v.setUint16(32,4,true);
    v.setUint16(34,16,true);tag(36,'data');v.setUint32(40,n*4,true);
    for(let i=0;i<n;i++){const s=Math.round(7000*Math.sin(2*Math.PI*(i%48)/48));v.setInt16(44+4*i,s,true);v.setInt16(46+4*i,s,true);}
    e('matchTools').open=true;file('matchFile',b,'estratto.wav','audio/wav');
    await until(()=>e('matchSummary').textContent.includes('dB')&&!e('hearA').disabled,'Allineamento');
    click('hearA');
    await until(()=>e('hearA').getAttribute('aria-pressed')==='true','Ascolto allineato');
    const common=10*Math.log10((Math.pow(10,-.6)+Math.pow(10,-.2))/2);
    await snap('07-confronto-volume',[common,common]);
    click('compareClose');await until(()=>!e('compareDialog').open,'Ripristino');
    check(JSON.stringify(await api('profile/state'))===JSON.stringify(original),'Il confronto ha modificato il profilo');
    check(JSON.stringify(await api('presets'))===JSON.stringify(catalog),'Il confronto ha modificato l’archivio');
    click('viewTune'); click('openRew');
    const text=g=>'Filter Settings file\nFilter 1: ON PK Fc 1000 Hz Gain '+g+' dB Q 1.00\n';
    select('rewTarget','pair');file('rewFirst',text(-3),'sinistro.txt','text/plain');
    file('rewSecond','Filter 1: unsupported','destro.txt','text/plain');
    await until(()=>!e('rewIssues').hidden,'Rifiuto file incompleto');
    check(e('rewConfirm').disabled,'La coppia incompleta sarebbe applicabile');
    file('rewSecond',text(-9),'destro.txt','text/plain');
    await until(()=>!e('rewConfirm').disabled,'Accettazione coppia');
    e('rewCurve').open=true;
    await until(()=>Boolean(e('rewGraph').querySelector('path')),'Curva di anteprima');
    await snap('08-importa-coppia',[-6,-2]);
    click('rewCancel');await until(()=>!e('rewDialog').open,'Annullamento');
    check(JSON.stringify(await api('profile/state'))===JSON.stringify(original),'Annulla ha cambiato il profilo');
    click('openRew');select('rewTarget','pair');
    file('rewFirst',text(-3),'sinistro.txt','text/plain');file('rewSecond',text(-9),'destro.txt','text/plain');
    await until(()=>!e('rewConfirm').disabled,'Coppia pronta');
    click('rewConfirm');await until(()=>!e('rewDialog').open,'Conferma importazione');
    await until(async()=>((await api('profile/state')).profile.peq.R[0].gain===-9),'Importato sul motore');
    click('save');await until(async()=>(await api('presets')).presets.find(p=>p.id==='sample-peq').peq.R[0].gain===-9,'Salva profilo');
    await snap('09-taratura-applicata',[-3,-9]);
    window.qaState={kind:'reload'};
  }catch(error){window.qaState={kind:'error',error:String(error?.message||error)};}
})();
