import test from 'node:test';
import assert from 'node:assert/strict';
import {
  canonicalFixtureKey, validPublishedForecast, latestForecastsByMatch,
  latestUpcomingForecasts, needsForecastRevision, selectLatestEvaluations
} from '../lib/forecast-store.js';
import { buildHistory, auditSummary } from '../lib/history.js';

const before = '2026-11-05T12:00:00Z';
const newer = '2026-11-07T12:00:00Z';
const kickoff = '2026-11-08T16:00:00Z';
const forecast = (generatedAt, key, p1x2 = [.65, .2, .15], version = 1) => ({
  fixtureId: '501', forecastKey: key, kickoffUtc: kickoff, generatedAt,
  forecastVersion: version, trainingMatches: version === 1 ? 90 : 95,
  homeVenueMatches: 4, awayVenueMatches: 4, model: 'v1',
  homeTeam: 'Home', awayTeam: 'Away', p1x2
});
const original = forecast(before, '501:2026-11-08T16:00:00Z');
const revision = forecast(newer, '501:2026-11-08T16:00:00Z:revision-2', [.45,.3,.25], 2);
const evaluation = (f, version, outcome = 'H') => ({
  fixtureId: '501', forecastKey: f.forecastKey, version,
  observedAt: '2026-11-09T00:00:00Z', outcome, logLoss1x2: 1.2
});

test('canonical fixture keys normalize provider timestamp formatting', () => {
  assert.equal(canonicalFixtureKey(original),
    '501:2026-11-08T16:00:00.000Z');
  assert.equal(canonicalFixtureKey({ ...original,
    kickoffUtc: '2026-11-08T16:00:00.000Z' }),
    canonicalFixtureKey(original));
  assert.equal(canonicalFixtureKey({ ...original, kickoffUtc: 'invalid' }), null);
});

test('revisions are selected by publication time, not array order', () => {
  const selected = latestForecastsByMatch([revision, original]);
  assert.equal(selected.size, 1);
  assert.equal([...selected.values()][0].forecastKey, revision.forecastKey);
  assert.equal(latestUpcomingForecasts([revision, original],
    new Date('2026-11-07T18:00:00Z'))[0].forecastKey, revision.forecastKey);
});

test('only new evidence or model change triggers an immutable revision', () => {
  assert.equal(needsForecastRevision(null, revision), true);
  assert.equal(needsForecastRevision(original, { ...revision,
    trainingMatches: 90 }), false);
  assert.equal(needsForecastRevision(original, revision), true);
  assert.equal(needsForecastRevision(original, { ...revision,
    trainingMatches: 90 }, { scoreCorrected: true }), true);
  assert.equal(needsForecastRevision(original, { ...revision,
    trainingMatches: 90, model: 'v2' }), true);
  assert.equal(needsForecastRevision(original, null), false);
});

test('evaluation scoring selects only latest pre-kickoff revision and score correction', () => {
  const old = evaluation(original, 1);
  const current = evaluation(revision, 1);
  const corrected = evaluation(revision, 2, 'D');
  const selected = selectLatestEvaluations([original, revision],
    [old, current, corrected]);
  assert.equal(selected.length, 1);
  assert.equal(selected[0].forecastKey, revision.forecastKey);
  assert.equal(selected[0].version, 2);
  assert.equal(selected[0].outcome, 'D');
  assert.equal(selectLatestEvaluations([original, revision], [old]).length, 0);
});

test('history never matches old evaluation to new revision', () => {
  const finished = [{
    fixtureId: '501', kickoffUtc: kickoff,
    homeTeam: 'Home', awayTeam: 'Away',
    home: 0, away: 0, outcome: 'D', resultVersion: 1
  }];
  const pending = buildHistory(finished, [original, revision],
    [evaluation(original, 1)]);
  assert.equal(pending[0].forecastStatus, 'PENDING_EVALUATION');
  assert.equal(pending[0].predictedConfidence, .45);
  const resolved = buildHistory(finished, [original, revision],
    [evaluation(original, 1), evaluation(revision, 1)]);
  assert.equal(resolved[0].forecastStatus, 'INCORRECT');
  const audit = auditSummary(resolved);
  assert.equal(audit.evaluated, 1);
  assert.equal(audit.greenEvaluated, 0);
  assert.equal(audit.drawMisses, 1);
});

test('future or post-kickoff forecasts are excluded from evaluation', () => {
  const post = forecast('2026-11-09T00:00:00Z', 'post');
  assert.equal(validPublishedForecast(post), false);
  assert.equal(latestForecastsByMatch([original, post]).size, 1);
  assert.equal(selectLatestEvaluations([original, post],
    [evaluation(original, 1), evaluation(post, 1)]).length, 1);
});

test('rescheduled fixtures display only latest scheduled kickoff', () => {
  const rescheduled = {
    ...revision, kickoffUtc: '2026-11-10T16:00:00Z',
    forecastKey: '501:2026-11-10T16:00:00Z'
  };
  const rows = latestUpcomingForecasts([original, rescheduled],
    new Date('2026-11-07T18:00:00Z'));
  assert.equal(rows.length, 1);
  assert.equal(rows[0].kickoffUtc, rescheduled.kickoffUtc);
});
