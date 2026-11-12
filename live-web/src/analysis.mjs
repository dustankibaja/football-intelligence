const LEAGUES = new Set(["E0", "SP1", "I1", "D1"]);
const MAX_GOALS = 50;
const HEX = /^[0-9a-f]{64}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const PROB_KEYS = ["home", "draw", "away"];
function requireCondition(ok, message) { if (!ok) throw new Error(message); }
function isProbability(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1; }
function probabilities(row, prefix) {
  const p = PROB_KEYS.map(key => row[prefix + "_prob_" + key]);
  requireCondition(p.every(isProbability) && Math.abs(p.reduce((a,b) => a+b, 0)-1) <= 0.0001, "Invalid probability vector");
  return p;
}
function poisson(lambda) {
  const values = [Math.exp(-lambda)];
  for (let k=1;k<=MAX_GOALS;k++) values.push(values[k-1]*lambda/k);
  return values;
}
export function calculateScenario(home, away) {
  requireCondition(typeof home === "number" && typeof away === "number" && Number.isFinite(home) && Number.isFinite(away) && home>=0 && home<=8 && away>=0 && away<=8, "Enter finite expected goals between 0 and 8.");
  const ph=poisson(home), pa=poisson(away);
  let H=0, D=0, A=0, over=0, mass=0;
  const scores=[];
  for(let i=0;i<ph.length;i++) for(let j=0;j<pa.length;j++){
    const p=ph[i]*pa[j]; mass+=p;
    if(i>j) H+=p; else if(i===j) D+=p; else A+=p;
    if(i+j>2) over+=p;
    scores.push({home:i,away:j,probability:p});
  }
  requireCondition(Math.abs(mass-1)<1e-10, "Probability mass not conserved.");
  const btts=(1-Math.exp(-home))*(1-Math.exp(-away));
  return {home:H/mass,draw:D/mass,away:A/mass,over:over/mass,under:1-over/mass,bttsYes:btts,bttsNo:1-btts,scorelines:scores.sort((a,b)=>b.probability-a.probability||a.home-b.home||a.away-b.away).slice(0,6),mass};
}
export function validateLedger(value) {
  requireCondition(value && value.schema_version==="independent_prospective_elo_challenger_1.0.0", "Unsupported research ledger schema.");
  requireCondition(value.status==="RESEARCH_ONLY_AWAITING_AUTHENTIC_POSTMATCH_RESULTS" && value.model?.publication_eligible===false, "Not an eligible research-only ledger.");
  requireCondition(typeof value.ledger_sha256==="string" && HEX.test(value.ledger_sha256), "Missing claimed original ledger fingerprint.");
  requireCondition(typeof value.generated_at_utc==="string" && Number.isFinite(Date.parse(value.generated_at_utc)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.generated_at_utc), "Invalid issuance timestamp.");
  const issuedDay = new Date(value.generated_at_utc).toISOString().slice(0,10);
  requireCondition(Array.isArray(value.predictions) && value.predictions.length>0 && value.predictions.length<=10000, "Missing or oversized forecast cohort.");
  const n=value.predictions.length;
  requireCondition(value.counts?.challenger_issued===n && value.counts?.baseline_future_qualified===n,"Cohort counts do not match.");
  const seen=new Set();
  for(const row of value.predictions) {
    requireCondition(row && HEX.test(row.fixture_sha256) && !seen.has(row.fixture_sha256),"Invalid or duplicate fixture identity.");
    seen.add(row.fixture_sha256);
    requireCondition(LEAGUES.has(row.league) && typeof row.provider_home==="string" && row.provider_home.length>0 && row.provider_home.length<=150 && typeof row.provider_away==="string" && row.provider_away.length>0 && row.provider_away.length<=150 && row.provider_home!==row.provider_away,"Invalid fixture teams or league.");
    requireCondition(DATE.test(row.expected_calendar_date) && row.expected_calendar_date>issuedDay && Number.isFinite(Date.parse(row.expected_calendar_date)) && new Date(row.expected_calendar_date).toISOString().slice(0,10)===row.expected_calendar_date,"Invalid or non-future match date.");
    requireCondition(row.status==="PAIRED_PREMATCH_SHADOW_UNSCORED","Invalid original prediction status.");
    probabilities(row,"baseline"); probabilities(row,"candidate");
  }
  return value;
}
export function validateAssessment(value, ledger) {
  requireCondition(value?.schema_version==="independent_paired_prospective_1x2_assessment_1.0.0","Unsupported result assessment schema.");
  requireCondition(value.original_paired_ledger_sha256===ledger.ledger_sha256 && value.total_original_paired===ledger.predictions.length,"Assessment does not refer to this ledger.");
  requireCondition(typeof value.report_sha256==="string" && HEX.test(value.report_sha256),"Assessment fingerprint missing.");
  requireCondition(Number.isInteger(value.finished_evaluated) && value.finished_evaluated>=0 && Number.isInteger(value.pending_or_unverified) && value.pending_or_unverified>=0 && value.finished_evaluated+value.pending_or_unverified===ledger.predictions.length,"Invalid result counts.");
  requireCondition(Array.isArray(value.matched_receipts) && value.matched_receipts.length===value.finished_evaluated,"Invalid matched receipts.");
  const byId=new Map(ledger.predictions.map(p=>[p.fixture_sha256,p])); const seen=new Set();
  for(const result of value.matched_receipts){
    const row=byId.get(result.fixture_sha256);
    requireCondition(row && !seen.has(result.fixture_sha256) && result.league===row.league,"Result identity mismatch or duplicate.");
    seen.add(result.fixture_sha256);
    requireCondition(Array.isArray(result.score) && result.score.length===2 && result.score.every(g=>Number.isInteger(g)&&g>=0&&g<=20),"Invalid final score.");
    requireCondition(DATE.test(result.result_date) && result.result_date>=row.expected_calendar_date,"Result predates original fixture.");
    const label=result.score[0]>result.score[1]?0:result.score[0]===result.score[1]?1:2;
    requireCondition(result.actual_1x2===label,"Result contradicts score.");
    for(const key of ["baseline","candidate"]){
      const p=probabilities(row,key);
      const top=p.indexOf(Math.max(...p));
      requireCondition(result[key]?.predicted_1x2===top && result[key]?.correct===(top===label),"Result assessment contradicts original prediction.");
    }
  }
  return value;
}
export function summarizeLedger(ledger, assessment=null) {
  const selected=ledger.predictions.filter(p=>Math.max(...probabilities(p,"candidate"))>=0.5);
  const receipts=assessment?.matched_receipts||[];
  const selectedIds=new Set(selected.map(p=>p.fixture_sha256));
  const selectedResults=receipts.filter(r=>selectedIds.has(r.fixture_sha256));
  return {total:ledger.predictions.length,selected:selected.length,abstained:ledger.predictions.length-selected.length,scored:receipts.length,selectedScored:selectedResults.length,accuracy:receipts.length?receipts.filter(r=>r.candidate.correct).length/receipts.length:null,conditionalAccuracy:selectedResults.length?selectedResults.filter(r=>r.candidate.correct).length/selectedResults.length:null};
}
export function filterLedger(ledger, {league="", search="", confidence=false}={}) {
  const term=search.trim().toLocaleLowerCase();
  return ledger.predictions.filter(p=>(!league||p.league===league)&&(!term||(p.provider_home+" "+p.provider_away).toLocaleLowerCase().includes(term))&&(!confidence||Math.max(...probabilities(p,"candidate"))>=0.5));
}
