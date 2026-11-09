import { createHash } from 'node:crypto';
import { validFinished, evaluateForecast, summarizeEvaluations } from './model.js';
import { canonicalFixtureKey, selectLatestEvaluations } from './forecast-store.js';

// Research candidate only. This is NOT the certified C1/M1 system and does
// not supply user-facing picks. Historical tuning/selection must happen offline.
export const SHADOW_MODEL_ID = 'footmo-shadow-decay-dc-research-v1';
const DAY_MS = 86400000;
const HALF_LIFE_DAYS = 180;
const RHO = -0.08; // fixed research hypothesis, NOT tuned on live results
const PSEUDO_GAMES = 6;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const round = x => Math.round(x * 1e6) / 1e6;

function poisson(k, lambda) {
  let value = Math.exp(-lambda);
  for (let i = 1; i <= k; i++) value *= lambda / i;
  return value;
}

export function dixonColesFactor(home, away, lambdaHome, lambdaAway, rho) {
  if (home === 0 && away === 0) return 1 - lambdaHome * lambdaAway * rho;
  if (home === 0 && away === 1) return 1 + lambdaHome * rho;
  if (home === 1 && away === 0) return 1 + lambdaAway * rho;
  if (home === 1 && away === 1) return 1 - rho;
  return 1;
}

function weightedRate(rows, value, baseline, now, pseudo = PSEUDO_GAMES) {
  let numerator = baseline * pseudo, denominator = pseudo;
  for (const m of rows) {
    const days = (now.getTime() - Date.parse(m.utcDate)) / DAY_MS;
    const weight = Math.pow(0.5, days / HALF_LIFE_DAYS);
    numerator += weight * value(m);
    denominator += weight;
  }
  return numerator / denominator;
}

function weightedMean(rows, value, now) {
  let numerator = 0, denominator = 0;
  for (const m of rows) {
    const days = (now.getTime() - Date.parse(m.utcDate)) / DAY_MS;
    const weight = Math.pow(0.5, days / HALF_LIFE_DAYS);
    numerator += weight * value(m);
    denominator += weight;
  }
  return denominator ? numerator / denominator : 0;
}

export function shadowSelection(prediction) {
  if (!prediction || prediction.trainingMatches < 60 ||
      prediction.homeVenueMatches < 4 || prediction.awayVenueMatches < 4) {
    return { status: 'ABSTAIN', reason: 'INSUFFICIENT_VERIFIED_HISTORY' };
  }
  const ranked = prediction.p1x2.map((p, i) => ({ p, i }))
    .sort((a, b) => b.p - a.p);
  if (ranked[0].i === 1 || ranked[0].p <= 0.60 ||
      ranked[0].p - ranked[1].p < 0.12 || prediction.p1x2[1] > 0.28) {
    return { status: 'ABSTAIN', reason: 'UNCERTAIN_OR_DRAW_RISK' };
  }
  return { status: 'WATCH_ONLY', reason: 'UNCALIBRATED_RESEARCH_CANDIDATE' };
}

