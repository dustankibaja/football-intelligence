import { COMPETITIONS } from './model.js';

const BASE = 'https://api.football-data.org/v4';
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function providerGet(path, attempt = 0) {
  if (!process.env.LIVE_DATA_API_KEY) throw new Error('PROVIDER_CREDENTIAL_MISSING');
  const response = await fetch(BASE + path, {
    headers: { 'X-Auth-Token': process.env.LIVE_DATA_API_KEY,
      'Accept': 'application/json' },
    signal: AbortSignal.timeout(15000),
    cache: 'no-store'
  });
  if (response.status === 429 && attempt < 1) {
    // Respect provider quota; never spin in an unbounded retry loop.
    const delay = Math.min(12000,
      Math.max(2000, Number(response.headers.get('retry-after') || 10) * 1000));
    await sleep(delay);
    return providerGet(path, attempt + 1);
  }
  if (!response.ok) {
    const error = new Error(`FOOTBALL_DATA_HTTP_${response.status}`);
    error.status = response.status;
    throw error;
  }
  const remaining = response.headers.get('x-requests-available-minute');
  const available = remaining === null ? null : Number(remaining);
  const data = await response.json();
  if (!Array.isArray(data.matches)) throw new Error('PROVIDER_MATCH_SCHEMA_INVALID');
  return { matches: data.matches, available: Number.isFinite(available) ? available : null };
}

export async function fetchRealFixtures(now = new Date()) {
  const end = new Date(now.getTime() + 30 * 86400000);
  const leagues = [], errors = [];
  for (const competition of COMPETITIONS) {
    try {
      // One current-season request per league obtains both pre-match fixtures
      // and final results. This conserves the free 10-requests/minute quota.
      const response = await providerGet(
        `/competitions/${competition.code}/matches`);
      const all = response.matches;
      leagues.push({
        code: competition.code, name: competition.name,
        finished: all.filter(m => m.status === 'FINISHED'),
        upcoming: all.filter(m =>
          ['SCHEDULED', 'TIMED'].includes(m.status) &&
          new Date(m.utcDate) > now && new Date(m.utcDate) <= end)
      });
      if (response.available !== null && response.available < 1) break;
      await sleep(500);
    } catch (err) {
      errors.push({ competition: competition.code, code: err.message });
      if ([401, 429].includes(err.status)) break;
    }
  }
  return { leagues, errors };
}
