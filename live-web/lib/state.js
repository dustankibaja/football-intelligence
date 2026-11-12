import { get, put, list } from '@vercel/blob';
import { predictFixture, evaluateForecast, summarizeEvaluations,
  MODEL_ID, walkForwardBacktest } from './model.js';
import { fetchRealFixtures } from './provider.js';
import { predictShadowFixture, evaluateShadowForecast,
  shadowComparison } from './shadow.js';
import { predictAccuracyPair, evaluateAccuracyPair,
  prospectiveAccuracyComparison, historicalAccuracyComparison } from './accuracy-research.js';
import frozenMultiseason from '../research/artifacts/multiseason-strength-v1.json' with { type: 'json' };
import { makeLivePairs, evaluateLivePair, summarizeLivePairs } from '../research/live-multiseason.js';
import { normalizeFinished, buildHistory, auditSummary, inRollingWindow } from './history.js';
import { canonicalFixtureKey, latestForecastsByMatch,
  latestUpcomingForecasts, needsForecastRevision,
  selectLatestEvaluations } from './forecast-store.js';

const PREFIX = 'footmo/private-trial/snapshots/';
const HOURS = 60 * 60 * 1000;
const emptyState = () => ({
  schema: 1, source: 'football-data.org/v4', model: MODEL_ID,
  lastRefreshAt: null, predictions: [], evaluations: [],
  shadowPredictions: [], shadowEvaluations: [],
  accuracyPredictions: [], accuracyEvaluations: [], accuracyBacktest: null,
  multiseasonPredictions: [], multiseasonEvaluations: [], multiseasonResearchErrors: [],
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

export function applyProviderData(previous, feed, now) {
  const data = structuredClone(previous);
  // Existing v1 snapshots predate shadow mode. Never rewrite old snapshots.
  data.shadowPredictions ||= [];
  data.shadowEvaluations ||= [];
  data.accuracyPredictions ||= [];
  data.accuracyEvaluations ||= [];
  data.multiseasonPredictions ||= [];
  data.multiseasonEvaluations ||= [];
  data.multiseasonResearchErrors = [];
  const latestForecastByMatch = latestForecastsByMatch(data.predictions);
  const shadowLatestByMatch = latestForecastsByMatch(data.shadowPredictions);
  const accuracyLatestByMatch = latestForecastsByMatch(data.accuracyPredictions);
  const multiseasonLatestByMatch = latestForecastsByMatch(data.multiseasonPredictions);
  const correctedLeagues = new Set();
  const completed = new Map((data.finishedMatches || []).map(m => [m.fixtureId, m]));
  const corrections = data.resultCorrections || [];
  let created = 0, revised = 0, evaluated = 0;
  let shadowCreated = 0, shadowRevised = 0, shadowEvaluated = 0;
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
    // Independent multi-season candidate. Any research failure is isolated:
    // the original model, existing forecasts and live match updates continue.
    if (process.env.MULTISEASON_RESEARCH_ENABLED !== 'false') {
      try {
        const batch = makeLivePairs(league, frozenMultiseason, now);
        for (const pair of batch.pairs) {
          const key = canonicalFixtureKey(pair);
          const prior = multiseasonLatestByMatch.get(key);
          if (!needsForecastRevision(prior, pair, {
            scoreCorrected: correctedLeagues.has(league.code)
          })) continue;
          const version = prior ? (prior.forecastVersion || 1) + 1 : 1;
          pair.forecastKey = `multiseason:${pair.fixtureId}:${pair.kickoffUtc}:revision-${version}:${now.toISOString()}`;
          pair.forecastVersion = version;
          data.multiseasonPredictions.push(pair);
          multiseasonLatestByMatch.set(key, pair);
        }
        if(batch.reason) data.multiseasonResearchErrors.push({competition:league.code,reason:batch.reason});
      } catch (error) {
        data.multiseasonResearchErrors.push({competition:league.code,reason:String(error.message).slice(0,120)});
      }
    }
    for (const fixture of league.upcoming) {
      const key = canonicalFixtureKey({
        fixtureId: fixture.id, kickoffUtc: fixture.utcDate
      });
      if (!key) continue;
      // Production model and challenger are independently versioned.
      const prior = latestForecastByMatch.get(key);
      const prediction = predictFixture(fixture, league.finished, now);
      if (needsForecastRevision(prior, prediction, {
        scoreCorrected: correctedLeagues.has(league.code)
      })) {
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
      // One independent improvement lane, paired at the same cutoff with
      // an original-engine estimate. Old forecasts and shadow forecasts stay intact.
      if (process.env.ACCURACY_RESEARCH_ENABLED !== 'false') {
        const accuracyPrior = accuracyLatestByMatch.get(key);
        const pair = predictAccuracyPair(fixture, league.finished, now);
        if (needsForecastRevision(accuracyPrior, pair, {
          scoreCorrected: correctedLeagues.has(league.code)
        })) {
          const version = accuracyPrior ? (accuracyPrior.forecastVersion || 1) + 1 : 1;
          pair.forecastKey = `accuracy:${fixture.id}:${fixture.utcDate}:revision-${version}:${now.toISOString()}`;
          pair.forecastVersion = version;
          pair.competition = league.name;
          pair.competitionCode = league.code;
          data.accuracyPredictions.push(pair);
          accuracyLatestByMatch.set(key, pair);
        }
      }
      if (process.env.SHADOW_MODE_ENABLED === 'false') continue;
      const shadowPrior = shadowLatestByMatch.get(key);
      const candidate = predictShadowFixture(fixture, league.finished, now);
      if (!needsForecastRevision(shadowPrior, candidate, {
        scoreCorrected: correctedLeagues.has(league.code)
      })) continue;
      const version = shadowPrior ? (shadowPrior.forecastVersion || 1) + 1 : 1;
      candidate.forecastKey = shadowPrior
        ? `shadow:${fixture.id}:${fixture.utcDate}:revision-${version}:${now.toISOString()}`
        : `shadow:${fixture.id}:${fixture.utcDate}`;
      candidate.forecastVersion = version;
      candidate.competition = league.name;
      candidate.competitionCode = league.code;
      data.shadowPredictions.push(candidate);
      shadowLatestByMatch.set(key, candidate);
      if (shadowPrior) shadowRevised++;
      else shadowCreated++;
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
    for (const pair of data.multiseasonPredictions) {
      if (pair.competitionCode !== league.code) continue;
      const final = finals.get(pair.fixtureId);
      if (!final) continue;
      const score = evaluateLivePair(pair, final, now);
      if (!score) continue;
      const versions = data.multiseasonEvaluations.filter(e => e.forecastKey === pair.forecastKey);
      const latest = versions.at(-1);
      if (latest?.scoreFingerprint === score.scoreFingerprint) continue;
      data.multiseasonEvaluations.push({...score, version:(latest?.version || 0)+1});
    }
    for (const pair of data.accuracyPredictions) {
      if (pair.competitionCode !== league.code) continue;
      const final = finals.get(pair.fixtureId);
      if (!final) continue;
      const score = evaluateAccuracyPair(pair, final, now);
      if (!score) continue;
      const versions = data.accuracyEvaluations.filter(e => e.forecastKey === pair.forecastKey);
      const latest = versions.at(-1);
      if (latest?.scoreFingerprint === score.scoreFingerprint) continue;
      data.accuracyEvaluations.push({...score, version:(latest?.version || 0)+1});
    }
    // Shadow outcomes are evaluated only if an actual pre-kickoff challenger
    // forecast was persisted. Never backfill older fixtures retrospectively.
    for (const prediction of data.shadowPredictions) {
      if (prediction.competitionCode !== league.code) continue;
      const final = finals.get(prediction.fixtureId);
      if (!final || new Date(final.utcDate).toISOString() !== prediction.kickoffUtc) continue;
      const score = evaluateShadowForecast(prediction, final, now);
      if (!score) continue;
      const versions = data.shadowEvaluations.filter(e =>
        e.forecastKey === prediction.forecastKey);
      const latest = versions.at(-1);
      if (latest?.scoreFingerprint === score.scoreFingerprint) continue;
      data.shadowEvaluations.push({
        ...score, forecastKey: prediction.forecastKey,
        kickoffUtc: prediction.kickoffUtc,
        competitionCode: prediction.competitionCode,
        version: (latest?.version || 0) + 1
      });
      shadowEvaluated++;
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
  data.accuracyBacktest = historicalAccuracyComparison(feed.leagues);
  return { data, created, revised, evaluated,
    shadowCreated, shadowRevised, shadowEvaluated };
}

export function viewState(data, now = new Date()) {
  // Only one primary score per completed fixture: the latest published
  // pre-kickoff forecast. All earlier revisions remain in private snapshots.
  const evaluations = selectLatestEvaluations(data.predictions, data.evaluations);
  const history = buildHistory(data.finishedMatches || [], data.predictions, evaluations);
  const upcoming = latestUpcomingForecasts(data.predictions, now);
  const shadowUpcoming = latestUpcomingForecasts(data.shadowPredictions || [], now);
  const shadowWatch = shadowUpcoming.filter(p => p.selection?.status === 'WATCH_ONLY').length;
  const accuracyResearch = prospectiveAccuracyComparison(
    data.accuracyPredictions || [], data.accuracyEvaluations || []);
  const accuracyUpcoming = latestUpcomingForecasts(data.accuracyPredictions || [], now);
  const multiseasonResearch = summarizeLivePairs(data.multiseasonPredictions || [], data.multiseasonEvaluations || []);
  const multiseasonUpcoming = latestUpcomingForecasts(data.multiseasonPredictions || [], now);
  const shadow = shadowComparison(data.predictions, data.evaluations,
    data.shadowPredictions || [], data.shadowEvaluations || []);
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
    historyAudit48h: auditSummary(history.filter(m =>
      inRollingWindow(m.kickoffUtc, 2, now, 'past'))),
    resultCorrections: (data.resultCorrections || []).length,
    performance: summarizeEvaluations(evaluations),
    backtest: data.backtest || null,
    accuracyResearch: {...accuracyResearch, pendingUpcoming:accuracyUpcoming.length,
      historical: data.accuracyBacktest || null, publicPredictions:false},
    multiseasonResearch: {...multiseasonResearch, pendingUpcoming:multiseasonUpcoming.length,
      knownHistoricalTeamPairs:multiseasonUpcoming.filter(p=>p.historicalNameCoverage).length,
      unknownHistoricalTeams:[...new Set(multiseasonUpcoming.flatMap(p=>p.unknownHistoricalTeams||[]))].sort(),
      artifactSha256:frozenMultiseason.artifactSha256,
      errors:data.multiseasonResearchErrors || [],publicPredictions:false},
    shadow: {
      ...shadow, enabled: process.env.SHADOW_MODE_ENABLED !== 'false',
      pendingUpcoming: shadowUpcoming.length, watchOnlyUpcoming: shadowWatch,
      abstainedUpcoming: shadowUpcoming.length - shadowWatch,
      // Research-only aggregate. No shadow predictions are used in Picks.
      publicRecommendations: false
    }
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
  const { data, created, revised, evaluated,
    shadowCreated, shadowRevised, shadowEvaluated } = applyProviderData(current.data, feed, now);
  const unique = `${now.toISOString().replace(/[:.]/g, '-')}-${Math.random().toString(36).slice(2, 10)}`;
  await put(`${PREFIX}${unique}.json`, JSON.stringify(data), {
    access: 'private', contentType: 'application/json',
    addRandomSuffix: true, cacheControlMaxAge: 60
  });
  return {
    skipped: false, created, revised, evaluated,
    shadowCreated, shadowRevised, shadowEvaluated,
    verifiedLeagues: feed.leagues.length,
    providerErrors: feed.errors, view: viewState(data)
  };
}
