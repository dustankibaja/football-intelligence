import test from 'node:test';
import assert from 'node:assert/strict';
import {causalRows,fit,predict,compare,validateSeason} from '../research/multiseason-strength.js';
const rows=Array.from({length:150},(_,i)=>{
  const h=i%8,a=(h+1+Math.floor(i/8)%7)%8;
  return {id:'match'+i,league:'en',season:'2025-26',
    date:new Date(Date.UTC(2025,7,1+Math.floor(i/2))).toISOString().slice(0,10),
    home:'Club '+h,away:'Club '+a,hg:i%4,ag:(i+2)%3};
});
test('same-day and future results cannot change the earlier feature rows',()=>{
  const orig=causalRows(rows),cut=rows[100].date;
  const changed=rows.map(m=>m.date>=cut?{...m,hg:19,ag:0}:m);
  const after=causalRows(changed);
  const a=orig.filter(r=>r.date<=cut),b=after.filter(r=>r.date<=cut);
  assert.deepEqual(a.map(r=>r.features),b.map(r=>r.features));
  assert.deepEqual(a.map(r=>r.baseline),b.map(r=>r.baseline));
  assert.ok(orig.some(r=>r.baseline!==null));
});
test('fixed trained classifier returns finite probabilities and paired coverage',()=>{
  const data=causalRows(rows);
  const m=fit(data.slice(0,110));
  const p=predict(m,data[120].features);
  assert.equal(p.length,3);
  assert.ok(p.every(x=>Number.isFinite(x)&&x>=0&&x<=1));
  assert.ok(Math.abs(p.reduce((s,x)=>s+x,0)-1)<1e-10);
  const r=compare(data.slice(110),m);
  assert.equal(r.total,40);
  assert.equal(r.fullCandidate.n,40);
  assert.equal(r.pairedBaseline.n,r.pairedCandidate.n);
  assert.equal(r.pairedCandidate.correct-r.pairedBaseline.correct,r.candidateOnlyCorrect-r.baselineOnlyCorrect);
});
test('source validation rejects incomplete or impossible dates instead of repairing them',()=>{
  const raw={matches:Array.from({length:380},(_,i)=>({date:'2025-08-15',team1:'H'+i,team2:'A'+i,score:{ft:[1,0]}}))};
  assert.equal(validateSeason(raw,'2025-26','en').length,380);
  assert.throws(()=>validateSeason({matches:raw.matches.slice(1)},'2025-26','en'),/Incomplete/);
  raw.matches[0].date='2024-08-15';
  assert.throws(()=>validateSeason(raw,'2025-26','en'),/Invalid match\/date/);
});
