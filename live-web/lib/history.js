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
  const eligible = predictions.filter(p => p &&
    new Date(p.generatedAt).getTime() < new Date(p.kickoffUtc).getTime());
  // Provider timestamps may use Z while stored ISO timestamps use .000Z.
  // Compare canonical instants, not their original string formatting.
  const forecastByKey = new Map(eligible.map(p =>
    [canonicalKey(p.fixtureId, p.kickoffUtc), p]));
  const originalForecastByKey = new Map(eligible.map(p => [p.forecastKey, p]));
  const evaluatedByKey = new Map();
  for (const ev of evaluations) {
    const linked = originalForecastByKey.get(ev.forecastKey);
    const key = canonicalKey(ev.fixtureId ?? linked?.fixtureId,
      ev.kickoffUtc ?? linked?.kickoffUtc);
    if (!key) continue;
    const prev = evaluatedByKey.get(key);
    if (!prev || ev.version > prev.version) evaluatedByKey.set(key, ev);
  }
  return finishedMatches.map(match => {
    const key = canonicalKey(match.fixtureId, match.kickoffUtc);
    const forecast = forecastByKey.get(key);
    const pick = forecast ? bestPick(forecast.p1x2) : null;
    const evaluation = evaluatedByKey.get(key);
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
  const correct = history.filter(m => m.forecastStatus === 'CORRECT').length;
  const incorrect = history.filter(m => m.forecastStatus === 'INCORRECT').length;
  const pending = history.filter(m => m.forecastStatus === 'PENDING_EVALUATION').length;
  const evaluated = correct + incorrect;
  return {
    finishedMatches: history.length, evaluated, correct, incorrect, pending,
    notPredicted: history.filter(m => m.forecastStatus === 'NO_FORECAST').length,
    accuracy: evaluated ? Math.round(correct / evaluated * 1000000) / 1000000 : null
  };
}

export function inRollingWindow(iso, days, now = new Date(), direction = 'past') {
  const date = new Date(iso).getTime();
  const current = now.getTime();
  if (!Number.isFinite(date) || !Number.isFinite(current)) return false;
  if (days === 'ALL') return direction === 'past' ? date <= current : date > current;
  if (![1, 7, 14, 30].includes(days)) return false;
  const span = days * 86400000;
  return direction === 'past'
    ? date <= current && date >= current - span
    : date > current && date <= current + span;
}
