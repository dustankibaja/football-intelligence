import { createHash } from 'node:crypto';

export const COMPETITIONS = [
  { code: 'PL', name: 'Premier League' },
  { code: 'PD', name: 'La Liga' },
  { code: 'SA', name: 'Serie A' },
  { code: 'BL1', name: 'Bundesliga' }
];
export const MODEL_ID = 'footmo-empirical-poisson-trial-v1';
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const finite = (x) => typeof x === 'number' && Number.isFinite(x);
const rounded = (x) => Math.round(x * 1000000) / 1000000;
const mean = (arr) => arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;

export function validFinished(match) {
  return match?.status === 'FINISHED'
    && finite(match?.score?.fullTime?.home)
    && finite(match?.score?.fullTime?.away)
    && !!match?.homeTeam?.id && !!match?.awayTeam?.id;
}

function poisson(k, lambda) {
  let p = Math.exp(-lambda);
  for (let i = 1; i <= k; i++) p *= lambda / i;
  return p;
}

function teamVenueStats(finished, teamId, venue) {
  const rows = finished.filter(m => venue === 'home'
    ? m.homeTeam.id === teamId : m.awayTeam.id === teamId);
  return {
    played: rows.length,
    for: rows.map(m => venue === 'home' ? m.score.fullTime.home : m.score.fullTime.away),
    against: rows.map(m => venue === 'home' ? m.score.fullTime.away : m.score.fullTime.home)
  };
}

function empiricalRate(goals, baseline, pseudoGames = 5) {
  // A gamma-Poisson-style shrinkage estimator. Small sample estimates
  // revert toward the actual current-season competition average.
  return (goals.reduce((a, b) => a + b, 0) + pseudoGames * baseline) /
    (goals.length + pseudoGames);
}

export function predictFixture(fixture, finished, now = new Date()) {
  const kickoff = new Date(fixture?.utcDate || '');
  if (!Number.isFinite(kickoff.getTime()) || kickoff <= now ||
      !['SCHEDULED', 'TIMED'].includes(fixture?.status)) return null;
  const previous = finished.filter(m => validFinished(m) &&
    new Date(m.utcDate) < now && new Date(m.utcDate) < kickoff);
  // Never publish a scientifically unsupported forecast from no match data.
  if (previous.length < 15) return null;
  const homeAvg = Math.max(0.35, mean(previous.map(m => m.score.fullTime.home)));
  const awayAvg = Math.max(0.35, mean(previous.map(m => m.score.fullTime.away)));
  const home = teamVenueStats(previous, fixture.homeTeam.id, 'home');
  const away = teamVenueStats(previous, fixture.awayTeam.id, 'away');
  if (home.played < 2 || away.played < 2) return null;

  const homeAttack = empiricalRate(home.for, homeAvg) / homeAvg;
  const awayDefence = empiricalRate(away.against, homeAvg) / homeAvg;
  const awayAttack = empiricalRate(away.for, awayAvg) / awayAvg;
  const homeDefence = empiricalRate(home.against, awayAvg) / awayAvg;
  const lambdaHome = clamp(homeAvg * homeAttack * awayDefence, 0.25, 4.0);
  const lambdaAway = clamp(awayAvg * awayAttack * homeDefence, 0.25, 4.0);
  const matrix = [];
  let total = 0;
  for (let h = 0; h <= 10; h++) {
    for (let a = 0; a <= 10; a++) {
      const p = poisson(h, lambdaHome) * poisson(a, lambdaAway);
      matrix.push({ h, a, p });
      total += p;
    }
  }
  for (const cell of matrix) cell.p /= total;
  const p1x2 = [
    matrix.filter(x => x.h > x.a).reduce((s, x) => s + x.p, 0),
    matrix.filter(x => x.h === x.a).reduce((s, x) => s + x.p, 0),
    matrix.filter(x => x.h < x.a).reduce((s, x) => s + x.p, 0)
  ];
  const btts = matrix.filter(x => x.h > 0 && x.a > 0)
    .reduce((s, x) => s + x.p, 0);
  const over25 = matrix.filter(x => x.h + x.a >= 3)
    .reduce((s, x) => s + x.p, 0);
  matrix.sort((a, b) => b.p - a.p);
  const topScores = matrix.slice(0, 3).map(x => ({
    score: `${x.h}-${x.a}`, probability: rounded(x.p)
  }));
  const observedHistory = previous.map(x => ({
    id: String(x.id), utcDate: x.utcDate,
    home: x.score.fullTime.home, away: x.score.fullTime.away
  })).sort((a, b) => a.id.localeCompare(b.id));
  const fingerprint = createHash('sha256')
    .update(JSON.stringify({ model: MODEL_ID, fixture: fixture.id,
      cutoff: now.toISOString(), observedHistory })).digest('hex');
  return {
    fixtureId: String(fixture.id),
    competition: fixture.competition?.name || '',
    competitionCode: fixture.competition?.code || '',
    homeTeam: fixture.homeTeam.name, awayTeam: fixture.awayTeam.name,
    homeTeamId: fixture.homeTeam.id, awayTeamId: fixture.awayTeam.id,
    kickoffUtc: kickoff.toISOString(), generatedAt: now.toISOString(),
    model: MODEL_ID, certified: false, provenanceSha256: fingerprint,
    trainingMatches: previous.length, homeVenueMatches: home.played,
    awayVenueMatches: away.played,
    lambdaHome: rounded(lambdaHome), lambdaAway: rounded(lambdaAway),
    p1x2: p1x2.map(rounded), pBtts: rounded(btts),
    pOver25: rounded(over25), topScores
  };
}

