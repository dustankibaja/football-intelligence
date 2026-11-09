import { latestForecastsByMatch } from './forecast-store.js';
// Auditable match history for the private trial. A football result and a
// correct pre-match prediction are different facts; never conflate them.
export const OUTCOMES = Object.freeze({ H: 'Home win', D: 'Draw', A: 'Away win' });

export function outcomeCode(home, away) {
  if (!Number.isInteger(home) || !Number.isInteger(away) ||
      home < 0 || away < 0) return null;
  return home > away ? 'H' : home < away ? 'A' : 'D';
}

export function normalizeFinished(match, competition, now, previous = null) {
  const home = match?.score?.fullTime?.home;
  const away = match?.score?.fullTime?.away;
  const code = outcomeCode(home, away);
  const kickoff = new Date(match?.utcDate || '');
  if (match?.status !== 'FINISHED' || !code ||
      !Number.isFinite(kickoff.getTime()) || kickoff >= now ||
      match?.id == null || !match?.homeTeam?.name || !match?.awayTeam?.name) return null;
  const changed = previous && (previous.home !== home || previous.away !== away ||
    previous.kickoffUtc !== kickoff.toISOString());
  return {
    fixtureId: String(match.id),
    competition: competition.name, competitionCode: competition.code,
    kickoffUtc: kickoff.toISOString(),
    homeTeam: match.homeTeam.name, awayTeam: match.awayTeam.name,
    home, away, outcome: code,
    firstObservedAt: previous?.firstObservedAt || now.toISOString(),
    lastObservedAt: changed ? now.toISOString()
      : previous?.lastObservedAt || now.toISOString(),
    resultVersion: previous ? previous.resultVersion + Number(Boolean(changed)) : 1,
    source: 'football-data.org/v4'
  };
}

function bestPick(probabilities) {
  if (!Array.isArray(probabilities) || probabilities.length !== 3 ||
      probabilities.some(p => typeof p !== 'number' || !Number.isFinite(p) || p < 0)) {
    return null;
  }
  const best = probabilities.indexOf(Math.max(...probabilities));
  return { code: ['H', 'D', 'A'][best], confidence: probabilities[best] };
}

function canonicalKey(id, kickoff) {
  const date = new Date(kickoff || '');
  return id == null || !Number.isFinite(date.getTime())
    ? null : `${id}:${date.toISOString()}`;
}

export function buildHistory(finishedMatches = [], predictions = [], evaluations = []) {
  // A fixture can have multiple immutable forecasts. Score only the most
  // recent valid pre-kickoff revision, and only its matching evaluation.
  const forecastByKey = latestForecastsByMatch(predictions);
  const evaluatedByForecastKey = new Map();
  for (const ev of evaluations) {
    if (!ev?.forecastKey) continue;
    const prev = evaluatedByForecastKey.get(ev.forecastKey);
    if (!prev || Number(ev.version || 0) > Number(prev.version || 0)) {
      evaluatedByForecastKey.set(ev.forecastKey, ev);
    }
  }
  return finishedMatches.map(match => {
    const key = canonicalKey(match.fixtureId, match.kickoffUtc);
    const forecast = forecastByKey.get(key);
    const pick = forecast ? bestPick(forecast.p1x2) : null;
    const evaluation = forecast ? evaluatedByForecastKey.get(forecast.forecastKey) : null;
    const verdict = !forecast || !pick ? 'NO_FORECAST'
      : !evaluation ? 'PENDING_EVALUATION'
        : pick.code === match.outcome ? 'CORRECT' : 'INCORRECT';
    return {
      ...match,
      winner: match.outcome === 'D' ? 'Draw'
        : match.outcome === 'H' ? match.homeTeam : match.awayTeam,
      forecastStatus: verdict,
      predictedOutcome: pick?.code || null,
      predictedLabel: pick ? OUTCOMES[pick.code] : null,
      predictedConfidence: pick?.confidence ?? null,
      predictionGeneratedAt: forecast?.generatedAt || null,
      logLoss1x2: evaluation?.logLoss1x2 ?? null,
      evaluationVersion: evaluation?.version ?? null
    };
  }).sort((a, b) => b.kickoffUtc.localeCompare(a.kickoffUtc));
}

export function auditSummary(history) {
  const scored = history.filter(m =>
    m.forecastStatus === 'CORRECT' || m.forecastStatus === 'INCORRECT');
  const correct = scored.filter(m => m.forecastStatus === 'CORRECT').length;
  const incorrect = scored.length - correct;
  const green = scored.filter(m => m.predictedOutcome !== 'D' &&
    typeof m.predictedConfidence === 'number' && m.predictedConfidence > 0.60);
  const greenCorrect = green.filter(m => m.forecastStatus === 'CORRECT').length;
  const misses = scored.filter(m => m.forecastStatus === 'INCORRECT');
  const round = x => Math.round(x * 1000000) / 1000000;
  return {
    finishedMatches: history.length, evaluated: scored.length, correct, incorrect,
    pending: history.filter(m => m.forecastStatus === 'PENDING_EVALUATION').length,
    notPredicted: history.filter(m => m.forecastStatus === 'NO_FORECAST').length,
    accuracy: scored.length ? round(correct / scored.length) : null,
    greenEvaluated: green.length, greenCorrect,
    greenIncorrect: green.length - greenCorrect,
    greenAccuracy: green.length ? round(greenCorrect / green.length) : null,
    drawMisses: misses.filter(m => m.outcome === 'D').length,
    nonDrawMisses: misses.filter(m => m.outcome !== 'D').length,
    expectedCorrect: scored.length ? round(scored.reduce((sum, m) =>
      sum + m.predictedConfidence, 0)) : null
  };
}

export function inRollingWindow(iso, days, now = new Date(), direction = 'past') {
  const date = new Date(iso).getTime();
  const current = now.getTime();
  if (!Number.isFinite(date) || !Number.isFinite(current)) return false;
  if (days === 'ALL') return direction === 'past' ? date <= current : date > current;
  if (![1, 2, 7, 14, 30].includes(days)) return false;
  const span = days * 86400000;
  return direction === 'past'
    ? date <= current && date >= current - span
    : date > current && date <= current + span;
}
