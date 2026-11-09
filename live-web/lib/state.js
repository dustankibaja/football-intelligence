import { get, put, list } from '@vercel/blob';
import { predictFixture, evaluateForecast, summarizeEvaluations,
  MODEL_ID, walkForwardBacktest } from './model.js';
import { fetchRealFixtures } from './provider.js';
import { normalizeFinished, buildHistory, auditSummary } from './history.js';
import { canonicalFixtureKey, latestForecastsByMatch,
  latestUpcomingForecasts, needsForecastRevision,
  selectLatestEvaluations } from './forecast-store.js';

const PREFIX = 'footmo/private-trial/snapshots/';
const HOURS = 60 * 60 * 1000;
const emptyState = () => ({
  schema: 1, source: 'football-data.org/v4', model: MODEL_ID,
  lastRefreshAt: null, predictions: [], evaluations: [],
  providerErrors: [], leagueCounts: [], backtest: null,
  finishedMatches: [], resultCorrections: []
});

export async function readState() {
  // Immutable, timestamped private snapshots avoid Blob ETag/CDN races.
  // At one scheduled refresh per day, 1000 snapshots covers years of trials.
  const objects = await list({ prefix: PREFIX, limit: 1000 });
  const latest = (objects.blobs || []).sort((a, b) =>
    new Date(b.uploadedAt).getTime() - new Date(a.uploadedAt).getTime())[0];
  if (!latest) return { data: emptyState() };
  const result = await get(latest.pathname, { access: 'private' });
  if (!result || result.statusCode !== 200 || !result.stream) {
    throw new Error('LATEST_PRIVATE_SNAPSHOT_UNAVAILABLE');
  }
  const text = await new Response(result.stream).text();
  const data = JSON.parse(text);
  if (data.schema !== 1 || !Array.isArray(data.predictions) ||
      !Array.isArray(data.evaluations)) throw new Error('STATE_SCHEMA_INVALID');
  return { data };
}

function applyProviderData(previous, feed, now) {
  const data = structuredClone(previous);
  const latestForecastByMatch = latestForecastsByMatch(data.predictions);
  const correctedLeagues = new Set();
  const completed = new Map((data.finishedMatches || []).map(m => [m.fixtureId, m]));
  const corrections = data.resultCorrections || [];
  let created = 0, revised = 0, evaluated = 0;
  for (const league of feed.leagues) {
    // Source match history includes games played before this trial began.
    // Those matches must never be presented as successful/failed forecasts.
    for (const finished of league.finished) {
      const id = String(finished.id);
      const prior = completed.get(id);
      const record = normalizeFinished(finished, league, now, prior);
      if (!record) continue;
      if (prior && record.resultVersion > prior.resultVersion) {
        correctedLeagues.add(league.code);
        corrections.push({ fixtureId: id, competitionCode: league.code,
          from: `${prior.home}-${prior.away}`,
          to: `${record.home}-${record.away}`,
          observedAt: now.toISOString(), version: record.resultVersion });
      }
      completed.set(id, record);
    }
    for (const fixture of league.upcoming) {
      const key = canonicalFixtureKey({
        fixtureId: fixture.id, kickoffUtc: fixture.utcDate
      });
      if (!key) continue;
      const prior = latestForecastByMatch.get(key);
      // Recompute when new results have become available. Previous forecasts
      // remain immutable, and revisions are published before kickoff only.
      const prediction = predictFixture(fixture, league.finished, now);
      if (!needsForecastRevision(prior, prediction, {
        scoreCorrected: correctedLeagues.has(league.code)
      })) continue;
      const version = prior ? (prior.forecastVersion || 1) + 1 : 1;
      prediction.forecastKey = prior
        ? `${fixture.id}:${fixture.utcDate}:revision-${version}:${now.toISOString()}`
        : `${fixture.id}:${fixture.utcDate}`;
      prediction.forecastVersion = version;
      prediction.revisionReason = prior ? 'NEW_VERIFIED_RESULTS_OR_CORRECTION' : 'INITIAL';
      prediction.competition = league.name;
      prediction.competitionCode = league.code;
      data.predictions.push(prediction);
      latestForecastByMatch.set(key, prediction);
      if (prior) revised++;
      else created++;
    }
    const finals = new Map(league.finished.map(m => [String(m.id), m]));
    for (const prediction of data.predictions) {
      if (prediction.competitionCode !== league.code) continue;
      const final = finals.get(prediction.fixtureId);
      if (!final) continue;
      // Rescheduled matches need a fresh pre-kickoff forecast; never score
      // a forecast against a match that kicked off at a different time.
      if (new Date(final.utcDate).toISOString() !== prediction.kickoffUtc) continue;
      const score = evaluateForecast(prediction, final, now);
      if (!score) continue;
      const versions = data.evaluations.filter(e =>
        e.forecastKey === prediction.forecastKey);
      const latest = versions.at(-1);
      if (latest?.scoreFingerprint === score.scoreFingerprint) continue;
      data.evaluations.push({
        ...score, forecastKey: prediction.forecastKey,
        homeTeam: prediction.homeTeam, awayTeam: prediction.awayTeam,
        competition: prediction.competition,
        kickoffUtc: prediction.kickoffUtc,
        version: (latest?.version || 0) + 1
      });
      evaluated++;
    }
  }
  data.finishedMatches = [...completed.values()];
  data.resultCorrections = corrections;
  data.lastRefreshAt = now.toISOString();
  data.providerErrors = feed.errors;
  data.leagueCounts = feed.leagues.map(x => ({
    competition: x.name, finished: x.finished.length,
    scheduled: x.upcoming.length
  }));
  const byLeague = feed.leagues.map(x => ({
    competition: x.name, ...walkForwardBacktest(x.finished)
  }));
  const total = byLeague.reduce((sum, x) => sum + x.sampleSize, 0);
  const weighted = key => total ? Math.round(
    byLeague.reduce((sum, x) => sum + x.sampleSize * (x[key] || 0), 0) /
    total * 1000000) / 1000000 : null;
  data.backtest = {
    sampleSize: total,
    status: total < 50 ? 'SMALL_SAMPLE' : 'RETROSPECTIVE_ONLY',
    meanLogLoss1x2: weighted('meanLogLoss1x2'),
    meanBrier1x2: weighted('meanBrier1x2'),
    meanBttsLogLoss: weighted('meanBttsLogLoss'),
    meanOu25LogLoss: weighted('meanOu25LogLoss'),
    top1ScoreHitRate: weighted('top1ScoreHitRate'),
    perLeague: byLeague
  };
  return { data, created, revised, evaluated };
}

