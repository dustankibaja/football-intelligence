/* Footmo staging client: no fabricated data, no hard-coded secrets. */
"use strict";

const state = { apiBase: "", apiToken: "", predictions: [], evaluations: [], performance: null, league: "ALL" };
const $ = (id) => document.getElementById(id);

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function percent(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 && n <= 1 ? `${(n * 100).toFixed(1)}%` : "—";
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unavailable";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium", timeStyle: "short", timeZone: "UTC"
  }).format(date) + " UTC";
}

function setConnection(kind, message) {
  $("connection-dot").className = `status-dot ${kind === "connected" ? "connected" : kind === "error" ? "error" : ""}`;
  $("connection-label").textContent = message;
}

function emptyState(title, message) {
  const card = element("div", "empty-state");
  card.append(element("span", "empty-icon", "◎"));
  card.append(element("h3", "", title));
  card.append(element("p", "", message));
  return card;
}

function makeFixtureCard(prediction) {
  const p = prediction.one_x_two || {};
  const ph = Number(p.home), pd = Number(p.draw), pa = Number(p.away);
  const card = element("article", "fixture-card");

  const meta = element("div", "fixture-meta");
  meta.append(element("span", "", prediction.competition || "Unknown competition"));
  meta.append(element("span", "quality-pill", prediction.quality_state || "UNKNOWN"));
  card.append(meta);

  const teams = element("div", "fixture-teams");
  teams.append(element("strong", "", prediction.home_team || "Home team"));
  teams.append(element("span", "vs", "VS"));
  teams.append(element("strong", "", prediction.away_team || "Away team"));
  card.append(teams);
  card.append(element("div", "fixture-kickoff", formatDate(prediction.kickoff_utc)));

  const labels = element("div", "prob-labels");
  for (const [name, value] of [["HOME", ph], ["DRAW", pd], ["AWAY", pa]]) {
    const box = element("div");
    box.append(element("span", "", name), element("strong", "", percent(value)));
    labels.append(box);
  }
  card.append(labels);

  const bar = element("div", "prob-bar");
  bar.setAttribute("role", "img");
  bar.setAttribute("aria-label", `Home ${percent(ph)}, draw ${percent(pd)}, away ${percent(pa)}`);
  for (const value of [ph, pd, pa]) {
    const span = element("span");
    span.style.width = Number.isFinite(value) && value >= 0 && value <= 1 ? `${value * 100}%` : "0%";
    bar.append(span);
  }
  card.append(bar);

  const markets = element("div", "markets");
  const first = prediction.top_scorelines && prediction.top_scorelines[0];
  const rows = [
    ["BTTS YES", percent(prediction.btts?.yes)],
    ["OVER 2.5", percent(prediction.over_under_2_5?.over)],
    ["TOP SCORE", first ? `${first.home_goals} – ${first.away_goals}` : "—"]
  ];
  for (const [label, value] of rows) {
    const box = element("div");
    box.append(element("span", "", label), element("strong", "", value));
    markets.append(box);
  }
  card.append(markets);
  return card;
}

function renderFixtures() {
  const list = $("fixtures-list");
  list.replaceChildren();
  const rows = state.predictions.filter(p => state.league === "ALL" || p.competition_code === state.league);
  $("fixture-count").textContent = `${rows.length} verified prediction${rows.length === 1 ? "" : "s"}`;
  if (!rows.length) {
    list.append(emptyState(
      "No verified fixtures available",
      "No scientifically verified predictions are available for this selection. Footmo will not display synthetic forecasts."
    ));
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const prediction of rows) fragment.append(makeFixtureCard(prediction));
  list.append(fragment);
}

function renderLeagues() {
  const select = $("league-filter");
  const codes = new Map(state.predictions.map(p => [p.competition_code, p.competition]));
  select.replaceChildren(element("option", "", "All leagues"));
  select.firstChild.value = "ALL";
  for (const [code, name] of [...codes].sort((a, b) => String(a[1]).localeCompare(String(b[1])))) {
    const option = element("option", "", name || code);
    option.value = code;
    select.append(option);
  }
  select.disabled = codes.size === 0;
  state.league = "ALL";
  select.value = "ALL";
}

function renderResults() {
  const body = $("results-body");
  body.replaceChildren();
  if (!state.evaluations.length) {
    const tr = element("tr");
    const td = element("td", "table-empty", "No verified completed-match evaluations yet.");
    td.colSpan = 5;
    tr.append(td);
    body.append(tr);
    return;
  }
  for (const ev of state.evaluations) {
    const tr = element("tr");
    const score = ev.actual_score ? `${ev.actual_score.home} – ${ev.actual_score.away}` : "—";
    const cols = [
      ev.home_team && ev.away_team ? `${ev.home_team} vs ${ev.away_team}` : "Fixture unavailable",
      score, ev.actual_1x2 || "—",
      Number.isFinite(Number(ev.log_loss_1x2)) ? Number(ev.log_loss_1x2).toFixed(3) : "—",
      formatDate(ev.result_observed_at)
    ];
    for (const value of cols) tr.append(element("td", "", value));
    body.append(tr);
  }
}

