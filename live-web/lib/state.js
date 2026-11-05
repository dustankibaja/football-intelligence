import { get, put, BlobPreconditionFailedError } from '@vercel/blob';
import { predictFixture, evaluateForecast, summarizeEvaluations,
  MODEL_ID } from './model.js';
import { fetchRealFixtures } from './provider.js';

const PATH = 'footmo/private-trial/state-v1.json';
const HOURS = 60 * 60 * 1000;
const emptyState = () => ({
  schema: 1, source: 'football-data.org/v4', model: MODEL_ID,
  lastRefreshAt: null, predictions: [], evaluations: [],
  providerErrors: [], leagueCounts: []
});

export async function readState() {
  const result = await get(PATH, { access: 'private' });
  if (!result || result.statusCode !== 200 || !result.stream) {
    return { data: emptyState(), etag: null };
  }
  const text = await new Response(result.stream).text();
  const data = JSON.parse(text);
  if (data.schema !== 1 || !Array.isArray(data.predictions) ||
      !Array.isArray(data.evaluations)) throw new Error('STATE_SCHEMA_INVALID');
  return { data, etag: result.blob.etag };
}

function applyProviderData(previous, feed, now) {
  const data = structuredClone(previous);
  const forecastKeys = new Set(data.predictions.map(p => p.forecastKey));
  let created = 0, evaluated = 0;
  for (const league of feed.leagues) {
    for (const fixture of league.upcoming) {
      const forecastKey = `${fixture.id}:${fixture.utcDate}`;
      if (forecastKeys.has(forecastKey)) continue;
      const prediction = predictFixture(fixture, league.finished, now);
      if (!prediction) continue;
      prediction.forecastKey = forecastKey;
      prediction.competition = league.name;
      prediction.competitionCode = league.code;
      data.predictions.push(prediction);
      forecastKeys.add(forecastKey);
      created++;
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
  data.lastRefreshAt = now.toISOString();
  data.providerErrors = feed.errors;
  data.leagueCounts = feed.leagues.map(x => ({
    competition: x.name, finished: x.finished.length,
    scheduled: x.upcoming.length
  }));
  return { data, created, evaluated };
}

export function viewState(data, now = new Date()) {
  const latest = new Map();
  for (const evaluation of data.evaluations) {
    const prior = latest.get(evaluation.forecastKey);
    if (!prior || prior.version < evaluation.version) {
      latest.set(evaluation.forecastKey, evaluation);
    }
  }
  const evaluations = [...latest.values()].sort((a, b) =>
    b.observedAt.localeCompare(a.observedAt));
  const upcoming = data.predictions.filter(p =>
    new Date(p.kickoffUtc) > now &&
    new Date(p.generatedAt) < new Date(p.kickoffUtc))
    .sort((a, b) => a.kickoffUtc.localeCompare(b.kickoffUtc));
  return {
    source: data.source, model: data.model,
    scientificCertification: 'NOT_CERTIFIED_PROVISIONAL_POISSON',
    lastRefreshAt: data.lastRefreshAt,
    automated: process.env.AUTOMATION_ENABLED !== 'false',
    providerErrors: data.providerErrors,
    leagueCounts: data.leagueCounts,
    upcoming: upcoming.slice(0, 100),
    results: evaluations.slice(0, 100),
    performance: summarizeEvaluations(evaluations)
  };
}

export async function runRefresh({ force = false } = {}) {
  if (process.env.AUTOMATION_ENABLED === 'false') {
    return { skipped: true, reason: 'AUTOMATION_DISABLED' };
  }
  const before = await readState();
  if (!force && before.data.lastRefreshAt &&
      Date.now() - new Date(before.data.lastRefreshAt).getTime() < 20 * HOURS) {
    return { skipped: true, reason: 'RECENT_REFRESH', view: viewState(before.data) };
  }
  const now = new Date();
  const feed = await fetchRealFixtures(now);
  if (!feed.leagues.length) {
    const codes = feed.errors.map(e => e.code).join(',');
    throw new Error('NO_VERIFIED_PROVIDER_DATA:' + codes);
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    const current = attempt ? await readState() : before;
    const { data, created, evaluated } = applyProviderData(current.data, feed, now);
    try {
      await put(PATH, JSON.stringify(data), {
        access: 'private', contentType: 'application/json',
        addRandomSuffix: false, allowOverwrite: !!current.etag,
        cacheControlMaxAge: 60,
        ...(current.etag ? { ifMatch: current.etag } : {})
      });
      return {
        skipped: false, created, evaluated,
        verifiedLeagues: feed.leagues.length,
        providerErrors: feed.errors, view: viewState(data)
      };
    } catch (err) {
      if (!(err instanceof BlobPreconditionFailedError) || attempt === 2) throw err;
    }
  }
  throw new Error('STATE_WRITE_CONFLICT');
}
