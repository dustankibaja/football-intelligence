import { readState, runRefresh, viewState } from '../lib/state.js';

export const config = { maxDuration: 60 };

export default async function handler(request, response) {
  response.setHeader('Cache-Control', 'private, no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  if (request.method !== 'GET') return response.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
  try {
    let { data } = await readState();
    let refreshError = null;
    const ageMs = data.lastRefreshAt
      ? Date.now() - new Date(data.lastRefreshAt).getTime() : Infinity;
    const manual = new URL(request.url, 'https://footmo.local')
      .searchParams.get('force') === '1';
    if (ageMs > 4 * 3600000 || (manual && ageMs > 30 * 60000)) {
      try {
        const refreshed = await runRefresh({ force: manual && ageMs > 30 * 60000 });
        if (!refreshed.skipped) return response.status(200).json({
          ...refreshed.view, refresh: { created: refreshed.created,
            revised: refreshed.revised, evaluated: refreshed.evaluated }
        });
      } catch (err) {
        refreshError = String(err.message).slice(0, 180);
      }
      ({ data } = await readState());
    }
    return response.status(refreshError && !data.lastRefreshAt ? 503 : 200).json({
      ...viewState(data), ...(refreshError ? { refreshError } : {})
    });
  } catch (err) {
    return response.status(503).json({
      error: 'PRIVATE_DATA_UNAVAILABLE', reason: String(err.message).slice(0, 160)
    });
  }
}
