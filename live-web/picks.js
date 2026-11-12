import { isFresh, landingMatches } from './picks-core.js';

const $ = id => document.getElementById(id);
let state = null;
let days = 14;
let view = 'all';
let visible = 12;
let pendingRequest = false;

const pct = p => (p * 100).toFixed(1) + '%';
const dateTime = iso => new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium', timeStyle: 'short'
}).format(new Date(iso));

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function showEmpty(title, detail) {
  const box = element('div', 'empty-state');
  box.append(element('strong', '', title), element('span', '', detail));
  return box;
}

function cardFor({ forecast: p, candidate, green }) {
  const card = element('article', 'match-card' + (green ? ' green' : ''));
  const meta = element('div', 'match-meta');
  meta.append(
    element('span', 'match-league', p.competition || p.competitionCode || 'Football'),
    element('span', 'match-kickoff', dateTime(p.kickoffUtc))
  );

  const title = element('div', 'match-title');
  const teams = element('div', 'match-teams');
  teams.append(element('div', 'pick-caption',
    green ? 'STRONGER PICK' : 'TEAM MORE LIKELY TO WIN'));
  teams.append(element('h3', '', candidate.team));
  const opponent = candidate.side === 'HOME' ? p.awayTeam : p.homeTeam;
  teams.append(element('div', 'match-opponent',
    `vs ${opponent} · ${candidate.side === 'HOME' ? 'Home team' : 'Visiting team'}`));

  const chance = element('div', 'match-percent');
  chance.append(element('strong', '', pct(candidate.probability)),
    element('small', '', 'win chance'));
  title.append(teams, chance);

  const bar = element('div', 'chance-bar');
  bar.setAttribute('aria-label', `Estimated win chance: ${pct(candidate.probability)}`);
  const fill = element('span');
  fill.style.width = (candidate.probability * 100) + '%';
  bar.append(fill);

  const footer = element('div', 'match-footer');
  if (green) {
    const badge = element('span', 'signal-badge');
    badge.append(element('span', 'green-dot'), element('span', '', 'STRONGER PICK'));
    footer.append(badge);
  } else {
    footer.append(element('span', 'neutral-badge', 'No stronger pick'));
  }
  const odds = element('span', 'outcomes');
  odds.append(
    element('span', '', 'Home ' ), element('b', '', pct(p.p1x2[0])),
    element('span', '', ' · Draw ' ), element('b', '', pct(p.p1x2[1])),
    element('span', '', ' · Visiting ' ), element('b', '', pct(p.p1x2[2]))
  );
  odds.setAttribute('aria-label',
    `Home ${pct(p.p1x2[0])}, draw ${pct(p.p1x2[1])}, away ${pct(p.p1x2[2])}`);
  footer.append(odds);
  card.append(meta, title, bar, footer);
  return card;
}

function healthy() {
  return Boolean(state && navigator.onLine &&
    isFresh(state.lastRefreshAt) && !state.refreshError &&
    !(state.providerErrors || []).length);
}

function render() {
  if (!state) return;
  const a = state.historyAudit;
  $('trial-result').textContent = a?.evaluated ? 'So far, we got ' + a.correct + ' of ' + a.evaluated + ' predictions right (' + pct(a.accuracy) + ').' : 'We are still checking our earlier predictions.';
  const current = Date.now();
  const fresh = healthy();
  const options = { days, fresh, now: current };
  const green = landingMatches(state.upcoming, { ...options, view: 'green' });
  const all = landingMatches(state.upcoming, { ...options, view: 'all' });
  const rows = view === 'green' ? green : all;
  $('green-count').textContent = String(green.length);
  $('last-update').textContent = state.lastRefreshAt
    ? 'Checked ' + dateTime(state.lastRefreshAt)
    : 'No recent update yet';
  $('data-status').textContent = !navigator.onLine ? 'Offline'
    : fresh ? 'Match data ready' : 'Updates delayed';
  $('data-status').classList.toggle('error', !fresh);
  $('offline-banner').hidden = navigator.onLine;

  const errors = [
    state.refreshError ? 'Match update delayed' : null,
    ...(state.providerErrors || []).map(x => x.competition
      ? x.competition + ' update delayed' : 'Match update delayed')
  ].filter(Boolean);
  const notice = !navigator.onLine
    ? 'Reconnect to see the latest matches.'
    : !fresh ? 'Stronger picks are hidden until we receive a fresh match update.'
      : '';
  $('error-message').hidden = !notice && !errors.length;
  $('error-message').textContent = [notice, ...errors].join(' · ');

  let children;
  if (rows.length) {
    children = rows.slice(0, visible).map(cardFor);
  } else if (!fresh && view === 'green') {
    children = [showEmpty('Stronger picks are paused',
      'We need a fresh match update. You can still browse all matches.')];
  } else if (!all.length) {
    children = [showEmpty('No upcoming matches in this period',
      'Try a longer time period or check again after our next update.')];
  } else {
    children = [showEmpty('No stronger picks right now',
      'No team has a win chance above 60% in this period. Choose All matches to see the other games.')];
  }
  $('picks-list').replaceChildren(...children);
  $('load-more').hidden = rows.length <= visible;
  if (!$('load-more').hidden) {
    $('load-more').textContent = `Show more matches (${rows.length - visible} remaining)`;
  }
}

