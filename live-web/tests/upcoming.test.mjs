import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createUpcomingFeed,filterUpcoming,makeGoalModel,validateSource,normalizeSource} from "../src/upcoming.mjs";
const docs=await Promise.all(["en","es","it","de"].map(async l=>JSON.parse(await readFile(new URL("../data/"+l+".json",import.meta.url),"utf8"))));
const asOf="2026-11-12";
test("open-data fixture and past-score receipts are complete and consistently pinned",()=>{
  for(const d of docs)assert.equal(validateSource(d,d.league),d);
  assert.equal(new Set(docs.map(d=>d.source_commit)).size,1);
  assert.ok(docs.every(d=>d.license==="CC0-1.0"));
});
test("real upcoming match feed covers all four leagues with dated, normalized estimates",()=>{
  const feed=createUpcomingFeed(docs,asOf);
  assert.equal(feed.generatedOn,asOf);
  const next=filterUpcoming(feed,asOf);
  // The initial pinned source has 88 next-21-day fixtures. Future provider updates
  // can legitimately change the schedule, so only pin this initial commit.
  if(feed.sourceCommit==="e6744429ee395bc86f247348c6184bb08d4eb361"){
    assert.equal(next.length,88);
    assert.ok(feed.leagues.every(l=>l.latestResult==="2026-09-20"));
  }
  assert.ok(next.length>0);
  assert.equal(new Set(next.map(m=>m.league)).size,4);
  assert.ok(next.every(m=>m.date>asOf&&m.date<="2026-12-03"));
  const predicted=next.filter(m=>m.prediction);
  assert.ok(predicted.length>=next.length*.8);
  for(const m of predicted){
    const p=m.prediction;
    assert.ok(Math.abs(p.home+p.draw+p.away-1)<1e-10);
    assert.ok(Math.abs(p.threeOrMore+p.twoOrFewer-1)<1e-10);
    assert.ok(Math.abs(p.bothScore+p.neitherOrOne-1)<1e-10);
    assert.ok(p.scorelines.every(s=>s.probability>=0&&s.probability<=1));
  }
  const found=next[0];
  assert.ok(filterUpcoming(feed,asOf,{league:found.league,search:found.home}).some(m=>m.home===found.home&&m.away===found.away));
  assert.equal(filterUpcoming(feed,"2027-06-01").length,0);
});
test("same-day and future scores never change the goal rates",()=>{
  const rows=docs[0].matches;
  const model=makeGoalModel(rows,asOf);
  const [,,,,]=rows[0];
  const next=rows.find(m=>m[0]==="2026-27"&&m[1]>asOf);
  const changed=rows.map(m=>m[1]>=asOf?[...m.slice(0,4),[20,0]]:m);
  changed.push(["2026-27",asOf,next[2],next[3],[0,20]]);
  const second=makeGoalModel(changed,asOf);
  assert.deepEqual(model.rates(next[2],next[3]),second.rates(next[2],next[3]));
  assert.equal(model.historyCount,second.historyCount);
  assert.equal(model.latestResult,second.latestResult);
  assert.equal(model.rates("Unknown team",next[3]),null);
});
test("duplicate fixtures and invalid score data fail closed",()=>{
  const d=structuredClone(docs[0]);d.matches[1]=[...d.matches[0]];
  assert.throws(()=>validateSource(d,d.league),/Duplicate|receipt/);
  const raw={matches:Array.from({length:380},(_,i)=>({date:"2026-09-01",team1:"Home"+i,team2:"Away"+i,score:{ft:[-1,2]}}))};
  assert.throws(()=>normalizeSource(raw,"2026-27","en"),/Invalid final score/);
});
