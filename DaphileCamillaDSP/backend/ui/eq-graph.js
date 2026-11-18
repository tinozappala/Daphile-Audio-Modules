/* Local SVG editor. Drawing and gestures never issue audio commands. */
'use strict';
// Profiles and chart arrays are plain JSON data throughout the controller.
function copyEqData(value) {
  return typeof structuredClone === 'function' ? structuredClone(value) : JSON.parse(JSON.stringify(value));
}
function setEqChildren(element, ...children) {
  if (element.replaceChildren) element.replaceChildren(...children);
  else {
    element.textContent = '';
    element.append(...children);
  }
}
class EqGraph {
  static colors=['#71e2cc','#87b3ff','#bf9aff','#ffc078','#ff91ac','#8edc9a'];
  constructor(svg,{change=()=>{},select=()=>{},inspect=()=>{}}={}) {
    this.svg=svg;this.change=change;this.select=select;this.inspect=inspect;this.selected=null;this.drag=null;
    svg.setAttribute('viewBox','0 0 1100 410');svg.setAttribute('role','group');
    svg.addEventListener('focusin', e => {
      const band = e.target.closest('[data-band]')?.dataset.band;
      if (!band || band === this.selected) return;
      this.selected = band;
      this.select(band);
      this.drawRange();
    });
    svg.addEventListener('pointerdown',e=>{
      const node=e.target.closest('[data-band]');if(!node||e.button!==0)return;
      const band=this.data.bands.find(b=>String(b.id)===node.dataset.band);if(!band?.editable)return;
      e.preventDefault();this.selected=band.id;this.select(band.id);this.drag={id:band.id,pointer:e.pointerId,offset:band.gainOffset||0};svg.setPointerCapture(e.pointerId);this.draw();
    });
    svg.addEventListener('pointermove',e=>{
      if(!this.data)return;const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const t=p.matrixTransform(svg.getScreenCTM().inverse());
      const frequency=this.frequency(t.x),gain=this.gain(t.y);this.inspect({frequency,gain});
      if(this.drag){const band=this.data.bands.find(b=>b.id===this.drag.id);if(band)this.change(band.id,{frequency,gain:band.gainEditable===false?band.gain-(band.gainOffset||0):gain-this.drag.offset},false);}
    });
    const finish=e=>{if(!this.drag||e.pointerId!==this.drag.pointer)return;const id=this.drag.id;this.drag=null;if(svg.hasPointerCapture(e.pointerId))svg.releasePointerCapture(e.pointerId);this.change(id,{},true);};
    svg.addEventListener('pointerup',finish);svg.addEventListener('pointercancel',finish);
    svg.addEventListener('lostpointercapture',e=>{if(this.drag)finish(e);});
    svg.addEventListener('wheel',e=>{const node=e.target.closest('[data-band]');if(!node)return;const b=this.data.bands.find(b=>String(b.id)===node.dataset.band);if(!b?.editable)return;e.preventDefault();this.selected=b.id;this.select(b.id);this.change(b.id,{q:b.q+(e.deltaY<0?1:-1)*(b.qStep||.1)},true);},{passive:false});
    svg.addEventListener('keydown',e=>{
      const node=e.target.closest('[data-band]');if(!node)return;const b=this.data.bands.find(b=>String(b.id)===node.dataset.band);if(!b?.editable)return;
      const delta=e.shiftKey?5:1,patch={};
      if(e.key==='ArrowLeft'||e.key==='ArrowRight')patch.frequency=b.frequency*Math.pow(2,(e.key==='ArrowRight'?delta:-delta)/48);
      else if((e.key==='ArrowUp'||e.key==='ArrowDown')&&b.gainEditable!==false)patch.gain=b.gain-(b.gainOffset||0)+(e.key==='ArrowUp'?delta:-delta)*(b.gainStep||.1);
      else if(e.key==='+'||e.key==='=')patch.q=b.q+(b.qStep||.1)*delta;
      else if(e.key==='-')patch.q=b.q-(b.qStep||.1)*delta;else return;
      e.preventDefault();this.selected=b.id;this.select(b.id);this.change(b.id,patch,true);
      this.svg.querySelector(`[data-band="${CSS.escape(String(b.id))}"]`)?.focus();
    });
  }
  set(data){this.data=data;this.draw();}
  x(f){return (this.left||64)+Math.log(f/this.data.lo)/Math.log(this.data.hi/this.data.lo)*(1072-(this.left||64));}
  y(g){return (this.bottom||356)-(g-this.data.minDb)/(this.data.maxDb-this.data.minDb)*((this.bottom||356)-28);}
  frequency(x){return Math.max(this.data.lo,Math.min(this.data.hi,this.data.lo*Math.pow(this.data.hi/this.data.lo,(x-(this.left||64))/(1072-(this.left||64)))));}
  gain(y){return Math.max(this.data.minDb,Math.min(this.data.maxDb,this.data.maxDb-(y-28)/((this.bottom||356)-28)*(this.data.maxDb-this.data.minDb)));}
  node(tag,attrs={},text){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
  drawRange() {
    const layer = this.rangeLayer;
    if (!layer) return;
    setEqChildren(layer);
    layer.removeAttribute('aria-label');
    const {bands, rate, lo, hi} = this.data;
    const band = bands.find(item => item.id === this.selected);
    if (!band || band.frequency < lo || band.frequency > hi) return;
    const range = eqBandwidthMarkers(band, rate);
    if (!range) return;
    const left = Math.max(lo, range.leftFreq ?? lo);
    const right = Math.min(hi, range.rightFreq ?? hi);
    const top = 28;
    layer.append(this.node('rect', {x: this.x(left), y: top, width: this.x(right) - this.x(left),
      height: this.bottom - top, fill: band.color, 'fill-opacity': 0.07}));
    for (const frequency of [range.leftFreq, range.rightFreq]) {
      if (frequency === null || frequency < lo || frequency > hi) continue;
      layer.append(this.node('line', {x1: this.x(frequency), x2: this.x(frequency),
        y1: top, y2: this.bottom, stroke: band.color, 'stroke-dasharray': '4 4', 'stroke-opacity': 0.7}));
    }
    const leftLabel = range.leftFreq === null ? 'meno di 20' : Math.round(range.leftFreq).toLocaleString('it-IT');
    const rightLabel = range.rightFreq === null ? 'oltre 20.000' : Math.round(range.rightFreq).toLocaleString('it-IT');
    const summary = 'Zona di intervento · ' + leftLabel + ' – ' + rightLabel + ' Hz';
    layer.setAttribute('aria-label', summary);
    const size = 11 * Math.max(1, 1100 / (this.svg.getBoundingClientRect().width || 1100));
    layer.append(this.node('text', {x: this.left + 6, y: top + size + 3, fill: band.color, 'font-size': size}, summary));
  }
  draw(){
    if(!this.data)return;const svg=this.svg,d=this.data;setEqChildren(svg);
    const width=svg.getBoundingClientRect?.().width||1100,small=width<500,scale=Math.max(1,1100/width),font=11*scale;
    this.left=small?110:64;this.bottom=small?596:356;svg.setAttribute('viewBox',`0 0 1100 ${small?this.bottom+48*scale:410}`);
    const defs=this.node('defs'),clip=this.node('clipPath',{id:svg.id+'-clip'});clip.append(this.node('rect',{x:this.left,y:28,width:1072-this.left,height:this.bottom-28}));defs.append(clip);svg.append(defs);
    svg.append(this.node('text',{x:this.left+8,y:Math.max(20,Math.min(30,font)),fill:'#8293ab','font-size':font},'dB'));
    for(let g=Math.ceil(d.minDb/6)*6;g<=d.maxDb;g+=6){svg.append(this.node('line',{x1:this.left,x2:1072,y1:this.y(g),y2:this.y(g),stroke:g===0?'#52677f':'#263347','stroke-width':g===0?1.3:.7}));svg.append(this.node('text',{x:this.left-12,y:this.y(g)+4*scale,'text-anchor':'end',fill:'#90a0b6','font-size':font},g>0?'+'+g:g));}
    for(const f of small?[20,100,1000,10000,20000]:[20,30,50,100,200,300,500,1000,2000,3000,5000,10000,20000]){if(f<d.lo||f>d.hi)continue;svg.append(this.node('line',{x1:this.x(f),x2:this.x(f),y1:28,y2:this.bottom,stroke:'#263347','stroke-width':.7}));svg.append(this.node('text',{x:this.x(f),y:this.bottom+26*scale,'text-anchor':'middle',fill:'#90a0b6','font-size':font},f>=1000?f/1000+'k':f));}
    if(!small)svg.append(this.node('text',{x:1072,y:403,'text-anchor':'end',fill:'#8293ab','font-size':font},'Hz'));
    const plot=this.node('g',{'clip-path':`url(#${svg.id}-clip)`});svg.append(plot);
    this.rangeLayer = this.node('g', {'class':'bandwidth-markers', 'pointer-events':'none', role:'img'});
    plot.append(this.rangeLayer);
    this.drawRange();
    for(const curve of d.curves){if(curve.visible===false)continue;const points=curve.frequency.map((f,i)=>(i?'L':'M')+this.x(f).toFixed(2)+','+this.y(curve.values[i]).toFixed(2)).join(' ');plot.append(this.node('path',{d:points,fill:'none',stroke:curve.color,'stroke-width':curve.width||2.4,'stroke-dasharray':curve.dashed?'6 6':'none',opacity:curve.opacity??1,'stroke-linejoin':'round'}));}
    for(const b of d.bands){if(b.frequency<d.lo||b.frequency>d.hi)continue;const selected=b.id===this.selected;const g=this.node('g',{'data-band':b.id,tabindex:0,role:'button','aria-label':`${b.label}: ${Math.round(b.frequency)} Hz, ${b.gain.toFixed(1)} dB, Q ${b.q}. Frecce per frequenza e guadagno, più e meno per Q.`});g.style.cursor=b.editable?'grab':'default';
      g.append(this.node('circle',{cx:this.x(b.frequency),cy:this.y(b.gain),r:Math.max(17,18*scale),fill:'transparent'}));
      if(selected)g.append(this.node('circle',{cx:this.x(b.frequency),cy:this.y(b.gain),r:14*scale,fill:b.color,'fill-opacity':.16,stroke:b.color,'stroke-opacity':.45}));
      g.append(this.node('circle',{cx:this.x(b.frequency),cy:this.y(b.gain),r:(selected?7:6)*scale,fill:b.color,stroke:'#101823','stroke-width':2*scale}));
      g.append(this.node('text',{x:this.x(b.frequency),y:this.y(b.gain)-19*scale,'text-anchor':'middle',fill:b.color,'font-size':font,'font-weight':700},b.label));
      g.append(this.node('title',{},`${b.label} · ${Math.round(b.frequency)} Hz · ${b.gain.toFixed(1)} dB · Q ${b.q}`));plot.append(g);
    }
  }
}
// Standard RBJ/Q response for an explicitly selected preview rate.
// RME proprietary coefficients are not inferred from this model.
function eqCoefficients(type,f,g,q,rate){
  const w=2*Math.PI*f/rate,c=Math.cos(w),a=Math.sin(w)/(2*q),A=10**(g/40),k=2*Math.sqrt(A)*a;let n,d;
  if(type==='Peaking'){n=[1+a*A,-2*c,1-a*A];d=[1+a/A,-2*c,1-a/A];}
  else if(type==='Lowpass'||type==='Highpass'){const low=type==='Lowpass',v=low?(1-c)/2:(1+c)/2;n=[v,(low?2:-2)*v,v];d=[1+a,-2*c,1-a];}
  else if(type==='Notch'){n=[1,-2*c,1];d=[1+a,-2*c,1-a];}
  else if(type==='Lowshelf'){n=[A*((A+1)-(A-1)*c+k),2*A*((A-1)-(A+1)*c),A*((A+1)-(A-1)*c-k)];d=[(A+1)+(A-1)*c+k,-2*((A-1)+(A+1)*c),(A+1)+(A-1)*c-k];}
  else if(type==='Highshelf'){n=[A*((A+1)+(A-1)*c+k),-2*A*((A-1)+(A+1)*c),A*((A+1)+(A-1)*c-k)];d=[(A+1)-(A-1)*c+k,2*((A-1)-(A+1)*c),(A+1)-(A-1)*c-k];}
  else throw Error('Filtro non supportato');return [...n.map(v=>v/d[0]),1,d[1]/d[0],d[2]/d[0]];
}
function eqResponse(s,f,rate){const w=2*Math.PI*f/rate,c=Math.cos(w),j=Math.sin(w),c2=Math.cos(2*w),j2=Math.sin(2*w);const n=(s[0]+s[1]*c+s[2]*c2)**2+(s[1]*j+s[2]*j2)**2,d=(1+s[4]*c+s[5]*c2)**2+(s[4]*j+s[5]*j2)**2;return 10*Math.log10(Math.max(n/d,1e-30));}

// Adapted from CamillaEQ's client/src/dsp/bandwidthMarkers.ts (MIT).
// Revision e8afd8de351b1d759c5a7b6a93aa2a7e8418d64c. License in ../licenses/CamillaEQ-MIT.txt.
// Use Daphile's actual digital response. Half the gain in dB defines the
// displayed span for a bell, including cuts and boosts smaller than 3 dB.
function eqBandwidthMarkers(band, rate) {
  if (!Number.isFinite(rate) || (band.type !== 'Peaking' && band.type !== 'Notch')) return null;
  const f0 = band.frequency;
  const gain = band.gain - (band.gainOffset || 0);
  if (band.type === 'Peaking' && Math.abs(gain) < 1e-6) return null;
  const targetDb = band.type === 'Notch' ? -3 : gain / 2;
  const coefficients = eqCoefficients(band.type, f0, gain, band.q, rate);
  const maxHz = Math.min(20000, rate / 2 * (1 - 1e-6));

  function findCrossing(left, right) {
    const response = frequency => eqResponse(coefficients, frequency, rate) - targetDb;
    const atLeft = response(left);
    const atRight = response(right);
    if (atLeft === 0) return left;
    if (atRight === 0) return right;
    if ((atLeft > 0) === (atRight > 0)) return null;
    for (let i = 0; i < 30 && right - left > 0.05; i++) {
      const mid = (left + right) / 2;
      if ((atLeft > 0) === (response(mid) > 0)) left = mid;
      else right = mid;
    }
    return (left + right) / 2;
  }
  return {leftFreq: findCrossing(20, f0), rightFreq: findCrossing(f0, maxHz)};
}

// The editor never waits for HTTP to draw. Cache unchanged band responses,
// including identical linked channels, and retain the compiler's 600-point grid.
class EqPreview {
  constructor(){this.cache=new Map();this.rate=null;}
  calculate(p,rate){
    if(rate!==this.rate){this.rate=rate;this.cache.clear();this.frequency=Array.from({length:600},(_,i)=>20*(Math.min(20000,rate/2*.999)/20)**(i/599));}
    const frequency=this.frequency,curves={},band_curves={},effective_gain_db={},used=new Set();
    for(const ch of ['L','R']){
      const level=p.levels.pre_gain+p.levels[ch];effective_gain_db[ch]=level;
      const active=p.enabled&&!p.bypass&&!p.channel_bypass[ch];
      curves[ch]=frequency.map(()=>level);band_curves[ch]={};
      if(active)for(const b of p.peq[ch]){if(!b.enabled)continue;
        const key=JSON.stringify([b.type,b.freq,b.gain,b.q]);used.add(key);
        if(!this.cache.has(key)){const coefficients=eqCoefficients(b.type,b.freq,b.gain,b.q,rate);this.cache.set(key,frequency.map(f=>eqResponse(coefficients,f,rate)));}
        const values=this.cache.get(key);band_curves[ch][b.id]=values;
        for(let i=0;i<frequency.length;i++)curves[ch][i]+=values[i];
      }
    }
    for(const key of this.cache.keys())if(!used.has(key))this.cache.delete(key);
    return {frequency,curves,band_curves,effective_gain_db,auto_attenuation_db:0};
  }
}

// Automatic recovery of editing state. Browser-local, separately scoped for
// each plugin; never applies audio or overwrites the four device preset slots.
class EqEditStore {
  constructor(key,storage){
    this.key=key;this.items=new Map();this.timer=null;
    try{this.storage=storage===undefined?globalThis.localStorage:storage;
      const raw=this.storage?.getItem(key);if(raw&&raw.length<=262144){const value=JSON.parse(raw);
        if(value.schema===1&&Array.isArray(value.items))for(const row of value.items.slice(-16))
          if(Array.isArray(row)&&typeof row[0]==='string'&&row[0].length<=128)this.items.set(row[0],row[1]);
      }
    }catch{this.storage=null;}
  }
  read(key){const value=this.items.get(key);return value===undefined?null:copyEqData(value);}
  write(key,value){this.items.delete(key);this.items.set(key,copyEqData(value));while(this.items.size>16)this.items.delete(this.items.keys().next().value);this.schedule();}
  remove(key){this.items.delete(key);this.schedule();}
  schedule(){clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush(),250);}
  flush(){clearTimeout(this.timer);this.timer=null;try{const raw=JSON.stringify({schema:1,items:[...this.items]});if(raw.length<=262144)this.storage?.setItem(this.key,raw);}catch{/* Keep this session's edits in memory when storage is unavailable. */}}
}

