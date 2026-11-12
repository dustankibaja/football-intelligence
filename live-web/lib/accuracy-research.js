// Improve the original engine in an isolated, pre-match research lane.
// The public empirical Poisson model and its historical ledger stay unchanged.
import { createHash } from 'node:crypto';
import { MODEL_ID, predictFixture, validFinished, evaluateForecast } from './model.js';
import { canonicalFixtureKey, latestForecastsByMatch, selectLatestEvaluations } from './forecast-store.js';

export const ACCURACY_MODEL_ID = 'footmo-all-venue-goals-research-v1';
const FOUR_HOURS = 4 * 3600000;
const PRIOR_GAMES = 5;
const round = x => Math.round(x * 1e6) / 1e6;
const mean = xs => xs.reduce((s,x) => s+x,0)/xs.length;
const clamp = (x,a,b) => Math.max(a,Math.min(b,x));
const outcome = (h,a) => h>a ? 'H' : h===a ? 'D' : 'A';
const top = p => ['H','D','A'][p.indexOf(Math.max(...p))];
function poisson(k,lambda) {
  let p=Math.exp(-lambda);
  for(let i=1;i<=k;i++) p*=lambda/i;
  return p;
}
function goalMarkets(h,a) {
  const cells=[];
  let total=0;
  for(let i=0;i<=10;i++) for(let j=0;j<=10;j++){
    const p=poisson(i,h)*poisson(j,a);
    cells.push({i,j,p});total+=p;
  }
  const p1x2=[0,0,0];let btts=0,over=0;
  for(const c of cells){
    c.p/=total;
    p1x2[c.i>c.j?0:c.i===c.j?1:2]+=c.p;
    if(c.i&&c.j)btts+=c.p;
    if(c.i+c.j>=3)over+=c.p;
  }
  cells.sort((a,b)=>b.p-a.p);
  return {p1x2:p1x2.map(round),pBtts:round(btts),pOver25:round(over),
    topScores:cells.slice(0,3).map(c=>({score:c.i+'-'+c.j,probability:round(c.p)}))};
}
function teamAllVenue(history,id,homeMean,awayMean) {
  let n=0,attack=0,defence=0;
  for(const m of history){
    const {home,away}=m.score.fullTime;
    if(m.homeTeam.id===id){n++;attack+=home/homeMean;defence+=away/awayMean;}
    else if(m.awayTeam.id===id){n++;attack+=away/awayMean;defence+=home/homeMean;}
  }
  return [(attack+PRIOR_GAMES)/(n+PRIOR_GAMES),(defence+PRIOR_GAMES)/(n+PRIOR_GAMES)];
}
export function predictAccuracyPair(fixture,finished,now=new Date()){
  const cutoff=now.getTime(), kickoff=Date.parse(fixture?.utcDate||'');
  if(!Number.isFinite(cutoff)||!Number.isFinite(kickoff)||cutoff>=kickoff)return null;
  const history=finished.filter(m=>validFinished(m)&&
    Date.parse(m.utcDate)+FOUR_HOURS<cutoff&&Date.parse(m.utcDate)<kickoff);
  const baseline=predictFixture(fixture,history,now);
  if(!baseline)return null;
  const hm=Math.max(.35,mean(history.map(m=>m.score.fullTime.home)));
  const am=Math.max(.35,mean(history.map(m=>m.score.fullTime.away)));
  const [ha,hd]=teamAllVenue(history,fixture.homeTeam.id,hm,am);
  const [aa,ad]=teamAllVenue(history,fixture.awayTeam.id,hm,am);
  const h=clamp(hm*ha*ad,.25,4),a=clamp(am*aa*hd,.25,4);
  const provenanceSha256=createHash('sha256').update(JSON.stringify({
    model:ACCURACY_MODEL_ID,baseline:baseline.provenanceSha256,prior:PRIOR_GAMES,
    rates:[h,a]})).digest('hex');
  // Both estimates were made at the same time using the exact same eligible
  // league history. The nested original-model forecast is never displayed as
  // a replacement for the user's archived prediction.
  return {...baseline,...goalMarkets(h,a),lambdaHome:round(h),lambdaAway:round(a),
    model:ACCURACY_MODEL_ID,provenanceSha256,certified:false,researchOnly:true,
    pairedBaseline:baseline};
}
export function evaluateAccuracyPair(p,finished,now=new Date()){
  if(p?.model!==ACCURACY_MODEL_ID||p.researchOnly!==true||
      p.pairedBaseline?.model!==MODEL_ID||
      p.pairedBaseline.generatedAt!==p.generatedAt||
      canonicalFixtureKey(p)!==canonicalFixtureKey(p.pairedBaseline)||
      !p.forecastKey||!Number.isInteger(finished?.score?.fullTime?.home)||finished.score.fullTime.home<0||
      !Number.isInteger(finished?.score?.fullTime?.away)||finished.score.fullTime.away<0||
      Date.parse(finished?.utcDate||'')!==Date.parse(p.kickoffUtc)||
      now.getTime()<Date.parse(p.kickoffUtc)+FOUR_HOURS)return null;
  const candidate=evaluateForecast(p,finished,now);
  const baseline=evaluateForecast(p.pairedBaseline,finished,now);
  if(!candidate||!baseline||candidate.scoreFingerprint!==baseline.scoreFingerprint)return null;
  return {...candidate,forecastKey:p.forecastKey,kickoffUtc:p.kickoffUtc,
    competitionCode:p.competitionCode,candidatePick:top(p.p1x2),
    baselinePick:top(p.pairedBaseline.p1x2),baselineLogLoss:baseline.logLoss1x2,
    baselineBrier:baseline.brier1x2,researchOnly:true};
}
export function summarizeAccuracyPairs(rows){
  const n=rows.length;
  const count=fn=>rows.filter(fn).length;
  const bc=count(x=>x.baselinePick===x.outcome),cc=count(x=>x.candidatePick===x.outcome);
  const draws=count(x=>x.outcome==='D');
  const bdc=count(x=>x.outcome==='D'&&x.baselinePick==='D');
  const cdc=count(x=>x.outcome==='D'&&x.candidatePick==='D');
  return {sampleSize:n,baselineCorrect:bc,candidateCorrect:cc,
    baselineAccuracy:n?round(bc/n):null,candidateAccuracy:n?round(cc/n):null,
    accuracyDelta:n?round((cc-bc)/n):null,
    baselineLogLoss:n?round(mean(rows.map(x=>x.baselineLogLoss))):null,
    candidateLogLoss:n?round(mean(rows.map(x=>x.logLoss1x2))):null,
    logLossDelta:n?round(mean(rows.map(x=>x.logLoss1x2-x.baselineLogLoss))):null,
    draws,baselineDrawCorrect:bdc,candidateDrawCorrect:cdc,
    baselineDrawPicks:count(x=>x.baselinePick==='D'),candidateDrawPicks:count(x=>x.candidatePick==='D'),
    candidateOnlyCorrect:count(x=>x.candidatePick===x.outcome&&x.baselinePick!==x.outcome),
    baselineOnlyCorrect:count(x=>x.baselinePick===x.outcome&&x.candidatePick!==x.outcome),
    promotionEligible:false};
}
export function prospectiveAccuracyComparison(predictions=[],evaluations=[]){
  const valid=predictions.filter(p=>p.model===ACCURACY_MODEL_ID&&p.researchOnly===true&&
    p.pairedBaseline?.model===MODEL_ID&&p.pairedBaseline.generatedAt===p.generatedAt&&
    canonicalFixtureKey(p)===canonicalFixtureKey(p.pairedBaseline));
  const latest=latestForecastsByMatch(valid);
  const rows=selectLatestEvaluations(valid,evaluations).filter(e=>{
    const p=latest.get(canonicalFixtureKey(e));
    return p&&e.forecastKey===p.forecastKey&&e.researchOnly===true&&
      e.candidatePick===top(p.p1x2)&&e.baselinePick===top(p.pairedBaseline.p1x2)&&
      Number.isInteger(e.home)&&e.home>=0&&Number.isInteger(e.away)&&e.away>=0&&
      e.outcome===outcome(e.home,e.away)&&Number.isFinite(e.baselineLogLoss)&&Number.isFinite(e.logLoss1x2)&&
      Date.parse(e.observedAt)>=Date.parse(p.kickoffUtc)+FOUR_HOURS;
  });
  return {...summarizeAccuracyPairs(rows),status:rows.length?'RESEARCH_ONLY':'AWAITING_REAL_RESULTS',
    candidateModel:ACCURACY_MODEL_ID,primaryModel:MODEL_ID};
}
export function historicalAccuracyComparison(leagues){
  const rows=[];
  for(const league of leagues){
    const ordered=league.finished.filter(validFinished).sort((a,b)=>a.utcDate.localeCompare(b.utcDate));
    for(const m of ordered){
      const kickoff=Date.parse(m.utcDate),asOf=new Date(kickoff-60000);
      const p=predictAccuracyPair({...m,status:'SCHEDULED'},ordered,asOf);
      if(!p)continue;
      p.forecastKey='historical:'+m.id+':'+m.utcDate;
      const e=evaluateAccuracyPair(p,m,new Date(kickoff+FOUR_HOURS));
      if(e)rows.push(e);
    }
  }
  return {...summarizeAccuracyPairs(rows),status:'RETROSPECTIVE_ONLY',
    candidateModel:ACCURACY_MODEL_ID,primaryModel:MODEL_ID};
}
