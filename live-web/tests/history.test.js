import test from 'node:test';
import assert from 'node:assert/strict';
import { outcomeCode, normalizeFinished, buildHistory,
  auditSummary, inRollingWindow } from '../lib/history.js';
import { fetchRealFixtures } from '../lib/provider.js';

const now = new Date('2026-11-05T12:00:00Z');
const league = { name: 'Premier League', code: 'PL' };
const match = (id, home, away, date = '2026-11-04T18:00:00Z') => ({
  id, status: 'FINISHED', utcDate: date,
  homeTeam: { id: 1, name: 'Home FC' },
  awayTeam: { id: 2, name: 'Away FC' },
  score: { fullTime: { home, away } }
});

test('finished results identify real home, away and draw outcomes', () => {
  assert.equal(outcomeCode(3, 1), 'H');
  assert.equal(outcomeCode(1, 1), 'D');
  assert.equal(outcomeCode(0, 2), 'A');
  assert.equal(outcomeCode(null, 2), null);
  assert.equal(normalizeFinished({ ...match(1, 2, 1), status: 'IN_PLAY' },
    league, now), null);
});

test('provider score corrections create a new version, not a fake new fixture', () => {
  const first = normalizeFinished(match(1, 2, 1), league, now);
  assert.equal(first.resultVersion, 1);
  assert.equal(first.outcome, 'H');
  const unchanged = normalizeFinished(match(1, 2, 1), league,
    new Date('2026-11-05T13:00:00Z'), first);
  assert.equal(unchanged.resultVersion, 1);
  assert.equal(unchanged.firstObservedAt, first.firstObservedAt);
  const corrected = normalizeFinished(match(1, 1, 2), league,
    new Date('2026-11-05T14:00:00Z'), first);
  assert.equal(corrected.resultVersion, 2);
  assert.equal(corrected.outcome, 'A');
});

test('history marks correct, incorrect, pending and pre-trial games separately', () => {
  const results = [
    normalizeFinished(match(1, 2, 1), league, now),
    normalizeFinished(match(2, 1, 1), league, now),
    normalizeFinished(match(3, 0, 2), league, now),
    normalizeFinished(match(4, 0, 0), league, now)
  ];
  const predictions = [1, 2, 3].map(id => ({
    forecastKey: `${id}:2026-11-04T18:00:00.000Z`,
    fixtureId: String(id), kickoffUtc: '2026-11-04T18:00:00.000Z',
    generatedAt: '2026-11-03T12:00:00Z',
    p1x2: id === 1 ? [0.6, 0.2, 0.2]
      : id === 2 ? [0.7, 0.2, 0.1] : [0.2, 0.1, 0.7]
  }));
  const evaluations = [1, 2].map(id => ({
    forecastKey: predictions[id - 1].forecastKey,
    version: 1, logLoss1x2: 0.7
  }));
  const rows = buildHistory(results, predictions, evaluations);
  const byId = Object.fromEntries(rows.map(x => [x.fixtureId, x]));
  assert.equal(byId['1'].forecastStatus, 'CORRECT');
  assert.equal(byId['2'].forecastStatus, 'INCORRECT');
  assert.equal(byId['3'].forecastStatus, 'PENDING_EVALUATION');
  assert.equal(byId['4'].forecastStatus, 'NO_FORECAST');
  assert.equal(byId['1'].winner, 'Home FC');
  assert.equal(byId['2'].winner, 'Draw');
  assert.equal(byId['3'].winner, 'Away FC');
  assert.equal(byId['1'].predictedConfidence, 0.6);
  const audit = auditSummary(rows);
  assert.deepEqual(audit, {
    finishedMatches: 4, evaluated: 2, correct: 1, incorrect: 1,
    pending: 1, notPredicted: 1, accuracy: 0.5
  });
});

test('future forecasts and old fixtures are excluded from rolling windows', () => {
  assert.equal(inRollingWindow('2026-11-06T11:59:59Z', 1, now, 'future'), true);
  assert.equal(inRollingWindow('2026-11-06T12:00:01Z', 1, now, 'future'), false);
  assert.equal(inRollingWindow('2026-12-04T12:00:00Z', 30, now, 'future'), true);
  assert.equal(inRollingWindow('2026-12-06T12:00:00Z', 30, now, 'future'), false);
  assert.equal(inRollingWindow('2026-11-04T12:00:00Z', 1, now, 'past'), true);
  assert.equal(inRollingWindow('2026-11-04T11:59:59Z', 1, now, 'past'), false);
  assert.equal(inRollingWindow('2026-09-01T12:00:00Z', 'ALL', now, 'past'), true);
});

test('provider fetch covers 30 days and preserves actual completed scores', async () => {
  const originalFetch = globalThis.fetch;
  const originalKey = process.env.LIVE_DATA_API_KEY;
  const calls = [];
  process.env.LIVE_DATA_API_KEY = 'unit-test-only';
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return new Response(JSON.stringify({ matches: [
      match(1, 2, 1),
      { ...match(2, null, null, '2026-11-20T18:00:00Z'), status: 'SCHEDULED' },
      { ...match(3, null, null, '2026-12-04T18:00:00Z'), status: 'TIMED' },
      { ...match(4, null, null, '2026-12-07T18:00:00Z'), status: 'SCHEDULED' }
    ] }), { status: 200, headers: { 'x-requests-available-minute': '8' } });
  };
  try {
    const feed = await fetchRealFixtures(now);
    assert.equal(feed.leagues.length, 4);
    assert.equal(feed.errors.length, 0);
    assert.equal(calls.length, 4);
    assert.equal(feed.leagues[0].finished.length, 1);
    assert.deepEqual(feed.leagues[0].upcoming.map(x => x.id), [2, 3]);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalKey === undefined) delete process.env.LIVE_DATA_API_KEY;
    else process.env.LIVE_DATA_API_KEY = originalKey;
  }
});