// Count overlapping foreground operations, including startup. Background
// polling/preview must not flash a wait cursor every few seconds.
class EqBusy {
  constructor(root){this.root=root;this.pending=0;}
  async run(action){this.pending++;this.paint();try{return await action();}finally{this.pending--;this.paint();}}
  paint(){this.root.classList.toggle('operation-pending',this.pending>0);this.root.setAttribute('aria-busy',String(this.pending>0));}
}

// One automatic editor write at a time. Only explicit edits schedule writes;
// polling/startup never do. A failed write remains blocked until user retry.
class EqSyncQueue {
  constructor({read,write,confirmed=()=>{},failed=()=>{},paint=()=>{},valid=()=>true,delay=380}) {
    Object.assign(this,{read,write,confirmed,failed,paint,valid,delay});
    this.version=0;this.ack=0;this.timer=null;this.running=null;this.blocked=false;this.final=true;
  }
  changed(final=true){this.version++;this.final=final;clearTimeout(this.timer);this.timer=null;this.paint();
    if(final&&!this.blocked)this.timer=setTimeout(()=>this.flush(),this.delay);}
  async flush(retry=false){
    clearTimeout(this.timer);this.timer=null;
    if(this.running)return this.running;
    if((this.blocked&&!retry)||(!this.final&&!retry)||!this.valid()||this.ack===this.version)return;
    this.blocked=false;const version=this.version,profile=copyEqData(this.read());
    this.running=Promise.resolve().then(()=>this.write(profile)).then(result=>{
      this.ack=version;this.confirmed(profile,result);
    }).catch(error=>{this.blocked=true;this.failed(error);}).finally(()=>{
      this.running=null;this.paint();
      if(!this.blocked&&this.final&&this.version>this.ack)this.timer=setTimeout(()=>this.flush(),0);
    });
    this.paint();return this.running;
  }
  acknowledge(version=this.version){clearTimeout(this.timer);this.timer=null;this.ack=version;this.blocked=false;this.paint();}
  resume(){if(!this.running&&!this.blocked&&this.final&&this.version>this.ack){clearTimeout(this.timer);this.timer=setTimeout(()=>this.flush(),0);}}
  cancelTimer(){clearTimeout(this.timer);this.timer=null;}
}
