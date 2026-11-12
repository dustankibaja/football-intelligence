import { calculateScenario, validateLedger, validateAssessment, summarizeLedger, filterLedger } from "./analysis.mjs";
const byId=id=>document.getElementById(id);
const percent=p=>p===null?"Not yet":(p*100).toFixed(1)+"%";
const leagueNames={E0:"Premier League",SP1:"La Liga",I1:"Serie A",D1:"Bundesliga"};
const dateFormatter=new Intl.DateTimeFormat("en-GB",{day:"numeric",month:"short",year:"numeric",timeZone:"UTC"});
const displayDate=value=>dateFormatter.format(new Date(value));
let ledger=null, assessment=null, ledgerRequest=0, assessmentRequest=0;
function element(tag,text,cls){const el=document.createElement(tag);if(text!==null)el.textContent=text;if(cls)el.className=cls;return el;}
function replace(el,nodes){el.replaceChildren(...nodes);}
function metric(label,value){const m=element("div",null,"metric");m.append(element("span",label),element("strong",value));return m;}
function drawScenario(){
  const h=byId("home-lambda").value, a=byId("away-lambda").value;
  try{
    if(h.trim()===""||a.trim()===""||!Number.isFinite(Number(h))||!Number.isFinite(Number(a))||Number(h)<0||Number(h)>8||Number(a)<0||Number(a)>8){
      throw new Error("Please enter a number from 0 to 8 for both teams.");
    }
    const s=calculateScenario(Number(h),Number(a));
    byId("scenario-error").textContent="";
    replace(byId("outcomes"),[["Home team wins",s.home],["Draw (a tie)",s.draw],["Visiting team wins",s.away]].map(([k,v])=>metric(k,percent(v))));
    replace(byId("markets"),[["3 or more goals in total",s.over],["0 to 2 goals in total",s.under],["Both teams score",s.bttsYes],["At least one team scores no goals",s.bttsNo]].map(([k,v])=>{const m=element("div",null,"market");m.append(element("small",k),element("strong",percent(v)));return m;}));
    replace(byId("scorelines"),s.scorelines.map(x=>{const m=element("div",null,"score");m.append(element("strong",x.home+" – "+x.away),element("span",percent(x.probability)));return m;}));
    byId("scenario-summary").textContent="These chances are based only on the goals you entered: "+h+" for the home team and "+a+" for the visiting team. These numbers do not include how the teams have played recently, injuries or other team news.";
  }catch(e){
    byId("scenario-error").textContent="Please enter a number from 0 to 8 for both teams, then try again.";
    for(const id of ["outcomes","markets","scorelines"])byId(id).replaceChildren();
    byId("scenario-summary").textContent="";
  }
}
byId("scenario-form").addEventListener("submit",e=>{e.preventDefault();drawScenario();});drawScenario();
async function readJson(file){
  if(!file)throw new Error("Please choose a file first.");
  if(file.size>5000000)throw new Error("This file is too large. Please choose a smaller file (under 5 MB).");
  try{return JSON.parse(await file.text());}catch{throw new Error("We couldn't open this file. Please choose a saved file from Football Intelligence.");}
}
function drawEvidence(){
  const details=byId("evidence-details");details.hidden=!ledger;
  byId("assessment-file").disabled=!ledger;
  if(!ledger)return;
  const s=summarizeLedger(ledger,assessment);
  const groupCorrect=s.conditionalAccuracy===null?0:Math.round(s.conditionalAccuracy*s.selectedScored);
  replace(byId("evidence-summary"),[metric("Matches predicted",String(s.total)),metric("Stronger picks",String(s.selected)),metric("Results added",String(s.scored)),metric("Stronger picks right",percent(s.conditionalAccuracy))]);
  const filtered=filterLedger(ledger,{league:byId("league-filter").value,search:byId("search-filter").value,confidence:byId("confidence-filter").checked});
  const results=new Map((assessment?.matched_receipts||[]).map(r=>[r.fixture_sha256,r]));
  const rows=filtered.map(p=>{
    const tr=element("tr",null), result=results.get(p.fixture_sha256);
    const vec=prefix=>["home","draw","away"].map(k=>percent(p[prefix+"_prob_"+k])).join(" / ");
    for(const val of [displayDate(p.expected_calendar_date),p.provider_home+" vs "+p.provider_away+" · "+leagueNames[p.league],vec("baseline"),vec("candidate"),result?result.score.join("–")+(result.candidate.correct?" · Second prediction was right":" · Second prediction was wrong"):"No result added yet"])tr.append(element("td",val));
    return tr;
  });
  if(!rows.length){const row=element("tr",null), cell=element("td","No matches found. Try a different team or league.");cell.colSpan=5;row.append(cell);rows.push(row);}
  replace(byId("forecast-rows"),rows);
  const allCorrect=s.accuracy===null?0:Math.round(s.accuracy*s.scored);
  const allSummary=s.accuracy===null?"No match results have been added yet.":"Second prediction: "+allCorrect+" of "+s.scored+" matches with results were right ("+percent(s.accuracy)+").";
  const strongerSummary=s.selectedScored===0?"No results for stronger picks yet.":groupCorrect+" of "+s.selectedScored+" stronger picks with results were right ("+percent(s.conditionalAccuracy)+").";
  byId("filter-count").textContent="Showing "+filtered.length+" of "+s.total+" matches. "+s.abstained+" were not stronger picks. "+allSummary+" "+strongerSummary;
  byId("ledger-fingerprint").textContent="Predictions dated "+displayDate(ledger.generated_at_utc)+". We checked that the numbers in this file fit together, but we can't confirm where the file came from. These are saved test predictions, not live predictions.";
}
function reset(){ledgerRequest++;assessmentRequest++;ledger=null;assessment=null;byId("ledger-file").value="";byId("assessment-file").value="";byId("search-filter").value="";byId("confidence-filter").checked=false;byId("league-filter").replaceChildren(new Option("All leagues",""));byId("import-status").textContent="No predictions opened yet. Your files will not be sent anywhere.";byId("import-error").textContent="";drawEvidence();}
byId("ledger-file").addEventListener("change",async e=>{
  const request=++ledgerRequest;assessmentRequest++;
  ledger=null;assessment=null;byId("assessment-file").value="";byId("import-error").textContent="";drawEvidence();
  const file=e.target.files[0];
  if(!file){reset();return;}
  try{
    const data=await readJson(file);
    if(request!==ledgerRequest)return;
    try{ledger=validateLedger(data);}catch{throw new Error("This doesn't look like one of our saved predictions files, or some information is missing. Please choose the original predictions file.");}
    byId("league-filter").replaceChildren(new Option("All leagues",""),...[...new Set(ledger.predictions.map(p=>p.league))].sort().map(k=>new Option(leagueNames[k],k)));
    byId("search-filter").value="";byId("confidence-filter").checked=false;
    byId("import-status").textContent="Predictions opened. Choose the matching results file to see how they did.";drawEvidence();
  }catch(err){if(request!==ledgerRequest)return;ledger=null;drawEvidence();byId("import-status").textContent="No predictions opened.";byId("import-error").textContent=err.message;}
});
byId("assessment-file").addEventListener("change",async e=>{
  const request=++assessmentRequest;
  assessment=null;byId("import-error").textContent="";drawEvidence();
  const file=e.target.files[0];
  if(!file){byId("import-status").textContent="Predictions opened. No results file added.";return;}
  try{
    const data=await readJson(file);
    if(request!==assessmentRequest)return;
    try{assessment=validateAssessment(data,ledger);}catch{throw new Error("We couldn't match these results to your predictions. Please choose the results file for the same matches.");}
    byId("import-status").textContent="Match results added. You can now see which predictions were right.";drawEvidence();
  }catch(err){if(request!==assessmentRequest)return;byId("import-status").textContent="Predictions opened. No results file added.";byId("import-error").textContent=err.message;}
});
for(const id of ["league-filter","search-filter","confidence-filter"])byId(id).addEventListener("input",drawEvidence);
byId("clear-ledger").addEventListener("click",reset);
