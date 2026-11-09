const $ = id => document.getElementById(id);
let data = null, activeLeague = 'ALL', activeWindow = 14;
let fixtureVisible = 20;
let historyDays = 2, historyVerdict = 'ALL', historyLeague = 'ALL';
let historyVisible = 24;
const DAY_MS = 24 * 60 * 60 * 1000;
function withinWindow(iso, days, direction = 'future') {
  const when = new Date(iso).getTime();
  const now = Date.now();
  if (!Number.isFinite(when)) return false;
  if (days === 'ALL') return direction === 'past' ? when <= now : when > now;
  const duration = Number(days) * DAY_MS;
  return direction === 'past'
    ? when <= now && when >= now - duration
    : when > now && when <= now + duration;
}
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
  const filtered = (data.upcoming || []).filter(p =>
    (activeLeague === 'ALL' || p.competitionCode === activeLeague) &&
    withinWindow(p.kickoffUtc, activeWindow));
  $('fixture-count').textContent = `${filtered.length} verified-source forecasts`;
  $('fixtures').replaceChildren(...(filtered.length
    ? filtered.slice(0, fixtureVisible).map(fixtureCard)
    : [empty('No eligible pre-match forecasts for this filter yet. The system never invents upcoming fixtures or probabilities. Check back after the next provider update.')]));
  const more = $('fixtures-more');
  more.hidden = filtered.length <= fixtureVisible;
  if (!more.hidden) more.textContent =
    `Load more predictions (${filtered.length - fixtureVisible} remaining)`;
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
const OUTCOME_NAMES = { H: 'Home win', D: 'Draw', A: 'Away win' };
const VERDICTS = {
  CORRECT: ['Correct top outcome', 'correct'],
  INCORRECT: ['Incorrect top outcome', 'incorrect'],
  NO_FORECAST: ['Not predicted', 'unpredicted'],
  PENDING_EVALUATION: ['Awaiting evaluation', 'pending']
};
function historyCard(match) {
  const card = node('article', 'history-card');
  const top = node('div', 'history-card-top');
  top.append(node('span', 'fixture-league', match.competition),
    node('span', '', time(match.kickoffUtc)));
  const main = node('div', 'history-card-main');
  const teams = node('div', 'history-match');
  teams.append(node('span', 'history-team', match.homeTeam),
    node('strong', 'history-score', `${match.home} – ${match.away}`),
    node('span', 'history-team', match.awayTeam));
  const winner = node('div', 'history-winner',
    match.outcome === 'D' ? 'Full time · Draw' : `Full time · ${match.winner} won`);
  const verdictInfo = VERDICTS[match.forecastStatus] || VERDICTS.NO_FORECAST;
  const verdict = node('span', `verdict ${verdictInfo[1]}`, verdictInfo[0]);
  main.append(teams, winner, verdict);
  const detail = node('div', 'history-detail');
  if (match.forecastStatus === 'NO_FORECAST') {
    detail.textContent = 'No pre-kickoff Footmo forecast exists for this match.';
  } else {
    const pick = match.predictedOutcome === 'D' ? 'Draw'
      : match.predictedOutcome === 'H' ? match.homeTeam : match.awayTeam;
    const isGreen = match.predictedOutcome !== 'D' &&
      match.predictedConfidence > 0.60;
    detail.textContent = `Top outcome: ${pick} (${pct(match.predictedConfidence)}) · ` +
      (isGreen ? 'Qualifies >60% · ' : 'No green signal · ') +
      `Actual: ${match.outcome === 'D' ? 'Draw' : match.winner}` +
      (match.logLoss1x2 == null ? '' : ` · 1X2 log loss: ${metric(match.logLoss1x2)}`);
  }
  if (match.forecastVersion > 1) {
    detail.append(node('span', 'history-correction',
      ` · Pre-kickoff forecast revision v${match.forecastVersion}`));
  }
  if (match.resultVersion > 1) {
    detail.append(node('span', 'history-correction',
      ` · Provider score revision v${match.resultVersion}`));
  }
  card.append(top, main, detail);
  return card;
}
function renderHistory() {
  if (!data) return;
  const period = (data.history || []).filter(m =>
    withinWindow(m.kickoffUtc, historyDays, 'past') &&
    (historyLeague === 'ALL' || m.competitionCode === historyLeague));
  const correct = period.filter(m => m.forecastStatus === 'CORRECT').length;
  const incorrect = period.filter(m => m.forecastStatus === 'INCORRECT').length;
  const pending = period.filter(m => m.forecastStatus === 'PENDING_EVALUATION').length;
  const notPredicted = period.filter(m => m.forecastStatus === 'NO_FORECAST').length;
  const evaluated = correct + incorrect;
  const accuracy = evaluated ? pct(correct / evaluated) : '—';
  const scored = period.filter(m =>
    m.forecastStatus === 'CORRECT' || m.forecastStatus === 'INCORRECT');
  const isGreen = m => m.predictedOutcome !== 'D' &&
    typeof m.predictedConfidence === 'number' && m.predictedConfidence > 0.60;
  const green = scored.filter(isGreen);
  const greenCorrect = green.filter(m => m.forecastStatus === 'CORRECT').length;
  const drawMisses = scored.filter(m =>
    m.forecastStatus === 'INCORRECT' && m.outcome === 'D').length;
  const expectedCorrect = scored.reduce((sum, m) =>
    sum + (m.predictedConfidence || 0), 0);
  const stats = [
    ['Finished matches', period.length],
    ['All evaluated top outcomes', evaluated],
    ['Correct top outcomes', correct],
    ['Incorrect top outcomes', incorrect],
    ['All-outcome accuracy', accuracy],
    ['Qualified >60% forecasts', green.length],
    ['Qualified correct', greenCorrect],
    ['Qualified accuracy', green.length ? pct(greenCorrect / green.length) : '—'],
    ['Misses caused by draws', drawMisses],
    ['Expected correct (probability sum)', evaluated ? expectedCorrect.toFixed(1) : '—'],
    ['Not predicted', notPredicted]
  ];
  $('history-stats').replaceChildren(...stats.map(([label, value]) => {
    const box = node('div', 'history-stat');
    box.append(node('span', '', label), node('strong', '', String(value)));
    return box;
  }));
  const filtered = period.filter(m => {
    if (historyVerdict === 'GREEN') return scored.includes(m) && isGreen(m);
    if (historyVerdict === 'LOW_CONFIDENCE') return scored.includes(m) && !isGreen(m);
    return historyVerdict === 'ALL' || m.forecastStatus === historyVerdict;
  });
  $('history-count').textContent = `${filtered.length} results` +
    (pending ? ` · ${pending} pending evaluation` : '');
  $('history-list').replaceChildren(...(filtered.length
    ? filtered.slice(0, historyVisible).map(historyCard)
    : [empty('No verified full-time matches match these filters. Try a longer period, another league, or All results.')]));
  const more = $('history-more');
  more.hidden = filtered.length <= historyVisible;
  if (!more.hidden) more.textContent =
    `Load more results (${filtered.length - historyVisible} remaining)`;
}
function bindFilter(groupId, attribute, onSelect) {
  document.querySelectorAll(`#${groupId} [${attribute}]`).forEach(button => {
    button.addEventListener('click', () => {
      onSelect(button.getAttribute(attribute));
      document.querySelectorAll(`#${groupId} [${attribute}]`).forEach(other => {
        const selected = other === button;
        other.classList.toggle('active', selected);
        other.setAttribute('aria-pressed', String(selected));
      });
    });
  });
}
function render() {
  $('last-update').textContent = data.lastRefreshAt
    ? `Provider last checked: ${time(data.lastRefreshAt)}`
    : 'Waiting for the first verified provider update';
  const offline = !navigator.onLine;
  $('connection-banner').hidden = !offline;
  $('status').className = 'status' + (data.refreshError || offline ? ' error' : '');
  $('status').textContent = offline ? 'Offline · reconnect to update'
    : data.refreshError ? 'Provider update delayed'
      : data.automated ? 'Automatic daily updates enabled' : 'Automation paused';
  renderMetrics(data.performance);
  renderFixtures();
  renderResults();
  renderHistory();
  const errors = data.providerErrors || [];
  $('provider-errors').hidden = !errors.length && !data.refreshError;
  $('provider-errors').textContent = [data.refreshError,
    ...errors.map(x => `${x.competition}: ${x.code}`)].filter(Boolean).join(' · ');
}
async function refresh(manual = false) {
  const button = $('refresh');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.setAttribute('aria-label', 'Checking football data');
  button.querySelector('.refresh-label').textContent = 'Checking provider…';
  try {
    if (!navigator.onLine) throw new Error('Offline — reconnect to update match data');
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
      $('history-list').replaceChildren(empty('Match history requires a connection to verified football data.'));
    }
    $('connection-banner').hidden = navigator.onLine;
  } finally {
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.setAttribute('aria-label', 'Refresh match data');
    button.querySelector('.refresh-label').textContent = 'Refresh data';
  }
}
document.querySelectorAll('[data-league]').forEach(button => {
  button.addEventListener('click', () => {
    activeLeague = button.dataset.league;
    fixtureVisible = 20;
    document.querySelectorAll('[data-league]').forEach(x =>
      x.classList.toggle('active', x === button));
    renderFixtures();
  });
});
bindFilter('forecast-window', 'data-window', value => {
  activeWindow = Number(value);
  fixtureVisible = 20;
  renderFixtures();
});
bindFilter('history-window', 'data-history-days', value => {
  historyDays = value === 'ALL' ? 'ALL' : Number(value);
  historyVisible = 24;
  renderHistory();
});
bindFilter('history-verdict', 'data-verdict', value => {
  historyVerdict = value;
  historyVisible = 24;
  renderHistory();
});
bindFilter('history-league', 'data-history-league', value => {
  historyLeague = value;
  historyVisible = 24;
  renderHistory();
});
$('fixtures-more').addEventListener('click', () => {
  fixtureVisible += 20;
  renderFixtures();
});
$('history-more').addEventListener('click', () => {
  historyVisible += 24;
  renderHistory();
});
$('refresh').addEventListener('click', () => refresh(true));
refresh();
setInterval(() => {
  if (document.visibilityState === 'visible') refresh();
}, 5 * 60 * 1000);

