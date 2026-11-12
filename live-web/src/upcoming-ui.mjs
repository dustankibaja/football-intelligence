import { filterUpcoming, LEAGUES } from "./upcoming.mjs";
const $=id=>document.getElementById(id);
const fmt=new Intl.DateTimeFormat("en-GB",{day:"numeric",month:"short",year:"numeric",timeZone:"UTC"});
const date=d=>fmt.format(new Date(d));
const pct=p=>Math.round(p*100)+"%";
const el=(tag,text="",cls="")=>{const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n;};
const today=()=>new Date().toISOString().slice(0,10);
let feed=null, limit=12;
function validFeed(data){
  return data?.version===1&&data.source==="openfootball/football.json"&&data.license==="CC0-1.0"&&Array.isArray(data.matches)&&Array.isArray(data.leagues)&&typeof data.generatedOn==="string"&&data.matches.every(m=>LEAGUES[m.league]&&typeof m.date==="string"&&typeof m.home==="string"&&typeof m.away==="string"&&(!m.prediction||["home","draw","away","threeOrMore","twoOrFewer","bothScore","neitherOrOne"].every(k=>Number.isFinite(m.prediction[k])&&m.prediction[k]>=0&&m.prediction[k]<=1)));
}
function matchCard(match){
  const p=match.prediction, article=el("article","","panel match-card");
  const head=el("div","","match-head");
  head.append(el("span",LEAGUES[match.league],"eyebrow"),el("span",date(match.date),"small"));
  article.append(head,el("h3",match.home+" vs "+match.away,"match-title"));
  if(!p){article.append(el("p","We don't have enough past results for these teams yet.","small"));return article;}
  const row=el("div","","match-probs");
  for(const [label,value] of [[match.home+" wins",p.home],["Draw (a tie)",p.draw],[match.away+" wins",p.away]]){
    const metric=el("div","","metric");metric.append(el("span",label),el("strong",pct(value)));row.append(metric);
  }
  article.append(row);
  const choices=[[match.home+" win",p.home],["a draw",p.draw],[match.away+" win",p.away]].sort((a,b)=>b[1]-a[1]);
  article.append(el("p","Most likely result: "+choices[0][0]+" ("+pct(choices[0][1])+"). It can still go another way.","match-pick"));
  const details=el("details","","match-details");
  details.append(el("summary","More about this match"));
  const markets=el("div","","market-grid");
  for(const [label,value] of [["3 or more goals",p.threeOrMore],["0 to 2 goals",p.twoOrFewer],["Both teams score",p.bothScore],["At least one team scores no goals",p.neitherOrOne]]){
    const m=el("div","","market");m.append(el("small",label),el("strong",pct(value)));markets.append(m);
  }
  details.append(markets,el("p","Most likely final scores (home team first)","small"));
  const scores=el("div","","scorelines");
  for(const s of p.scorelines){const n=el("div","","score");n.append(el("strong",s.home+" – "+s.away),el("span",pct(s.probability)));scores.append(n);}
  details.append(scores);
  const info=feed.leagues.find(l=>l.league===match.league);
  details.append(el("p","Based on past scores available through "+(info?.latestResult?date(info.latestResult):"an earlier date")+". Newer results, injuries and team news may be missing.","small"));
  article.append(details);
  return article;
}
function render(){
  if(!feed)return;
  const matches=filterUpcoming(feed,today(),{league:$("upcoming-league").value,search:$("upcoming-search").value});
  $("upcoming-count").textContent=matches.length===0?"No upcoming matches found. Try another league or team.":"Showing "+Math.min(limit,matches.length)+" of "+matches.length+" scheduled matches over the next 21 days.";
  $("upcoming-cards").replaceChildren(...matches.slice(0,limit).map(matchCard));
  $("upcoming-more").hidden=matches.length<=limit;
}
async function load(){
  $("upcoming-status").textContent="Finding upcoming matches…";
  $("upcoming-retry").hidden=true;
  try{
    const response=await fetch("./upcoming-data.json",{cache:"no-cache"});
    if(!response.ok)throw Error("unavailable");
    const data=await response.json();
    if(!validFeed(data))throw Error("invalid");
    feed=data;
    const latest=data.leagues.map(l=>l.latestResult).filter(Boolean).sort().at(-1);
    const oldest=data.leagues.map(l=>l.latestResult).filter(Boolean).sort()[0];
    $("upcoming-status").textContent="Predictions made "+date(data.generatedOn)+". "+(latest?"The most recent scores we have are from "+date(latest)+".":"We have no past scores yet.");
    const old=oldest&&Date.parse(today())-Date.parse(oldest)>14*86400000;
    $("upcoming-warning").textContent=old?"Some recent match results are missing. Treat these as early guesses based on older scores, not up-to-the-minute predictions.":"These are early predictions based on past scores. They may change as more information becomes available.";
    $("upcoming-warning").hidden=false;
    $("upcoming-controls").hidden=false;
    render();
  }catch{
    feed=null;$("upcoming-status").textContent="We couldn't load the matches right now. Please try again.";
    $("upcoming-warning").hidden=true;$("upcoming-controls").hidden=true;
    $("upcoming-cards").replaceChildren();$("upcoming-count").textContent="";$("upcoming-more").hidden=true;
    $("upcoming-retry").hidden=false;
  }
}
for(const id of ["upcoming-league","upcoming-search"])$(id).addEventListener("input",()=>{limit=12;render();});
$("upcoming-more").addEventListener("click",()=>{limit+=12;render();});
$("upcoming-retry").addEventListener("click",load);
load();
