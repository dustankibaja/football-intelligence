import test from 'node:test';
import assert from 'node:assert/strict';
import {
  WIN_THRESHOLD, FRESHNESS_MS, isFresh, validForecast,
  teamWinCandidate, landingMatches
} from '../picks-core.js';

const NOW = Date.parse('2026-11-09T12:00:00Z');
const fixture = (id, [h, d, a], hours = 12) => ({
  fixtureId: String(id),
  homeTeam: 'Home FC', awayTeam: 'Away FC',
  competition: 'Premier League', competitionCode: 'PL',
  generatedAt: new Date(NOW - 3600000).toISOString(),
  kickoffUtc: new Date(NOW + hours * 3600000).toISOString(),
  p1x2: [h, d, a]
});

test('strict threshold: >60% only, never equal to 60%', () => {
  assert.equal(WIN_THRESHOLD, 0.60);
  const at60 = fixture(1, [.6, .25, .15]);
  const above60 = fixture(2, [.600001, .25, .149999]);
  assert.equal(teamWinCandidate(at60).aboveThreshold, false);
  assert.equal(teamWinCandidate(above60).aboveThreshold, true);
  assert.deepEqual(landingMatches([at60, above60], {
    days: 1, view: 'green', fresh: true, now: NOW
  }).map(x => x.forecast.fixtureId), ['2']);
});

test('away win can be green, but draw cannot be a team-to-win pick', () => {
  const away = fixture(3, [.12, .2, .68]);
  const draw = fixture(4, [.14, .7, .16]);
  assert.deepEqual(teamWinCandidate(away), {
    team: 'Away FC', side: 'AWAY', probability: .68, aboveThreshold: true
  });
  assert.equal(teamWinCandidate(draw).aboveThreshold, false);
  assert.equal(landingMatches([away, draw], {
    days: 7, view: 'green', fresh: true, now: NOW
  }).length, 1);
});

test('time windows include only genuine future fixtures inside the window', () => {
  const rows = [
    fixture(1, [.72, .2, .08], -1),
    fixture(2, [.73, .2, .07], 12),
    fixture(3, [.74, .2, .06], 72),
    fixture(4, [.75, .2, .05], 10 * 24),
    fixture(5, [.76, .2, .04], 15 * 24)
  ];
  const ids = days => landingMatches(rows, {
    days, view: 'all', fresh: true, now: NOW
  }).map(x => x.forecast.fixtureId);
  assert.deepEqual(ids(1), ['2']);
  assert.deepEqual(ids(7), ['2', '3']);
  assert.deepEqual(ids(14), ['2', '3', '4']);
});

test('green shortlist sorts by estimated win probability descending', () => {
  const rows = [
    fixture(1, [.65, .25, .1], 1),
    fixture(2, [.77, .15, .08], 5),
    fixture(3, [.69, .2, .11], 3)
  ];
  assert.deepEqual(landingMatches(rows, {
    days: 7, view: 'green', fresh: true, now: NOW
  }).map(x => x.forecast.fixtureId), ['2', '3', '1']);
});

test('no fabricated or malformed predictions can enter the watchlist', () => {
  const okay = fixture(1, [.7, .2, .1]);
  const invalid = [
    { ...okay, fixtureId: '2', p1x2: [.7, NaN, .1] },
    { ...okay, fixtureId: '3', p1x2: [.8, .4, .3] },
    { ...okay, fixtureId: '4', p1x2: [1.1, -.2, .1] },
    { ...okay, fixtureId: '5', generatedAt: okay.kickoffUtc },
    { ...okay, fixtureId: '6', homeTeam: '' },
    { ...okay, fixtureId: '7', p1x2: null },
    { ...okay, fixtureId: '8', kickoffUtc: 'not-a-date' }
  ];
  for (const item of invalid) assert.equal(validForecast(item), false);
  assert.deepEqual(landingMatches([...invalid, okay], {
    days: 7, view: 'green', fresh: true, now: NOW
  }).map(x => x.forecast.fixtureId), ['1']);
  assert.deepEqual(landingMatches(undefined, {
    days: 7, view: 'green', fresh: true, now: NOW
  }), []);
});

test('stale, future-dated and missing provider updates disable green signals', () => {
  const row = fixture(1, [.8, .1, .1]);
  assert.equal(isFresh(new Date(NOW - FRESHNESS_MS).toISOString(), NOW), true);
  assert.equal(isFresh(new Date(NOW - FRESHNESS_MS - 1).toISOString(), NOW), false);
  assert.equal(isFresh(new Date(NOW + 1000).toISOString(), NOW), false);
  assert.equal(isFresh('', NOW), false);
  assert.equal(landingMatches([row], {
    days: 7, view: 'green', fresh: false, now: NOW
  }).length, 0);
  const neutral = landingMatches([row], {
    days: 7, view: 'all', fresh: false, now: NOW
  });
  assert.equal(neutral.length, 1);
  assert.equal(neutral[0].green, false);
});

test('the only green UI class is assigned by the strict selector', () => {
  const rows = [
    fixture(1, [.6, .3, .1]),
    fixture(2, [.59, .2, .21]),
    fixture(3, [.61, .2, .19])
  ];
  const results = landingMatches(rows, {
    days: 7, view: 'all', fresh: true, now: NOW
  });
  assert.deepEqual(results.map(x => x.green), [false, false, true]);
});

test('rescheduled fixtures appear once using the latest pre-kickoff forecast', () => {
  const earlier = fixture(42, [.85, .1, .05], 12);
  const later = {
    ...fixture(42, [.67, .2, .13], 72),
    generatedAt: new Date(NOW - 10 * 60000).toISOString()
  };
  const selected = landingMatches([earlier, later], {
    days: 7, view: 'green', fresh: true, now: NOW
  });
  assert.equal(selected.length, 1);
  assert.equal(selected[0].forecast.kickoffUtc, later.kickoffUtc);
  assert.equal(selected[0].candidate.probability, .67);
});

test('predictions generated in the future cannot be recommended now', () => {
  const forecast = fixture(9, [.8, .1, .1], 12);
  forecast.generatedAt = new Date(NOW + 1000).toISOString();
  assert.equal(landingMatches([forecast], {
    days: 7, view: 'green', fresh: true, now: NOW
  }).length, 0);
});
