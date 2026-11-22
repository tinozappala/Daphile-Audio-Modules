const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const js=fs.readFileSync('iso_rc7_20261118/github_release/DaphileRME/backend/rme/ui/eq-graph.js','utf8');
const ctx=vm.createContext({});
vm.runInContext(js,ctx);
const coefficient=vm.runInContext('eqCoefficients',ctx);
const magnitude=vm.runInContext('eqResponse',ctx);
const fit=JSON.parse(fs.readFileSync('tmp/rme_headphone_fit.json','utf8'));
const banks=JSON.parse(fs.readFileSync('CONSOLIDAMENTO_T620_20261121/PROFILI_CUFFIE_RME_REVISIONE.json','utf8'));
for(const [index,key] of ['Denon AH-D9200','HiFiMAN Edition XS'].entries()){
 const fitBands=fit[key].after.bands;
 const actual=banks[index].channels.left;
 assert.deepEqual(banks[index].channels.right,actual);
 for(let i=0;i<5;i++){
  const a=actual[i],b=fitBands[i];
  assert.equal(a.frequency,b.frequency);assert.equal(a.gain,b.gain);assert.equal(a.q,b.q);
  if(i===0)assert.equal(a.type,b.kind==='low'?1:0);
  if(i===4)assert.equal(a.type,b.kind==='high'?1:0);
 }
 const coeffs=fitBands.map(b=>coefficient(({low:'Lowshelf',peak:'Peaking',high:'Highshelf'})[b.kind],b.frequency,b.gain,b.q,48000));
 const file=index===0?'tmp/denon_autoeq.csv':'tmp/xs_autoeq.csv';
 const rows=fs.readFileSync(file,'utf8').trim().split(/\r?\n/).slice(1);
 let count=0, sum=0, weightedSum=0, weightTotal=0;
 for(const row of rows){
  const parts=row.split(',').map(Number),f=parts[0],target=parts[5];
  if(f<20||f>16000)continue;
  const predicted=coeffs.reduce((n,c)=>n+magnitude(c,f,48000),0);
  const weight=f>8000?.4:1;
  const e=(predicted-target)**2;
  sum+=e;count++;weightedSum+=weight*e;weightTotal+=weight;
 }
 const unweighted=Math.sqrt(sum/count),weighted=Math.sqrt(weightedSum/weightTotal);
 assert.ok(Math.abs(unweighted-fit[key].after.metrics['20_16000_hz_rms_db'])<1e-6);
 assert.ok(Math.abs(weighted-fit[key].after.metrics.weighted_rms_db)<1e-6);
 console.log(key,'DAC rows + independent JS model PASS',unweighted.toFixed(4),weighted.toFixed(4));
}
