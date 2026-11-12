import test from 'node:test';
import assert from 'node:assert/strict';
import artifact from '../research/artifacts/multiseason-strength-v1.json' with {type:'json'};
import {verifyFrozenArtifact,makeLivePairs,evaluateLivePair,summarizeLivePairs} from '../research/live-multiseason.js';
import {MODEL_ID,predictFixture} from '../lib/model.js';
import {applyProviderData,viewState} from '../lib/state.js';

const now=new Date('2026-11-12T10:00:00Z');
const teams=Object.keys(artifact.seed.en.teams).slice(0,6).map((name,i)=>({id:i+1,name}));
const competition={code:'PL',name:'Premier League'};
const history=Array.from({length:48},(_,i)=>{
  const h=i%6,a=(h+1+Math.floor(i/6)%5)%6;
  return {id:i+1,status:'FINISHED',utcDate:new Date(now.getTime()-(48-i)*86400000).toISOString(),
    homeTeam:teams[h],awayTeam:teams[a],competition,score:{fullTime:{home:i%4,away:(i+2)%3}}};
});
const fixture={id:999,status:'TIMED',utcDate:'2026-11-20T15:00:00Z',homeTeam:teams[0],awayTeam:teams[1],competition};
const league={...competition,finished:history,upcoming:[fixture]};
const final={...fixture,status:'FINISHED',score:{fullTime:{home:1,away:1}}};
const after=new Date('2026-11-20T20:00:00Z');
test('frozen artifact verifies and tampering is rejected',()=>{
  assert.equal(verifyFrozenArtifact(artifact),artifact);
  const bad=structuredClone(artifact);bad.classifier.w[0][0]+=.01;
  assert.throws(()=>verifyFrozenArtifact(bad),/INVALID_FROZEN/);
});
test('live challenger uses provider identity and exactly paired original probabilities',()=>{
  const batch=makeLivePairs(league,artifact,now);
  assert.equal(batch.pairs.length,1);assert.deepEqual(batch.unmatchedNames,[]);
  const p=batch.pairs[0];
  assert.equal(p.historicalNameCoverage,true);
  assert.equal(p.market,'1X2');assert.equal(p.researchOnly,true);
  assert.deepEqual(p.pairedBaseline,predictFixture(fixture,history,now));
  assert.equal(p.generatedAt,now.toISOString());
  assert.ok(Math.abs(p.p1x2.reduce((s,x)=>s+x,0)-1)<.000003);
  assert.equal(p.sourceArtifactSha256,artifact.artifactSha256);
});
test('later or same-day scores never change fixed paired probabilities',()=>{
  const prior=makeLivePairs(league,artifact,now);
  const poison={...history[0],id:1001,utcDate:'2026-11-12T01:00:00Z',score:{fullTime:{home:20,away:0}}};
  const future={...poison,id:1002,utcDate:'2026-11-13T18:00:00Z'};
  assert.deepEqual(makeLivePairs({...league,finished:[...history,poison,future]},artifact,now),prior);
  const bad={...fixture,homeTeam:{id:teams[0].id,name:'Different identity'}};
  assert.throws(()=>makeLivePairs({...league,upcoming:[bad]},artifact,now),/AMBIGUOUS_LIVE_TEAM_IDENTITY/);
});
test('only genuine matching completed results can settle the saved pair',()=>{
  const p={...makeLivePairs(league,artifact,now).pairs[0],forecastKey:'pair:999',forecastVersion:1};
  assert.equal(evaluateLivePair(p,{...final,utcDate:'2026-11-21T15:00:00Z'},after),null);
  assert.equal(evaluateLivePair(p,{...final,homeTeam:teams[2]},after),null);
  assert.equal(evaluateLivePair(p,final,new Date('2026-11-20T17:00:00Z')),null);
  assert.equal(evaluateLivePair(p,{...final,status:'IN_PLAY'},after),null);
  const ev={...evaluateLivePair(p,final,after),version:1};
  assert.equal(ev.outcome,'D');
  const s=summarizeLivePairs([p],[ev]);
  assert.equal(s.sampleSize,1);assert.equal(s.draws,1);assert.equal(s.promotionEligible,false);
  assert.equal(summarizeLivePairs([p],[{...ev,logLoss1x2:0}]).sampleSize,0);
});
test('production refresh preserves original forecasts while separately persisting multi-season pairs',()=>{
  const previous={schema:1,source:'football-data.org/v4',model:MODEL_ID,predictions:[],evaluations:[],
    shadowPredictions:[],shadowEvaluations:[],finishedMatches:[],resultCorrections:[],providerErrors:[],leagueCounts:[]};
  const first=applyProviderData(previous,{leagues:[league],errors:[]},now).data;
  assert.equal(previous.predictions.length,0);
  assert.equal(first.predictions.length,1);assert.equal(first.predictions[0].model,MODEL_ID);
  assert.equal(first.multiseasonPredictions.length,1);
  assert.equal(first.multiseasonEvaluations.length,0);
  assert.deepEqual(first.multiseasonResearchErrors,[]);
  const saved=structuredClone(first.predictions);
  const v=viewState(first,now);
  assert.equal(v.upcoming[0].model,MODEL_ID);
  assert.equal(v.multiseasonResearch.pendingUpcoming,1);
  assert.equal(v.multiseasonResearch.publicPredictions,false);
  const second=applyProviderData(first,{leagues:[{...league,finished:[...history,final],upcoming:[]}],errors:[]},after).data;
  assert.deepEqual(second.predictions,saved);
  assert.equal(second.multiseasonEvaluations.length,1);
  assert.equal(viewState(second,after).multiseasonResearch.sampleSize,1);
  assert.equal(viewState(second,after).historyAudit.evaluated,1);
});
