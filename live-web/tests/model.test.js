import test from 'node:test';
import assert from 'node:assert/strict';
import { predictFixture, evaluateForecast, summarizeEvaluations, walkForwardBacktest, MODEL_ID } from '../lib/model.js';

const now = new Date('2026-11-05T12:00:00Z');
const makeFixture = (id, date, status = 'SCHEDULED', h = null, a = null) => ({
  id, utcDate: date, status,
  competition: { code: 'PL', name: 'Premier League' },
  homeTeam: { id: 1, name: 'Verified Home' },
  awayTeam: { id: 2, name: 'Verified Away' },
  score: { fullTime: { home: h, away: a } }
});
const history = Array.from({ length: 20 }, (_, i) => ({
  ...makeFixture(i + 1, new Date(now.getTime() - (i + 1) * 86400000).toISOString(),
    'FINISHED', i % 4, (i + 1) % 3),
  homeTeam: { id: i % 2 ? 3 : 1, name: 'Home' },
  awayTeam: { id: i % 2 ? 4 : 2, name: 'Away' }
}));

test('probabilities from real-format match data sum to one', () => {
  const p = predictFixture(makeFixture(300, '2026-11-08T14:00:00Z'), history, now);
  assert.ok(p);
  assert.equal(p.model, MODEL_ID);
  assert.equal(p.certified, false);
  assert.equal(p.trainingMatches, 20);
  assert.ok(Math.abs(p.p1x2.reduce((a, b) => a + b, 0) - 1) < 1e-5);
  assert.ok(p.pBtts >= 0 && p.pBtts <= 1);
  assert.ok(p.pOver25 >= 0 && p.pOver25 <= 1);
  assert.equal(p.provenanceSha256.length, 64);
});

test('refuses invented, past and insufficient-history forecasts', () => {
  const upcoming = makeFixture(300, '2026-11-08T14:00:00Z');
  assert.equal(predictFixture(upcoming, [], now), null);
  assert.equal(predictFixture(makeFixture(300, '2026-11-04T14:00:00Z'), history, now), null);
  assert.equal(predictFixture(makeFixture(300, '2026-11-08T14:00:00Z', 'FINISHED'), history, now), null);
});

test('final score scoring is finite and source-linked', () => {
  const p = predictFixture(makeFixture(300, '2026-11-08T14:00:00Z'), history, now);
  const final = makeFixture(300, '2026-11-08T14:00:00Z', 'FINISHED', 2, 1);
  const ev = evaluateForecast(p, final, new Date('2026-11-08T18:00:00Z'));
  assert.equal(ev.fixtureId, '300');
  assert.equal(ev.outcome, 'H');
  assert.ok(ev.logLoss1x2 > 0);
  assert.ok(ev.brier1x2 >= 0);
  assert.equal(ev.scoreFingerprint.length, 64);
  assert.equal(summarizeEvaluations([ev]).sampleSize, 1);
  assert.equal(summarizeEvaluations([ev]).status, 'SMALL_SAMPLE');
  assert.equal(summarizeEvaluations([]).meanLogLoss1x2, null);
});

test('future scores never enter a pre-match forecast history', () => {
  const future = { ...history[0], id: 999,
    utcDate: '2026-11-06T20:00:00Z' };
  const p = predictFixture(makeFixture(300, '2026-11-08T14:00:00Z'),
    [...history, future], now);
  assert.equal(p.trainingMatches, 20);
});

test('walk-forward benchmark never uses the target score in training', () => {
  const summary = walkForwardBacktest(history);
  assert.ok(summary.sampleSize > 0);
  assert.ok(summary.sampleSize <= history.length - 15);
  assert.equal(summary.status, 'SMALL_SAMPLE');
  assert.ok(Number.isFinite(summary.meanLogLoss1x2));
  assert.ok(Number.isFinite(summary.meanBrier1x2));
});
