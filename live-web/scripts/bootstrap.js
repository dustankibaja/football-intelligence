// Runs only during a production deployment. Proves actual provider access
// and initializes durable, private state before the app is declared ready.
import { runRefresh, readState, viewState } from '../lib/state.js';

if (process.env.VERCEL_ENV !== 'production') {
  console.log('FOOTMO_BOOTSTRAP_SKIPPED_NONPRODUCTION');
} else {
  if (!process.env.LIVE_DATA_API_KEY || !process.env.BLOB_READ_WRITE_TOKEN) {
    throw new Error('FOOTMO_BOOTSTRAP_MISSING_PRODUCTION_CREDENTIALS');
  }
  const result = await runRefresh({ force: true });
  const { data } = await readState();
  const view = viewState(data);
  if (!data.lastRefreshAt || result.verifiedLeagues < 1) {
    throw new Error('FOOTMO_BOOTSTRAP_NO_REAL_PROVIDER_EVIDENCE');
  }
  console.log(JSON.stringify({
    event: 'FOOTMO_PRODUCTION_PROVIDER_BOOTSTRAP',
    verifiedLeagues: result.verifiedLeagues,
    leagueCounts: data.leagueCounts,
    newForecasts: result.created,
    newEvaluations: result.evaluated,
    upcomingForecasts: view.upcoming.length,
    observedEvaluations: view.performance.sampleSize,
    source: view.source,
    provisionalModel: view.model,
    refreshedAt: data.lastRefreshAt
  }));
}