export function viewState(data, now = new Date()) {
  // Only one primary score per completed fixture: the latest published
  // pre-kickoff forecast. All earlier revisions remain in private snapshots.
  const evaluations = selectLatestEvaluations(data.predictions, data.evaluations);
  const history = buildHistory(data.finishedMatches || [], data.predictions, evaluations);
  const upcoming = latestUpcomingForecasts(data.predictions, now);
  return {
    source: data.source, model: data.model,
    scientificCertification: 'NOT_CERTIFIED_PROVISIONAL_POISSON',
    lastRefreshAt: data.lastRefreshAt,
    automated: process.env.AUTOMATION_ENABLED !== 'false',
    providerErrors: data.providerErrors,
    leagueCounts: data.leagueCounts,
    upcoming: upcoming.slice(0, 500),
    results: evaluations.slice(0, 100),
    history: history.slice(0, 2000),
    historyAudit: auditSummary(history),
    resultCorrections: (data.resultCorrections || []).length,
    performance: summarizeEvaluations(evaluations),
    backtest: data.backtest || null
  };
}

export async function runRefresh({ force = false } = {}) {
  if (process.env.AUTOMATION_ENABLED === 'false') {
    return { skipped: true, reason: 'AUTOMATION_DISABLED' };
  }
  const before = await readState();
  if (!force && before.data.lastRefreshAt &&
      Date.now() - new Date(before.data.lastRefreshAt).getTime() < 4 * HOURS) {
    return { skipped: true, reason: 'RECENT_REFRESH', view: viewState(before.data) };
  }
  const now = new Date();
  const feed = await fetchRealFixtures(now);
  if (!feed.leagues.length) {
    const codes = feed.errors.map(e => e.code).join(',');
    throw new Error('NO_VERIFIED_PROVIDER_DATA:' + codes);
  }
  const current = await readState();
  const { data, created, revised, evaluated } = applyProviderData(current.data, feed, now);
  const unique = `${now.toISOString().replace(/[:.]/g, '-')}-${Math.random().toString(36).slice(2, 10)}`;
  await put(`${PREFIX}${unique}.json`, JSON.stringify(data), {
    access: 'private', contentType: 'application/json',
    addRandomSuffix: true, cacheControlMaxAge: 60
  });
  return {
    skipped: false, created, revised, evaluated,
    verifiedLeagues: feed.leagues.length,
    providerErrors: feed.errors, view: viewState(data)
  };
}
