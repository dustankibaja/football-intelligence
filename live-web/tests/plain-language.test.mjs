import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("page explains football in everyday English without visible developer terminology",async()=>{
  const html=await readFile(new URL("../src/index.html",import.meta.url),"utf8");
  const visible=html.replace(/<[^>]*>/g," ");
  for(const word of ["Poisson","λ","BTTS","1X2","C1","M1","Elo","SHA-256","JSON","ledger","schema","cohort","fixture","canonical","prospective","artifact","calibration","pipeline","GitHub","API","release gates"]){
    assert.ok(!new RegExp("(^|[^a-zA-Z])"+word+"([^a-zA-Z]|$)","i").test(visible),"Visible technical term: "+word);
  }
  for(const phrase of ["Try a match","Who might win?","Goals for the home team","Goals for the visiting team","A draw means both teams finish with the same number of goals","How did our predictions do?","Live predictions for upcoming games are not available yet","not real team information","Your files stay on your device"]){
    assert.ok(visible.includes(phrase),"Missing plain-English text: "+phrase);
  }
});

class Node {
  constructor(tag="div"){this.tag=tag;this.children=[];this.value="";this.checked=false;this.disabled=false;this.hidden=false;this.files=[];this.listeners={};this._text="";}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join(" ");}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this._text="";this.children=nodes;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  async fire(name){await this.listeners[name]({target:this,preventDefault(){}});}
}
const fixture={fixture_sha256:"a".repeat(64),league:"E0",provider_home:"Home",provider_away:"Away",expected_calendar_date:"2026-11-21",baseline_prob_home:0.5,baseline_prob_draw:0.25,baseline_prob_away:0.25,candidate_prob_home:0.2,candidate_prob_draw:0.3,candidate_prob_away:0.5,status:"PAIRED_PREMATCH_SHADOW_UNSCORED"};
const ledger={schema_version:"independent_prospective_elo_challenger_1.0.0",status:"RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS",model:{publication_eligible:false},ledger_sha256:"b".repeat(64),generated_at_utc:"2026-11-11T08:00:00+00:00",counts:{challenger_issued:1,baseline_future_qualified:1},predictions:[fixture]};
const assessment={schema_version:"independent_paired_prospective_1x2_assessment_1.0.0",original_paired_ledger_sha256:ledger.ledger_sha256,report_sha256:"c".repeat(64),total_original_paired:1,finished_evaluated:1,pending_or_unverified:0,matched_receipts:[{fixture_sha256:fixture.fixture_sha256,league:"E0",score:[0,1],result_date:"2026-11-21",actual_1x2:2,baseline:{predicted_1x2:0,correct:false},candidate:{predicted_1x2:2,correct:true}}]};
const file=v=>({size:1000,text:async()=>JSON.stringify(v)});
test("interactive copy explains chances, correct-pick counts, errors and empty results",async()=>{
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Node());return nodes.get(id);};
  globalThis.document={getElementById:get,createElement:tag=>new Node(tag)};
  globalThis.Option=class extends Node {constructor(text,value){super("option");this.textContent=text;this.value=value;}};
  get("home-lambda").value="1.55";get("away-lambda").value="1.15";
  await import("../src/app.mjs");
  assert.match(get("outcomes").textContent,/Home team wins.*Draw \(a tie\).*Visiting team wins/);
  assert.match(get("markets").textContent,/3 or more goals in total.*0 to 2 goals in total.*Both teams score/);
  assert.match(get("scenario-summary").textContent,/based only on the goals you entered/);
  get("home-lambda").value="";
  await get("scenario-form").fire("submit");
  assert.match(get("scenario-error").textContent,/Please enter a number from 0 to 8/);
  get("home-lambda").value="1.55";await get("scenario-form").fire("submit");
  assert.equal(get("scenario-error").textContent,"");
  get("ledger-file").files=[file({})];await get("ledger-file").fire("change");
  assert.match(get("import-error").textContent,/doesn't look like one of our saved predictions files/);
  assert.equal(get("evidence-details").hidden,true);
  get("ledger-file").files=[{size:10,text:async()=>"{broken"}];await get("ledger-file").fire("change");
  assert.match(get("import-error").textContent,/couldn't open this file/);
  get("ledger-file").files=[file(ledger)];await get("ledger-file").fire("change");
  assert.equal(get("evidence-details").hidden,false);
  assert.match(get("forecast-rows").textContent,/21 Nov 2026.*No result added yet/);
  assert.match(get("evidence-summary").textContent,/Stronger picks rightNot yet/);
  assert.match(get("filter-count").textContent,/No match results have been added yet/);
  assert.match(get("ledger-fingerprint").textContent,/can't confirm where the file came from/);
  get("assessment-file").files=[file({...assessment,original_paired_ledger_sha256:"d".repeat(64)})];await get("assessment-file").fire("change");
  assert.match(get("import-error").textContent,/couldn't match these results to your predictions/);
  get("assessment-file").files=[file(assessment)];await get("assessment-file").fire("change");
  assert.match(get("forecast-rows").textContent,/Second prediction was right/);
  assert.match(get("filter-count").textContent,/1 of 1 matches with results were right \(100.0%\)/);
  assert.match(get("filter-count").textContent,/1 of 1 stronger picks with results were right/);
  get("search-filter").value="Not a team";await get("search-filter").fire("input");
  assert.match(get("forecast-rows").textContent,/No matches found/);
  await get("clear-ledger").fire("click");
  assert.equal(get("evidence-details").hidden,true);
  assert.equal(get("assessment-file").disabled,true);
});
