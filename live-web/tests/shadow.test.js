import test from 'node:test';
import assert from 'node:assert/strict';
import {
  predictShadowFixture, shadowSelection, shadowComparison,
  evaluateShadowForecast, dixonColesFactor, SHADOW_MODEL_ID
} from '../lib/shadow.js';

const now = new Date('2026-11-09T12:00:00Z');
const teams = [1, 2, 3, 4, 5, 6].map(id => ({ id, name: `Team ${id}` }));
const finished = Array.from({ length: 90 }, (_, i) => {
  const homeIndex = i % 6;
  const awayIndex = (i + 1 + Math.floor(i / 6) % 5) % 6;
  const away = awayIndex === homeIndex ? (awayIndex + 1) % 6 : awayIndex;
  return {
    id: i + 1, status: 'FINISHED',
    utcDate: new Date(now.getTime() - (90 - i) * 86400000).toISOString(),
    homeTeam: teams[homeIndex], awayTeam: teams[away],
    score: { fullTime: { home: i % 4, away: (i + 1) % 3 } }
  };
});
const fixture = {
  id: 999, status: 'SCHEDULED',
  utcDate: '2026-11-15T12:00:00Z',
  homeTeam: teams[0], awayTeam: teams[2],
  competition: { code: 'PL', name: 'Premier League' }
};

test('shadow forecast is real pre-kickoff research-only and normalized', () => {
  const p = predictShadowFixture(fixture, finished, now);
  assert.ok(p);
  assert.equal(p.model, SHADOW_MODEL_ID);
  assert.equal(p.researchOnly, true);
  assert.equal(p.certified, false);
  assert.equal(p.trainingMatches, 90);
  assert.equal(p.p1x2.length, 3);
  assert.ok(Math.abs(p.p1x2.reduce((a, b) => a + b, 0) - 1) < 0.000003);
  assert.equal(p.provenanceSha256.length, 64);
  assert.ok(['ABSTAIN', 'WATCH_ONLY'].includes(p.selection.status));
  assert.equal(predictShadowFixture({ ...fixture,
    status: 'FINISHED' }, finished, now), null);
  assert.equal(predictShadowFixture({ ...fixture,
    utcDate: '2026-11-09T11:59:59Z' }, finished, now), null);
});

test('future result injection does not change point-in-time shadow forecast', () => {
  const before = predictShadowFixture(fixture, finished, now);
  const poisoned = [...finished, {
    ...finished[0], id: 7777,
    utcDate: '2026-11-09T13:00:00Z',
    score: { fullTime: { home: 99, away: 0 } }
  }];
  const after = predictShadowFixture(fixture, poisoned, now);
  assert.deepEqual(after.p1x2, before.p1x2);
  assert.equal(after.provenanceSha256, before.provenanceSha256);
});

test('Dixon-Coles correction changes only four low-score cells', () => {
  assert.ok(dixonColesFactor(0, 0, 1.4, 1.1, -0.08) > 1);
  assert.ok(dixonColesFactor(0, 1, 1.4, 1.1, -0.08) < 1);
  assert.equal(dixonColesFactor(3, 2, 1.4, 1.1, -0.08), 1);
});

test('selective shadow policy abstains on low confidence and missing history', () => {
  const p = { trainingMatches: 90, homeVenueMatches: 8,
    awayVenueMatches: 8, p1x2: [.62, .20, .18] };
  assert.equal(shadowSelection(p).status, 'WATCH_ONLY');
  assert.equal(shadowSelection({ ...p, p1x2: [.60, .20, .20] }).status,
    'ABSTAIN');
  assert.equal(shadowSelection({ ...p, p1x2: [.61, .30, .09] }).status,
    'ABSTAIN');
  assert.equal(shadowSelection({ ...p, homeVenueMatches: 2 }).status,
    'ABSTAIN');
});

test('shadow evaluation never generates a false exact-score probability', () => {
  const p = predictShadowFixture(fixture, finished, now);
  p.forecastKey = 'shadow:999:2026-11-15T12:00:00Z';
  const completed = { ...fixture, status: 'FINISHED',
    score: { fullTime: { home: 1, away: 1 } } };
  const score = evaluateShadowForecast(p, completed,
    new Date('2026-11-15T20:00:00Z'));
  assert.ok(score);
  assert.equal(score.actualScoreProbability, null);
  assert.equal(score.researchOnly, true);
  assert.equal(evaluateShadowForecast({ ...p,
    generatedAt: '2026-11-15T13:00:00Z' }, completed), null);
});

test('paired shadow metrics require the same fixture and immutable forecast', () => {
  const p = predictShadowFixture(fixture, finished, now);
  const q = { ...p, model: 'primary', forecastKey: 'primary:999' };
  const shadow = { ...p, forecastKey: 'shadow:999' };
  const base = { fixtureId: '999', kickoffUtc: p.kickoffUtc,
    observedAt: '2026-11-16T12:00:00Z', version: 1 };
  const comparison = shadowComparison([q], [{
    ...base, forecastKey: q.forecastKey, logLoss1x2: 0.9, brier1x2: .55
  }], [shadow], [{
    ...base, forecastKey: shadow.forecastKey,
    logLoss1x2: 0.8, brier1x2: .50
  }]);
  assert.equal(comparison.pairedEvaluated, 1);
  assert.ok(Math.abs(comparison.pairedLogLossDelta + 0.1) < 1e-6);
  assert.equal(comparison.promotionEligible, false);
  assert.equal(shadowComparison([q], [], [shadow], []).pairedEvaluated, 0);
});
