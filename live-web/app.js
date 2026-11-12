const $ = id => document.getElementById(id);
let data = null, activeLeague = 'ALL', activeWindow = 14;
let fixtureVisible = 20;
let historyDays = 'ALL', historyVerdict = 'ALL', historyLeague = 'ALL';
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
const pct = p => p !== null && p !== undefined && Number.isFinite(Number(p)) ? `${(Number(p) * 100).toFixed(1)}%` : '—';
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
function renderShadow(research) {
  const paired = research?.sampleSize || 0;
  const pending = research?.pendingUpcoming || 0;
  $('shadow-summary').textContent = paired
    ? 'We compared two ways of predicting the same ' + paired + ' completed matches. Our original method still powers the website.'
    : pending
      ? 'We have made test predictions for ' + pending + ' upcoming matches. We will compare them when the games finish.'
      : 'We are preparing new test predictions. Our original method still powers the website.';
  const rows = [
    ['Matches waiting for results', pending],
    ['Matches compared', paired],
    ['Original method right', paired ? research.baselineCorrect + ' of ' + paired : 'Not yet'],
    ['New method right', paired ? research.candidateCorrect + ' of ' + paired : 'Not yet'],
    ['Original success rate', paired ? pct(research.baselineAccuracy) : 'Not yet'],
    ['New success rate', paired ? pct(research.candidateAccuracy) : 'Not yet']
  ];
  $('shadow-stats').replaceChildren(...rows.map(([label, value]) => {
    const box = node('div', 'history-stat');
    box.append(node('span', '', label), node('strong', '', String(value)));
    return box;
  }));
}
function renderMetrics(performance) {
  const a = data?.historyAudit || {};
  const values = [
    ['Predictions checked', String(a.evaluated ?? 0)],
    ['Correct predictions', String(a.correct ?? 0)],
    ['Incorrect predictions', String(a.incorrect ?? 0)],
    ['Overall success rate', pct(a.accuracy)],
    ['Stronger picks right', a.greenEvaluated ? a.greenCorrect + ' of ' + a.greenEvaluated : 'Not yet'],
    ['Exact final score right', pct(performance.top1ScoreHitRate)]
  ];
  $('metrics').replaceChildren(...values.map(([label, value]) => {
    const box = node('div', 'metric');
    box.append(node('span', '', label), node('strong', '', value));
    return box;
  }));
  const b = data?.accuracyResearch?.historical;
  $('backtest-summary').textContent = b?.sampleSize
    ? 'Earlier test of a different improvement: our original method got ' + b.baselineCorrect + ' of ' + b.sampleSize + ' right (' + pct(b.baselineAccuracy) + '). That test method got ' + b.candidateCorrect + ' right (' + pct(b.candidateAccuracy) + '). This is a test on past matches, not our live success rate.'
    : 'We are checking how new ideas would have performed on earlier matches.';
  $('sample-label').textContent = performance.sampleSize === 0
    ? 'Waiting for results'
    : performance.sampleSize < 50
      ? 'Only ' + performance.sampleSize + ' matches so far'
      : performance.sampleSize + ' matches checked';
  renderShadow(data?.multiseasonResearch || data?.accuracyResearch);
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
  [['Home team wins', p.p1x2[0]], ['Draw (same score)', p.p1x2[1]], ['Visiting team wins', p.p1x2[2]]]
    .forEach(([label, value]) => {
      const box = node('div', 'prob' + (value === best ? ' high' : ''));
      box.append(node('strong', '', pct(value)), node('small', '', label));
      odds.append(box);
    });
  const bottom = node('div', 'fixture-bottom');
  const items = [
    ['Both teams score', pct(p.pBtts)],
    ['3 or more goals', pct(p.pOver25)],
    ['Most likely score', p.topScores[0]?.score || '—'],
    ['Past matches used', `${p.trainingMatches} matches`]
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
  $('fixture-count').textContent = `${filtered.length} matches`;
  $('fixtures').replaceChildren(...(filtered.length
    ? filtered.slice(0, fixtureVisible).map(fixtureCard)
    : [empty('No upcoming matches found. Try a different league or a longer time period.')]));
  const more = $('fixtures-more');
  more.hidden = filtered.length <= fixtureVisible;
  if (!more.hidden) more.textContent =
    `Show more matches (${filtered.length - fixtureVisible} remaining)`;
}
function resultCard(ev) {
  const row = node('article', 'result');
  const label = node('div', 'result-title', `${ev.homeTeam} vs ${ev.awayTeam}`);
  label.append(node('small', '', `${ev.competition} · ${time(ev.kickoffUtc)}`));
  const score = node('div', 'score', `${ev.home} – ${ev.away}`);
  const stats = node('div', 'result-stats');
  const match = (data?.history || []).find(m => m.fixtureId === ev.fixtureId && m.kickoffUtc === ev.kickoffUtc);
  stats.append(node('span', '', match?.forecastStatus === 'CORRECT' ? 'Prediction was right' : match?.forecastStatus === 'INCORRECT' ? 'Prediction was wrong' : 'Result checked'), node('div', '', ev.top1ScoreHit ? 'Exact final score was right' : 'Exact final score was different'));
  row.append(label, score, stats);
  return row;
}
function renderResults() {
  if (!data) return;
  $('result-count').textContent = `${data.results.length} matches checked`;
  $('results').replaceChildren(...(data.results.length
    ? data.results.map(resultCard)
    : [empty('No results for our predictions yet. Final scores will appear here when matches finish.')]));
}
const OUTCOME_NAMES = { H: 'Home win', D: 'Draw', A: 'Away win' };
const VERDICTS = {
  CORRECT: ['Prediction right', 'correct'],
  INCORRECT: ['Prediction wrong', 'incorrect'],
  NO_FORECAST: ['Not predicted', 'unpredicted'],
  PENDING_EVALUATION: ['Waiting for results', 'pending']
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
    detail.textContent = 'We did not make a prediction before this match.';
  } else {
    const pick = match.predictedOutcome === 'D' ? 'Draw'
      : match.predictedOutcome === 'H' ? match.homeTeam : match.awayTeam;
    const isGreen = match.predictedOutcome !== 'D' &&
      match.predictedConfidence > 0.60;
    detail.textContent = `We thought: ${pick} (${pct(match.predictedConfidence)}) · ` +
      (isGreen ? 'Stronger pick · ' : 'Not a stronger pick · ') +
      `Result: ${match.outcome === 'D' ? 'Draw' : match.winner}`;
  }
  if (match.forecastVersion > 1) {
    detail.append(node('span', 'history-correction',
      ` · Prediction updated before the match`));
  }
  if (match.resultVersion > 1) {
    detail.append(node('span', 'history-correction',
      ` · Final score corrected`));
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
  const stats = [
    ['Finished matches', period.length],
    ['Predictions checked', evaluated],
    ['Correct predictions', correct],
    ['Incorrect predictions', incorrect],
    ['Success rate', accuracy],
    ['Stronger picks checked', green.length],
    ['Stronger picks right', greenCorrect],
    ['Stronger picks success rate', green.length ? pct(greenCorrect / green.length) : '—'],
    ['Missed draws', drawMisses],
    ['Other missed results', incorrect - drawMisses],
    
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
    (pending ? ` · ${pending} waiting for results` : '');
  $('history-list').replaceChildren(...(filtered.length
    ? filtered.slice(0, historyVisible).map(historyCard)
    : [empty('No results found. Try a longer period, another league or All results.')]));
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
    ? `Last updated: ${time(data.lastRefreshAt)}`
    : 'Waiting for match information';
  const offline = !navigator.onLine;
  $('connection-banner').hidden = !offline;
  $('status').className = 'status' + (data.refreshError || offline ? ' error' : '');
  $('status').textContent = offline ? 'Offline · reconnect to update'
    : data.refreshError ? 'Match update delayed'
      : data.automated ? 'Daily updates on' : 'Updates paused';
  renderMetrics(data.performance);
  renderFixtures();
  renderResults();
  renderHistory();
  const errors = data.providerErrors || [];
  $('provider-errors').hidden = !errors.length && !data.refreshError;
  $('provider-errors').textContent = [data.refreshError ? 'Match update delayed.' : null, ...errors.map(x => x.competition ? x.competition + ': match update delayed' : 'Match update delayed')].filter(Boolean).join(' · ');
}
async function refresh(manual = false) {
  const button = $('refresh');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.setAttribute('aria-label', 'Checking football data');
  button.querySelector('.refresh-label').textContent = 'Checking matches…';
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
    $('last-update').textContent = 'We could not update matches. Please try again.';
    if (!data) {
      $('fixtures').replaceChildren(empty('Match information is temporarily unavailable. Please try again.'));
      $('results').replaceChildren(empty('Waiting for match results.'));
      $('history-list').replaceChildren(empty('Connect to the internet to see match history.'));
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
