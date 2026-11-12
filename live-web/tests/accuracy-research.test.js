import test from 'node:test';
import assert from 'node:assert/strict';
import { predictFixture, MODEL_ID } from '../lib/model.js';
import { ACCURACY_MODEL_ID, predictAccuracyPair, evaluateAccuracyPair,
  prospectiveAccuracyComparison, historicalAccuracyComparison } from '../lib/accuracy-research.js';
import { applyProviderData, viewState } from '../lib/state.js';

const now=new Date('2026-11-12T10:00:00Z');
const teams=Array.from({length:8},(_,i)=>({id:i+1,name:'Club '+(i+1)}));
const league={code:'PL',name:'Premier League'};
const finished=Array.from({length:80},(_,i)=>{
  const h=i%8, a=(h+1+Math.floor(i/8)%7)%8;
  return {id:i+1,status:'FINISHED',utcDate:new Date(now.getTime()-(80-i)*86400000).toISOString(),
    homeTeam:teams[h],awayTeam:teams[a],competition:league,
    score:{fullTime:{home:i%4,away:(i+2)%3}}};
});
const fixture={id:999,status:'TIMED',utcDate:'2026-11-20T15:00:00Z',
  homeTeam:teams[0],awayTeam:teams[1],competition:league};
const final={...fixture,status:'FINISHED',score:{fullTime:{home:1,away:1}}};
const after=new Date('2026-11-20T20:00:00Z');
const before={schema:1,source:'football-data.org/v4',model:MODEL_ID,
  lastRefreshAt:null,predictions:[],evaluations:[],shadowPredictions:[],shadowEvaluations:[],
  finishedMatches:[],resultCorrections:[],providerErrors:[],leagueCounts:[]};
test('new estimate pairs the exact original method at the same time without changing it',()=>{
  const original=predictFixture(fixture,finished,now);
  const p=predictAccuracyPair(fixture,finished,now);
  assert.ok(p);
  assert.equal(p.model,ACCURACY_MODEL_ID);
  assert.equal(p.researchOnly,true);
  assert.equal(p.certified,false);
  assert.deepEqual(p.pairedBaseline,original);
  assert.equal(p.generatedAt,p.pairedBaseline.generatedAt);
  assert.ok(Math.abs(p.p1x2.reduce((s,x)=>s+x,0)-1)<0.000003);
  assert.notDeepEqual(p.p1x2,original.p1x2);
});
test('same-day unavailable and future scores cannot change either forecast',()=>{
  const p=predictAccuracyPair(fixture,finished,now);
  const poison=[...finished,{...finished[0],id:2001,utcDate:'2026-11-12T08:00:00Z',
    score:{fullTime:{home:99,away:0}}},{...finished[0],id:2002,utcDate:'2026-11-12T20:00:00Z',
    score:{fullTime:{home:99,away:0}}}];
  assert.deepEqual(predictAccuracyPair(fixture,poison,now),p);
  assert.equal(predictAccuracyPair({...fixture,status:'FINISHED'},finished,now),null);
});
test('only real matching finalized results settle the paired forecasts',()=>{
  const p={...predictAccuracyPair(fixture,finished,now),forecastKey:'accuracy:999',forecastVersion:1};
  assert.equal(evaluateAccuracyPair(p,final,new Date('2026-11-20T18:00:00Z')),null);
  assert.equal(evaluateAccuracyPair(p,{...final,utcDate:'2026-11-21T15:00:00Z'},after),null);
  assert.equal(evaluateAccuracyPair(p,{...final,status:'IN_PLAY'},after),null);
  const ev=evaluateAccuracyPair(p,final,after);
  assert.equal(ev.outcome,'D');
  assert.equal(ev.researchOnly,true);
  assert.equal(prospectiveAccuracyComparison([p],[]).sampleSize,0);
  const summary=prospectiveAccuracyComparison([p],[{...ev,version:1}]);
  assert.equal(summary.sampleSize,1);
  assert.equal(summary.draws,1);
  assert.equal(summary.promotionEligible,false);
  assert.equal(prospectiveAccuracyComparison([p],[{...ev,candidatePick:'D'===ev.candidatePick?'H':'D',version:1}]).sampleSize,0);
});
test('refresh keeps primary and previous shadow history and stores a separate candidate',()=>{
  const snapshot=structuredClone(before);
  const first=applyProviderData(snapshot,{errors:[],leagues:[{...league,finished,upcoming:[fixture]}]},now).data;
  assert.deepEqual(snapshot,before);
  assert.equal(first.predictions.length,1);
  assert.equal(first.predictions[0].model,MODEL_ID);
  assert.equal(first.accuracyPredictions.length,1);
  assert.equal(first.shadowPredictions.length,1);
  assert.equal(first.accuracyEvaluations.length,0);
  assert.equal(viewState(first,now).upcoming[0].model,MODEL_ID);
  assert.equal(viewState(first,now).accuracyResearch.pendingUpcoming,1);
  const saved=structuredClone(first.predictions);
  const second=applyProviderData(first,{errors:[],leagues:[{...league,finished:[...finished,final],upcoming:[]}]},after).data;
  assert.deepEqual(second.predictions,saved);
  assert.equal(second.evaluations.length,1);
  assert.equal(second.accuracyEvaluations.length,1);
  assert.equal(viewState(second,after).accuracyResearch.sampleSize,1);
  assert.equal(viewState(second,after).historyAudit.evaluated,1);
  assert.equal(viewState(second,after).accuracyResearch.publicPredictions,false);
});
test('retrospective comparison uses identical evaluated matches and remains distinct from live accuracy',()=>{
  const x=historicalAccuracyComparison([{...league,finished}]);
  assert.equal(x.status,'RETROSPECTIVE_ONLY');
  assert.ok(x.sampleSize>0);
  assert.ok(x.baselineCorrect<=x.sampleSize&&x.candidateCorrect<=x.sampleSize);
  assert.equal(x.candidateCorrect-x.baselineCorrect,x.candidateOnlyCorrect-x.baselineOnlyCorrect);
  assert.equal(x.promotionEligible,false);
});
