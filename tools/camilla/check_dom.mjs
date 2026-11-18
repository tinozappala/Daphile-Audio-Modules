// Integration test for the actual module HTML and JS. LinkeDOM provides DOM
// events and SVG trees; there is no rendering, audio or API mock here.
import vm from "node:vm";
import {readFile,open} from "node:fs/promises";
import {pathToFileURL} from "node:url";
import {webcrypto} from "node:crypto";
import {setTimeout as wait} from "node:timers/promises";
import assert from "node:assert/strict";
const [base, rawPath, wavPath, library] = process.argv.slice(2);
const {parseHTML, HTMLSelectElement, HTMLInputElement} = await import(library ? pathToFileURL(library) : "linkedom");
const requests = [];
let cookie = "";
async function get(url, options = {}) {
  const target = new URL(url, base);
  assert.equal(target.origin, new URL(base).origin);
  const headers = {...options.headers};
  if (cookie) headers.Cookie = cookie;
  if (options.method === "POST") headers.Origin = target.origin;
  const response = await fetch(target, {...options, headers});
  const setCookie = response.headers.get("Set-Cookie");
  if (setCookie) cookie = setCookie.split(";")[0];
  requests.push([options.method || "GET", target.pathname.substring(new URL(base).pathname.length), response.status]);
  return response;
}
const api = async (path) => (await get("api/"+path)).json();
async function until(condition, label) {
  const deadline = Date.now()+7500;
  do {
    if (await condition()) return;
    await wait(25);
  } while (Date.now() < deadline);
  const page = current?.document;
  throw Error(label + ": " + ["message","compareState","matchSummary","rewSummary","presetStatus","listenHint"].map(id => page?.getElementById(id)?.textContent||"").join(" / ") + " " + JSON.stringify(requests.slice(-10)));
}
Object.defineProperty(HTMLSelectElement.prototype, "value", {
  get(){const e=this.querySelector("option[selected]")||this.querySelector("option");return e?e.getAttribute("value")??e.textContent:"";},
  set(v){for(const e of this.querySelectorAll("option")) e.toggleAttribute("selected",(e.getAttribute("value")??e.textContent)===String(v));}
});
HTMLSelectElement.prototype.add=function(e){this.append(e);};
for (const field of ["min","max","step"]) Object.defineProperty(HTMLInputElement.prototype,field,{
  get(){return this.getAttribute(field)||"";},set(v){this.setAttribute(field,v);}
});
Object.defineProperty(HTMLInputElement.prototype,"checked",{
  get(){return this.hasAttribute("checked");},set(v){this.toggleAttribute("checked",Boolean(v));}
});
let storage=new Map(), current;
async function setup(){
  const html=await (await get("")).text();
  const {document,window}=parseHTML(html);
  const timers=new Set(), intervals=new Set();
  const local=new Map();
  const sandbox={
    document, Event:window.Event, CustomEvent:window.CustomEvent, window:null, parent:null,
    crypto:webcrypto, structuredClone, AbortController, performance,
    URL, Blob, File, TextDecoder, TextEncoder, Uint8Array, DataView, ArrayBuffer,
    Set, Map, Date, Math, console, fetch:get,
    location:new URL(base), navigator:{},
    localStorage:{getItem:k=>storage.get(k)||null, setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)},
    sessionStorage:{getItem:k=>local.get(k)||null, setItem:(k,v)=>local.set(k,String(v)),removeItem:k=>local.delete(k)},
    CSS:{escape:s=>String(s).replace(/[^a-zA-Z0-9_-]/g,"")},
    setTimeout:(f,n,...args)=>{const t=setTimeout(()=>{timers.delete(t);f(...args);},n);timers.add(t);return t;},
    clearTimeout:t=>{clearTimeout(t);timers.delete(t);},
    setInterval:(f,n,...a)=>{const i=setInterval(f,n,...a);intervals.add(i);return i;},
    clearInterval:i=>{clearInterval(i);intervals.delete(i);},
    requestAnimationFrame:f=>{const t=setTimeout(()=>{timers.delete(t);f(performance.now());},0);timers.add(t);return t;},cancelAnimationFrame:t=>{clearTimeout(t);timers.delete(t);},
    ResizeObserver:class{observe(){} disconnect(){}},
    Option:function(label,value){const el=document.createElement("option");el.textContent=label;el.value=value;return el;},
    addEventListener:window.addEventListener.bind(window),
    removeEventListener:window.removeEventListener.bind(window),
    dispatchEvent:window.dispatchEvent.bind(window),
    matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),
  };
  sandbox.window=sandbox.parent=sandbox;
  for(const f of document.querySelectorAll('input[type=file]')) f.files=[];
  for (const d of document.querySelectorAll('dialog')){
    d.showModal=()=>{d.open=true;d.setAttribute('open','');};
    d.close=()=>{d.open=false;d.removeAttribute('open');d.dispatchEvent(new window.Event('close'));};
  }
  const context=vm.createContext(sandbox);
  for(const script of document.querySelectorAll('script[src]')){
    const url=script.getAttribute('src');
    vm.runInContext(await (await get(url)).text(),context,{filename:url});
  }
  return {document, event:type=>new window.Event(type,{bubbles:true}), end(){
    window.dispatchEvent(new window.Event('pagehide'));
    for(const t of timers)clearTimeout(t);for(const i of intervals)clearInterval(i);
  }};
}
const el=id=>{const e=current.document.getElementById(id);assert(e,id);return e;};
const click=id=>{assert(!el(id).disabled,"disabled "+id);el(id).dispatchEvent(current.event('click'));};
const setSelect=(id,val)=>{el(id).value=val;assert.equal(el(id).value,val);el(id).dispatchEvent(current.event('change'));};
function file(id, value) { el(id).files=[value];el(id).dispatchEvent(current.event('change')); }
const rew=(name, gain)=>new File([`Filter Settings file\nFilter 1: ON PK Fc 1000 Hz Gain ${gain} dB Q 1.00\n`],name);
async function pcmExpected(left,right){
  const file=await open(rawPath,"r"), frames=1536, bytes=frames*4;
  try{
    await until(async()=>{
      const size=(await file.stat()).size;
      if(size<bytes)return false;
      const {buffer,bytesRead}=await file.read(Buffer.alloc(bytes),0,bytes,Math.floor(size/4)*4-bytes);
      if(bytesRead<bytes)return false;
      const sums=[0,0];
      for(let i=0;i<bytes;i+=4){sums[0]+=buffer.readInt16LE(i)**2;sums[1]+=buffer.readInt16LE(i+2)**2;}
      return sums.every((s,i)=>Math.abs(20*Math.log10(Math.sqrt(s/frames)/(7000/Math.sqrt(2)))-[left,right][i])<.06);
    },`PCM ${left}/${right} dB`);
  }finally{await file.close();}
}
async function waitProfile(id) {
  await until(()=>el('listenPresets').value===id&&!el('openCompare').disabled,'ready '+id);
}
async function openPair(){
  click('openRew');await until(()=>el('rewDialog').open,'REW dialog');
  setSelect('rewTarget','pair');
}
function uploads(left,right){
  file('rewFirst',rew('sinistro.txt',left));
  file('rewSecond',typeof right==='number'?rew('destro.txt',right):new File([right],'destro.txt'));
}
async function check(){
  await api("session");
  const archive=await api('presets'), starting=await api('profile/state');
  current=await setup();
  await waitProfile('sample-peq');
  assert(el('tuneView').hidden);assert(!el('listenView').hidden);
  assert.equal(el('listenPresets').querySelector('option[value="sample-peq"]').textContent,'Salotto');
  assert.equal(el('listenPresets').querySelector('option[value="flat"]').textContent,'Nessuna correzione');
  assert(!requests.some(([m,p])=>m==='POST'&&['api/apply','api/profile/prepare','api/audio/enable'].includes(p)));
  click('openCompare');
  await until(()=>el('compareDialog').open,'open comparison');
  assert.deepEqual(await api('profile/state'),starting);
  assert.deepEqual(await api('presets'),archive);
  assert.equal(el('compareA').value,'flat');
  click('hearA');await pcmExpected(0,0);
  await until(()=>!el('hearB').disabled,'switch A/B');
  click('hearB');await pcmExpected(-6,-2);
  await until(()=>!el('matchFile').disabled,'select reference file');
  const wav=new File([await readFile(wavPath)],'estratto.wav',{type:'audio/wav'});
  file('matchFile',wav);
  await until(()=>el('matchSummary').textContent.includes('dB')&&!el('hearA').disabled,'WAV aligned');
  click('hearA');
  const average=10*Math.log10((10**(-6/10)+10**(-2/10))/2);
  await pcmExpected(average,average);
  await until(()=>!el('hearB').disabled,'matched B');
  click('hearB');await pcmExpected(-6,-2);
  await until(()=>!el('compareClose').disabled,'restore');
  click('compareClose');await until(()=>!el('compareDialog').open,'close comparison');
  assert.deepEqual(await api('profile/state'),starting);
  assert.deepEqual(await api('presets'),archive);
  click('viewTune');
  assert(!el('tuneView').hidden);assert(el('listenView').hidden);
  await openPair();
  uploads(-3,'Filter 1: ON unsupported\n');
  await until(()=>!el('rewIssues').hidden,'invalid right');
  assert(el('rewConfirm').disabled);
  uploads(-3,-9);await until(()=>!el('rewConfirm').disabled,'valid pair');
  click('rewCancel'); await until(()=>!el('rewDialog').open,'cancel REW');
  assert.deepEqual(await api('profile/state'),starting);
  await openPair();uploads(-3,-9);await until(()=>!el('rewConfirm').disabled,'valid second time');
  click('rewConfirm');await until(()=>!el('rewDialog').open,'apply import');await pcmExpected(-3,-9);
  await until(async()=>(await api('profile/state')).profile.peq.R[0].gain===-9,'applied both');
  click('save');await until(async()=>(await api('presets')).presets.find(p=>p.id==='sample-peq').peq.L[0].gain===-3,'saved');
  current.end();current=await setup();await waitProfile('sample-peq');assert(el('tuneView').hidden);
  assert.deepEqual((await api('profile/state')).profile.peq,(await api('presets')).presets.find(p=>p.id==='sample-peq').peq);
  setSelect('listenPresets','flat');await pcmExpected(0,0);
  await until(()=>!el('listenPresets').disabled,'select saved');
  setSelect('listenPresets','sample-peq');await pcmExpected(-3,-9);
  console.log(JSON.stringify({renderer:'LinkeDOM (DOM/eventi, nessuna verifica del layout)',
    flusso:'Ascolto, Taratura, REW stereo (errore, annulla, conferma), A/B PCM, allineamento WAV, chiusura, salvataggio, riapertura e selezione',
    matched_db:Math.round(average*1000)/1000}));
}
try{await check();}finally{current?.end();}
