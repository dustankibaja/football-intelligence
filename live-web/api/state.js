import { readState, runRefresh, viewState } from '../lib/state.js';

export default async function handler(request, response) {
  response.setHeader('Cache-Control', 'private, no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  if (request.method !== 'GET') return response.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
  try {
    let { data } = await readState();
    let refreshError = null;
    if (!data.lastRefreshAt ||
        Date.now() - new Date(data.lastRefreshAt).getTime() > 22 * 3600000) {
      try {
        const refreshed = await runRefresh();
        if (!refreshed.skipped) return response.status(200).json({
          ...refreshed.view, refresh: { created: refreshed.created,
            evaluated: refreshed.evaluated }
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
