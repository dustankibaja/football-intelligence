// Pure, auditable selection rules for the Footmo landing page.
// "Green" means a strictly greater than 60% estimated HOME or AWAY win
// probability, not a profitable or guaranteed betting opportunity.
export const WIN_THRESHOLD = 0.60;
export const FRESHNESS_MS = 36 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
const WINDOWS = new Set([1, 7, 14]);

export function isFresh(lastRefreshAt, now = Date.now()) {
  const last = Date.parse(lastRefreshAt || '');
  const age = now - last;
  return Number.isFinite(last) && Number.isFinite(now) &&
    age >= 0 && age <= FRESHNESS_MS;
}

export function validForecast(p) {
  if (!p || !Array.isArray(p.p1x2) || p.p1x2.length !== 3) return false;
  const [home, draw, away] = p.p1x2;
  if (![home, draw, away].every(x =>
    typeof x === 'number' && Number.isFinite(x) && x >= 0 && x <= 1)) return false;
  if (Math.abs(home + draw + away - 1) > 0.005) return false;
  if (!p.homeTeam || !p.awayTeam ||
      typeof p.homeTeam !== 'string' || typeof p.awayTeam !== 'string') return false;
  const kickoff = Date.parse(p.kickoffUtc || '');
  const generated = Date.parse(p.generatedAt || '');
  return Number.isFinite(kickoff) && Number.isFinite(generated) &&
    generated < kickoff;
}

export function teamWinCandidate(p) {
  if (!validForecast(p)) return null;
  const home = p.p1x2[0], away = p.p1x2[2];
  const isHome = home >= away;
  return {
    team: isHome ? p.homeTeam : p.awayTeam,
    side: isHome ? 'HOME' : 'AWAY',
    probability: isHome ? home : away,
    // Draws are never recommended as a team-to-win selection.
    aboveThreshold: (isHome ? home : away) > WIN_THRESHOLD
  };
}

export function landingMatches(upcoming, { days = 7, view = 'green',
  fresh = false, now = Date.now() } = {}) {
  if (!Array.isArray(upcoming)) return [];
  const period = WINDOWS.has(Number(days)) ? Number(days) : 7;
  const end = now + period * DAY_MS;
  // A rescheduled fixture may have multiple immutable pre-kickoff forecasts.
  // Use only the latest generated version, never two competing picks.
  const latestByFixture = new Map();
  for (const p of upcoming) {
    if (!validForecast(p) || Date.parse(p.generatedAt) > now) continue;
    const key = p.fixtureId == null
      ? `${p.competitionCode}:${p.homeTeam}:${p.awayTeam}:${p.kickoffUtc}`
      : String(p.fixtureId);
    const prior = latestByFixture.get(key);
    if (!prior || Date.parse(p.generatedAt) > Date.parse(prior.generatedAt)) {
      latestByFixture.set(key, p);
    }
  }
  const rows = [];
  for (const p of latestByFixture.values()) {
    const kickoff = Date.parse(p.kickoffUtc);
    if (kickoff <= now || kickoff > end) continue;
    const candidate = teamWinCandidate(p);
    if (!candidate) continue;
    const green = fresh && candidate.aboveThreshold;
    if (view === 'green' && !green) continue;
    rows.push({ forecast: p, candidate, green });
  }
  rows.sort((a, b) => {
    if (view === 'green') {
      const delta = b.candidate.probability - a.candidate.probability;
      if (delta) return delta;
    }
    return Date.parse(a.forecast.kickoffUtc) - Date.parse(b.forecast.kickoffUtc) ||
      String(a.forecast.fixtureId).localeCompare(String(b.forecast.fixtureId));
  });
  return rows;
}
