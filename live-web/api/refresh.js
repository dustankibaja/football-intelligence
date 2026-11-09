import { runRefresh } from '../lib/state.js';

export const config = { maxDuration: 60 };

export default async function handler(request, response) {
  response.setHeader('Cache-Control', 'no-store');
  if (request.method !== 'GET') return response.status(405).json({ error: 'METHOD_NOT_ALLOWED' });
  const expected = process.env.CRON_SECRET;
  if (!expected || request.headers.authorization !== `Bearer ${expected}`) {
    return response.status(401).json({ error: 'UNAUTHORIZED' });
  }
  try {
    const result = await runRefresh();
    return response.status(200).json({
      ok: true, skipped: result.skipped,
      reason: result.reason || null, created: result.created || 0,
      revised: result.revised || 0, evaluated: result.evaluated || 0,
      shadowCreated: result.shadowCreated || 0,
      shadowRevised: result.shadowRevised || 0,
      shadowEvaluated: result.shadowEvaluated || 0,
      verifiedLeagues: result.verifiedLeagues || 0,
      providerErrors: result.providerErrors || []
    });
  } catch (err) {
    return response.status(503).json({
      ok: false, error: String(err.message).slice(0, 180)
    });
  }
}
