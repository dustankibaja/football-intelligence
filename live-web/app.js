const $ = id => document.getElementById(id);
let data = null, activeLeague = 'ALL';
const pct = p => Number.isFinite(Number(p)) ? `${(Number(p) * 100).toFixed(1)}%` : '—';
const metric = x => x === null || x === undefined ? '—' : Number(x).toFixed(3);
const time = iso => iso ? new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium', timeStyle: 'short'
}).format(new Date(iso)) : 'No data yet';

function node(tag, className, value) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (value !== undefined) el.textContent = value;
  return el;
}
function empty(message) { return node('div', 'empty', message); }
function renderMetrics(performance) {
  const values = [
    ['Evaluated fixtures', String(performance.sampleSize ?? 0)],
    ['1X2 log loss ↓', metric(performance.meanLogLoss1x2)],
    ['1X2 Brier ↓', metric(performance.meanBrier1x2)],
    ['BTTS log loss ↓', metric(performance.meanBttsLogLoss)],
    ['Over/Under log loss ↓', metric(performance.meanOu25LogLoss)],
    ['Exact-score hit rate', pct(performance.top1ScoreHitRate)]
  ];
  $('metrics').replaceChildren(...values.map(([label, value]) => {
    const box = node('div', 'metric');
    box.append(node('span', '', label), node('strong', '', value));
    return box;
  }));
  const b = data?.backtest;
  $('backtest-summary').textContent = b?.sampleSize
    ? `RETROSPECTIVE WALK-FORWARD BENCHMARK (not live certification): ${b.sampleSize} earlier fixtures · 1X2 log loss ${metric(b.meanLogLoss1x2)} · Brier ${metric(b.meanBrier1x2)}. Each test match was predicted using only earlier results.`
    : 'Historical walk-forward benchmark not yet available.';
  $('sample-label').textContent = performance.sampleSize === 0
    ? 'No observed evaluations yet'
    : performance.sampleSize < 50
      ? `Small sample · ${performance.sampleSize} matches`
      : `Observed · ${performance.sampleSize} matches`;
}
function fixtureCard(p) {
  const card = node('article', 'fixture');
  const head = node('div', 'fixture-top');
  head.append(node('span', 'fixture-league', p.competition),
    node('span', '', time(p.kickoffUtc)));
  const teams = node('div', 'teams');
  teams.append(node('span', '', p.homeTeam), node('span', '', 'VS'),
    node('span', '', p.awayTeam));
  const odds = node('div', 'probabilities');
  const best = Math.max(...p.p1x2);
  [['HOME', p.p1x2[0]], ['DRAW', p.p1x2[1]], ['AWAY', p.p1x2[2]]]
    .forEach(([label, value]) => {
      const box = node('div', 'prob' + (value === best ? ' high' : ''));
      box.append(node('strong', '', pct(value)), node('small', '', label));
      odds.append(box);
    });
  const bottom = node('div', 'fixture-bottom');
  const items = [
    ['BTTS', pct(p.pBtts)],
    ['Over 2.5', pct(p.pOver25)],
    ['Top score', p.topScores[0]?.score || '—'],
    ['History', `${p.trainingMatches} matches`]
  ];
  for (const [label, value] of items) {
    const item = node('span', '', label + ' ');
    item.append(node('b', '', value)); bottom.append(item);
  }
  card.append(head, teams, odds, bottom);
  return card;
}
function renderFixtures() {
  if (!data) return;
  const filtered = data.upcoming.filter(p =>
    activeLeague === 'ALL' || p.competitionCode === activeLeague);
  $('fixture-count').textContent = `${filtered.length} verified-source forecasts`;
  $('fixtures').replaceChildren(...(filtered.length
    ? filtered.map(fixtureCard)
    : [empty('No eligible pre-match forecasts for this filter yet. The system never invents upcoming fixtures or probabilities. Check back after the next provider update.')]));
}
function resultCard(ev) {
  const row = node('article', 'result');
  const label = node('div', 'result-title', `${ev.homeTeam} vs ${ev.awayTeam}`);
  label.append(node('small', '', `${ev.competition} · ${time(ev.kickoffUtc)}`));
  const score = node('div', 'score', `${ev.home} – ${ev.away}`);
  const stats = node('div', 'result-stats');
  const line = node('span', '', '1X2 log loss ');
  line.append(node('b', '', metric(ev.logLoss1x2)));
  stats.append(line, node('div', '', `Brier ${metric(ev.brier1x2)} · Score hit: ${ev.top1ScoreHit ? 'Yes' : 'No'} · Version ${ev.version}`));
  row.append(label, score, stats);
  return row;
}
function renderResults() {
  if (!data) return;
  $('result-count').textContent = `${data.results.length} verified outcomes`;
  $('results').replaceChildren(...(data.results.length
    ? data.results.map(resultCard)
    : [empty('No completed predicted fixtures have been verified yet. As real matches finish, final scores and scientific scoring will appear here automatically.')]));
}
function render() {
  $('last-update').textContent = data.lastRefreshAt
    ? `Provider last checked: ${time(data.lastRefreshAt)}`
    : 'Waiting for the first verified provider update';
  $('status').className = 'status' + (data.refreshError ? ' error' : '');
  $('status').textContent = data.refreshError ? 'Provider update delayed'
    : data.automated ? 'Automatic daily updates enabled' : 'Automation paused';
  renderMetrics(data.performance);
  renderFixtures();
  renderResults();
  const errors = data.providerErrors || [];
  $('provider-errors').hidden = !errors.length && !data.refreshError;
  $('provider-errors').textContent = [data.refreshError,
    ...errors.map(x => `${x.competition}: ${x.code}`)].filter(Boolean).join(' · ');
}
async function refresh(manual = false) {
  const button = $('refresh');
  button.disabled = true; button.textContent = '↻ Checking provider…';
  try {
    const response = await fetch(manual ? '/api/state?force=1' : '/api/state', { cache: 'no-store' });
    const json = await response.json();
    if (!response.ok && !json.lastRefreshAt) {
      throw new Error(json.refreshError || json.reason || json.error || 'Live data unavailable');
    }
    data = json;
    render();
  } catch (err) {
    $('status').textContent = 'Connection error';
    $('status').className = 'status error';
    $('last-update').textContent = String(err.message);
    if (!data) {
      $('fixtures').replaceChildren(empty('Real fixture data is temporarily unavailable. No synthetic predictions will be shown.'));
      $('results').replaceChildren(empty('Awaiting the verified data service.'));
    }
  } finally {
    button.disabled = false; button.textContent = '↻ Refresh data';
  }
}
document.querySelectorAll('[data-league]').forEach(button => {
  button.addEventListener('click', () => {
    activeLeague = button.dataset.league;
    document.querySelectorAll('[data-league]').forEach(x =>
      x.classList.toggle('active', x === button));
    renderFixtures();
  });
});
$('refresh').addEventListener('click', () => refresh(true));
refresh();
setInterval(() => {
  if (document.visibilityState === 'visible') refresh();
}, 5 * 60 * 1000);
