import { calculateScenario, validateLedger, validateAssessment, summarizeLedger, filterLedger } from "./analysis.mjs";
const byId=id=>document.getElementById(id);
const percent=p=>p===null?"—":(p*100).toFixed(1)+"%";
const leagueNames={E0:"Premier League",SP1:"La Liga",I1:"Serie A",D1:"Bundesliga"};
let ledger=null, assessment=null;
function element(tag,text,cls){const el=document.createElement(tag);if(text!==null)el.textContent=text;if(cls)el.className=cls;return el;}
function replace(el,nodes){el.replaceChildren(...nodes);}
function metric(label,value){const m=element("div",null,"metric");m.append(element("span",label),element("strong",value));return m;}
function drawScenario(){
  try{
    const h=byId("home-lambda").value, a=byId("away-lambda").value;
    if(h.trim()===""||a.trim()==="")throw new Error("Enter both expected goal values.");
    const s=calculateScenario(Number(h),Number(a));
    byId("scenario-error").textContent="";
    replace(byId("outcomes"),[["Home win",s.home],["Draw",s.draw],["Away win",s.away]].map(([k,v])=>metric(k,percent(v))));
    replace(byId("markets"),[["Over 2.5 goals",s.over],["Under 2.5 goals",s.under],["BTTS yes",s.bttsYes],["BTTS no",s.bttsNo]].map(([k,v])=>{const m=element("div",null,"market");m.append(element("small",k),element("strong",percent(v)));return m;}));
    replace(byId("scorelines"),s.scorelines.map(x=>{const m=element("div",null,"score");m.append(element("strong",x.home+" – "+x.away),element("span",percent(x.probability)));return m;}));
    byId("scenario-summary").textContent="Independent-Poisson scenario using λ "+h+" / "+a+". Probability mass conserved. This is not a team forecast.";
  }catch(e){
    byId("scenario-error").textContent=e.message;
    for(const id of ["outcomes","markets","scorelines"])byId(id).replaceChildren();
    byId("scenario-summary").textContent="";
  }
}
byId("scenario-form").addEventListener("submit",e=>{e.preventDefault();drawScenario();});drawScenario();
async function readJson(file){if(!file)throw new Error("No file selected.");if(file.size>5000000)throw new Error("Maximum file size is 5 MB.");return JSON.parse(await file.text());}
function drawEvidence(){
  const details=byId("evidence-details");details.hidden=!ledger;
  byId("assessment-file").disabled=!ledger;
  if(!ledger)return;
  const s=summarizeLedger(ledger,assessment);
  replace(byId("evidence-summary"),[metric("Original forecasts",String(s.total)),metric("50%+ confidence",String(s.selected)),metric("Final scores loaded",String(s.scored)),metric("Conditional accuracy",percent(s.conditionalAccuracy))]);
  const filtered=filterLedger(ledger,{league:byId("league-filter").value,search:byId("search-filter").value,confidence:byId("confidence-filter").checked});
  const results=new Map((assessment?.matched_receipts||[]).map(r=>[r.fixture_sha256,r]));
  const rows=filtered.map(p=>{
    const tr=element("tr",null), result=results.get(p.fixture_sha256);
    const vec=prefix=>["home","draw","away"].map(k=>percent(p[prefix+"_prob_"+k])).join(" / ");
    for(const val of [p.expected_calendar_date,p.provider_home+" vs "+p.provider_away+" · "+leagueNames[p.league],vec("baseline"),vec("candidate"),result?result.score.join("–")+(result.candidate.correct?" · Elo correct":" · Elo missed"):"Pending / unverified"])tr.append(element("td",val));
    return tr;
  });
  replace(byId("forecast-rows"),rows);
  byId("filter-count").textContent=filtered.length+" of "+s.total+" original fixtures. "+s.abstained+" below 50% confidence. "+(s.accuracy===null?"Full-cohort accuracy not yet evaluated.":"Full scored-cohort Elo accuracy: "+percent(s.accuracy)+"; "+s.selectedScored+" selected matches scored.");
  byId("ledger-fingerprint").textContent="Claimed original SHA-256: "+ledger.ledger_sha256+" · issued "+ledger.generated_at_utc+" · structure checked locally, not cryptographically authenticated.";
}
function reset(){ledger=null;assessment=null;byId("ledger-file").value="";byId("assessment-file").value="";byId("search-filter").value="";byId("confidence-filter").checked=false;byId("league-filter").replaceChildren(new Option("All leagues",""));byId("import-status").textContent="No evidence loaded. Nothing is uploaded to a server.";byId("import-error").textContent="";drawEvidence();}
byId("ledger-file").addEventListener("change",async e=>{
  ledger=null;assessment=null;byId("assessment-file").value="";byId("import-error").textContent="";drawEvidence();
  try{ledger=validateLedger(await readJson(e.target.files[0]));byId("league-filter").replaceChildren(new Option("All leagues",""),...[...new Set(ledger.predictions.map(p=>p.league))].sort().map(k=>new Option(leagueNames[k],k)));byId("import-status").textContent="Local ledger loaded. Schema and cohort checked; original fingerprint is claimed by the file, not independently verified.";drawEvidence();}catch(err){ledger=null;drawEvidence();byId("import-status").textContent="No valid evidence loaded.";byId("import-error").textContent=err.message;}
});
byId("assessment-file").addEventListener("change",async e=>{
  assessment=null;byId("import-error").textContent="";drawEvidence();
  try{assessment=validateAssessment(await readJson(e.target.files[0]),ledger);byId("import-status").textContent="Matching local assessment loaded. Results are structurally consistent; verify upstream provenance in GitHub.";drawEvidence();}catch(err){byId("import-error").textContent=err.message;}
});
for(const id of ["league-filter","search-filter","confidence-filter"])byId(id).addEventListener("input",drawEvidence);
byId("clear-ledger").addEventListener("click",reset);
