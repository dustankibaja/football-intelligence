import { createHash } from 'node:crypto';
import { MODEL_ID, predictFixture, validFinished } from '../lib/model.js';
import { causalRows, predict, MODEL, LEAGUES } from './multiseason-strength.js';
import { canonicalFixtureKey, latestForecastsByMatch, selectLatestEvaluations } from '../lib/forecast-store.js';
import { summarizeAccuracyPairs } from '../lib/accuracy-research.js';

const mapLeague={PL:'en',PD:'es',SA:'it',BL1:'de'};
const sourceRef='e6744429ee395bc86f247348c6184bb08d4eb361';
const round=x=>Math.round(x*1e6)/1e6;
const hash=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex');
const dayString=d=>new Date(d).toISOString().slice(0,10);
const code=i=>['H','D','A'][i];
const top=p=>code(p.indexOf(Math.max(...p)));
const probabilities=p=>Array.isArray(p)&&p.length===3&&p.every(x=>Number.isFinite(x)&&x>=0&&x<=1)&&Math.abs(p.reduce((a,b)=>a+b,0)-1)<.000003;
const logLoss=(p,i)=>round(-Math.log(Math.max(p[i],1e-12)));
const brier=(p,i)=>round(p.reduce((s,v,j)=>s+(v-+(i===j))**2,0));
const validScore=m=>validFinished(m)&&Number.isInteger(m.score.fullTime.home)&&m.score.fullTime.home>=0&&m.score.fullTime.home<=20&&Number.isInteger(m.score.fullTime.away)&&m.score.fullTime.away>=0&&m.score.fullTime.away<=20;

export function verifyFrozenArtifact(artifact){
  const {artifactSha256,...payload}=artifact||{};
  if(!/^[a-f0-9]{64}$/.test(artifactSha256||'')||hash(payload)!==artifactSha256||
      payload.version!==1||payload.model!==MODEL||payload.source!=='openfootball/football.json'||
      payload.sourceCommit!==sourceRef||payload.license!=='CC0-1.0'||
      payload.trainedThrough!=='2023-24'||payload.historyThrough!=='2025-26'||
      !Array.isArray(payload.files)||payload.files.length!==24||
      LEAGUES.some(l=>payload.seed?.[l]?.lastSeason!=='2025-26'))throw Error('INVALID_FROZEN_RESEARCH_ARTIFACT');
  const c=payload.classifier;
  if(!Array.isArray(c?.means)||c.means.length!==25||
      !Array.isArray(c.scales)||c.scales.length!==25||
      c.means.some(x=>!Number.isFinite(x))||c.scales.some(x=>!Number.isFinite(x)||x<=0)||
      !Array.isArray(c.w)||c.w.length!==3||c.w.some(x=>x.length!==26||x.some(y=>!Number.isFinite(y))))throw Error('INVALID_FROZEN_CLASSIFIER');
  return artifact;
}