export function predictShadowFixture(fixture, finished, now = new Date()) {
  const kickoff = Date.parse(fixture?.utcDate || '');
  if (!Number.isFinite(kickoff) || kickoff <= now.getTime() ||
      !['SCHEDULED', 'TIMED'].includes(fixture?.status)) return null;
  // 4-hour result availability embargo; no post-cutoff or same-kickoff data.
  const history = finished.filter(m => validFinished(m) &&
    Number.isFinite(Date.parse(m.utcDate)) &&
    Date.parse(m.utcDate) + 4 * 3600000 < now.getTime() &&
    Date.parse(m.utcDate) < kickoff);
  if (history.length < 30) return null;
  const home = history.filter(m => m.homeTeam.id === fixture.homeTeam?.id);
  const away = history.filter(m => m.awayTeam.id === fixture.awayTeam?.id);
  if (home.length < 3 || away.length < 3) return null;
  const homeAvg = clamp(weightedMean(history,
    m => m.score.fullTime.home, now), .35, 4);
  const awayAvg = clamp(weightedMean(history,
    m => m.score.fullTime.away, now), .35, 4);
  const homeAttack = weightedRate(home,
    m => m.score.fullTime.home, homeAvg, now) / homeAvg;
  const awayDefense = weightedRate(away,
    m => m.score.fullTime.home, homeAvg, now) / homeAvg;
  const awayAttack = weightedRate(away,
    m => m.score.fullTime.away, awayAvg, now) / awayAvg;
  const homeDefense = weightedRate(home,
    m => m.score.fullTime.away, awayAvg, now) / awayAvg;
  const lambdaHome = clamp(homeAvg * homeAttack * awayDefense, .25, 4);
  const lambdaAway = clamp(awayAvg * awayAttack * homeDefense, .25, 4);
  const cells = [];
  let total = 0;
  for (let h = 0; h <= 10; h++) {
    for (let a = 0; a <= 10; a++) {
      const probability = poisson(h, lambdaHome) * poisson(a, lambdaAway) *
        Math.max(0, dixonColesFactor(h, a, lambdaHome, lambdaAway, RHO));
      cells.push({ h, a, p: probability });
      total += probability;
    }
  }
  if (!(total > 0)) return null;
  const p1x2 = [0, 0, 0];
  let btts = 0, over25 = 0;
  for (const cell of cells) {
    cell.p /= total;
    p1x2[cell.h > cell.a ? 0 : cell.h === cell.a ? 1 : 2] += cell.p;
    if (cell.h && cell.a) btts += cell.p;
    if (cell.h + cell.a >= 3) over25 += cell.p;
  }
  cells.sort((a, b) => b.p - a.p);
  const fingerprint = createHash('sha256').update(JSON.stringify({
    model: SHADOW_MODEL_ID, fixtureId: fixture.id, cutoff: now.toISOString(),
    matches: history.map(m => [String(m.id), m.utcDate,
      m.score.fullTime.home, m.score.fullTime.away]).sort((a, b) =>
      a[0].localeCompare(b[0]))
  })).digest('hex');
  const prediction = {
    fixtureId: String(fixture.id), competition: fixture.competition?.name || '',
    competitionCode: fixture.competition?.code || '',
    homeTeam: fixture.homeTeam.name, awayTeam: fixture.awayTeam.name,
    homeTeamId: fixture.homeTeam.id, awayTeamId: fixture.awayTeam.id,
    kickoffUtc: new Date(kickoff).toISOString(), generatedAt: now.toISOString(),
    model: SHADOW_MODEL_ID, certified: false, researchOnly: true,
    provenanceSha256: fingerprint, trainingMatches: history.length,
    homeVenueMatches: home.length, awayVenueMatches: away.length,
    lambdaHome: round(lambdaHome), lambdaAway: round(lambdaAway), rho: RHO,
    p1x2: p1x2.map(round), pBtts: round(btts), pOver25: round(over25),
    topScores: cells.slice(0, 3).map(c => ({
      score: `${c.h}-${c.a}`, probability: round(c.p)
    }))
  };
  prediction.selection = shadowSelection(prediction);
  return prediction;
}

export function evaluateShadowForecast(prediction, finished, now = new Date()) {
  const evaluation = evaluateForecast(prediction, finished, now);
  if (!evaluation) return null;
  // The generic primary evaluator assumes independent Poisson for exact-score
  // probability; that number is not valid for this Dixon-Coles candidate.
  return { ...evaluation, actualScoreProbability: null,
    shadowModel: SHADOW_MODEL_ID, researchOnly: true };
}

export function shadowComparison(primaryPredictions, primaryEvaluations,
  shadowPredictions, shadowEvaluations) {
  const primary = selectLatestEvaluations(primaryPredictions, primaryEvaluations);
  const challenger = selectLatestEvaluations(shadowPredictions, shadowEvaluations);
  const primaryByFixture = new Map(primary.map(x =>
    [canonicalFixtureKey(x), x]));
  const paired = challenger.map(x => ({
    shadow: x, primary: primaryByFixture.get(canonicalFixtureKey(x))
  })).filter(x => x.primary);
  const mean = (rows, key) => rows.length ?
    round(rows.reduce((sum, row) => sum + row[key], 0) / rows.length) : null;
  const deltas = paired.map(x => ({
    logLoss: x.shadow.logLoss1x2 - x.primary.logLoss1x2,
    brier: x.shadow.brier1x2 - x.primary.brier1x2
  }));
  return {
    status: paired.length < 100 ? 'INSUFFICIENT_PAIRED_EVIDENCE' : 'RESEARCH_ONLY',
    candidateModel: SHADOW_MODEL_ID, certified: false,
    shadowEvaluated: challenger.length, primaryEvaluated: primary.length,
    pairedEvaluated: paired.length,
    meanShadowLogLoss: paired.length ? mean(paired.map(x => x.shadow),
      'logLoss1x2') : null,
    meanPrimaryLogLoss: paired.length ? mean(paired.map(x => x.primary),
      'logLoss1x2') : null,
    pairedLogLossDelta: mean(deltas, 'logLoss'),
    pairedBrierDelta: mean(deltas, 'brier'),
    shadowOverall: summarizeEvaluations(challenger),
    promotionEligible: false
  };
}