function renderWorkerStatus(status) {
  const stateLabel = {
    RECENT: "Recent scheduler activity",
    STALE: "Scheduler activity stale",
    NO_RUNS: "No completed scheduler cycles"
  };
  $("worker-state").textContent = stateLabel[status.worker_activity] || "Worker status unavailable";
  const detail = status.last_run_at ? `Last cycle: ${formatDate(status.last_run_at)} · ${status.last_run_status || "UNKNOWN"}`
    : "No completed live cycle has been recorded.";
  $("worker-last-run").textContent = status.last_failure_code
    ? `${detail} · ${status.last_failure_code}` : detail;
}

function renderPerformance() {
  const grid = $("performance-grid");
  grid.replaceChildren();
  const summary = state.performance || {};
  const metric = (value) => value === null || value === undefined ||
    !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(3);
  const rows = [
    ["Evaluated matches", summary.sample_size ?? 0],
    ["1X2 log loss ↓", metric(summary.mean_log_loss_1x2)],
    ["1X2 Brier ↓", metric(summary.mean_brier_1x2)],
    ["BTTS log loss ↓", metric(summary.mean_btts_log_loss)],
    ["O/U 2.5 log loss ↓", metric(summary.mean_over_under_2_5_log_loss)],
    ["Top exact score hit", summary.top1_score_hit_rate == null ? "—" : percent(summary.top1_score_hit_rate)]
  ];
  for (const [label, value] of rows) {
    const card = element("div", "performance-card");
    card.append(element("span", "", label), element("strong", "", value));
    grid.append(card);
  }
  const n = Number(summary.sample_size || 0);
  $("performance-caption").textContent = n === 0
    ? "No verified completed-match evaluations yet. No accuracy claims can be made."
    : `Observed from ${n} verified completed matches. ${n < 50 ? "Small sample: treat results as preliminary." : "Monitor calibration and league-level performance."}${summary.truncated ? " The evaluation history is truncated." : ""}`;
}

async function request(path) {
  const response = await fetch(`${state.apiBase}/api/v1/${path}`, {
    headers: { "X-Footmo-API-Key": state.apiToken, "Accept": "application/json" },
    cache: "no-store"
  });
  if (!response.ok) {
    if (response.status === 503) throw new Error("Customer API is not enabled on this server.");
    if (response.status === 401) throw new Error("Access key rejected. Verify your credentials.");
    throw new Error(`API returned HTTP ${response.status}.`);
  }
  return response.json();
}

async function refreshData() {
  $("refresh").disabled = true;
  $("connection-error").hidden = true;
  try {
    const [predictions, evaluations, performance, operations] = await Promise.all([
      request("predictions/?limit=100"),
      request("evaluations/?limit=100"),
      request("evaluations/summary"),
      request("status")
    ]);
    if (!Array.isArray(predictions.items) || !Array.isArray(evaluations.items)) {
      throw new Error("The API returned an unexpected response.");
    }
    state.predictions = predictions.items;
    state.evaluations = evaluations.items;
    state.performance = performance;
    renderPerformance();
    renderWorkerStatus(operations);
    renderLeagues();
    renderFixtures();
    renderResults();
    setConnection("connected", "API connected");
    $("connection-panel").hidden = true;
  } catch (error) {
    setConnection("error", "Connection unavailable");
    $("connection-panel").hidden = false;
    $("connection-error").textContent = error.message || "Unable to connect.";
    $("connection-error").hidden = false;
  } finally {
    $("refresh").disabled = false;
  }
}

$("connect-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("api-url").value.trim().replace(/\/+$/, "");
  let url;
  try { url = new URL(input); } catch { $("connection-error").textContent = "Enter a valid API URL."; $("connection-error").hidden = false; return; }
  if (!["http:", "https:"].includes(url.protocol)) {
    $("connection-error").textContent = "Only HTTP(S) API URLs are supported.";
    $("connection-error").hidden = false;
    return;
  }
  if (url.protocol === "http:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) {
    $("connection-error").textContent = "Use HTTPS for remote APIs to protect the private access key.";
    $("connection-error").hidden = false;
    return;
  }
  state.apiBase = url.origin + url.pathname.replace(/\/+$/, "");
  state.apiToken = $("api-token").value;
  $("api-token").value = "";
  refreshData();
});
$("refresh").addEventListener("click", refreshData);
$("league-filter").addEventListener("change", (event) => {
  state.league = event.target.value;
  renderFixtures();
});
$("year").textContent = String(new Date().getFullYear());

// Browser auto-refresh while open; unattended backend scheduling is separate.
setInterval(() => {
  if (state.apiBase && state.apiToken && document.visibilityState === "visible") {
    refreshData();
  }
}, 5 * 60 * 1000);