// Issue a single research forecast per eligible fixture at the actual cutoff.
// Date-only historical observations and current live observations must both
// precede the issue calendar day. The original provider ID remains authoritative.
export function makeLivePairs(league,artifact,now=new Date()){
  verifyFrozenArtifact(artifact);
  const cutoff=dayString(now),l=mapLeague[league.code];
  if(!l)return {pairs:[],unmatchedNames:[]};
  if(cutoff<'2026-07-01'||cutoff>='2027-07-01')return {pairs:[],unmatchedNames:[],reason:'SEASON_OUTSIDE_FROZEN_ARTIFACT'};
  const names=new Map(),ids=new Map();
  const checkTeam=t=>{
    if(!t||t.id==null||typeof t.name!=='string'||!t.name.trim())throw Error('INVALID_LIVE_TEAM');
    const id=String(t.id),name=t.name;
    if((names.has(name)&&names.get(name)!==id)||(ids.has(id)&&ids.get(id)!==name))throw Error('AMBIGUOUS_LIVE_TEAM_IDENTITY');
    names.set(name,id);ids.set(id,name);
  };
  const seen=new Set();
  const history=(league.finished||[]).filter(m=>{
    if(!validScore(m)||!Number.isFinite(Date.parse(m.utcDate)))return false;
    const d=dayString(m.utcDate);
    return d>= '2026-07-01'&&d<cutoff;
  });
  const row=m=>{
    checkTeam(m.homeTeam);checkTeam(m.awayTeam);
    const id=String(m.id);
    if(seen.has(id))throw Error('DUPLICATE_LIVE_FIXTURE');
    seen.add(id);
    return {id,league:l,season:'2026-27',date:dayString(m.utcDate),home:m.homeTeam.name,away:m.awayTeam.name};
  };
  const observed=history.map(m=>({...row(m),hg:m.score.fullTime.home,ag:m.score.fullTime.away}));
  const fixtures=(league.upcoming||[]).filter(m=>['TIMED','SCHEDULED'].includes(m.status)&&
    Number.isFinite(Date.parse(m.utcDate))&&Date.parse(m.utcDate)>now.getTime()&&dayString(m.utcDate)<'2027-07-01');
  const future=fixtures.map(m=>({...row(m),upcoming:true,hg:null,ag:null}));
  const featuresById=new Map(causalRows([...observed,...future],{seed:artifact.seed,cutoff})
    .filter(r=>r.upcoming).map(r=>[r.id,r]));
  const pairs=[];
  for(const fixture of fixtures){
    const x=featuresById.get(String(fixture.id));
    const baseline=predictFixture(fixture,history,now);
    if(!x||!baseline)continue;
    // The exact same original method in the historical feature builder must
    // agree numerically with the real provider-ID baseline.
    if(!x.baseline||x.baseline.some((v,i)=>Math.abs(v-baseline.p1x2[i])>1e-6))throw Error('BASELINE_FEATURE_MISMATCH');
    const p=predict(artifact.classifier,x.features).map(round);
    if(!probabilities(p))throw Error('INVALID_RESEARCH_PROBABILITIES');
    const matchKey=canonicalFixtureKey(baseline);
    const unknown=[fixture.homeTeam.name,fixture.awayTeam.name].filter(n=>!Object.hasOwn(artifact.seed[l].teams,n));
    pairs.push({
      fixtureId:baseline.fixtureId,kickoffUtc:baseline.kickoffUtc,generatedAt:now.toISOString(),
      competition:league.name,competitionCode:league.code,homeTeam:baseline.homeTeam,awayTeam:baseline.awayTeam,
      homeTeamId:baseline.homeTeamId,awayTeamId:baseline.awayTeamId,
      model:MODEL,certified:false,researchOnly:true,market:'1X2',
      p1x2:p,pairedBaseline:baseline,trainingMatches:baseline.trainingMatches,
      homeVenueMatches:baseline.homeVenueMatches,awayVenueMatches:baseline.awayVenueMatches,
      sourceArtifactSha256:artifact.artifactSha256,knownHistoryCutoffDay:cutoff,
      historicalNameCoverage:unknown.length===0,unknownHistoricalTeams:unknown,
      provenanceSha256:hash({artifact:artifact.artifactSha256,matchKey,cutoff,
        baseline:baseline.provenanceSha256,features:x.features,p})
    });
  }
  return {pairs,unmatchedNames:[...new Set(pairs.flatMap(p=>p.unknownHistoricalTeams))].sort()};
}
export function evaluateLivePair(p,final,now=new Date()){
  const b=p?.pairedBaseline;
  if(p?.model!==MODEL||p.market!=='1X2'||p.researchOnly!==true||!p.forecastKey||
      b?.model!==MODEL_ID||b.generatedAt!==p.generatedAt||canonicalFixtureKey(p)!==canonicalFixtureKey(b)||
      !validScore(final)||String(final.id)!==p.fixtureId||
      Date.parse(final.utcDate)!==Date.parse(p.kickoffUtc)||String(final.homeTeam.id)!==String(p.homeTeamId)||
      String(final.awayTeam.id)!==String(p.awayTeamId)||Date.parse(p.generatedAt)>=Date.parse(p.kickoffUtc)||
      now.getTime()<Date.parse(p.kickoffUtc)+4*3600000||!probabilities(p.p1x2)||!probabilities(b.p1x2))return null;
  const h=final.score.fullTime.home,a=final.score.fullTime.away,i=h>a?0:h===a?1:2;
  return {fixtureId:p.fixtureId,forecastKey:p.forecastKey,kickoffUtc:p.kickoffUtc,competitionCode:p.competitionCode,
    observedAt:now.toISOString(),home:h,away:a,outcome:code(i),source:'football-data.org/v4',
    scoreFingerprint:hash({fixtureId:p.fixtureId,h,a}),candidatePick:top(p.p1x2),baselinePick:top(b.p1x2),
    logLoss1x2:logLoss(p.p1x2,i),brier1x2:brier(p.p1x2,i),baselineLogLoss:logLoss(b.p1x2,i),
    baselineBrier:brier(b.p1x2,i),researchOnly:true};
}
export function summarizeLivePairs(predictions=[],evaluations=[]){
  const valid=predictions.filter(p=>p.model===MODEL&&p.researchOnly===true&&p.market==='1X2'&&
    p.pairedBaseline?.model===MODEL_ID&&p.pairedBaseline.generatedAt===p.generatedAt&&
    canonicalFixtureKey(p)===canonicalFixtureKey(p.pairedBaseline)&&probabilities(p.p1x2)&&probabilities(p.pairedBaseline.p1x2));
  const latest=latestForecastsByMatch(valid);
  const rows=selectLatestEvaluations(valid,evaluations).filter(e=>{
    const p=latest.get(canonicalFixtureKey(e));
    if(!p||e.forecastKey!==p.forecastKey||e.researchOnly!==true||!Number.isInteger(e.home)||e.home<0||!Number.isInteger(e.away)||e.away<0)return false;
    const i=e.home>e.away?0:e.home===e.away?1:2;
    return e.outcome===code(i)&&e.candidatePick===top(p.p1x2)&&e.baselinePick===top(p.pairedBaseline.p1x2)&&
      e.logLoss1x2===logLoss(p.p1x2,i)&&e.baselineLogLoss===logLoss(p.pairedBaseline.p1x2,i)&&
      Date.parse(e.observedAt)>=Date.parse(p.kickoffUtc)+4*3600000;
  });
  return {...summarizeAccuracyPairs(rows),status:rows.length?'RESEARCH_ONLY':'AWAITING_REAL_RESULTS',
    candidateModel:MODEL,primaryModel:MODEL_ID};
}
