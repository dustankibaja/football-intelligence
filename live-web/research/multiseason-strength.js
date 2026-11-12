// Independent research only. The original live engine and stored forecasts are not modified.
import { predictFixture } from '../lib/model.js';
export const MODEL='footmo-multiseason-strength-research-v1';
export const SEASONS=['2020-21','2021-22','2022-23','2023-24','2024-25','2025-26'];
export const LEAGUES=['en','es','it','de'];
const round=x=>Math.round(x*1e6)/1e6;
const mean=a=>a.reduce((s,x)=>s+x,0)/a.length;
const day=86400000;
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const pick=p=>p.indexOf(Math.max(...p));
const loss=(p,y)=>-Math.log(Math.max(1e-12,p[y]));
const result=(h,a)=>h>a?0:h===a?1:2;
const validDate=d=>typeof d==='string'&&/^\d{4}-\d\d-\d\d$/.test(d)&&Number.isFinite(Date.parse(d))&&new Date(d).toISOString().slice(0,10)===d;
export function validateSeason(raw,season,league){
  const year=+season.slice(0,4), expected=league==='de'?306:380;
  if(!Array.isArray(raw?.matches)||raw.matches.length!==expected)throw Error('Incomplete season '+season+'/'+league);
  const seen=new Set();
  return raw.matches.flatMap(m=>{
    const d=m.date,h=m.team1,a=m.team2,s=m.score?.ft??(Array.isArray(m.score)?m.score:null);
    if(!validDate(d)||d<year+'-07-01'||d>=(year+1)+'-08-01'||typeof h!=='string'||!h.trim()||typeof a!=='string'||!a.trim()||h===a)throw Error('Invalid match/date '+season+'/'+league+'/'+d);
    const key=h+'|'+a;if(seen.has(key))throw Error('Duplicate pairing '+season+'/'+league);seen.add(key);
    if(s===null||s===undefined){
      // The pinned source omits the final ten Spanish and final ten Italian
      // results in 2024/25. Keep the fixture identity validated; never invent
      // the scores or treat them as observations. No other missing scores allowed.
      if(season==='2024-25'&&['es','it'].includes(league)&&d>='2025-05-23'&&d<='2025-05-25'&&m.round==='Matchday 38')return [];
      throw Error('Missing score '+season+'/'+league+'/'+d);
    }
    if(!Array.isArray(s)||s.length!==2||s.some(x=>!Number.isInteger(x)||x<0||x>20))throw Error('Invalid score '+season+'/'+league+'/'+d);
    return [{season,league,date:d,home:h,away:a,hg:s[0],ag:s[1],id:season+'/'+league+'/'+key}];
  });
}
function blank(){return {rating:1500,n:0,homeN:0,awayN:0,last:null,recent:[],gf:0,ga:0};}
function recent(t){
  const r=t.recent.slice(-10),n=r.length;
  return [(r.reduce((s,x)=>s+x[0],0)+3*1.35)/(n+3),
    (r.reduce((s,x)=>s+x[1],0)+3*1.35)/(n+3),
    (r.reduce((s,x)=>s+x[2],0)+3*1.35)/(n+3)];
}
function providerShape(m){return {id:m.id,utcDate:m.date+'T12:00:00Z',status:'FINISHED',homeTeam:{id:m.home,name:m.home},awayTeam:{id:m.away,name:m.away},competition:{code:m.league,name:m.league},score:{fullTime:{home:m.hg,away:m.ag}}};}
export function causalRows(matches,{seed=null,includeSeed=false,cutoff=null}={}){
  const rows=[],finalSeed={};
  for(const league of LEAGUES){
    const ordered=matches.filter(m=>m.league===league).sort((a,b)=>a.date.localeCompare(b.date)||a.id.localeCompare(b.id));
    const teams=new Map(Object.entries(seed?.[league]?.teams||{}).map(([name,t])=>[name,structuredClone(t)]));
    let lastSeason=seed?.[league]?.lastSeason??null,seasonHistory=[];
    const team=name=>{if(!teams.has(name))teams.set(name,blank());return teams.get(name);};
    for(let i=0;i<ordered.length;){
      const date=ordered[i].date, group=[];while(i<ordered.length&&ordered[i].date===date)group.push(ordered[i++]);
      // A whole calendar day is one observation block. No match on this day may see any result from this day.
      const seasons=new Set(group.map(m=>m.season));if(seasons.size!==1)throw Error('Overlapping season days '+league+'/'+date);
      const season=group[0].season;
      if(lastSeason!==season){
        for(const t of teams.values()){t.rating=1500+.8*(t.rating-1500);t.homeN=0;t.awayN=0;}
        lastSeason=season;seasonHistory=[];
      }
      const updates=[];
      for(const m of group){
        const h=team(m.home),a=team(m.away),hr=recent(h),ar=recent(a);
        const f=providerShape(m),asOf=new Date((m.upcoming&&cutoff?cutoff:date)+'T00:00:00Z');
        const base=predictFixture({...f,status:'SCHEDULED'},seasonHistory,asOf);
        const leagueN=seasonHistory.length;
        const hm=(seasonHistory.reduce((s,x)=>s+x.score.fullTime.home,0)+30*1.5)/(leagueN+30);
        const am=(seasonHistory.reduce((s,x)=>s+x.score.fullTime.away,0)+30*1.15)/(leagueN+30);
        const e=1/(1+10**((a.rating-h.rating-60)/400));
        const rest=t=>t.last?clamp((Date.parse(date)-Date.parse(t.last))/day,0,60)/14:1;
        // Only pre-match, previously observed scores. Missing clubs use fixed league priors.
        const features=[(h.rating-a.rating)/400,e,(h.rating+a.rating-3000)/400,
          hr[0]-ar[0],hr[1]-ar[1],hr[2]-ar[2],hr[0],ar[0],hr[1],ar[1],
          (h.gf+5*1.35)/(h.n+5)-(a.gf+5*1.35)/(a.n+5),
          (h.ga+5*1.35)/(h.n+5)-(a.ga+5*1.35)/(a.n+5),
          hm,am,rest(h)-rest(a),Math.log1p(h.n),Math.log1p(a.n),
          base?.p1x2[0]??.45,base?.p1x2[1]??.26,base?.p1x2[2]??.29,
          base?1:0,...LEAGUES.map(l=>+(league===l))];
        const y=m.upcoming?null:result(m.hg,m.ag);
        rows.push({id:m.id,date,season,league,y,features,baseline:base?.p1x2??null,upcoming:!!m.upcoming});
        if(m.upcoming)continue;
        const delta=24*((y===0?1:y===1?.5:0)-e);
        updates.push({m,h,a,delta});
      }
      for(const {m,h,a,delta} of updates){
        h.rating+=delta;a.rating-=delta;
        h.n++;a.n++;h.homeN++;a.awayN++;
        h.gf+=m.hg;h.ga+=m.ag;a.gf+=m.ag;a.ga+=m.hg;
        h.recent.push([m.hg,m.ag,m.hg>m.ag?3:m.hg===m.ag?1:0]);h.recent=h.recent.slice(-10);
        a.recent.push([m.ag,m.hg,m.ag>m.hg?3:m.hg===m.ag?1:0]);a.recent=a.recent.slice(-10);
        h.last=date;a.last=date;
        seasonHistory.push(providerShape(m));
      }
    }
    finalSeed[league]={lastSeason,teams:Object.fromEntries(teams)};
  }
  rows.sort((a,b)=>a.date.localeCompare(b.date)||a.id.localeCompare(b.id));
  return includeSeed?{rows,seed:finalSeed}:rows;
}
function softmax(z){const max=Math.max(...z),e=z.map(v=>Math.exp(v-max)),s=e.reduce((a,b)=>a+b,0);return e.map(v=>v/s);}
export function fit(rows){
  if(rows.length<100)throw Error('Insufficient training rows');
  const d=rows[0].features.length;
  const means=Array.from({length:d},(_,j)=>mean(rows.map(r=>r.features[j])));
  const scales=means.map((m,j)=>Math.max(.01,Math.sqrt(mean(rows.map(r=>(r.features[j]-m)**2)))));
  const x=rows.map(r=>[1,...r.features.map((v,j)=>(v-means[j])/scales[j])]);
  const w=Array.from({length:3},()=>Array(d+1).fill(0));
  // Fixed L2 multinomial logistic model. No holdout-driven hyperparameter tuning.
  for(let epoch=0;epoch<280;epoch++){
    const g=Array.from({length:3},()=>Array(d+1).fill(0));
    for(let i=0;i<x.length;i++){
      const p=softmax(w.map(a=>a.reduce((s,v,j)=>s+v*x[i][j],0)));
      for(let k=0;k<3;k++){const e=p[k]-+(rows[i].y===k);for(let j=0;j<=d;j++)g[k][j]+=e*x[i][j];}
    }
    const lr=.18/Math.sqrt(1+epoch/50);
    for(let k=0;k<3;k++)for(let j=0;j<=d;j++)w[k][j]-=lr*(g[k][j]/x.length+(j?.018*w[k][j]:0));
  }
  return {means,scales,w};
}
export function predict(model,features){
  const x=[1,...features.map((v,j)=>(v-model.means[j])/model.scales[j])];
  return softmax(model.w.map(a=>a.reduce((s,v,j)=>s+v*x[j],0)));
}
function metrics(rows,key){
  const n=rows.length;if(!n)return {n:0,correct:0,accuracy:null,logLoss:null};
  const correct=rows.filter(r=>pick(r[key])===r.y).length;
  return {n,correct,accuracy:round(correct/n),logLoss:round(mean(rows.map(r=>loss(r[key],r.y)))),
    draws:rows.filter(r=>r.y===1).length,drawPicks:rows.filter(r=>pick(r[key])===1).length,
    drawCorrect:rows.filter(r=>r.y===1&&pick(r[key])===1).length};
}
export function compare(rows,model){
  const scored=rows.map(r=>({...r,candidate:predict(model,r.features),homePrior:[.45,.26,.29]}));
  const paired=scored.filter(r=>r.baseline);
  return {total:rows.length,baselineCoverage:round(paired.length/rows.length),
    fullCandidate:metrics(scored,'candidate'),fullHomePrior:metrics(scored,'homePrior'),
    pairedBaseline:metrics(paired,'baseline'),pairedCandidate:metrics(paired,'candidate'),
    candidateOnlyCorrect:paired.filter(r=>pick(r.candidate)===r.y&&pick(r.baseline)!==r.y).length,
    baselineOnlyCorrect:paired.filter(r=>pick(r.baseline)===r.y&&pick(r.candidate)!==r.y).length,
    leagues:LEAGUES.map(league=>({league,baseline:metrics(paired.filter(r=>r.league===league),'baseline'),candidate:metrics(paired.filter(r=>r.league===league),'candidate')}))};
}
export function run(matches){
  const {rows,seed}=causalRows(matches,{includeSeed:true});
  const training=rows.filter(r=>r.season<'2024-25');
  const development=rows.filter(r=>r.season==='2024-25');
  const test=rows.filter(r=>r.season==='2025-26');
  const model=fit(training);
  return {model:MODEL,status:'RETROSPECTIVE_RESEARCH_ONLY',training:training.length,
    development:compare(development,model),test:compare(test,model),promotionEligible:false,
    artifact:{version:1,model:MODEL,trainedThrough:'2023-24',historyThrough:'2025-26',classifier:model,seed}};
}