/* Mobile app lifecycle: navigation, install prompts and honest offline status. */
function selectMobileNav(id) {
  document.querySelectorAll('[data-mobile-nav]').forEach(link => {
    const selected = link.dataset.mobileNav === id;
    link.classList.toggle('active', selected);
    if (selected) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
}
document.querySelectorAll('[data-mobile-nav]').forEach(link => {
  link.addEventListener('click', () => selectMobileNav(link.dataset.mobileNav));
});
let scrollPending = false;
window.addEventListener('scroll', () => {
  if (scrollPending) return;
  scrollPending = true;
  requestAnimationFrame(() => {
    scrollPending = false;
    let current = 'predictions';
    for (const id of ['predictions', 'history', 'performance']) {
      if ($(id).getBoundingClientRect().top < window.innerHeight * .32) current = id;
    }
    selectMobileNav(current);
  });
}, { passive: true });

window.addEventListener('offline', () => {
  $('connection-banner').hidden = false;
  $('status').className = 'status error';
  $('status').textContent = 'Offline · reconnect to update';
});
window.addEventListener('online', () => {
  $('connection-banner').hidden = true;
  refresh();
});
$('connection-banner').hidden = navigator.onLine;

let installPrompt = null;
const installButton = $('install-app');
const iosTip = $('ios-install-tip');
const standalone = () => window.matchMedia('(display-mode: standalone)').matches ||
  window.navigator.standalone === true;
const isIOS = /iPhone|iPad|iPod/i.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
if (isIOS && !standalone()) iosTip.hidden = false;
window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  if (standalone()) return;
  installPrompt = event;
  installButton.hidden = false;
});
installButton.addEventListener('click', async () => {
  if (!installPrompt) return;
  const event = installPrompt;
  installPrompt = null;
  installButton.hidden = true;
  try {
    await event.prompt();
    await event.userChoice;
  } catch {
    // Browser may decline or no longer support the installation prompt.
  }
});
window.addEventListener('appinstalled', () => {
  installPrompt = null;
  installButton.hidden = true;
  iosTip.hidden = true;
});
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
      // Normal online website still works without offline shell support.
    });
  });
}
