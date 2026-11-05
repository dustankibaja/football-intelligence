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
  const available = Number(response.headers.get('x-requests-available-minute'));
  const data = await response.json();
  if (!Array.isArray(data.matches)) throw new Error('PROVIDER_MATCH_SCHEMA_INVALID');
  return { matches: data.matches, available: Number.isFinite(available) ? available : null };
}

export async function fetchRealFixtures(now = new Date()) {
  const start = now.toISOString().slice(0, 10);
  const end = new Date(now.getTime() + 14 * 86400000).toISOString().slice(0, 10);
  const leagues = [], errors = [];
  for (const competition of COMPETITIONS) {
    try {
      const past = await providerGet(
        `/competitions/${competition.code}/matches?status=FINISHED`);
      await sleep(350);
      const schedule = await providerGet(
        `/competitions/${competition.code}/matches?dateFrom=${start}&dateTo=${end}`);
      leagues.push({
        code: competition.code, name: competition.name,
        finished: past.matches,
        upcoming: schedule.matches.filter(m =>
          ['SCHEDULED', 'TIMED'].includes(m.status) &&
          new Date(m.utcDate) > now)
      });
      if (schedule.available !== null && schedule.available < 1) break;
      await sleep(350);
    } catch (err) {
      errors.push({ competition: competition.code, code: err.message });
      if ([401, 403, 429].includes(err.status)) {
        // A bad credential or exhausted quota cannot be repaired by
        // fabricating fixtures. Preserve existing predictions instead.
        if (err.status === 401 || err.status === 429) break;
      }
    }
  }
  return { leagues, errors };
}