function updateSelection(groupId, attribute, value) {
  document.querySelectorAll(`#${groupId} [${attribute}]`).forEach(button => {
    const selected = button.getAttribute(attribute) === String(value);
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed', String(selected));
  });
}
document.querySelectorAll('[data-days]').forEach(button => {
  button.addEventListener('click', () => {
    days = Number(button.dataset.days);
    visible = 12;
    updateSelection('period-filters', 'data-days', days);
    render();
  });
});
document.querySelectorAll('[data-view]').forEach(button => {
  button.addEventListener('click', () => {
    view = button.dataset.view;
    visible = 12;
    updateSelection('view-filters', 'data-view', view);
    render();
  });
});
$('load-more').addEventListener('click', () => {
  visible += 12;
  render();
});

async function refresh(manual = false) {
  if (pendingRequest) return;
  pendingRequest = true;
  $('refresh').disabled = true;
  $('refresh').setAttribute('aria-busy', 'true');
  $('data-status').textContent = 'Checking…';
  try {
    if (!navigator.onLine) throw new Error('No internet connection');
    const url = manual ? '/api/state?force=1' : '/api/state';
    const response = await fetch(url, { cache: 'no-store', credentials: 'same-origin' });
    const result = await response.json();
    if (!response.ok && !result.lastRefreshAt) {
      throw new Error(result.refreshError || result.reason || result.error || 'Data unavailable');
    }
    if (!Array.isArray(result.upcoming)) throw new Error('Invalid football data response');
    state = result;
    render();
  } catch (error) {
    if (state) {
      // No fresh data means no green signals. Never present old picks as live.
      state = { ...state, refreshError: String(error.message) };
      render();
    } else {
      $('green-count').textContent = '0';
      $('data-status').textContent = 'Connection error';
      $('data-status').classList.add('error');
      $('last-update').textContent = 'Unable to update matches';
      $('error-message').hidden = false;
      $('error-message').textContent = 'We could not load matches right now. Please try again.';
      $('picks-list').replaceChildren(showEmpty('Unable to load matches',
        'Please check your connection and try refreshing.'));
      $('load-more').hidden = true;
    }
  } finally {
    pendingRequest = false;
    $('refresh').disabled = false;
    $('refresh').removeAttribute('aria-busy');
  }
}
$('refresh').addEventListener('click', () => refresh(true));
window.addEventListener('offline', () => {
  $('offline-banner').hidden = false;
  if (state) render();
});
window.addEventListener('online', () => {
  $('offline-banner').hidden = true;
  refresh();
});
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') refresh();
});
setInterval(() => {
  if (document.visibilityState === 'visible') refresh();
}, 5 * 60 * 1000);

// Keep the home page installable as an Android/iOS progressive web app.
let installPrompt = null;
const installButton = $('install-app');
const iosTip = $('ios-install-tip');
const standalone = () => window.matchMedia('(display-mode: standalone)').matches ||
  navigator.standalone === true;
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
    // Installation may be dismissed without affecting the website.
  }
});
window.addEventListener('appinstalled', () => {
  installPrompt = null;
  installButton.hidden = true;
  iosTip.hidden = true;
});
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  });
}
refresh();
