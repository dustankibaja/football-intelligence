// Immutable forecast revisions: a prediction already shown to a user is never
// overwritten. For each fixture, only the latest valid pre-kickoff revision is
// used in the current dashboard and primary performance summary.
export function canonicalFixtureKey(item) {
  const kickoff = Date.parse(item?.kickoffUtc || '');
  if (item?.fixtureId == null || !Number.isFinite(kickoff)) return null;
  return `${String(item.fixtureId)}:${new Date(kickoff).toISOString()}`;
}

export function validPublishedForecast(p) {
  const generated = Date.parse(p?.generatedAt || '');
  const kickoff = Date.parse(p?.kickoffUtc || '');
  return canonicalFixtureKey(p) !== null && Number.isFinite(generated) &&
    generated < kickoff && typeof p?.forecastKey === 'string' &&
    p.forecastKey.length > 0;
}

function newer(p, prior) {
  return !prior || Date.parse(p.generatedAt) > Date.parse(prior.generatedAt) ||
    (Date.parse(p.generatedAt) === Date.parse(prior.generatedAt) &&
      (p.forecastVersion || 1) > (prior.forecastVersion || 1));
}

export function latestForecastsByMatch(predictions = []) {
  const latest = new Map();
  for (const p of predictions) {
    if (!validPublishedForecast(p)) continue;
    const key = canonicalFixtureKey(p);
    if (newer(p, latest.get(key))) latest.set(key, p);
  }
  return latest;
}

export function latestUpcomingForecasts(predictions = [], now = new Date()) {
  const current = now.getTime();
  const latest = new Map();
  for (const p of predictions) {
    if (!validPublishedForecast(p) || Date.parse(p.generatedAt) > current ||
        Date.parse(p.kickoffUtc) <= current) continue;
    // Rescheduled fixtures must never show both the old and new kickoff.
    const key = String(p.fixtureId);
    if (newer(p, latest.get(key))) latest.set(key, p);
  }
  return [...latest.values()].sort((a, b) =>
    a.kickoffUtc.localeCompare(b.kickoffUtc));
}

export function needsForecastRevision(prior, candidate, { scoreCorrected = false } = {}) {
  if (!candidate || !validPublishedForecast({
    ...candidate, forecastKey: candidate.forecastKey || 'candidate'
  })) return false;
  if (!prior) return true;
  if (candidate.model !== prior.model || scoreCorrected) return true;
  return candidate.trainingMatches > prior.trainingMatches ||
    candidate.homeVenueMatches > prior.homeVenueMatches ||
    candidate.awayVenueMatches > prior.awayVenueMatches;
}

export function selectLatestEvaluations(predictions = [], evaluations = []) {
  const byKey = new Map();
  for (const ev of evaluations) {
    if (!ev?.forecastKey) continue;
    const prior = byKey.get(ev.forecastKey);
    if (!prior || Number(ev.version || 0) > Number(prior.version || 0) ||
        (Number(ev.version || 0) === Number(prior.version || 0) &&
          String(ev.observedAt || '') > String(prior.observedAt || ''))) {
      byKey.set(ev.forecastKey, ev);
    }
  }
  return [...latestForecastsByMatch(predictions).values()]
    .map(p => byKey.get(p.forecastKey))
    .filter(Boolean)
    .sort((a, b) => String(b.observedAt || '').localeCompare(String(a.observedAt || '')));
}
