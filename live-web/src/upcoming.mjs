import { calculateScenario } from "./analysis.mjs";
export const LEAGUES = {en:"Premier League",es:"La Liga",it:"Serie A",de:"Bundesliga"};
const DAY=86400000;
const SOURCE="openfootball/football.json";
const validDay=d=>typeof d==="string" && /^\d{4}-\d{2}-\d{2}$/.test(d) && Number.isFinite(Date.parse(d)) && new Date(d).toISOString().slice(0,10)===d;
const scoreOK=s=>Array.isArray(s)&&s.length===2&&s.every(x=>Number.isInteger(x)&&x>=0&&x<=20);
const age=(a,b)=>(Date.parse(a)-Date.parse(b))/DAY;
const clamp=(x,min,max)=>Math.max(min,Math.min(max,x));
export function normalizeSource(raw, season, league) {
  if(!raw||!Array.isArray(raw.matches))throw Error("Invalid source match list");
  const expected=league==="de"?306:380;
  if(raw.matches.length!==expected)throw Error("Incomplete source season "+season+"/"+league);
  const seen=new Set();
  return raw.matches.map(m=>{
    const {date:d,team1:h,team2:a}=m;
    const score=m.score?.ft??(Array.isArray(m.score)?m.score:null);
    if(!validDay(d)||typeof h!=="string"||typeof a!=="string"||!h.trim()||!a.trim()||h===a||h.length>150||a.length>150)throw Error("Invalid match details");
    if(score!==null&&!scoreOK(score))throw Error("Invalid final score");
    const id=h+"|"+a;
    if(seen.has(id))throw Error("Duplicate team pairing in source season");
    seen.add(id);
    return [season,d,h,a,score];
  });
}
export function validateSource(doc,league){
  if(!doc||doc.source!==SOURCE||doc.license!=="CC0-1.0"||doc.league!==league||!/^[a-f0-9]{40}$/.test(doc.source_commit)||!(/^\d{4}-\d{2}$/).test(doc.season)||!Array.isArray(doc.matches)||!Array.isArray(doc.receipts)||doc.receipts.length!==3)throw Error("Invalid open source snapshot");
  const start=Number(doc.season.slice(0,4)), seasons=[start-2,start-1,start].map(y=>y+"-"+String((y+1)%100).padStart(2,"0"));
  if(doc.season!==seasons[2])throw Error("Invalid season");
  const total=league==="de"?306:380;
  const seen=new Set();
  for(const s of seasons){
    const rows=doc.matches.filter(m=>m[0]===s);
    const receipt=doc.receipts.find(r=>r.path===s+"/"+league+".1.json");
    if(!receipt||!(/^[a-f0-9]{40}$/).test(receipt.blob_sha)||rows.length!==total||receipt.rows!==total||receipt.finished!==rows.filter(m=>m[4]!==null).length)throw Error("Invalid source receipt or coverage");
    for(const m of rows){
      if(!Array.isArray(m)||m.length!==5||!validDay(m[1])||typeof m[2]!=="string"||typeof m[3]!=="string"||!m[2]||!m[3]||m[2]===m[3]||(m[4]!==null&&!scoreOK(m[4])))throw Error("Invalid source match");
      const id=s+"|"+m[2]+"|"+m[3];
      if(seen.has(id))throw Error("Duplicate source pairing");seen.add(id);
    }
  }
  if(doc.matches.length!==total*3)throw Error("Unexpected source records");
  return doc;
}
// A deliberately simple, fixed, independently sourced goal model. No bookmaker
// odds or current-match information. All scores must precede the issue day.
export function makeGoalModel(matches, asOf) {
  if(!validDay(asOf))throw Error("Invalid calculation day");
  const history=matches.filter(m=>m[4]!==null&&m[1]<asOf&&age(asOf,m[1])<=730);
  if(history.length<100)return null;
  let weight=0, homeGoals=0, awayGoals=0;
  const teams=new Map();
  const stats=(name)=>{if(!teams.has(name))teams.set(name,{n:0,w:0,gf:0,ga:0});return teams.get(name);};
  const add=(name,w,gf,ga)=>{const t=stats(name);t.n++;t.w+=w;t.gf+=w*gf;t.ga+=w*ga;};
  // Time decay makes recent results more influential; smoothing avoids extreme
  // estimates for clubs with a short history in this league.
  for(const [,date,h,a,[hg,ag]] of history){
    const w=2**(-age(asOf,date)/180);
    weight+=w;homeGoals+=w*hg;awayGoals+=w*ag;add(h,w,hg,ag);add(a,w,ag,hg);
  }
  const homeMean=homeGoals/weight, awayMean=awayGoals/weight, mean=(homeMean+awayMean)/2;
  if(!(mean>0))return null;
  function rates(h,a){
    const ht=teams.get(h),at=teams.get(a);
    if(!ht||!at||ht.n<5||at.n<5)return null;
    const prior=5;
    const attack=t=>(t.gf+prior*mean)/(t.w+prior)/mean;
    const defence=t=>(t.ga+prior*mean)/(t.w+prior)/mean;
    return [clamp(homeMean*attack(ht)*defence(at),0.15,5),clamp(awayMean*attack(at)*defence(ht),0.15,5)];
  }
  return {historyCount:history.length,latestResult:history.reduce((max,m)=>m[1]>max?m[1]:max,""),rates};
}
export function createUpcomingFeed(documents,asOf){
  if(!validDay(asOf))throw Error("Invalid forecast date");
  const sourceCommit=documents[0]?.source_commit, season=documents[0]?.season;
  if(documents.length!==4||new Set(documents.map(d=>d.league)).size!==4)throw Error("Four distinct leagues required");
  const leagues=[],matches=[];
  for(const doc of documents){
    const {league}=doc;
    if(!LEAGUES[league]||doc.source_commit!==sourceCommit||doc.season!==season)throw Error("Inconsistent open source snapshot");
    validateSource(doc,league);
    const model=makeGoalModel(doc.matches,asOf);
    const upcoming=doc.matches.filter(m=>m[0]===season&&m[1]>asOf&&m[4]===null).sort((a,b)=>a[1].localeCompare(b[1]));
    const last=model?.latestResult??null;
    const missingPast=doc.matches.filter(m=>m[0]===season&&m[1]<asOf&&m[4]===null).length;
    leagues.push({league,name:LEAGUES[league],latestResult:last,missingPast,historyCount:model?.historyCount??0});
    for(const [,date,home,away] of upcoming){
      const rates=model?.rates(home,away);
      const p=rates?calculateScenario(...rates):null;
      const prediction=p?{home:p.home,draw:p.draw,away:p.away,threeOrMore:p.over,twoOrFewer:p.under,bothScore:p.bttsYes,neitherOrOne:p.bttsNo,scorelines:p.scorelines.slice(0,3)}:null;
      matches.push({league,date,home,away,prediction});
    }
  }
  matches.sort((a,b)=>a.date.localeCompare(b.date)||a.league.localeCompare(b.league)||a.home.localeCompare(b.home));
  return {version:1,source:SOURCE,license:"CC0-1.0",sourceCommit,season,generatedOn:asOf,method:"OPEN_GOALS_V1_UNVALIDATED",leagues,matches};
}
export function filterUpcoming(feed,today,{league="",search="",days=21}={}){
  if(!validDay(today))return [];
  const end=new Date(Date.parse(today)+days*DAY).toISOString().slice(0,10);
  const term=search.trim().toLowerCase();
  return feed.matches.filter(m=>m.date>today&&m.date<=end&&(!league||m.league===league)&&(!term||(m.home+" "+m.away).toLowerCase().includes(term)));
}