const binaryLoss = (p, actual) =>
  -Math.log(clamp(actual ? p : 1 - p, 1e-12, 1));

export function evaluateForecast(prediction, finished, observedAt = new Date()) {
  if (!validFinished(finished) || String(finished.id) !== prediction.fixtureId) return null;
  if (new Date(prediction.generatedAt) >= new Date(prediction.kickoffUtc)) return null;
  const h = finished.score.fullTime.home, a = finished.score.fullTime.away;
  const outcome = h > a ? 0 : h === a ? 1 : 2;
  const p = prediction.p1x2;
  if (!Array.isArray(p) || p.length !== 3 || p.some(x => !finite(x))) return null;
  const actualScore = `${h}-${a}`;
  const scoreProb = poisson(h, prediction.lambdaHome) *
    poisson(a, prediction.lambdaAway);
  return {
    fixtureId: prediction.fixtureId,
    observedAt: observedAt.toISOString(),
    home: h, away: a, outcome: ['H', 'D', 'A'][outcome],
    source: 'football-data.org/v4',
    scoreFingerprint: createHash('sha256')
      .update(JSON.stringify({ fixtureId: prediction.fixtureId, h, a }))
      .digest('hex'),
    logLoss1x2: rounded(-Math.log(clamp(p[outcome], 1e-12, 1))),
    brier1x2: rounded(p.reduce((sum, prob, idx) =>
      sum + (prob - (idx === outcome ? 1 : 0)) ** 2, 0)),
    bttsLogLoss: rounded(binaryLoss(prediction.pBtts, h > 0 && a > 0)),
    ou25LogLoss: rounded(binaryLoss(prediction.pOver25, h + a >= 3)),
    top1ScoreHit: prediction.topScores[0]?.score === actualScore,
    top3ScoreHit: prediction.topScores.some(x => x.score === actualScore),
    actualScoreProbability: rounded(scoreProb)
  };
}

export function summarizeEvaluations(evaluations) {
  const n = evaluations.length;
  if (!n) return { sampleSize: 0, status: 'NO_EVIDENCE',
    meanLogLoss1x2: null, meanBrier1x2: null, meanBttsLogLoss: null,
    meanOu25LogLoss: null, top1ScoreHitRate: null, top3ScoreHitRate: null };
  const avg = key => rounded(mean(evaluations.map(x => Number(x[key]))));
  return {
    sampleSize: n, status: n < 50 ? 'SMALL_SAMPLE' : 'OBSERVED',
    meanLogLoss1x2: avg('logLoss1x2'),
    meanBrier1x2: avg('brier1x2'),
    meanBttsLogLoss: avg('bttsLogLoss'),
    meanOu25LogLoss: avg('ou25LogLoss'),
    top1ScoreHitRate: rounded(mean(evaluations.map(x => Number(x.top1ScoreHit)))),
    top3ScoreHitRate: rounded(mean(evaluations.map(x => Number(x.top3ScoreHit))))
  };
}

/**
 * Retrospective walk-forward benchmark, kept STRICTLY separate from forecasts
 * genuinely published before kickoff. Uses only earlier matches whose
 * scheduled kickoffs were at least four hours before the simulated cutoff.
 * This is not a substitute for independent live calibration certification.
 */
export function walkForwardBacktest(finished) {
  const ordered = finished.filter(validFinished)
    .sort((a, b) => a.utcDate.localeCompare(b.utcDate));
  const evaluations = [];
  for (let i = 15; i < ordered.length; i++) {
    const match = ordered[i];
    const kickoff = new Date(match.utcDate);
    const asOf = new Date(kickoff.getTime() - 60000);
    const eligibleHistory = ordered.slice(0, i).filter(m =>
      new Date(m.utcDate).getTime() + 4 * 3600000 < asOf.getTime());
    const scheduled = { ...match, status: 'SCHEDULED' };
    const forecast = predictFixture(scheduled, eligibleHistory, asOf);
    if (!forecast) continue;
    const ev = evaluateForecast(forecast, match,
      new Date(kickoff.getTime() + 4 * 3600000));
    if (ev) evaluations.push(ev);
  }
  return summarizeEvaluations(evaluations);
}
