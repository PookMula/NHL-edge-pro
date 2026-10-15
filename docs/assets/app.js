/* NHL EDGE PRO — static front end. Reads JSON written by the Python pipeline (docs/data/).
   Never invents numbers: every figure shown comes from a data file; missing data renders as "unavailable". */
"use strict";

const DEMO = new URLSearchParams(location.search).has("demo");
const BASE = DEMO ? "data/demo/" : "data/";
const FILES = ["meta", "slate", "edges", "parlays", "teams", "goalies", "backtest", "market_eval", "odds_history", "model_params", "props"];
const S = { data: {}, charts: {}, book: "best" };

/* ---------------- utilities ---------------- */
const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = x => typeof x === "number" && isFinite(x);
const pct = (p, d = 1) => isNum(p) ? (100 * p).toFixed(d) + "%" : "—";
const pp = (x, d = 1) => isNum(x) ? (x >= 0 ? "+" : "") + (100 * x).toFixed(d) + " pp" : "—";
const roi = (x, d = 1) => isNum(x) ? (x >= 0 ? "+" : "") + (100 * x).toFixed(d) + "%" : "—";
const fx = (x, d = 2) => isNum(x) ? x.toFixed(d) : "—";
const am2dec = a => a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a);
const dec2am = d => d >= 2 ? (d - 1) * 100 : -100 / (d - 1);
const prob2am = p => (p > 0 && p < 1) ? dec2am(1 / p) : NaN;
const fmtAm = a => isNum(a) ? (Math.round(a) > 0 ? "+" : "") + Math.round(a) : "—";
const evCls = x => !isNum(x) ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "";
const timeLocal = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "—";
const dtLocal = iso => iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
function ago(iso) {
  if (!iso) return "never";
  const m = (Date.now() - new Date(iso).getTime()) / 60000;
  if (m < 1) return "just now"; if (m < 60) return Math.round(m) + "m ago";
  if (m < 2880) return Math.round(m / 60) + "h ago"; return Math.round(m / 1440) + "d ago";
}
const chip = (txt, cls = "") => `<span class="chip ${cls}">${esc(txt)}</span>`;
const goalieChip = st => chip(st || "UNKNOWN", st === "CONFIRMED" ? "ok" : st === "PROJECTED" ? "warn" : "bad");
const tierChip = t => chip(t || "EXPERIMENTAL", t === "EDGE_SUPPORTED" ? "ok" : t === "MARKET_COMPETITIVE" ? "info" : t === "BEATS_BASELINE" ? "info" : "warn");
const logo = (url, abbr) => url ? `<img class="logo" src="${esc(url)}" alt="${esc(abbr)}" loading="lazy" onerror="this.outerHTML='<div class=logo-ph>${esc(abbr)}</div>'">` : `<div class="logo-ph">${esc(abbr)}</div>`;
const empty = (big, msg) => `<div class="empty"><div class="big">${esc(big)}</div><div>${msg}</div></div>`;
const stat = (k, v, s = "", cls = "") => `<div class="stat"><div class="k">${esc(k)}</div><div class="v ${cls}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
const marketLabel = (m, side, point, g) => {
  const team = side === "home" ? g?.home?.abbr : side === "away" ? g?.away?.abbr : null;
  if (m === "h2h") return `${team} ML`;
  if (m === "spreads") return `${team} ${point > 0 ? "+" : ""}${point}`;
  if (m === "totals") return `${side === "over" ? "Over" : "Under"} ${point}`;
  return `${m} ${side} ${point ?? ""}`;
};
const gameById = id => (S.data.slate?.games || []).find(g => String(g.game_id) === String(id));
const gameTitle = g => `${g.away.abbr} @ ${g.home.abbr}`;

async function loadJSON(name) {
  try {
    const r = await fetch(`${BASE}${name}.json`, { cache: "no-cache" });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

function chart(id, cfg) {
  if (S.charts[id]) { S.charts[id].destroy(); delete S.charts[id]; }
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  Chart.defaults.color = "#8a9bb8"; Chart.defaults.borderColor = "#1e2c47";
  Chart.defaults.font.family = "Inter, system-ui, sans-serif";
  cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { labels: { boxWidth: 10 } } } }, cfg.options || {});
  S.charts[id] = new Chart(el, cfg);
}
function destroyCharts() { Object.values(S.charts).forEach(c => c.destroy()); S.charts = {}; }

function sortableTable(el) {
  el.querySelectorAll("th.sortable").forEach((th, idx) => th.addEventListener("click", () => {
    const tbody = el.querySelector("tbody"); const col = [...th.parentNode.children].indexOf(th);
    const dir = th.dataset.dir === "asc" ? "desc" : "asc"; th.dataset.dir = dir;
    const rows = [...tbody.querySelectorAll("tr")];
    const val = tr => { const c = tr.children[col]; const v = c?.dataset.v ?? c?.textContent ?? ""; const n = parseFloat(v); return isNaN(n) ? v : n; };
    rows.sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (dir === "asc" ? 1 : -1); });
    rows.forEach(r => tbody.appendChild(r));
  }));
}

/* ---------------- shell ---------------- */
function renderStatus() {
  const m = S.data.meta; const el = $("#topbar-status");
  if (!m) { el.innerHTML = chip("NO DATA", "bad keep"); return; }
  const parts = [];
  if (DEMO) parts.push(chip("DEMO", "warn keep"));
  (m.sources || []).forEach(s => parts.push(chip(`${s.short || s.name}: ${s.status === "ok" ? ago(s.fetched_at) : s.status}`, s.status === "ok" ? "ok" : s.status === "missing_key" ? "warn" : "bad")));
  parts.push(chip(`MODEL ${m.model?.status || "—"}`, "info"));
  el.innerHTML = parts.join("");
}

const ROUTES = {
  dashboard: pageDashboard, game: pageGame, pick: pagePick, moneyline: pageMoneyline, puckline: pagePuckline,
  totals: pageTotals, props: pageProps, parlays: pageParlays, parlay: pageParlay, goalies: pageGoalies,
  teams: pageTeams, odds: pageOdds, sim: pageSim, backtest: pageBacktest, performance: pagePerformance,
  tracker: pageTracker, about: pageAbout, keys: pageKeys, player: pagePlayer,
};

function route() {
  const [name, arg] = (location.hash.replace(/^#\/?/, "") || "dashboard").split("/");
  document.querySelectorAll(".sidebar a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === `#/${name === "pick" ? "dashboard" : name === "parlay" ? "parlays" : name === "player" ? "props" : name}`));
  $("#sidebar").classList.remove("open");
  destroyCharts();
  const app = $("#app");
  if (!S.data.meta && !["about", "tracker", "keys"].includes(name)) { app.innerHTML = pageSetup(); return; }
  try { (ROUTES[name] || pageDashboard)(app, arg ? decodeURIComponent(arg) : null); }
  catch (e) { console.error(e); app.innerHTML = `<div class="notice bad">Failed to render this page: ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}

function pageSetup() {
  return `<div class="page-head"><div><h1>No pipeline output found</h1><p>The site reads JSON files that the data pipeline writes to <code>docs/data/</code>. None were found, so nothing is shown — the platform never fills gaps with invented numbers.</p></div></div>
  <div class="panel"><h2>Get real data flowing</h2><ol class="ul">
  <li>Push this repository to GitHub and enable <b>Pages</b> (Settings → Pages → Deploy from branch → <code>main</code> / <code>/docs</code>).</li>
  <li>Add your odds key as a repository secret named <code>ODDS_API_KEY</code> (optional — game models run without it, but EV needs prices).</li>
  <li>Run the <b>NHL EDGE PRO pipeline</b> workflow once with mode <code>backfill</code> (≈1 hour), then once with <code>backtest</code>. Scheduled runs keep it current after that.</li></ol>
  <p class="muted">Or run locally: <code>python -m pipeline.run backfill</code>, <code>python -m pipeline.run backtest</code>, <code>python -m pipeline.run daily</code>.</p>
  <p><a href="?demo=1">Open DEMO MODE</a> to see every page working on clearly labeled synthetic data.</p></div>`;
}

/* ---------------- shared game helpers ---------------- */
function bestPrice(g, market, side, point) {
  const rows = (g.odds?.rows || []).filter(r => r.market === market && r.side === side && (point == null || r.point === point) && (S.book === "best" || r.book === S.book));
  if (!rows.length) return null;
  return rows.reduce((a, b) => am2dec(b.price) > am2dec(a.price) ? b : a);
}
function modelProb(g, market, side, point) {
  const m = g.model; if (!m) return null;
  const key = `${market}|${side}|${point ?? ""}`;
  return m.markets?.[key] || null;
}
function evOf(pw, ppush, price) { if (!isNum(pw) || !isNum(price)) return NaN; const d = am2dec(price); return pw * (d - 1) - (1 - pw - (ppush || 0)); }
function bookOptions() {
  const books = new Set(); (S.data.slate?.games || []).forEach(g => (g.odds?.rows || []).forEach(r => books.add(r.book)));
  return `<label class="ctl">Sportsbook<select id="book-sel"><option value="best">Best available</option>${[...books].sort().map(b => `<option ${S.book === b ? "selected" : ""}>${esc(b)}</option>`).join("")}</select></label>`;
}
function bindBook(rerender) { const s = $("#book-sel"); if (s) s.onchange = () => { S.book = s.value; rerender(); }; }
function freshness() {
  const m = S.data.meta || {}; const sl = S.data.slate || {};
  return `<div class="controls small muted">Slate ${esc(sl.date || "—")} · model fit ${ago(m.model?.fitted_at)} · results through ${esc(m.data_through || "—")} · odds ${ago(sl.odds_fetched_at)}</div>`;
}

/* ---------------- Dashboard ---------------- */
function pageDashboard(app) {
  const sl = S.data.slate || { games: [] }; const ed = S.data.edges || {};
  const q = (ed.qualified || []);
  const cands = (ed.candidates || []);
  app.innerHTML = `
  <div class="page-head"><div><h1>Dashboard</h1><p>Every NHL game on today's slate with model probabilities, fair prices and the best available sportsbook prices.</p></div>
    <div class="controls"><label class="ctl">Team<input type="text" id="team-q" placeholder="e.g. TOR"></label>${bookOptions()}</div></div>
  ${freshness()}
  ${!DEMO && !getKey() && !(sl.games || []).some(g => g.odds?.rows?.length) ? `<div class="notice info">No sportsbook prices loaded. <a href="#/keys">Add your Odds API key</a> to load live odds in this browser.</div>` : ""}
  <div class="panel"><h2>Today's top model edges</h2>${edgesBlock(q, ed)}</div>
  ${cands.length ? `<div class="panel section"><h2>Unvalidated model discrepancies <span class="chip warn">EXPERIMENTAL</span></h2>
    <p class="small muted">Prices where the model's estimate beats the price even at the pessimistic (10th-percentile) end of its uncertainty, but the model has not yet earned the validation tier required to call them edges (${esc(ed.gate_tier)}). They are logged automatically so closing-line value can prove or disprove them.</p>
    ${edgeTable(cands.slice(0, 25))}</div>` : ""}
  <div class="section"><h2>Today's games (${sl.games.length})</h2>
  ${sl.games.length ? `<div class="cards" id="cards"></div>` : empty("NO NHL GAMES ON TODAY'S SLATE", `Source: ${esc(sl.source || "NHL API")} · checked ${ago(sl.fetched_at)}`)}</div>`;
  const draw = () => {
    const t = ($("#team-q")?.value || "").trim().toUpperCase();
    const el = $("#cards"); if (!el) return;
    el.innerHTML = sl.games.filter(g => !t || g.home.abbr.includes(t) || g.away.abbr.includes(t) || (g.home.name + g.away.name).toUpperCase().includes(t)).map(gameCard).join("");
    el.querySelectorAll(".game-card").forEach(c => c.onclick = () => location.hash = `#/game/${c.dataset.id}`);
  };
  $("#team-q").oninput = draw; bindBook(() => pageDashboard(app)); draw();
  app.querySelectorAll("tr[data-edge]").forEach(tr => tr.onclick = () => location.hash = `#/pick/${tr.dataset.edge}`);
}

function edgesBlock(q, ed) {
  if (!q.length) {
    const why = ed.no_edge_reason ? `<div class="small" style="margin-top:6px">${esc(ed.no_edge_reason)}</div>` : "";
    return empty("NO QUALIFIED EDGE FOUND TODAY", `No bet passed every gate (EV ≥ ${roi(ed.thresholds?.min_ev)}, pessimistic EV &gt; ${roi(ed.thresholds?.min_ev_lower)}, fresh odds, known goalies, model tier ≥ ${esc(ed.gate_tier || "—")}).${why}`);
  }
  return edgeTable(q) + `<div class="formula" style="margin-top:8px">${esc(ed.ranking_formula || "")}</div>`;
}

function edgeTable(rows) {
  return `<div class="table-wrap"><table><thead><tr><th>Game</th><th>Bet</th><th>Book</th><th class="n">Price</th><th class="n">Model</th><th class="n">No-vig mkt</th><th class="n">Fair</th><th class="n">Edge</th><th class="n">EV</th><th class="n">EV p10</th><th>Goalies</th><th>Tier</th></tr></thead><tbody>
  ${rows.map(e => `<tr class="click" data-edge="${esc(e.id)}"><td>${esc(e.game)}</td><td><b>${esc(e.label)}</b></td><td>${esc(e.book)}</td>
  <td class="n">${fmtAm(e.price)}</td><td class="n">${pct(e.p_win)}</td><td class="n">${pct(e.p_market_novig)}</td><td class="n">${fmtAm(e.fair_american)}</td>
  <td class="n">${pp(e.prob_edge)}</td><td class="n ${evCls(e.ev)}">${roi(e.ev)}</td><td class="n ${evCls(e.ev_p10)}">${roi(e.ev_p10)}</td>
  <td>${goalieChip(e.goalie_status)}</td><td>${tierChip(e.tier)}</td></tr>`).join("")}</tbody></table></div>`;
}

function gameCard(g) {
  const m = g.model || {}; const hb = bestPrice(g, "h2h", "home"), ab = bestPrice(g, "h2h", "away");
  const live = g.state && !["FUT", "PRE"].includes(g.state);
  const nEdges = (g.edge_ids || []).length;
  const team = (t, p, b) => `<div class="gc-team">${logo(t.logo, t.abbr)}<div><div class="gc-name">${esc(t.name || t.abbr)}</div>
    <div class="gc-sub">${esc(t.goalie?.name || "Goalie unknown")} ${goalieChip(t.goalie?.status)}${t.b2b ? " " + chip("B2B", "warn") : ""}</div></div>
    <div class="gc-prob">${pct(p, 0)}</div><div class="gc-price">${b ? fmtAm(b.price) : "no line"}<br><span class="dim">${b ? esc(b.book) : ""}</span></div></div>`;
  return `<div class="panel game-card" data-id="${g.game_id}">
    <div class="gc-head"><span>${timeLocal(g.start_utc)} · ${esc(g.venue || "")}</span>${live ? chip(g.state, "info") : ""}</div>
    ${team(g.away, m.p_away_ml, ab)}${team(g.home, m.p_home_ml, hb)}
    <div class="probbar"><div style="width:${100 * (m.p_away_ml || 0)}%;background:var(--blue)"></div><div style="flex:1;background:var(--cyan)"></div></div>
    <div class="gc-foot">${chip(`PROJ ${fx(m.exp_away, 1)}–${fx(m.exp_home, 1)}`, "info")}${chip(`TOTAL ${fx(m.exp_total, 2)}`)}${chip(`SOG ${fx(m.shots_away, 0)}–${fx(m.shots_home, 0)}`)}
    ${nEdges ? chip(`${nEdges} candidate${nEdges > 1 ? "s" : ""}`, "warn") : ""}${g.odds?.rows?.length ? "" : chip("NO ODDS", "bad")}</div>
    ${(g.picks || []).length ? `<div class="gc-picks"><span class="dim small">PICKS</span> ${picksRow(g)}</div>` : ""}</div>`;
}

/* ---------------- Game Analyzer ---------------- */
function gameSelect(cur, target) {
  const gs = S.data.slate?.games || [];
  return `<label class="ctl">Game<select id="game-sel">${gs.map(g => `<option value="${g.game_id}" ${String(g.game_id) === String(cur) ? "selected" : ""}>${esc(gameTitle(g))} · ${timeLocal(g.start_utc)}</option>`).join("")}</select></label>`;
}
function bindGameSel(target) { const s = $("#game-sel"); if (s) s.onchange = () => location.hash = `#/${target}/${s.value}`; }

function pageGame(app, id) {
  const gs = S.data.slate?.games || [];
  if (!gs.length) { app.innerHTML = empty("NO GAMES TODAY", "Nothing to analyze on this slate."); return; }
  const g = gameById(id) || gs[0]; const m = g.model || {};
  const iv = m.interval || {};
  app.innerHTML = `
  <div class="page-head"><div><h1>${esc(g.away.name)} @ ${esc(g.home.name)}</h1><p>${dtLocal(g.start_utc)} · ${esc(g.venue || "")} · game ${g.game_id}</p></div>
  <div class="controls">${gameSelect(g.game_id)}${bookOptions()}</div></div>
  ${m.p_home_ml == null ? `<div class="notice bad">No model prediction for this game: ${esc(g.model_error || "unavailable")}</div>` : ""}
  <div class="grid g4">
    ${stat(`${g.home.abbr} win (full game)`, pct(m.p_home_ml), `80% band ${pct(iv.p_home_ml?.[0])}–${pct(iv.p_home_ml?.[1])} · fair ${fmtAm(prob2am(m.p_home_ml))}`)}
    ${stat(`${g.away.abbr} win (full game)`, pct(m.p_away_ml), `fair ${fmtAm(prob2am(m.p_away_ml))}`)}
    ${stat("Projected score", `${fx(m.exp_away, 2)}–${fx(m.exp_home, 2)}`, `${g.away.abbr}–${g.home.abbr} · margin ${fx(m.exp_margin, 2)}`)}
    ${stat("Projected total", fx(m.exp_total, 2), `80% band ${fx(iv.exp_total?.[0], 2)}–${fx(iv.exp_total?.[1], 2)}`)}
    ${stat("Regulation 3-way", `${pct(m.p_reg_away, 0)} / ${pct(m.p_reg_tie, 0)} / ${pct(m.p_reg_home, 0)}`, `${g.away.abbr} / tie / ${g.home.abbr}`)}
    ${stat("OT · shootout", `${pct(m.p_ot)} · ${pct(m.p_so)}`, "ties are resolved, never left as a final tie")}
    ${stat(`${g.home.abbr} −1.5 / ${g.away.abbr} −1.5`, `${pct(m.p_home_m15)} / ${pct(m.p_away_m15)}`, `lose by exactly 1: ${g.home.abbr} ${pct(m.margin_dist?.["-1"])} · ${g.away.abbr} ${pct(m.margin_dist?.["1"])}`)}
    ${stat("Projected shots", `${fx(m.shots_away, 1)}–${fx(m.shots_home, 1)}`, "full-game SOG model")}
  </div>
  <div class="panel section"><h2>Model picks</h2>${picksPanel(g)}</div>
  <div class="grid g2 section">
    <div class="panel"><h2>Team form — L5 / L10 / L15 / L20</h2><div class="table-wrap">${formTable(g)}</div></div>
    <div class="panel"><h2>Head-to-head</h2><div class="table-wrap">${h2hTable(g)}</div></div>
  </div>
  <div class="panel section"><h2>Top player projections</h2>${gamePropsMini(g)}</div>
  <div class="grid g2 section">
    <div class="panel"><h2>Final goal margin (${esc(g.home.abbr)} − ${esc(g.away.abbr)})</h2><div class="chart-box"><canvas id="c-margin"></canvas></div></div>
    <div class="panel"><h2>Final total goals</h2><div class="chart-box"><canvas id="c-total"></canvas></div></div>
  </div>
  <div class="grid g2 section">
    <div class="panel"><h2>Goalie matchup</h2>${goalieMatchup(g)}</div>
    <div class="panel"><h2>Most likely final scores</h2><table><thead><tr><th>${esc(g.away.abbr)}</th><th>${esc(g.home.abbr)}</th><th class="n">Prob</th><th class="n">Fair</th></tr></thead><tbody>
      ${(m.top_scorelines || []).map(s => `<tr><td class="mono">${s.away}</td><td class="mono">${s.home}</td><td class="n">${pct(s.p)}</td><td class="n">${fmtAm(prob2am(s.p))}</td></tr>`).join("")}</tbody></table></div>
  </div>
  <div class="panel section"><h2>Every priced market for this game</h2>${gameMarketTable(g)}</div>
  <div class="grid g2 section">
    <div class="panel"><h2>Matchup notes (computed from data)</h2><ul class="ul">${(g.insights || []).map(i => `<li>${esc(i)}</li>`).join("") || "<li class='muted'>none</li>"}</ul></div>
    <div class="panel"><h2>Data used</h2><dl class="kv">${(g.data_quality || []).map(d => `<dt>${esc(d.item)}</dt><dd>${esc(d.value)}</dd>`).join("")}</dl></div>
  </div>`;
  bindGameSel("game"); bindBook(() => pageGame(app, g.game_id));
  app.querySelectorAll("tr[data-edge]").forEach(tr => tr.onclick = () => location.hash = `#/pick/${tr.dataset.edge}`);
  marginChart("c-margin", m.margin_dist, g); totalChart("c-total", m.total_dist, g);
}

function goalieMatchup(g) {
  const row = t => { const gl = t.goalie || {};
    return `<tr><td>${logo(t.logo, t.abbr)}</td><td><b>${esc(gl.name || "Unknown")}</b><div class="small muted">${goalieChip(gl.status)} ${esc(gl.source || "")}</div></td>
      <td class="n">${gl.sv_rating ? fx(gl.sv_rating, 3) : "—"}</td><td class="n">${gl.season_sv ? fx(gl.season_sv, 3) : "—"}</td><td class="n">${fx(gl.season_gsaa, 1)}</td><td class="n">${gl.rest_days ?? "—"}</td></tr>`; };
  const swaps = (g.goalie_swap || []).map(s => `<li>If <b>${esc(s.alt_name)}</b> starts for ${esc(s.team)} instead: ${g.home.abbr} win ${pp(s.d_p_home_ml)}, total ${s.d_exp_total >= 0 ? "+" : ""}${fx(s.d_exp_total, 2)} goals${s.flips ? ` — <span class="amber">flips ${s.flips} candidate(s) to negative EV</span>` : ""}</li>`).join("");
  return `<table><thead><tr><th></th><th>Goalie</th><th class="n">Rating SV%</th><th class="n">Season SV%</th><th class="n">GSAA</th><th class="n">Rest</th></tr></thead>
    <tbody>${row(g.away)}${row(g.home)}</tbody></table>
    <p class="small muted">Rating = shrunk, time-decayed save % used by the model (league ${fx(S.data.goalies?.league_sv, 3)}). GSAA is vs league-average save %, not expected-goals based.</p>
    <h3 style="margin-top:10px">Goalie-change sensitivity</h3><ul class="ul small">${swaps || "<li class='muted'>No alternate goalie identified.</li>"}</ul>`;
}

function gameMarketTable(g) {
  const rows = [];
  const seen = new Set();
  (g.odds?.rows || []).forEach(r => { const k = `${r.market}|${r.side}|${r.point ?? ""}`; if (!seen.has(k)) { seen.add(k); rows.push(r); } });
  if (!rows.length) return empty("NO ODDS AVAILABLE", g.odds?.status_note || "No sportsbook prices were returned for this game.");
  return `<div class="table-wrap"><table><thead><tr><th>Market</th><th class="n">Best</th><th>Book</th><th class="n">Model</th><th class="n">Push</th><th class="n">No-vig mkt</th><th class="n">Fair</th><th class="n">Edge</th><th class="n">EV</th><th class="n">EV p10</th></tr></thead><tbody>
  ${rows.map(r => { const b = bestPrice(g, r.market, r.side, r.point); const mp = modelProb(g, r.market, r.side, r.point) || {};
    if (!b) return "";
    const ev = evOf(mp.p, mp.push, b.price); const be = (1 - (mp.push || 0)) / am2dec(b.price);
    const e = (g.edge_rows || []).find(x => x.market === r.market && x.side === r.side && x.point === r.point && x.book === b.book);
    return `<tr ${e ? `class="click" data-edge="${esc(e.id)}"` : ""}><td>${esc(marketLabel(r.market, r.side, r.point, g))}</td><td class="n">${fmtAm(b.price)}</td><td>${esc(b.book)}</td>
    <td class="n">${pct(mp.p)}</td><td class="n">${mp.push ? pct(mp.push) : "—"}</td><td class="n">${pct(mp.novig)}</td><td class="n">${fmtAm(mp.fair)}</td>
    <td class="n">${pp(isNum(mp.p) ? mp.p - be : NaN)}</td><td class="n ${evCls(ev)}">${roi(ev)}</td><td class="n ${evCls(e?.ev_p10)}">${e ? roi(e.ev_p10) : "—"}</td></tr>`; }).join("")}
  </tbody></table></div><p class="small muted">Edge = model win probability − break-even probability (percentage points). EV = expected profit per unit staked (percent). Pushes on whole-number lines are priced in. No-vig = multiplicative margin removal over both sides at the same book, median across books.</p>`;
}

function marginChart(id, dist, g) {
  if (!dist) return; const ks = Object.keys(dist).map(Number).sort((a, b) => a - b);
  chart(id, { type: "bar", data: { labels: ks.map(k => k > 0 ? "+" + k : k), datasets: [{ label: "P(final margin)", data: ks.map(k => dist[k]), backgroundColor: ks.map(k => k > 0 ? "#22d3ee" : "#3b82f6") }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => pct(c.raw) } } }, scales: { y: { ticks: { callback: v => pct(v, 0) } }, x: { title: { display: true, text: `${g.home.abbr} wins by → / ← ${g.away.abbr} wins by` } } } } });
}
function totalChart(id, dist, g, line) {
  if (!dist) return; const labels = dist.map((_, i) => i).slice(0, 15);
  chart(id, { type: "bar", data: { labels, datasets: [{ label: "P(total)", data: dist.slice(0, 15), backgroundColor: labels.map(i => line != null && i > line ? "#22c55e" : "#3b82f6") }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => pct(c.raw) } } }, scales: { y: { ticks: { callback: v => pct(v, 0) } } } } });
}

/* ---------------- Pick analysis ---------------- */
function findEdge(id) {
  const ed = S.data.edges || {};
  return [...(ed.qualified || []), ...(ed.candidates || []), ...(ed.all || [])].find(e => e.id === id);
}
function pagePick(app, id) {
  const e = findEdge(id); if (!e) { app.innerHTML = empty("BET NOT FOUND", "It may have been removed after the latest odds refresh."); return; }
  const g = gameById(e.game_id) || { home: {}, away: {}, model: {} };
  app.innerHTML = `
  <div class="page-head"><div><h1>${esc(e.game)} — ${esc(e.label)}</h1><p>${e.qualified ? chip("QUALIFIED", "ok") : chip("NOT QUALIFIED", "warn")} ${tierChip(e.tier)} · odds ${esc(e.book)} updated ${ago(e.odds_updated)}</p></div><a href="#/game/${e.game_id}">Full game analysis →</a></div>
  <div class="grid g4">
    ${stat("Sportsbook odds", fmtAm(e.price), `${esc(e.book)} · decimal ${fx(am2dec(e.price), 3)}`)}
    ${stat("Model probability", pct(e.p_win), `80% band ${pct(e.p_win_lo)}–${pct(e.p_win_hi)}${e.p_push ? ` · push ${pct(e.p_push)}` : ""}`)}
    ${stat("Fair odds", fmtAm(e.fair_american), `break-even at this price ${pct(e.break_even)}`)}
    ${stat("Expected ROI", roi(e.ev), `pessimistic (p10) ${roi(e.ev_p10)} · edge ${pp(e.prob_edge)}`, evCls(e.ev))}
    ${stat("No-vig market", pct(e.p_market_novig), e.novig_method || "")}
    ${stat("Goalie status", (e.goalie_status || "UNKNOWN"), esc(e.goalie_detail || ""))}
    ${stat("Goalie-swap EV", roi(e.ev_goalie_swap), e.ev_goalie_swap != null && e.ev_goalie_swap < 0 ? "edge disappears if the other goalie starts" : "")}
    ${stat("¼ Kelly (info only)", pct(e.kelly_quarter, 2), "of bankroll; see Bankroll warnings")}
  </div>
  <div class="grid g2 section">
    <div class="panel"><h2>Why the model prices it this way</h2><ul class="ul">${(e.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>
    <div class="panel"><h2>What could make it lose</h2><ul class="ul">${(e.risks || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div>
  </div>
  <div class="grid g2 section">
    <div class="panel"><h2>${e.market === "totals" ? "Total goals distribution" : "Final margin distribution"}</h2><div class="chart-box"><canvas id="c-pick"></canvas></div></div>
    <div class="panel"><h2>Validation status for this market</h2>${validationBlurb(e.market)}</div>
  </div>`;
  if (e.market === "totals") totalChart("c-pick", g.model?.total_dist, g, e.point); else marginChart("c-pick", g.model?.margin_dist, g);
}

function validationBlurb(market) {
  const me = S.data.market_eval || {}; const t = me.tiers?.[market] || {};
  return `<p>${tierChip(t.tier)} ${esc(t.reason || "No validation evidence recorded yet.")}</p>
  <dl class="kv"><dt>Backtest (vs simple baseline)</dt><dd>${esc(t.backtest || "—")}</dd><dt>Live model vs closing market</dt><dd>${esc(t.market || "—")}</dd><dt>Closing-line value of logged candidates</dt><dd>${esc(t.clv || "—")}</dd></dl>`;
}

/* ---------------- Moneyline / Puck-line / Totals labs ---------------- */
function labTable(cols, rows) {
  return `<div class="table-wrap"><table class="st"><thead><tr>${cols.map(c => `<th class="sortable ${c.n ? "n" : ""}">${c.t}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function evCell(g, market, side, point) {
  const b = bestPrice(g, market, side, point); const mp = modelProb(g, market, side, point) || {};
  if (!b) return `<td class="n dim">no line</td><td class="n">—</td>`;
  const ev = evOf(mp.p, mp.push, b.price);
  return `<td class="n" data-v="${b.price}">${fmtAm(b.price)} <span class="dim small">${esc(b.book)}</span></td><td class="n ${evCls(ev)}" data-v="${ev}">${roi(ev)}</td>`;
}
function pageMoneyline(app) {
  const gs = S.data.slate?.games || [];
  app.innerHTML = `<div class="page-head"><div><h1>Moneyline Lab</h1><p>Full-game win probability includes overtime and shootout. Regulation 3-way probabilities are shown separately; a regulation tie is never treated as a final tie.</p></div><div class="controls">${bookOptions()}</div></div>
  ${freshness()}
  <div class="panel">${labTable([{ t: "Game" }, { t: "Reg A/T/H" }, { t: "OT" , n: 1}, { t: "SO", n: 1 }, { t: "Away win", n: 1 }, { t: "Fair", n: 1 }, { t: "Best", n: 1 }, { t: "EV", n: 1 }, { t: "Home win", n: 1 }, { t: "Fair", n: 1 }, { t: "Best", n: 1 }, { t: "EV", n: 1 }],
    gs.map(g => { const m = g.model || {}; return `<tr class="click" onclick="location.hash='#/game/${g.game_id}'"><td>${esc(gameTitle(g))}</td><td class="mono">${pct(m.p_reg_away, 0)}/${pct(m.p_reg_tie, 0)}/${pct(m.p_reg_home, 0)}</td>
      <td class="n">${pct(m.p_ot)}</td><td class="n">${pct(m.p_so)}</td><td class="n" data-v="${m.p_away_ml}">${pct(m.p_away_ml)}</td><td class="n">${fmtAm(prob2am(m.p_away_ml))}</td>${evCell(g, "h2h", "away")}
      <td class="n" data-v="${m.p_home_ml}">${pct(m.p_home_ml)}</td><td class="n">${fmtAm(prob2am(m.p_home_ml))}</td>${evCell(g, "h2h", "home")}</tr>`; }))}</div>
  <div class="panel section"><h2>Model vs no-vig market (home win)</h2><div class="chart-box tall"><canvas id="c-ml"></canvas></div></div>`;
  app.querySelectorAll("table.st").forEach(sortableTable); bindBook(() => pageMoneyline(app));
  const lab = gs.map(gameTitle);
  chart("c-ml", { type: "bar", data: { labels: lab, datasets: [
    { label: "Model", data: gs.map(g => g.model?.p_home_ml ?? null), backgroundColor: "#22d3ee" },
    { label: "No-vig market", data: gs.map(g => g.model?.markets?.["h2h|home|"]?.novig ?? null), backgroundColor: "#3b82f6" }] },
    options: { scales: { y: { min: 0, max: 1, ticks: { callback: v => pct(v, 0) } } }, plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${pct(c.raw)}` } } } } });
}

function pagePuckline(app, id) {
  const gs = S.data.slate?.games || [];
  app.innerHTML = `<div class="page-head"><div><h1>Puck-Line Lab</h1><p>Win-by-2+ probabilities come from the full final-margin distribution, including the empty-net effect (estimated from data) and the fact that OT/shootout wins are always by exactly one goal.</p></div><div class="controls">${bookOptions()}</div></div>
  ${freshness()}
  <div class="panel">${labTable([{ t: "Game" }, { t: "Exp margin", n: 1 }, { t: "Away −1.5", n: 1 }, { t: "Fair", n: 1 }, { t: "Best", n: 1 }, { t: "EV", n: 1 }, { t: "Home −1.5", n: 1 }, { t: "Fair", n: 1 }, { t: "Best", n: 1 }, { t: "EV", n: 1 }, { t: "Home loses by 1", n: 1 }],
    gs.map(g => { const m = g.model || {}; return `<tr class="click" data-g="${g.game_id}"><td>${esc(gameTitle(g))}</td><td class="n">${fx(m.exp_margin, 2)}</td>
      <td class="n">${pct(m.p_away_m15)}</td><td class="n">${fmtAm(prob2am(m.p_away_m15))}</td>${evCell(g, "spreads", "away", -1.5)}
      <td class="n">${pct(m.p_home_m15)}</td><td class="n">${fmtAm(prob2am(m.p_home_m15))}</td>${evCell(g, "spreads", "home", -1.5)}<td class="n">${pct(m.margin_dist?.["-1"])}</td></tr>`; }))}
  <p class="small muted">+1.5 sides are the complements (e.g. Home +1.5 = 1 − Away −1.5). Alternate lines appear in the Game Analyzer when a book posts them.</p></div>
  <div class="panel section"><div class="controls">${gameSelect(id || gs[0]?.game_id)}</div><div class="chart-box tall"><canvas id="c-pl"></canvas></div></div>`;
  app.querySelectorAll("table.st").forEach(sortableTable); bindBook(() => pagePuckline(app, id)); bindGameSel("puckline");
  app.querySelectorAll("tr[data-g]").forEach(tr => tr.onclick = () => location.hash = `#/puckline/${tr.dataset.g}`);
  const g = gameById(id) || gs[0]; if (g) marginChart("c-pl", g.model?.margin_dist, g);
}

function pageTotals(app, id) {
  const gs = S.data.slate?.games || []; const L = [4.5, 5.5, 6.5, 7.5];
  app.innerHTML = `<div class="page-head"><div><h1>Totals Lab</h1><p>Full-game totals include OT and the shootout-winner goal, matching standard sportsbook settlement. Whole-number lines carry an explicit push probability.</p></div><div class="controls">${bookOptions()}</div></div>
  ${freshness()}
  <div class="panel">${labTable([{ t: "Game" }, { t: "Exp total", n: 1 }, { t: "80% band", n: 1 }, ...L.map(l => ({ t: `O${l}`, n: 1 })), { t: "Book line", n: 1 }, { t: "Over best", n: 1 }, { t: "EV", n: 1 }, { t: "Under best", n: 1 }, { t: "EV", n: 1 }],
    gs.map(g => { const m = g.model || {}; const line = g.odds?.main_total;
      return `<tr class="click" data-g="${g.game_id}"><td>${esc(gameTitle(g))}</td><td class="n">${fx(m.exp_total, 2)}</td><td class="n small">${fx(m.interval?.exp_total?.[0], 2)}–${fx(m.interval?.exp_total?.[1], 2)}</td>
      ${L.map(l => `<td class="n">${pct(m.totals?.[l]?.over)}</td>`).join("")}<td class="n">${line ?? "—"}</td>
      ${line != null ? evCell(g, "totals", "over", line) + evCell(g, "totals", "under", line) : `<td class="n dim">—</td><td class="n">—</td><td class="n dim">—</td><td class="n">—</td>`}</tr>`; }))}</div>
  <div class="panel section"><div class="controls">${gameSelect(id || gs[0]?.game_id)}</div><div class="chart-box tall"><canvas id="c-tot"></canvas></div></div>`;
  app.querySelectorAll("table.st").forEach(sortableTable); bindBook(() => pageTotals(app, id)); bindGameSel("totals");
  app.querySelectorAll("tr[data-g]").forEach(tr => tr.onclick = () => location.hash = `#/totals/${tr.dataset.g}`);
  const g = gameById(id) || gs[0]; if (g) totalChart("c-tot", g.model?.total_dist, g, g.odds?.main_total);
}

/* ---------------- Player props ---------------- */
const STAT_LABEL = { sog: "Shots on goal", points: "Points", goals: "Goals", assists: "Assists", saves: "Goalie saves" };
const STAT_COL = { goals: 2, assists: 3, points: 4, sog: 5 };           // index in skater log rows
const P = () => S.data.props || {};
const pOver = (pmf, line) => { let o = 0, pu = 0; pmf.forEach((p, k) => { if (k > line + 1e-9) o += p; else if (Math.abs(k - line) < 1e-9) pu += p; }); return [o, pu]; };
const statVals = (pl, stat, rows) => stat === "saves" ? rows.map(r => r[3]) : rows.map(r => r[STAT_COL[stat]]);
const hr = (vals, line) => vals.length ? vals.filter(v => v > line).length / vals.length : null;
const hrCell = (vals, line) => { const h = hr(vals, line); return `<td class="n ${h == null ? "dim" : h >= 0.6 ? "pos" : h <= 0.4 ? "neg" : ""}" data-v="${h ?? -1}">${h == null ? "—" : `${Math.round(100 * h)}%<span class="dim small"> ${vals.filter(v => v > line).length}/${vals.length}</span>`}</td>`; };
const fairOf = p => (p > 0 && p < 1) ? fmtAm(prob2am(p)) : "—";
function propPlayers(stat) { return stat === "saves" ? (P().goalies || []) : (P().players || []); }
function propPmf(pl, stat) { return stat === "saves" ? pl.pmf : pl.pmf?.[stat]; }
function propOdds(pid, stat, line) { return (P().odds || []).concat(S.browserProps || []).filter(o => o.player_id === pid && o.stat === stat && (line == null || o.line === line)); }
function bestPropPrice(pid, stat, line, side) { const xs = propOdds(pid, stat, line).filter(o => o.side === side); return xs.length ? xs.reduce((a, b) => am2dec(b.price) > am2dec(a.price) ? b : a) : null; }
function propEV(pl, stat, line, side, price) { const [o, pu] = pOver(propPmf(pl, stat), line); const pw = side === "over" ? o : 1 - o - pu; return { pw, pu, ev: pw * (am2dec(price) - 1) - (1 - pw - pu) }; }
function defaultLine(stat) { return { sog: 2.5, points: 0.5, goals: 0.5, assists: 0.5, saves: 26.5 }[stat]; }

function pageProps(app) {
  const pr = P();
  if (!pr.available) { app.innerHTML = `<div class="page-head"><div><h1>Player Props Lab</h1></div></div>${empty("PROPS UNAVAILABLE", esc(pr.reason || "Props have not been generated yet — the next pipeline run creates them."))}`; return; }
  const st = JSON.parse(localStorage.getItem("np_props") || "{}");
  const stat = st.stat || "sog", line = st.line ?? defaultLine(stat), gameF = st.game || "", teamF = st.team || "", q = st.q || "";
  const lines = stat === "saves" ? pr.saves_lines : pr.lines[stat];
  const games = S.data.slate?.games || [];
  const tierP = (S.data.meta?.model?.prop_tiers || {})[stat];
  const bt = pr.backtest?.available ? (stat === "saves" ? pr.backtest.saves?.lines?.[String(line)] : pr.backtest.stats?.[stat]?.lines?.[String(line)]) : null;
  const edges = (pr.edges || []).concat((S.browserPropEdges || []));
  app.innerHTML = `<div class="page-head"><div><h1>Player Props Lab</h1><p>Projected shots, goals, assists, points and goalie saves for every projected skater and starter tonight, with L5 / L10 / L15 / L20 / season / H2H hit rates and the model's probability for any line.</p></div></div>
  <div class="notice info">${esc(pr.lineup_note)} Model ${tierP ? "beats the last-10-games hit rate out of sample" : "not yet validated"} for ${esc(STAT_LABEL[stat].toLowerCase())}${bt ? ` (line ${line}: log loss ${fx(bt.model_ll, 4)} vs L10 hit rate ${fx(bt.base_ll, 4)})` : ""}. <a href="#/backtest">Details →</a></div>
  <div class="panel"><h2>Prop value board</h2>${propEdgeTable(edges)}</div>
  <div class="panel section"><div class="controls">
    <label class="ctl">Stat<select id="pp-stat">${Object.entries(STAT_LABEL).map(([k, v]) => `<option value="${k}" ${k === stat ? "selected" : ""}>${v}</option>`).join("")}</select></label>
    <label class="ctl">Line<select id="pp-line">${lines.map(l => `<option ${l === line ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <label class="ctl">Game<select id="pp-game"><option value="">All games</option>${games.map(g => `<option value="${g.game_id}" ${String(g.game_id) === gameF ? "selected" : ""}>${esc(gameTitle(g))}</option>`).join("")}</select></label>
    <label class="ctl">Team<input type="text" id="pp-team" value="${esc(teamF)}" placeholder="TOR" style="width:70px"></label>
    <label class="ctl">Player<input type="text" id="pp-q" value="${esc(q)}" placeholder="search"></label>
  </div><div class="table-wrap" id="pp-tab"></div>
  <p class="small muted">Over % = model probability the player goes over the line (negative binomial on the projected mean). Hit-rate columns count past games over the line — they are context; the backtest shows the model's probability is more accurate than any single window. H2H = his last ≤10 games vs tonight's opponent. Click a player for the full breakdown.</p></div>`;
  const save = () => { localStorage.setItem("np_props", JSON.stringify({ stat: $("#pp-stat").value, line: +$("#pp-line").value, game: $("#pp-game").value, team: $("#pp-team").value.toUpperCase(), q: $("#pp-q").value })); };
  $("#pp-stat").onchange = () => { localStorage.setItem("np_props", JSON.stringify({ ...JSON.parse(localStorage.getItem("np_props") || "{}"), stat: $("#pp-stat").value, line: defaultLine($("#pp-stat").value) })); pageProps(app); };
  ["#pp-line", "#pp-game"].forEach(s => $(s).onchange = () => { save(); pageProps(app); });
  ["#pp-team", "#pp-q"].forEach(s => $(s).oninput = () => { save(); draw(); });
  const draw = () => {
    const tf = $("#pp-team").value.toUpperCase().trim(), qq = $("#pp-q").value.toLowerCase().trim(), gf = $("#pp-game").value;
    const rows = propPlayers(stat).filter(p => (!gf || String(p.game_id) === gf) && (!tf || p.team.includes(tf)) && (!qq || p.name.toLowerCase().includes(qq)));
    const sorted = rows.map(p => ({ p, o: pOver(propPmf(p, stat), line)[0] })).sort((a, b) => b.o - a.o);
    $("#pp-tab").innerHTML = `<table class="st"><thead><tr>${["Player", "Matchup", "Proj", "Over %", "Fair", "Best over", "EV", "L5", "L10", "L15", "L20", "Season", "H2H"].map((t, i) => `<th class="sortable ${i > 1 ? "n" : ""}">${t}</th>`).join("")}</tr></thead><tbody>
    ${sorted.slice(0, 400).map(({ p, o }) => {
      const logs = p.logs || []; const vals = statVals(p, stat, logs).reverse();   // most recent first
      const seasonVals = statVals(p, stat, logs.filter(r => r[0] >= (S.data.slate?.season_start || "0000"))).reverse();
      const h2h = stat === "saves" ? (p.h2h || []).map(r => r[2]) : (p.h2h || []).map(r => r[{ goals: 1, assists: 2, points: 3, sog: 4 }[stat]]);
      const proj = stat === "saves" ? p.saves_proj : p.proj?.[stat];
      const bo = bestPropPrice(p.id, stat, line, "over"); const ev = bo ? propEV(p, stat, line, "over", bo.price).ev : NaN;
      const seasonHits = stat === "saves" ? null : p.season;
      return `<tr class="click" data-pid="${p.id}"><td><b>${esc(p.name)}</b> <span class="dim small">${esc(p.pos || "G")}</span>${p.status && p.status !== "PROJECTED" ? " " + goalieChip(p.status) : ""}</td>
      <td>${esc(p.team)} ${p.home === false ? "@" : "vs"} ${esc(p.opp)}</td><td class="n">${fx(proj, 2)}</td><td class="n" data-v="${o}"><b>${pct(o)}</b></td><td class="n">${fairOf(o)}</td>
      <td class="n" data-v="${bo ? bo.price : ""}">${bo ? `${fmtAm(bo.price)} <span class="dim small">${esc(bo.book)}</span>` : "<span class='dim'>—</span>"}</td><td class="n ${evCls(ev)}" data-v="${isNum(ev) ? ev : -9}">${roi(ev)}</td>
      ${hrCell(vals.slice(0, 5), line)}${hrCell(vals.slice(0, 10), line)}${hrCell(vals.slice(0, 15), line)}${hrCell(vals.slice(0, 20), line)}
      ${stat === "saves" ? `<td class="n dim">—</td>` : `<td class="n">${seasonHits?.gp ? fx(seasonHits[stat] / seasonHits.gp, 2) + "/g" : "—"}</td>`}${hrCell(h2h, line)}</tr>`; }).join("")}</tbody></table>`;
    sortableTable($("#pp-tab"));
    app.querySelectorAll("tr[data-pid]").forEach(tr => tr.onclick = () => location.hash = `#/player/${tr.dataset.pid}`);
  };
  draw();
  app.querySelectorAll("tr[data-pe]").forEach(tr => tr.onclick = () => location.hash = `#/player/${tr.dataset.pe}`);
  const lb = $("#pp-loadodds"); if (lb) lb.onclick = loadBrowserProps;
}

function propEdgeTable(edges) {
  const haveOdds = (P().odds || []).length || (S.browserProps || []).length;
  const key = !DEMO && getKey();
  const nGames = (S.data.slate?.games || []).filter(g => g.odds?.event_id && ["FUT", "PRE", undefined, null].includes(g.state)).length;
  const loadBtn = key && nGames ? `<button class="btn ghost" id="pp-loadodds">Load prop odds with my key (~${nGames * 4} credits)</button>` : "";
  if (!haveOdds) return `${empty("NO PROP PRICES LOADED", `Projections and hit rates below work without odds. EV needs sportsbook prop prices: the pipeline fetches them once a day when your Odds API key has enough credits (≈4 credits per game). ${key ? "" : 'Add your key on the <a href="#/keys">Odds API Key</a> page to load them yourself.'}`)}<div class="controls" style="margin-top:8px">${loadBtn}</div>`;
  if (!edges.length) return `${empty("NO PROP VALUE FOUND", "No prop price beats the model by at least the EV threshold under the pessimistic (±15% projection) check.")}<div class="controls" style="margin-top:8px">${loadBtn}</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Player</th><th>Bet</th><th>Book</th><th class="n">Price</th><th class="n">Proj</th><th class="n">Model</th><th class="n">Fair</th><th class="n">EV</th><th class="n">EV if 15% off</th><th>Tier</th></tr></thead><tbody>
  ${edges.slice(0, 20).map(e => `<tr class="click" data-pe="${e.player_id}"><td><b>${esc(e.player)}</b> <span class="dim small">${esc(e.team)} vs ${esc(e.opp)}</span></td><td>${e.side === "over" ? "Over" : "Under"} ${e.line} ${esc(STAT_LABEL[e.stat])}</td><td>${esc(e.book)}</td>
  <td class="n">${fmtAm(e.price)}</td><td class="n">${fx(e.proj, 2)}</td><td class="n">${pct(e.p_win)}</td><td class="n">${fmtAm(e.fair)}</td><td class="n ${evCls(e.ev)}">${roi(e.ev)}</td><td class="n ${evCls(e.ev_lo)}">${roi(e.ev_lo)}</td><td>${tierChip(e.tier)}</td></tr>`).join("")}</tbody></table></div>
  <p class="small muted">Showing the top ${Math.min(20, edges.length)} of ${edges.length}. Prices from ${esc(dtLocal(edges[0]?.odds_updated))}. Unvalidated until logged prop bets beat closing prices. Lineups are projected; a scratch voids most books' props but check your book's rules.</p><div class="controls">${loadBtn}</div>`;
}

async function loadBrowserProps() {
  const key = getKey(); if (!key) { location.hash = "#/keys"; return; }
  const btn = $("#pp-loadodds"); if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  const markets = "player_shots_on_goal,player_points,player_goal_scorer_anytime,player_total_saves";
  const MAP = { player_shots_on_goal: "sog", player_points: "points", player_assists: "assists", player_goals: "goals", player_goal_scorer_anytime: "goals", player_total_saves: "saves" };
  const out = [], edges = []; let left = null, err = null; const now = new Date().toISOString();
  for (const g of (S.data.slate?.games || []).filter(g => g.odds?.event_id && ["FUT", "PRE", undefined, null].includes(g.state))) {
    try {
      const r = await fetch(`https://api.the-odds-api.com/v4/sports/icehockey_nhl/events/${g.odds.event_id}/odds?apiKey=${encodeURIComponent(key)}&regions=us&markets=${markets}&oddsFormat=american`);
      if (!r.ok) { err = `HTTP ${r.status}`; continue; } left = r.headers.get("x-requests-remaining");
      const ev = await r.json();
      for (const bk of ev.bookmakers || []) for (const mk of bk.markets || []) { const stat = MAP[mk.key]; if (!stat) continue;
        for (const o of mk.outcomes || []) { const nm = String(o.name).toLowerCase(); const side = nm === "under" || nm === "no" ? "under" : "over";
          const player = ["over", "under", "yes", "no"].includes(nm) ? o.description : o.name; const line = o.point ?? 0.5;
          const pool = stat === "saves" ? (P().goalies || []).filter(x => x.game_id === g.game_id) : (P().players || []).filter(x => x.game_id === g.game_id);
          const pl = matchName(player, pool); if (!pl) continue;
          const row = { player_id: pl.id, player: pl.name, team: pl.team, opp: pl.opp, stat, side, line, book: bk.title, price: o.price, odds_updated: now };
          out.push(row);
          const { pw, pu, ev: e } = propEV(pl, stat, line, side, o.price);
          if (e >= (S.data.edges?.thresholds?.min_ev ?? 0.02)) edges.push({ ...row, p_win: pw, p_push: pu, ev: e, ev_lo: NaN, fair: prob2am(pw), proj: stat === "saves" ? pl.saves_proj : pl.proj[stat], tier: "EXPERIMENTAL" });
        } }
    } catch (e) { err = e.message; }
  }
  S.browserProps = out; S.browserPropEdges = edges.sort((a, b) => b.ev - a.ev);
  route();
  setTimeout(() => { const n = document.createElement("div"); n.className = "notice info"; n.textContent = `Loaded ${out.length} prop prices in this browser${left ? ` · ${left} credits left` : ""}${err ? ` · some requests failed (${err})` : ""}. Browser-loaded prices are not archived or used for validation.`; $("#app").prepend(n); }, 50);
}
function matchName(name, pool) {
  const n = nameNorm(name); let m = pool.find(p => nameNorm(p.name) === n); if (m) return m;
  const parts = n.split(" "); const c = pool.filter(p => { const q = nameNorm(p.name).split(" "); return q[q.length - 1] === parts[parts.length - 1] && q[0][0] === parts[0][0]; });
  return c.length === 1 ? c[0] : null;
}

function pagePlayer(app, id) {
  const pid = Number(id); const pl = (P().players || []).find(p => p.id === pid); const gk = (P().goalies || []).find(p => p.id === pid);
  const x = pl || gk; if (!x) { app.innerHTML = empty("PLAYER NOT ON TONIGHT'S SLATE", "Only projected skaters and starting goalies for today's games are listed."); return; }
  const isG = !pl; const stats = isG ? ["saves"] : ["sog", "points", "goals", "assists"];
  const st = JSON.parse(localStorage.getItem("np_pstat") || "{}"); let stat = stats.includes(st.stat) ? st.stat : stats[0]; let line = st.stat === stat && st.line != null ? st.line : defaultLine(stat);
  const g = gameById(x.game_id);
  const lines = isG ? P().saves_lines : P().lines[stat];
  const logs = (x.logs || []).slice().reverse(); const vals = statVals(x, stat, logs);
  const h2hVals = isG ? (x.h2h || []).map(r => r[2]) : (x.h2h || []).map(r => r[{ goals: 1, assists: 2, points: 3, sog: 4 }[stat]]);
  const pmf = propPmf(x, stat); const [o, pu] = pOver(pmf, line);
  const coefs = isG ? [] : (P().models?.[stat]?.coefs || []);
  app.innerHTML = `<div class="page-head"><div><h1>${esc(x.name)} <span class="muted" style="font-weight:400">${esc(x.team)} · ${esc(x.pos || "G")}</span></h1>
  <p>${g ? `${esc(gameTitle(g))} · ${timeLocal(g.start_utc)}` : ""} · ${isG ? `${goalieChip(x.status)} ${esc(x.source || "")}` : `lineup ${chip("PROJECTED", "warn")} (played ${esc(x.team)}'s last game)`}</p></div><a href="#/props">← all props</a></div>
  <div class="grid g4">${isG ? `${stat_("Projected saves", fx(x.saves_proj, 1), `shots faced ${fx(x.shots_proj, 1)} × save % ${fx(x.sv_proj, 3)}`)}${stat_("Save % (shrunk, pre-game)", fx(x.sv_proj, 3), "time-decayed, regressed to league")}` :
    stats.map(s => stat_(STAT_LABEL[s], fx(x.proj[s], 2), `per game · base ${fx(x.factors?.base?.[s], 2)}`)).join("")}
    ${isG ? "" : stat_("Projected TOI", fx(x.toi_proj, 1) + " min", "recency-weighted")}${isG ? "" : stat_("Opponent shot suppression", fx(x.factors?.opp_sa, 3), ">1 = allows more shots than average")}</div>
  <div class="panel section"><div class="controls">${stats.length > 1 ? `<label class="ctl">Stat<select id="pl-stat">${stats.map(s => `<option value="${s}" ${s === stat ? "selected" : ""}>${STAT_LABEL[s]}</option>`).join("")}</select></label>` : ""}
  <label class="ctl">Line<select id="pl-line">${lines.map(l => `<option ${l === line ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
  <div class="grid g4">${stat_(`Model over ${line}`, pct(o), `fair ${fairOf(o)}${pu ? ` · push ${pct(pu)}` : ""}`)}${stat_(`Model under ${line}`, pct(1 - o - pu), `fair ${fairOf(1 - o - pu)}`)}
  ${["L5", "L10", "L15", "L20"].map((w, i) => { const n = [5, 10, 15, 20][i]; const h = hr(vals.slice(0, n), line); return stat_(`${w} hit rate`, h == null ? "—" : pct(h, 0), `${vals.slice(0, n).filter(v => v > line).length}/${Math.min(n, vals.length)} games · avg ${fx(vals.slice(0, n).reduce((a, b) => a + b, 0) / Math.max(1, Math.min(n, vals.length)), 2)}`); }).join("")}
  ${stat_(`H2H vs ${x.opp}`, h2hVals.length ? pct(hr(h2hVals, line), 0) : "—", `${h2hVals.filter(v => v > line).length}/${h2hVals.length} games · avg ${h2hVals.length ? fx(h2hVals.reduce((a, b) => a + b, 0) / h2hVals.length, 2) : "—"}`)}
  ${isG ? "" : stat_("Season", x.season?.gp ? fx(x.season[stat] / x.season.gp, 2) + "/g" : "—", `${x.season?.gp || 0} GP · last season ${x.prev_season?.gp ? fx(x.prev_season[stat] / x.prev_season.gp, 2) + "/g" : "—"}`)}</div>
  ${propPriceTable(x, stat)}</div>
  <div class="grid g2 section"><div class="panel"><h2>Last ${vals.length} games vs line ${line}</h2><div class="chart-box"><canvas id="c-pl-log"></canvas></div></div>
  <div class="panel"><h2>Model distribution tonight</h2><div class="chart-box"><canvas id="c-pl-pmf"></canvas></div></div></div>
  <div class="grid g2 section"><div class="panel"><h2>Game log</h2><div class="table-wrap">${isG ? `<table><thead><tr><th>Date</th><th>Opp</th><th class="n">SA</th><th class="n">SV</th><th class="n">GA</th><th class="n">SV%</th><th class="n">TOI</th></tr></thead><tbody>${logs.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td class="n">${r[2]}</td><td class="n ${r[3] > line ? "pos" : ""}">${r[3]}</td><td class="n">${r[4]}</td><td class="n">${r[2] ? fx(r[3] / r[2], 3) : "—"}</td><td class="n">${fx(r[5], 1)}</td></tr>`).join("")}</tbody></table>` :
    `<table><thead><tr><th>Date</th><th>Opp</th><th class="n">G</th><th class="n">A</th><th class="n">P</th><th class="n">SOG</th><th class="n">TOI</th></tr></thead><tbody>${logs.map(r => `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td>${[2, 3, 4, 5].map(i => `<td class="n ${i === STAT_COL[stat] && r[i] > line ? "pos" : ""}">${r[i]}</td>`).join("")}<td class="n">${fx(r[6], 1)}</td></tr>`).join("")}</tbody></table>`}</div></div>
  <div class="panel"><h2>Head-to-head vs ${esc(x.opp)} (last ${(x.h2h || []).length})</h2>${(x.h2h || []).length ? `<div class="table-wrap">${isG ? `<table><thead><tr><th>Date</th><th class="n">SA</th><th class="n">SV</th><th class="n">GA</th></tr></thead><tbody>${x.h2h.slice().reverse().map(r => `<tr><td>${esc(r[0])}</td><td class="n">${r[1]}</td><td class="n ${r[2] > line ? "pos" : ""}">${r[2]}</td><td class="n">${r[3]}</td></tr>`).join("")}</tbody></table>` :
    `<table><thead><tr><th>Date</th><th class="n">G</th><th class="n">A</th><th class="n">P</th><th class="n">SOG</th><th class="n">TOI</th></tr></thead><tbody>${x.h2h.slice().reverse().map(r => `<tr><td>${esc(r[0])}</td>${[1, 2, 3, 4].map(i => `<td class="n ${i === { goals: 1, assists: 2, points: 3, sog: 4 }[stat] && r[i] > line ? "pos" : ""}">${r[i]}</td>`).join("")}<td class="n">${fx(r[5], 1)}</td></tr>`).join("")}</tbody></table>`}</div>` : `<p class="muted small">No previous meetings in the data.</p>`}
  ${coefs.length ? `<h3 style="margin-top:12px">What drives the ${esc(STAT_LABEL[stat].toLowerCase())} model</h3><table><thead><tr><th>Input</th><th class="n">Weight</th></tr></thead><tbody>${coefs.map(c => `<tr><td class="small">${esc(c.label)}</td><td class="n ${evCls(c.coef)}">${fx(c.coef, 3)} <span class="dim small">±${fx(c.se, 3)}</span></td></tr>`).join("")}</tbody></table>
  <p class="small muted">Fit on all past player-games. Negative weights on "last 5/10/20 vs base" mean hot and cold streaks tend to revert — the model fades them rather than chasing them. H2H has a small positive weight.</p>` : ""}</div></div>`;
  const saveSt = (s, l) => localStorage.setItem("np_pstat", JSON.stringify({ stat: s, line: l }));
  if ($("#pl-stat")) $("#pl-stat").onchange = e => { saveSt(e.target.value, defaultLine(e.target.value)); pagePlayer(app, id); };
  $("#pl-line").onchange = e => { saveSt(stat, +e.target.value); pagePlayer(app, id); };
  const lv = vals.slice().reverse(); const ld = logs.slice().reverse();
  chart("c-pl-log", { type: "bar", data: { labels: ld.map(r => `${r[0].slice(5)} ${r[1]}`), datasets: [{ label: STAT_LABEL[stat], data: lv, backgroundColor: lv.map(v => v > line ? "#22c55e" : "#3b82f6") }] },
    options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { ticks: { maxRotation: 70, minRotation: 45, font: { size: 10 } } } } } });
  const lo = isG ? Math.max(0, Math.floor(x.saves_proj - 14)) : 0, hi = isG ? Math.min(pmf.length, Math.ceil(x.saves_proj + 15)) : pmf.length;
  const ks = []; for (let k = lo; k < hi; k++) ks.push(k);
  chart("c-pl-pmf", { type: "bar", data: { labels: ks, datasets: [{ data: ks.map(k => pmf[k]), backgroundColor: ks.map(k => k > line ? "#22c55e" : "#3b82f6") }] },
    options: { plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => pct(c.raw) } } }, scales: { y: { ticks: { callback: v => pct(v, 0) } } } } });
}
const stat_ = stat;
function propPriceTable(x, stat) {
  const rows = propOdds(x.id, stat, null); if (!rows.length) return `<p class="small muted" style="margin-top:8px">No sportsbook prices loaded for this player/stat.</p>`;
  const keys = [...new Map(rows.map(r => [`${r.side}|${r.line}`, r])).values()].sort((a, b) => a.line - b.line || (a.side > b.side ? 1 : -1));
  return `<div class="table-wrap" style="margin-top:10px"><table><thead><tr><th>Bet</th><th class="n">Best price</th><th>Book</th><th class="n">Model</th><th class="n">Fair</th><th class="n">EV</th></tr></thead><tbody>
  ${keys.map(k => { const b = bestPropPrice(x.id, stat, k.line, k.side); const { pw, ev } = propEV(x, stat, k.line, k.side, b.price);
    return `<tr><td>${k.side === "over" ? "Over" : "Under"} ${k.line}</td><td class="n">${fmtAm(b.price)}</td><td>${esc(b.book)}</td><td class="n">${pct(pw)}</td><td class="n">${fairOf(pw)}</td><td class="n ${evCls(ev)}">${roi(ev)}</td></tr>`; }).join("")}</tbody></table></div>`;
}

function gamePropsMini(g) {
  const pl = (P().players || []).filter(p => p.game_id === g.game_id); const gk = (P().goalies || []).filter(p => p.game_id === g.game_id);
  if (!pl.length) return `<p class="muted small">No player projections for this game.</p>`;
  const top = pl.slice().sort((a, b) => b.proj.sog - a.proj.sog).slice(0, 8);
  return `<div class="table-wrap"><table><thead><tr><th>Player</th><th class="n">SOG</th><th class="n">O2.5 SOG</th><th class="n">Points</th><th class="n">1+ point</th><th class="n">Goal</th><th class="n">Anytime goal</th></tr></thead><tbody>
  ${top.map(p => `<tr class="click" onclick="location.hash='#/player/${p.id}'"><td><b>${esc(p.name)}</b> <span class="dim small">${esc(p.team)}</span></td><td class="n">${fx(p.proj.sog, 2)}</td><td class="n">${pct(pOver(p.pmf.sog, 2.5)[0])}</td><td class="n">${fx(p.proj.points, 2)}</td><td class="n">${pct(pOver(p.pmf.points, 0.5)[0])}</td><td class="n">${fx(p.proj.goals, 2)}</td><td class="n">${pct(pOver(p.pmf.goals, 0.5)[0])}</td></tr>`).join("")}
  ${gk.map(x => `<tr class="click" onclick="location.hash='#/player/${x.id}'"><td><b>${esc(x.name)}</b> <span class="dim small">${esc(x.team)} G</span></td><td class="n" colspan="6">Saves ${fx(x.saves_proj, 1)} (shots ${fx(x.shots_proj, 1)}, SV% ${fx(x.sv_proj, 3)}) · over 26.5 saves ${pct(pOver(x.pmf, 26.5)[0])}</td></tr>`).join("")}</tbody></table></div>
  <p class="small"><a href="#/props">All props for tonight →</a></p>`;
}

function h2hBacktestHTML() {
  const t = S.data.meta?.model?.h2h_test; if (!t) return "";
  const row = (k) => { const v = t[k]; return `<tr><td>${esc(k)}</td><td class="n">${fx(v.coef, 2)}</td><td class="n">${fx(v.improvement, 5)}</td><td class="n">${ciTxt(v.ci)}</td><td>${t.helps?.[k] ? chip("HELPS", "ok") : chip("NO VALUE", "warn")}</td></tr>`; };
  return `<div class="panel section"><h2>Does team head-to-head history help?</h2><table><thead><tr><th>Market</th><th class="n">Best-fit H2H weight</th><th class="n">Log-loss change</th><th class="n">95% CI</th><th>Verdict</th></tr></thead><tbody>${row("moneyline")}${row("over 5.5")}</tbody></table><p class="small muted">${esc(t.note)} H2H results are shown on game pages for context but do not change the probabilities.</p></div>`;
}
function propsBacktestHTML() {
  const b = P().backtest; if (!b?.available) return `<div class="panel section"><h2>Player props backtest</h2>${empty("NOT RUN YET", esc(b?.reason || "Runs with the weekly backtest."))}</div>`;
  const rows = [];
  Object.entries(b.stats).forEach(([s, v]) => Object.entries(v.lines).forEach(([l, r]) => rows.push([`${STAT_LABEL[s]} over ${l}`, r])));
  Object.entries(b.saves?.lines || {}).forEach(([l, r]) => rows.push([`Saves over ${l}`, r]));
  const abl = (s) => { const a = b.stats[s]?.ablations || {}; return Object.entries(a).map(([k, v]) => `<td class="n ${evCls(v.delta_logscore)}">${fx(v.delta_logscore, 5)}<div class="dim small">${ciTxt(v.ci)}</div></td>`).join(""); };
  return `<div class="panel section"><h2>Player props backtest — trained ${esc(b.train_period)}, tested ${esc(b.test_period)}</h2>
  <p class="small muted">${esc(b.n_test.toLocaleString())} out-of-sample player-games (${esc(b.eligibility)}). Baseline = ${esc(b.baseline_desc)}. Lower log loss is better.</p>
  <div class="table-wrap"><table><thead><tr><th>Prop</th><th class="n">Hit rate</th><th class="n">Model LL</th><th class="n">L10 hit-rate LL</th><th class="n">L20 hit-rate LL</th><th class="n">Improvement vs L10</th><th class="n">95% CI</th><th>Verdict</th></tr></thead><tbody>
  ${rows.map(([n, r]) => `<tr><td>${esc(n)}</td><td class="n">${pct(r.hit_rate)}</td><td class="n">${fx(r.model_ll, 4)}</td><td class="n">${fx(r.base_ll, 4)}</td><td class="n">${r.base20_ll != null ? fx(r.base20_ll, 4) : "—"}</td><td class="n ${evCls(r.improvement)}">${fx(r.improvement, 4)}</td><td class="n">${ciTxt(r.ci)}</td><td>${r.ci && r.ci[0] > 0 ? chip("BEATS L10", "ok") : chip("NOT SHOWN", "warn")}</td></tr>`).join("")}</tbody></table></div>
  <h3 style="margin-top:12px">Do recent form, H2H and opponent matter? (out-of-sample log-score gain from including each input)</h3>
  <div class="table-wrap"><table><thead><tr><th>Stat</th><th class="n">L5/L10/L20/season form</th><th class="n">H2H vs opponent</th><th class="n">Opponent &amp; goalie</th></tr></thead><tbody>
  ${["sog", "points", "goals", "assists"].map(s => `<tr><td>${STAT_LABEL[s]}</td>${abl(s)}</tr>`).join("")}</tbody></table></div>
  <p class="small muted">Positive = the model is more accurate with that input. All three help, but by small amounts; the biggest single driver is the player's long-run, ice-time-aware rate. Recent-form weights are negative relative to that base: streaks regress.</p>
  <div class="chart-box tall section"><canvas id="c-cal-props"></canvas></div></div>`;
}

/* ---------------- Game picks ---------------- */
const GRADE_CLS = { BET: "ok", VALUE: "info", LEAN: "", PASS: "bad", "NO PRICE": "" };
function picksRow(g) {
  return (g.picks || []).map(p => `<span class="chip ${GRADE_CLS[p.grade] || ""}" title="${esc(p.title)}: ${esc(p.grade)}"><b>${esc(p.grade === "NO PRICE" ? "LEAN" : p.grade)}</b>&nbsp;${esc(p.label)}${p.price != null ? " " + fmtAm(p.price) : ""}</span>`).join("");
}
function picksPanel(g) {
  if (!(g.picks || []).length) return "";
  return `<div class="grid g3">${g.picks.map(p => `<div class="stat"><div class="k">${esc(p.title)} · <span class="chip ${GRADE_CLS[p.grade] || ""}">${esc(p.grade)}</span></div>
    <div class="v">${esc(p.label)}${p.price != null ? ` <span class="small muted">${fmtAm(p.price)} ${esc(p.book || "")}</span>` : ""}</div>
    <div class="s">Model ${pct(p.p)} · fair ${fmtAm(p.fair)}${p.ev != null ? ` · EV <span class="${evCls(p.ev)}">${roi(p.ev)}</span> (p10 ${roi(p.ev_p10)})` : " · no price yet"}</div>
    <ul class="ul small" style="margin-top:6px">${p.why.map(w => `<li>${esc(w)}</li>`).join("")}</ul></div>`).join("")}</div>
    <p class="small muted">Grades: <b>BET</b> = validated edge · <b>VALUE</b> = positive EV even at the model's pessimistic estimate, not yet validated · <b>LEAN</b> = small positive EV · <b>PASS</b> = no side has positive EV at current prices (model's preferred side shown) · <b>NO PRICE</b> = model side and fair price only. Totals have not beaten a naive baseline in backtests — treat total picks with extra caution.</p>`;
}
function formTable(g) {
  const W = ["L5", "L10", "L15", "L20", "season"];
  const row = (side) => { const t = g[side]; const f = g.form?.[side] || {};
    return `<tr><td><b>${esc(t.abbr)}</b></td>${W.map(w => { const x = f[w] || {}; return `<td class="n">${x.gp ? `${x.w}-${x.gp - x.w}<div class="dim small">${fx(x.gf, 1)}–${fx(x.ga, 1)} · SOG ${fx(x.sf, 0)}–${fx(x.sa, 0)}</div>` : "—"}</td>`; }).join("")}</tr>`; };
  return `<table><thead><tr><th>Team</th>${W.map(w => `<th class="n">${w}</th>`).join("")}</tr></thead><tbody>${row("away")}${row("home")}</tbody></table><p class="small muted">W-L (any result) · goals for–against per game · shots for–against per game. Windows run back into last season when needed.</p>`;
}
function h2hTable(g) {
  const h = g.h2h || {}; if (!h.n) return `<p class="muted small">No meetings in the data.</p>`;
  return `<div class="grid g3" style="margin-bottom:10px">${stat(`${g.home.abbr} record`, `${h.home_wins}-${h.away_wins}`, `last ${h.n} meetings`)}${stat("Avg total", fx(h.avg_total, 1), `over 5.5 in ${pct(h.over_55, 0)}`)}${stat(`Avg margin (${g.home.abbr})`, (h.avg_margin_home >= 0 ? "+" : "") + fx(h.avg_margin_home, 2))}</div>
  <table><thead><tr><th>Date</th><th>Away</th><th class="n">Score</th><th>Home</th><th></th></tr></thead><tbody>${h.games.map(r => `<tr><td>${esc(r.date)}</td><td>${esc(r.away)}</td><td class="n">${r.away_goals}–${r.home_goals}</td><td>${esc(r.home)}</td><td class="small muted">${esc(r.ot)}</td></tr>`).join("")}</tbody></table>
  <p class="small muted">Shown for context. In the walk-forward backtest, adjusting probabilities for past meetings did not improve accuracy (${esc(hhNote())}), so H2H does not change the model's numbers.</p>`;
}
function hhNote() { const t = S.data.meta?.model?.h2h_test; if (!t) return "test not run yet"; const m = t.moneyline; return `best-fit H2H weight ${fx(m.coef, 2)}, change in log loss ${fx(m.improvement, 5)}`; }

/* ---------------- Parlay lab ---------------- */
const BUCKETS = [["600-999", "+600 to +999"], ["1000-1499", "+1000 to +1499"], ["1500-2499", "+1500 to +2499"], ["2500+", "+2500+"], ["sgp", "Same-game"], ["multi", "Multi-game"]];
function pageParlays(app, tab) {
  const P = S.data.parlays || {}; tab = tab || "600-999";
  const minT = Number(localStorage.getItem("np_min") || 600), maxL = Number(localStorage.getItem("np_legs") || P.settings?.max_legs || 4);
  const all = [...(P.qualified || []).map(p => ({ ...p, qualified: true })), ...(P.candidates || [])];
  let list = tab === "sgp" ? [] : tab === "multi" ? all : all.filter(p => p.bucket === tab);
  list = list.filter(p => p.american >= minT && p.n_legs <= maxL);
  app.innerHTML = `<div class="page-head"><div><h1>Parlay Intelligence Lab</h1><p>Searches combinations of priced legs for +600-or-longer parlays and ranks them by pessimistic expected value, not payout.</p></div>
  <div class="controls"><label class="ctl">Minimum payout<select id="p-min">${[600, 1000, 1500, 2000].map(v => `<option value="${v}" ${v === minT ? "selected" : ""}>+${v}</option>`).join("")}</select></label>
  <label class="ctl">Max legs<select id="p-legs">${[2, 3, 4, 5, 6].map(v => `<option ${v === maxL ? "selected" : ""}>${v}</option>`).join("")}</select></label></div></div>
  ${freshness()}
  <div class="grid g4">
    ${stat("Combinations searched", (P.n_combos_searched ?? "—").toLocaleString?.() ?? "—", "more searching = more chance of a lucky-looking combo")}
    ${stat("Qualified parlays", (P.qualified || []).length, `gate: ${esc(P.gate_tier || "—")}`)}
    ${stat("Unvalidated candidates", (P.candidates || []).length, "positive pessimistic EV, model not validated")}
    ${stat("Parlay track record", P.track?.n_settled ?? 0, P.track?.n_settled ? `hit ${pct(P.track.hit_rate)} vs expected ${pct(P.track.expected_hit_rate)}` : "settled logged parlays")}
  </div>
  <div class="notice info">Four different things — never the same: <b>high payout</b> (just long odds) · <b>relatively likely</b> (high joint probability) · <b>positive modeled EV</b> (model says price is too long) · <b>out-of-sample supported</b> (logged candidates have actually beaten closing prices). Only the last earns "qualified".</div>
  <div class="tabs">${BUCKETS.map(([k, t]) => `<button class="tab ${k === tab ? "active" : ""}" data-t="${k}">${t}</button>`).join("")}</div>
  ${tab === "sgp" ? empty("SAME-GAME PARLAYS UNAVAILABLE", esc(P.sgp_note || "Same-game parlays need the sportsbook's actual SGP quote; the connected odds feed does not provide one, so none are priced.")) :
    !list.length ? empty("NO QUALIFIED PARLAY FOUND", `No combination at +${minT} or longer with ≤ ${maxL} legs has positive pessimistic EV from individually positive-EV legs. ${esc(P.no_parlay_reason || "")}`) :
    `<div class="cards">${list.slice(0, 30).map(parlayCard).join("")}</div>`}
  <div class="panel section"><h2>Alert rules (stored in this browser)</h2>${alertForm()}</div>`;
  app.querySelectorAll(".tab").forEach(b => b.onclick = () => location.hash = `#/parlays/${b.dataset.t}`);
  $("#p-min").onchange = e => { localStorage.setItem("np_min", e.target.value); pageParlays(app, tab); };
  $("#p-legs").onchange = e => { localStorage.setItem("np_legs", e.target.value); pageParlays(app, tab); };
  app.querySelectorAll(".pcard").forEach(c => c.onclick = () => location.hash = `#/parlay/${c.dataset.id}`);
  bindAlerts();
}
function parlayCard(p) {
  return `<div class="panel game-card pcard" data-id="${esc(p.id)}"><div class="gc-head"><span>${esc(p.book)} · ${p.n_legs} legs · ${p.type === "sgp" ? "same-game" : "multi-game"}</span>${p.qualified ? chip("QUALIFIED", "ok") : chip("UNVALIDATED", "warn")}</div>
  ${p.legs.map(l => `<div class="leg"><div><b>${esc(l.label)}</b><div class="small muted">${esc(l.game)} · ${goalieChip(l.goalie_status)}</div></div><div class="mono">${fmtAm(l.price)}</div></div>`).join("")}
  <div class="grid g3" style="margin-top:10px">${stat("Offered", fmtAm(p.american))}${stat("Joint prob", pct(p.p_joint), `fair ${fmtAm(p.fair_american)}`)}${stat("EV", roi(p.ev), `p10 ${roi(p.ev_p10)}`, evCls(p.ev))}</div></div>`;
}
function pageParlay(app, id) {
  const P = S.data.parlays || {}; const p = [...(P.qualified || []), ...(P.candidates || [])].find(x => x.id === id);
  if (!p) { app.innerHTML = empty("PARLAY NOT FOUND", "It may have dropped out after the latest odds refresh."); return; }
  app.innerHTML = `<div class="page-head"><div><h1>NHL EDGE PRO — ${fmtAm(p.american)} parlay analysis</h1><p>${esc(p.book)} · ${p.n_legs} legs · generated ${ago(p.generated_at)} · odds ${ago(p.odds_updated)}</p></div><a href="#/parlays">← all parlays</a></div>
  <div class="grid g4">${stat("Combined sportsbook odds", fmtAm(p.american), `decimal ${fx(p.decimal, 2)} (${esc(p.payout_basis)})`)}
  ${stat("Model joint hit probability", pct(p.p_joint, 2), `80% band ${pct(p.p_joint_lo, 2)}–${pct(p.p_joint_hi, 2)}`)}
  ${stat("Break-even probability", pct(p.break_even, 2), `fair odds ${fmtAm(p.fair_american)}`)}
  ${stat("Expected ROI", roi(p.ev), `pessimistic (p10) ${roi(p.ev_p10)}`, evCls(p.ev))}
  ${stat("Naive product of leg probs", pct(p.p_joint_naive, 2), `correlation impact ${pp(p.p_joint - p.p_joint_naive, 2)}`)}
  ${stat("Goalies", p.goalies_confirmed ? "All confirmed" : "Not all confirmed", "")}
  ${stat("Status", p.qualified ? "QUALIFIED" : "UNVALIDATED", esc(p.status_reason || ""))}
  ${stat("Selection-bias context", (P.n_combos_searched || 0).toLocaleString(), "combinations searched today")}</div>
  <div class="panel section"><h2>Legs</h2><table><thead><tr><th>Leg</th><th>Game</th><th class="n">Price</th><th class="n">Model</th><th class="n">Leg EV</th><th>Goalies</th><th>Why included</th></tr></thead><tbody>
  ${p.legs.map(l => `<tr><td><b>${esc(l.label)}</b></td><td>${esc(l.game)}</td><td class="n">${fmtAm(l.price)}</td><td class="n">${pct(l.p_win)}</td><td class="n ${evCls(l.ev)}">${roi(l.ev)}</td><td>${goalieChip(l.goalie_status)}</td><td class="small">${esc(l.reason)}</td></tr>`).join("")}</tbody></table></div>
  <div class="grid g2 section"><div class="panel"><h2>Remove one leg</h2><table><thead><tr><th>Without</th><th class="n">Odds</th><th class="n">Joint prob</th><th class="n">EV</th><th class="n">EV p10</th></tr></thead><tbody>
  ${(p.leg_removal || []).map(r => `<tr><td>${esc(r.removed)}</td><td class="n">${fmtAm(r.american)}</td><td class="n">${pct(r.p_joint, 2)}</td><td class="n ${evCls(r.ev)}">${roi(r.ev)}</td><td class="n ${evCls(r.ev_p10)}">${roi(r.ev_p10)}</td></tr>`).join("")}</tbody></table></div>
  <div class="panel"><h2>Joint probability across model uncertainty</h2><div class="chart-box"><canvas id="c-pj"></canvas></div></div></div>
  <div class="grid g2 section"><div class="panel"><h2>Correlation handling</h2><p class="small">${esc(p.correlation_note || "")}</p></div>
  <div class="panel"><h2>Primary ways this parlay loses</h2><ul class="ul">${(p.risks || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div></div>`;
  const h = p.p_joint_hist || {}; if (h.counts) chart("c-pj", { type: "bar", data: { labels: h.edges.slice(0, -1).map(x => pct(x, 1)), datasets: [{ label: "posterior draws", data: h.counts, backgroundColor: "#a78bfa" }] }, options: { plugins: { legend: { display: false } } } });
}

function alertForm() {
  const a = JSON.parse(localStorage.getItem("np_alerts") || "{}");
  const v = (k, d) => a[k] ?? d;
  return `<div class="controls">
  <label class="ctl">Min odds<input type="number" id="al-odds" value="${v("odds", 600)}" style="width:90px"></label>
  <label class="ctl">Min EV %<input type="number" id="al-ev" value="${v("ev", 5)}" style="width:80px"></label>
  <label class="ctl">Max legs<input type="number" id="al-legs" value="${v("legs", 4)}" style="width:70px"></label>
  <label class="ctl">Max odds age (min)<input type="number" id="al-age" value="${v("age", 120)}" style="width:90px"></label>
  <label class="ctl">Confirmed goalies only<select id="al-goalies"><option value="0">No</option><option value="1" ${v("goalies", 0) == 1 ? "selected" : ""}>Yes</option></select></label>
  <label class="ctl">Qualified (validated) only<select id="al-val"><option value="1">Yes</option><option value="0" ${v("val", 1) == 0 ? "selected" : ""}>No</option></select></label>
  <button class="btn" id="al-save">Save</button></div><div id="al-out"></div>`;
}
function bindAlerts() {
  const save = $("#al-save"); if (!save) return;
  const run = () => {
    const a = JSON.parse(localStorage.getItem("np_alerts") || "{}"); const P = S.data.parlays || {};
    const seen = new Set(JSON.parse(localStorage.getItem("np_seen") || "[]"));
    const pool = [...(P.qualified || []).map(p => ({ ...p, qualified: true })), ...(a.val == 0 ? (P.candidates || []) : [])];
    const hits = pool.filter(p => p.american >= (a.odds ?? 600) && p.ev * 100 >= (a.ev ?? 5) && p.n_legs <= (a.legs ?? 4)
      && (!a.goalies || p.goalies_confirmed) && (Date.now() - new Date(p.odds_updated).getTime()) / 60000 <= (a.age ?? 120));
    $("#al-out").innerHTML = hits.length ? `<div class="notice info">${hits.length} parlay(s) match your rules${hits.some(h => !seen.has(h.id)) ? " — includes NEW since your last visit" : ""}: ${hits.map(h => `<a href="#/parlay/${esc(h.id)}">${fmtAm(h.american)} @ ${esc(h.book)} (odds ${ago(h.odds_updated)})</a>`).join(", ")}</div>` : `<p class="small muted">No current parlay matches these rules. Stale odds never trigger an alert.</p>`;
    hits.forEach(h => seen.add(h.id)); localStorage.setItem("np_seen", JSON.stringify([...seen].slice(-500)));
  };
  save.onclick = () => { localStorage.setItem("np_alerts", JSON.stringify({ odds: +$("#al-odds").value, ev: +$("#al-ev").value, legs: +$("#al-legs").value, age: +$("#al-age").value, goalies: +$("#al-goalies").value, val: +$("#al-val").value })); run(); };
  run();
}

/* ---------------- Goalie lab ---------------- */
function pageGoalies(app) {
  const G = S.data.goalies; const gs = S.data.slate?.games || [];
  if (!G?.goalies?.length) { app.innerHTML = empty("GOALIE DATA UNAVAILABLE", "Run the pipeline backfill to build goalie histories."); return; }
  app.innerHTML = `<div class="page-head"><div><h1>Goalie Lab</h1><p>Starter status is CONFIRMED only with a named confirmation source; otherwise PROJECTED from recent usage and rest, or UNKNOWN.</p></div>
  <div class="controls"><label class="ctl">Search<input type="text" id="g-q" placeholder="name or team"></label><label class="ctl">Min GP<input type="number" id="g-gp" value="5" style="width:70px"></label></div></div>
  <div class="panel"><h2>Today's goalie matchups</h2><div class="table-wrap"><table><thead><tr><th>Game</th><th>Away goalie</th><th>Status</th><th class="n">Rating</th><th>Home goalie</th><th>Status</th><th class="n">Rating</th><th class="n">Swap impact (home win)</th></tr></thead><tbody>
  ${gs.map(g => `<tr class="click" onclick="location.hash='#/game/${g.game_id}'"><td>${esc(gameTitle(g))}</td><td>${esc(g.away.goalie?.name || "—")}</td><td>${goalieChip(g.away.goalie?.status)}</td><td class="n">${fx(g.away.goalie?.sv_rating, 3)}</td>
  <td>${esc(g.home.goalie?.name || "—")}</td><td>${goalieChip(g.home.goalie?.status)}</td><td class="n">${fx(g.home.goalie?.sv_rating, 3)}</td><td class="n small">${(g.goalie_swap || []).map(s => `${esc(s.team)}: ${pp(s.d_p_home_ml)}`).join(" · ") || "—"}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="panel section"><h2>All goalies — season ${esc(G.season || "")} (league SV% ${fx(G.league_sv, 3)})</h2><div class="table-wrap" id="g-tab"></div>
  <p class="small muted">GSAA = saves − shots × league SV% (season). Not shot-quality adjusted: high-danger SV%, rebound control and GSAx require a licensed expected-goals feed (listed as unavailable in Methodology).</p></div>`;
  const draw = () => {
    const q = $("#g-q").value.trim().toLowerCase(), mgp = +$("#g-gp").value || 0;
    const rows = G.goalies.filter(x => (x.season?.gp || 0) >= mgp && (!q || (x.name + " " + x.team).toLowerCase().includes(q)));
    $("#g-tab").innerHTML = `<table class="st"><thead><tr>${["Goalie", "Team", "GP", "GS", "SV%", "GAA", "GSAA", "GSAA/60", "L3 SV%", "L5 SV%", "L10 SV%", "Career SV%", "Rating", "SA/start", "Rest", "Starts L7d"].map((t, i) => `<th class="sortable ${i > 1 ? "n" : ""}">${t}</th>`).join("")}</tr></thead><tbody>
    ${rows.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.team)}</td><td class="n">${x.season.gp}</td><td class="n">${x.season.gs}</td><td class="n">${fx(x.season.sv_pct, 3)}</td><td class="n">${fx(x.season.gaa, 2)}</td>
    <td class="n ${evCls(x.season.gsaa)}" data-v="${x.season.gsaa}">${fx(x.season.gsaa, 1)}</td><td class="n">${fx(x.season.gsaa60, 2)}</td><td class="n">${fx(x.l3?.sv_pct, 3)}</td><td class="n">${fx(x.l5?.sv_pct, 3)}</td><td class="n">${fx(x.l10?.sv_pct, 3)}</td>
    <td class="n">${fx(x.career?.sv_pct, 3)}</td><td class="n">${fx(x.rating_sv, 3)}</td><td class="n">${fx(x.season.sa_per_start, 1)}</td><td class="n">${x.rest_days ?? "—"}</td><td class="n">${x.starts_last7 ?? 0}</td></tr>`).join("")}</tbody></table>`;
    sortableTable($("#g-tab"));
  };
  $("#g-q").oninput = draw; $("#g-gp").oninput = draw; draw();
}

/* ---------------- Team analytics ---------------- */
function pageTeams(app) {
  const T = S.data.teams; if (!T?.teams?.length) { app.innerHTML = empty("TEAM DATA UNAVAILABLE", "Run the pipeline backfill."); return; }
  const win = localStorage.getItem("np_win") || "season";
  app.innerHTML = `<div class="page-head"><div><h1>Team Analytics</h1><p>Model ratings (with uncertainty) and rolling box-score metrics. Ratings are opponent-adjusted; raw rates are not.</p></div>
  <div class="controls"><label class="ctl">Window<select id="t-win">${["L3", "L5", "L10", "L20", "season", "prev_season", "home", "away"].map(w => `<option ${w === win ? "selected" : ""}>${w}</option>`).join("")}</select></label>
  <label class="ctl">Search<input type="text" id="t-q" placeholder="team"></label></div></div>
  <div class="panel"><div class="table-wrap" id="t-tab"></div><p class="small muted">Att/Def = log-scale goal ratings vs league average (+ is better for both). ±sd from the model's posterior. ${esc(T.unavailable_note || "")}</p></div>
  <div class="panel section"><h2>Compare (percentile vs league, ${esc(win)})</h2><div class="controls"><select id="t-a"></select><select id="t-b"></select></div><div class="chart-box tall"><canvas id="c-radar"></canvas></div></div>`;
  const draw = () => {
    const w = $("#t-win").value; localStorage.setItem("np_win", w); const q = $("#t-q").value.trim().toLowerCase();
    const rows = T.teams.filter(t => !q || (t.abbr + t.name).toLowerCase().includes(q));
    $("#t-tab").innerHTML = `<table class="st"><thead><tr>${["Team", "Att", "Def", "Net", "GP", "GF/G", "GA/G", "SF/G", "SA/G", "SF%", "Sh%", "Sv%", "PDO", "PPG/G"].map((t, i) => `<th class="sortable ${i ? "n" : ""}">${t}</th>`).join("")}</tr></thead><tbody>
    ${rows.map(t => { const s = t.windows?.[w] || {}; const r = t.rating || {}; const net = (r.att ?? NaN) + (r.def ?? NaN);
      return `<tr><td>${logo(t.logo, t.abbr).replace('class="logo"', 'class="logo" style="width:22px;height:22px;vertical-align:middle;margin-right:6px"')}${esc(t.abbr)}</td>
      <td class="n ${evCls(r.att)}" data-v="${r.att}">${fx(r.att, 3)}<span class="dim small"> ±${fx(r.att_sd, 2)}</span></td><td class="n ${evCls(r.def)}" data-v="${r.def}">${fx(r.def, 3)}<span class="dim small"> ±${fx(r.def_sd, 2)}</span></td><td class="n ${evCls(net)}" data-v="${net}">${fx(net, 3)}</td>
      <td class="n">${s.gp ?? "—"}</td><td class="n">${fx(s.gf, 2)}</td><td class="n">${fx(s.ga, 2)}</td><td class="n">${fx(s.sf, 1)}</td><td class="n">${fx(s.sa, 1)}</td><td class="n">${pct(s.sf_pct)}</td><td class="n">${pct(s.sh_pct)}</td><td class="n">${fx(s.sv_pct, 3)}</td><td class="n">${fx(s.pdo, 3)}</td><td class="n">${fx(s.ppg, 2)}</td></tr>`; }).join("")}</tbody></table>`;
    sortableTable($("#t-tab")); radar();
  };
  const opts = T.teams.map(t => `<option>${esc(t.abbr)}</option>`).join("");
  $("#t-a").innerHTML = opts; $("#t-b").innerHTML = opts; $("#t-b").selectedIndex = 1;
  const radar = () => {
    const w = $("#t-win").value; const keys = [["gf", 1, "GF/G"], ["ga", -1, "GA/G (inv)"], ["sf", 1, "SF/G"], ["sa", -1, "SA/G (inv)"], ["sh_pct", 1, "Sh%"], ["sv_pct", 1, "Sv%"], ["ppg", 1, "PPG/G"]];
    const pctile = (abbr, k, sign) => { const vals = T.teams.map(t => t.windows?.[w]?.[k]).filter(isNum); const v = T.teams.find(t => t.abbr === abbr)?.windows?.[w]?.[k];
      if (!isNum(v) || !vals.length) return null; return vals.filter(x => sign > 0 ? x < v : x > v).length / Math.max(1, vals.length - 1); };
    const ds = [$("#t-a").value, $("#t-b").value].map((a, i) => ({ label: a, data: keys.map(([k, s]) => pctile(a, k, s)), borderColor: i ? "#3b82f6" : "#22d3ee", backgroundColor: i ? "rgba(59,130,246,.15)" : "rgba(34,211,238,.15)" }));
    chart("c-radar", { type: "radar", data: { labels: keys.map(k => k[2]), datasets: ds }, options: { scales: { r: { min: 0, max: 1, ticks: { display: false }, grid: { color: "#1e2c47" }, angleLines: { color: "#1e2c47" } } } } });
  };
  $("#t-win").onchange = draw; $("#t-q").oninput = draw; $("#t-a").onchange = radar; $("#t-b").onchange = radar; draw();
}

/* ---------------- Odds screen ---------------- */
function pageOdds(app, id) {
  const gs = S.data.slate?.games || []; const g = gameById(id) || gs[0];
  if (!g) { app.innerHTML = empty("NO GAMES TODAY", ""); return; }
  const rows = g.odds?.rows || []; const books = [...new Set(rows.map(r => r.book))].sort();
  const keys = [...new Map(rows.map(r => [`${r.market}|${r.side}|${r.point ?? ""}`, r])).values()];
  const H = S.data.odds_history?.games?.[g.game_id] || {};
  app.innerHTML = `<div class="page-head"><div><h1>Odds Screen</h1><p>Line shopping across connected books. "First seen" is the first price this pipeline captured — not necessarily the book's opening line.</p></div><div class="controls">${gameSelect(g.game_id)}</div></div>
  <div class="small muted" style="margin-bottom:8px">Source: ${esc(S.data.slate?.odds_source || "—")} · fetched ${dtLocal(S.data.slate?.odds_fetched_at)} (${ago(S.data.slate?.odds_fetched_at)})</div>
  ${!rows.length ? empty("NO ODDS FOR THIS GAME", esc(g.odds?.status_note || "Odds feed unavailable or no books posted this game.")) : `
  <div class="panel"><div class="table-wrap"><table><thead><tr><th>Market</th>${books.map(b => `<th class="n">${esc(b)}</th>`).join("")}<th class="n">First seen</th><th class="n">Move (same book)</th><th class="n">Model fair</th><th class="n">EV @ best</th></tr></thead><tbody>
  ${keys.map(k => { const best = bestPrice(Object.assign({}, g), k.market, k.side, k.point); const mp = modelProb(g, k.market, k.side, k.point) || {};
    const hk = `${k.market}|${k.side}|${k.point ?? ""}`; const first = H[hk]?.first; const ser = first ? H[hk].series?.[first.book] : null; const cur = ser?.length ? ser[ser.length - 1][1] : null;
    const ev = best ? evOf(mp.p, mp.push, best.price) : NaN;
    return `<tr><td>${esc(marketLabel(k.market, k.side, k.point, g))}</td>${books.map(b => { const r = rows.find(x => x.book === b && x.market === k.market && x.side === k.side && x.point === k.point);
      return `<td class="n ${r && best && r.price === best.price && r.book === best.book ? "pos" : ""}">${r ? fmtAm(r.price) : "<span class='dim'>—</span>"}</td>`; }).join("")}
      <td class="n">${first ? `${fmtAm(first.price)} <span class="dim small">${esc(first.book)}</span>` : "—"}</td>
      <td class="n">${first && cur != null ? ((am2dec(cur) > am2dec(first.price)) ? "▲ longer" : (am2dec(cur) < am2dec(first.price)) ? "▼ shorter" : "—") : "—"}</td>
      <td class="n">${fmtAm(mp.fair)}</td><td class="n ${evCls(ev)}">${roi(ev)}</td></tr>`; }).join("")}</tbody></table></div></div>
  <div class="panel section"><h2>Line movement</h2><div class="controls"><select id="mv-key">${keys.map(k => `<option value="${esc(`${k.market}|${k.side}|${k.point ?? ""}`)}">${esc(marketLabel(k.market, k.side, k.point, g))}</option>`).join("")}</select></div><div class="chart-box tall"><canvas id="c-move"></canvas></div></div>`}`;
  bindGameSel("odds");
  const mv = $("#mv-key"); if (!mv) return;
  const draw = () => { const ser = H[mv.value]?.series || {};
    const colors = ["#22d3ee", "#3b82f6", "#a78bfa", "#22c55e", "#f59e0b", "#ef4444", "#e8eef9"];
    chart("c-move", { type: "line", data: { datasets: Object.entries(ser).map(([b, pts], i) => ({ label: b, data: pts.map(p => ({ x: new Date(p[0]).getTime(), y: 100 / am2dec(p[1]) })), borderColor: colors[i % 7], pointRadius: 2, stepped: true })) },
      options: { parsing: false, scales: { x: { type: "linear", ticks: { callback: v => new Date(v).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) } }, y: { title: { display: true, text: "implied prob %" } } } } }); };
  mv.onchange = draw; draw();
}

/* ---------------- Simulation center ---------------- */
function mulberry32(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function rnorm(r) { let u = 0, v = 0; while (u === 0) u = r(); v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
function rgamma(r, k) { if (k < 1) return rgamma(r, k + 1) * Math.pow(r(), 1 / k); const d = k - 1 / 3, c = 1 / Math.sqrt(9 * d);
  for (;;) { let x, v; do { x = rnorm(r); v = 1 + c * x; } while (v <= 0); v = v * v * v; const u = r(); if (u < 1 - .0331 * x ** 4 || Math.log(u) < .5 * x * x + d * (1 - v + Math.log(v))) return d * v; } }
function rpois(r, l) { const L = Math.exp(-l); let k = 0, p = 1; do { k++; p *= r(); } while (p > L); return k - 1; }
function simulate(lh, la, aux, n, seed) {
  const r = mulberry32(seed); const out = { h: new Int16Array(n), a: new Int16Array(n), end: new Int8Array(n), rh: new Int16Array(n), ra: new Int16Array(n) };
  const d = (lh - la) / (lh + la); const qot = Math.min(.95, Math.max(.05, aux.ot_base + aux.kappa * d));
  for (let i = 0; i < n; i++) {
    const g = aux.phi > 1e-9 ? rgamma(r, 1 / aux.phi) * aux.phi : 1;
    let h = rpois(r, lh * g), a = rpois(r, la * g);
    if (r() < aux.theta) { if (h - a === 1) h++; else if (a - h === 1) a++; }
    out.rh[i] = h; out.ra[i] = a; let e = 0;
    if (h === a) { const so = r() < aux.p_so; e = so ? 2 : 1; if (r() < (so ? aux.q_so : qot)) h++; else a++; }
    out.h[i] = h; out.a[i] = a; out.end[i] = e;
  }
  return out;
}
function pageSim(app) {
  const M = S.data.model_params; const G = S.data.goalies;
  if (!M?.teams) { app.innerHTML = empty("MODEL PARAMETERS UNAVAILABLE", "Run the pipeline to fit the model."); return; }
  const teamOpts = M.teams.map(t => `<option>${esc(t)}</option>`).join("");
  app.innerHTML = `<div class="page-head"><div><h1>Simulation Center</h1><p>Seeded Monte Carlo in your browser, using the fitted model parameters (point estimates) and the same scoring assumptions as the pipeline. Same seed → identical result.</p></div></div>
  <div class="panel"><div class="controls">
  <label class="ctl">Away<select id="s-away">${teamOpts}</select></label><label class="ctl">Away goalie<select id="s-ag"></select></label>
  <label class="ctl">Home<select id="s-home">${teamOpts}</select></label><label class="ctl">Home goalie<select id="s-hg"></select></label>
  <label class="ctl">Away B2B<select id="s-ab"><option value="0">No</option><option value="1">Yes</option></select></label>
  <label class="ctl">Home B2B<select id="s-hb"><option value="0">No</option><option value="1">Yes</option></select></label>
  <label class="ctl">Neutral<select id="s-neu"><option value="0">No</option><option value="1">Yes</option></select></label>
  <label class="ctl">Simulations<select id="s-n"><option>10000</option><option>50000</option><option>100000</option></select></label>
  <label class="ctl">Seed<input type="number" id="s-seed" value="${M.seed ?? 20261013}" style="width:110px"></label>
  <button class="btn" id="s-run">Run</button></div><div id="s-out"></div></div>
  <div class="grid g2 section"><div class="panel"><h2>Total goals</h2><div class="chart-box"><canvas id="c-s-tot"></canvas></div></div><div class="panel"><h2>Final margin (home − away)</h2><div class="chart-box"><canvas id="c-s-mar"></canvas></div></div></div>
  <p class="small muted">Simulation precision is not model accuracy: with 10,000 runs a 50% probability has ±0.5 pp Monte-Carlo error, but the model's own error is far larger — see Model Performance.</p>`;
  $("#s-home").selectedIndex = 1;
  const goalieOpts = (team) => { const list = (G?.goalies || []).filter(x => x.team === team).sort((a, b) => (b.season?.gs || 0) - (a.season?.gs || 0));
    return `<option value="">League average</option>` + list.map(x => `<option value="${x.rating_sv}">${esc(x.name)} (${fx(x.rating_sv, 3)})</option>`).join(""); };
  const syncG = () => { $("#s-ag").innerHTML = goalieOpts($("#s-away").value); $("#s-hg").innerHTML = goalieOpts($("#s-home").value); };
  $("#s-away").onchange = syncG; $("#s-home").onchange = syncG; syncG();
  $("#s-run").onclick = () => {
    const h = M.teams.indexOf($("#s-home").value), a = M.teams.indexOf($("#s-away").value);
    if (h === a) { $("#s-out").innerHTML = `<div class="notice bad">Pick two different teams.</div>`; return; }
    const lg = G?.league_sv || .905; const adj = sv => sv ? Math.log((1 - sv) / (1 - lg)) : 0;
    const gh = adj(+$("#s-hg").value || 0), ga = adj(+$("#s-ag").value || 0);
    const hb = +$("#s-hb").value, ab = +$("#s-ab").value, neu = +$("#s-neu").value;
    const lh = Math.exp(M.mu + M.home * (1 - neu) + M.att[h] - M.def[a] + M.b2b_off * hb + M.b2b_def * ab + M.goalie_coef * ga);
    const la = Math.exp(M.mu + M.att[a] - M.def[h] + M.b2b_off * ab + M.b2b_def * hb + M.goalie_coef * gh);
    const n = +$("#s-n").value, seed = +$("#s-seed").value; const t0 = performance.now();
    const s = simulate(lh, la, M.aux, n, seed);
    let hw = 0, rh = 0, rt = 0, ot = 0, so = 0, h2 = 0, a2 = 0; const tot = new Array(16).fill(0), mar = {}, sc = {};
    for (let i = 0; i < n; i++) { const d = s.h[i] - s.a[i]; if (d > 0) hw++; if (s.rh[i] > s.ra[i]) rh++; if (s.rh[i] === s.ra[i]) rt++; if (s.end[i] === 1) ot++; if (s.end[i] === 2) so++;
      if (d >= 2) h2++; if (d <= -2) a2++; tot[Math.min(15, s.h[i] + s.a[i])]++; const dm = Math.max(-6, Math.min(6, d)); mar[dm] = (mar[dm] || 0) + 1; const k = `${s.a[i]}-${s.h[i]}`; sc[k] = (sc[k] || 0) + 1; }
    const P = x => x / n; const top = Object.entries(sc).sort((x, y) => y[1] - x[1]).slice(0, 8);
    const hn = $("#s-home").value, an = $("#s-away").value;
    $("#s-out").innerHTML = `<div class="grid g4 section">${stat(`${hn} win`, pct(P(hw)), `±${pct(1.96 * Math.sqrt(P(hw) * (1 - P(hw)) / n), 2)} MC error · fair ${fmtAm(prob2am(P(hw)))}`)}${stat(`${an} win`, pct(1 - P(hw)), `fair ${fmtAm(prob2am(1 - P(hw)))}`)}
    ${stat("Reg win / tie", `${pct(P(rh))} / ${pct(P(rt))}`, `${hn} reg win / regulation tie`)}${stat("OT / SO", `${pct(P(ot))} / ${pct(P(so))}`)}
    ${stat("Expected goals", `${fx(la, 2)}–${fx(lh, 2)}`, `${an}–${hn} regulation rates`)}${stat(`${hn} −1.5`, pct(P(h2)), `${an} −1.5 ${pct(P(a2))}`)}
    ${stat("Over 5.5 / 6.5", `${pct(P(tot.slice(6).reduce((x, y) => x + y, 0)))} / ${pct(P(tot.slice(7).reduce((x, y) => x + y, 0)))}`)}${stat("Run time", `${Math.round(performance.now() - t0)} ms`, `${n.toLocaleString()} sims, seed ${seed}`)}</div>
    <table class="section"><thead><tr><th>Most common final (${esc(an)}–${esc(hn)})</th><th class="n">Prob</th></tr></thead><tbody>${top.map(([k, v]) => `<tr><td class="mono">${k}</td><td class="n">${pct(P(v))}</td></tr>`).join("")}</tbody></table>`;
    chart("c-s-tot", { type: "bar", data: { labels: tot.map((_, i) => i === 15 ? "15+" : i), datasets: [{ data: tot.map(P), backgroundColor: "#3b82f6" }] }, options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => pct(v, 0) } } } } });
    const mk = Object.keys(mar).map(Number).sort((x, y) => x - y);
    chart("c-s-mar", { type: "bar", data: { labels: mk, datasets: [{ data: mk.map(k => P(mar[k])), backgroundColor: mk.map(k => k > 0 ? "#22d3ee" : "#3b82f6") }] }, options: { plugins: { legend: { display: false } }, scales: { y: { ticks: { callback: v => pct(v, 0) } } } } });
  };
}

/* ---------------- Backtest & performance ---------------- */
function ciTxt(c) { return c ? `[${fx(c[0], 4)}, ${fx(c[1], 4)}]` : "—"; }
function pageBacktest(app) {
  const B = S.data.backtest;
  if (!B?.available) { app.innerHTML = `<div class="page-head"><div><h1>Historical Backtester</h1></div></div>${empty("NO BACKTEST RESULTS YET", esc(B?.reason || "Run `python -m pipeline.run backtest` (or the workflow in backtest mode) after the historical backfill."))}`; return; }
  const mk = B.markets || {};
  app.innerHTML = `<div class="page-head"><div><h1>Historical Backtester</h1><p>Chronological walk-forward: before every game date the model is refit using only games already played; goalies are the rule-projected starters, not the actual ones. Hyperparameters were chosen on the tuning seasons only and are evaluated on later seasons they never saw.</p></div></div>
  <div class="grid g4">${stat("Test games", (B.n_test_games || 0).toLocaleString(), `${esc(B.test_period)}`)}${stat("Tuning period", esc(B.tuning_period || "—"), `chosen: half-life ${B.hyper?.half_life_days}d, ridge ${B.hyper?.ridge}`)}
  ${stat("Burn-in", esc(B.burn_in_period || "—"), "training only, never scored")}${stat("Generated", ago(B.generated_at), esc(B.runtime || ""))}</div>
  <div class="panel section"><h2>Out-of-sample scores vs baselines</h2><div class="table-wrap"><table><thead><tr><th>Market</th><th>Metric</th><th class="n">Model</th><th class="n">Baseline</th><th class="n">Improvement</th><th class="n">95% CI (date-block bootstrap)</th><th>Verdict</th></tr></thead><tbody>
  ${Object.entries(mk).map(([k, v]) => `<tr><td><b>${esc(v.label)}</b></td><td>${esc(v.metric)}</td><td class="n">${fx(v.model, 4)}</td><td class="n">${fx(v.baseline, 4)}</td><td class="n ${evCls(v.improvement)}">${fx(v.improvement, 4)}</td><td class="n">${ciTxt(v.ci)}</td><td>${v.ci && v.ci[0] > 0 ? chip("BEATS BASELINE", "ok") : chip("NOT SHOWN", "warn")}</td></tr>`).join("")}</tbody></table></div>
  <p class="small muted">Baseline = ${esc(B.baseline_desc || "")}. Lower log loss / Brier is better; improvement = baseline − model. Beating a naive baseline is necessary but NOT sufficient for betting: the market is a far stronger benchmark (see Market comparison below and Model Performance).</p></div>
  <div class="grid g2 section"><div class="panel"><h2>Calibration — full-game home win</h2><div class="chart-box tall"><canvas id="c-cal"></canvas></div></div>
  <div class="panel"><h2>Calibration — home −1.5 & over 5.5</h2><div class="chart-box tall"><canvas id="c-cal2"></canvas></div></div></div>
  <div class="panel section"><h2>Persistence across seasons (moneyline log-loss improvement)</h2><div class="table-wrap"><table><thead><tr><th>Season</th><th class="n">Games</th><th class="n">Model LL</th><th class="n">Baseline LL</th><th class="n">Improvement</th><th class="n">95% CI</th><th class="n">Totals LL impr.</th></tr></thead><tbody>
  ${(B.by_season || []).map(s => `<tr><td>${esc(s.season)}</td><td class="n">${s.n}</td><td class="n">${fx(s.model_ll, 4)}</td><td class="n">${fx(s.base_ll, 4)}</td><td class="n ${evCls(s.improvement)}">${fx(s.improvement, 4)}</td><td class="n">${ciTxt(s.ci)}</td><td class="n ${evCls(s.totals_improvement)}">${fx(s.totals_improvement, 4)}</td></tr>`).join("")}</tbody></table></div></div>
  <div class="grid g2 section"><div class="panel"><h2>Goalie-information sensitivity</h2><p class="small">Moneyline log loss with <b>projected</b> starters: <span class="mono">${fx(B.goalie_sensitivity?.projected_ll, 4)}</span>; with <b>actual</b> starters (hindsight, upper bound): <span class="mono">${fx(B.goalie_sensitivity?.actual_ll, 4)}</span>. The gap is what better starter confirmation is worth.</p></div>
  <div class="panel"><h2>Market comparison & historical ROI</h2>${B.market ? `<dl class="kv"><dt>Games with historical odds</dt><dd>${B.market.n}</dd><dt>Model log loss</dt><dd>${fx(B.market.model_ll, 4)}</dd><dt>No-vig market log loss</dt><dd>${fx(B.market.market_ll, 4)}</dd><dt>Model − market</dt><dd class="${evCls(-B.market.diff)}">${fx(B.market.diff, 4)} ${ciTxt(B.market.ci)}</dd><dt>Flat-stake bets (EV≥${roi(B.market.min_ev)})</dt><dd>${B.market.n_bets}</dd><dt>Realized ROI</dt><dd class="${evCls(B.market.roi)}">${roi(B.market.roi)} [${roi(B.market.roi_ci?.[0])}, ${roi(B.market.roi_ci?.[1])}]</dd><dt>Max drawdown</dt><dd>${fx(B.market.max_dd, 1)} u</dd></dl>` : `<p class="small muted">${esc(B.market_note || "No historical sportsbook odds supplied. Add data/historical_odds.csv from a licensed source to enable market benchmarking and historical ROI. Odds are never reconstructed or invented.")}</p>`}</div></div>`;
  const cal = (id, sets) => chart(id, { type: "scatter", data: { datasets: [{ label: "perfect", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], showLine: true, borderColor: "#5d6e8c", borderDash: [4, 4], pointRadius: 0 },
    ...sets.map(([lab, c, col]) => ({ label: lab, data: (c || []).map(b => ({ x: b.p_mean, y: b.y_mean, n: b.n })), borderColor: col, backgroundColor: col, showLine: true, pointRadius: 4 }))] },
    options: { scales: { x: { min: 0, max: 1, title: { display: true, text: "predicted" } }, y: { min: 0, max: 1, title: { display: true, text: "observed" } } }, plugins: { tooltip: { callbacks: { label: c => `pred ${pct(c.raw.x)} obs ${pct(c.raw.y)} n=${c.raw.n ?? ""}` } } } } });
  app.insertAdjacentHTML("beforeend", h2hBacktestHTML() + propsBacktestHTML());
  const pb = P().backtest; if (pb?.available) cal("c-cal-props", [["SOG over 2.5", pb.stats.sog?.lines?.["2.5"]?.calibration, "#22d3ee"], ["1+ point", pb.stats.points?.lines?.["0.5"]?.calibration, "#a78bfa"], ["anytime goal", pb.stats.goals?.lines?.["0.5"]?.calibration, "#22c55e"]]);
  cal("c-cal", [["home win", mk.ml?.calibration, "#22d3ee"]]); cal("c-cal2", [["home −1.5", mk.pl_home?.calibration, "#a78bfa"], ["over 5.5", mk.o55?.calibration, "#22c55e"]]);
}

function pagePerformance(app) {
  const E = S.data.market_eval || {}; const B = S.data.backtest || {}; const P = S.data.parlays?.track || {};
  const tiers = E.tiers || {};
  app.innerHTML = `<div class="page-head"><div><h1>Model Performance</h1><p>Live, forward-only record: every pre-game prediction and every flagged candidate is logged before puck drop and scored against closing prices and results.</p></div></div>
  <div class="panel"><h2>Validation tiers by market</h2><table><thead><tr><th>Market</th><th>Tier</th><th>Why</th></tr></thead><tbody>
  ${["h2h", "spreads", "totals"].map(m => `<tr><td>${{ h2h: "Moneyline", spreads: "Puck line", totals: "Totals" }[m]}</td><td>${tierChip(tiers[m]?.tier)}</td><td class="small">${esc(tiers[m]?.reason || "No evidence yet")}</td></tr>`).join("")}
  ${Object.entries(S.data.meta?.model?.prop_tiers || {}).map(([k, v]) => `<tr><td>Prop: ${esc(STAT_LABEL[k])}</td><td>${tierChip(v ? "BEATS_BASELINE" : "EXPERIMENTAL")}</td><td class="small">${v ? "Beats the last-10-games hit rate out of sample (95% CI above 0)." : "Not shown to beat the last-10 hit rate."} Needs logged prop bets beating closing prices to go further.</td></tr>`).join("")}</tbody></table>
  <p class="small muted">EXPERIMENTAL → BEATS_BASELINE (backtest beats naive baseline, CI above 0) → MARKET_COMPETITIVE (≥${esc(E.min_market_games ?? "—")} live games, log loss no worse than the closing no-vig market) → EDGE_SUPPORTED (≥${esc(E.min_clv_bets ?? "—")} logged candidates with positive closing-line value, CI above 0). Only EDGE_SUPPORTED markets can produce qualified edges by default.</p></div>
  <div class="grid g4 section">${stat("Live games scored", E.n_games ?? 0, "prediction locked pre-game + closing odds + result")}${stat("Model − market log loss", fx(E.ml?.diff, 4), E.ml?.ci ? `95% CI ${ciTxt(E.ml.ci)}` : "needs data", evCls(-(E.ml?.diff ?? 0)))}
  ${stat("Logged candidates", E.clv?.n ?? 0, "with a closing price to compare")}${stat("Mean CLV", roi(E.clv?.mean), E.clv?.ci ? `95% CI [${roi(E.clv.ci[0])}, ${roi(E.clv.ci[1])}]` : "needs data", evCls(E.clv?.mean))}</div>
  <div class="grid g2 section"><div class="panel"><h2>Expected vs realized (logged candidates, 1u flat)</h2><dl class="kv"><dt>Settled</dt><dd>${E.realized?.n ?? 0}</dd><dt>Expected ROI (model)</dt><dd>${roi(E.realized?.expected_roi)}</dd><dt>Realized ROI</dt><dd class="${evCls(E.realized?.roi)}">${roi(E.realized?.roi)}</dd><dt>95% CI</dt><dd>${E.realized?.ci ? `[${roi(E.realized.ci[0])}, ${roi(E.realized.ci[1])}]` : "—"}</dd><dt>Max drawdown</dt><dd>${fx(E.realized?.max_dd, 1)} u</dd></dl>
  <div class="chart-box section"><canvas id="c-pnl"></canvas></div></div>
  <div class="panel"><h2>Parlay track record</h2><dl class="kv"><dt>Logged parlays settled</dt><dd>${P.n_settled ?? 0}</dd><dt>Hit rate</dt><dd>${pct(P.hit_rate)}</dd><dt>Expected hit rate</dt><dd>${pct(P.expected_hit_rate)}</dd><dt>Avg offered odds</dt><dd>${fmtAm(P.avg_american)}</dd><dt>Avg expected ROI</dt><dd>${roi(P.avg_expected_roi)}</dd><dt>Realized ROI</dt><dd class="${evCls(P.roi)}">${roi(P.roi)}</dd><dt>Max drawdown</dt><dd>${fx(P.max_dd, 1)} u</dd></dl>
  <h3 style="margin-top:10px">By odds range / legs</h3><table><thead><tr><th>Group</th><th class="n">n</th><th class="n">Hits</th><th class="n">Exp. hits</th><th class="n">ROI</th></tr></thead><tbody>${(P.groups || []).map(r => `<tr><td>${esc(r.group)}</td><td class="n">${r.n}</td><td class="n">${r.hits}</td><td class="n">${fx(r.expected_hits, 1)}</td><td class="n ${evCls(r.roi)}">${roi(r.roi)}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>No settled parlays yet</td></tr>"}</tbody></table>
  <p class="small muted">Selection bias: many combinations are searched daily and only the best-looking are logged, so expected hit rates are optimistic by construction. Compare realized vs expected hits before trusting any parlay EV.</p></div></div>
  <div class="panel section"><h2>Model drift (rolling 100-game live log loss, model vs market)</h2><div class="chart-box"><canvas id="c-drift"></canvas></div></div>`;
  const pnl = E.realized?.curve || []; if (pnl.length) chart("c-pnl", { type: "line", data: { labels: pnl.map((_, i) => i + 1), datasets: [{ label: "cumulative units", data: pnl, borderColor: "#22d3ee", pointRadius: 0 }] } });
  const d = E.drift || []; if (d.length) chart("c-drift", { type: "line", data: { labels: d.map(x => x.date), datasets: [{ label: "model", data: d.map(x => x.model), borderColor: "#22d3ee", pointRadius: 0 }, { label: "market", data: d.map(x => x.market), borderColor: "#3b82f6", pointRadius: 0 }] } });
}

/* ---------------- Bet tracker & bankroll ---------------- */
function bets() { try { return JSON.parse(localStorage.getItem("np_bets") || "[]"); } catch { return []; } }
function pageTracker(app) {
  const B = bets();
  const settle = b => { const d = am2dec(b.odds); return b.result === "win" ? b.stake * (d - 1) : b.result === "loss" ? -b.stake : 0; };
  const summary = type => { const xs = B.filter(b => b.type === type && b.result && b.result !== "open"); const st = xs.reduce((s, b) => s + (b.result === "push" ? 0 : +b.stake), 0); const pl = xs.reduce((s, b) => s + settle(b), 0);
    const clv = xs.filter(b => b.close).map(b => am2dec(b.odds) / am2dec(b.close) - 1); const exp = xs.filter(b => b.p).map(b => (b.p * (am2dec(b.odds) - 1) - (1 - b.p)) * b.stake);
    let peak = 0, cum = 0, dd = 0; xs.forEach(b => { cum += settle(b); peak = Math.max(peak, cum); dd = Math.max(dd, peak - cum); });
    return { n: xs.length, pl, roi: st ? pl / st : NaN, clv: clv.length ? clv.reduce((a, b) => a + b, 0) / clv.length : NaN, exp: exp.reduce((a, b) => a + b, 0), dd }; };
  const H = summary("hypothetical"), A = summary("actual");
  const expo = {}; B.filter(b => b.result === "open").forEach(b => { const k = `${b.team || "—"} · ${b.market || "—"}`; expo[k] = (expo[k] || 0) + (+b.stake); });
  app.innerHTML = `<div class="page-head"><div><h1>Bet Tracker &amp; Bankroll</h1><p>Hypothetical and actual bets are tracked separately. Stored only in this browser (localStorage) — export to keep a copy.</p></div></div>
  <div class="grid g2"><div class="panel"><h2>Hypothetical</h2>${trackSummary(H)}</div><div class="panel"><h2>Actual</h2>${trackSummary(A)}</div></div>
  <div class="panel section"><h2>Add bet</h2><div class="controls">
  <label class="ctl">Type<select id="b-type"><option>hypothetical</option><option>actual</option></select></label>
  <label class="ctl">Date<input type="date" id="b-date" value="${new Date().toISOString().slice(0, 10)}"></label>
  <label class="ctl">Bet<input type="text" id="b-desc" placeholder="TOR ML vs BOS"></label>
  <label class="ctl">Team<input type="text" id="b-team" style="width:70px"></label>
  <label class="ctl">Market<select id="b-mkt"><option>ML</option><option>Puck line</option><option>Total</option><option>Parlay</option><option>Prop</option></select></label>
  <label class="ctl">Odds<input type="number" id="b-odds" value="-110" style="width:80px"></label>
  <label class="ctl">Stake<input type="number" id="b-stake" value="1" step="0.1" style="width:70px"></label>
  <label class="ctl">Model prob %<input type="number" id="b-p" step="0.1" style="width:80px"></label>
  <button class="btn" id="b-add">Add</button></div></div>
  <div class="panel section"><h2>Bets</h2><div class="table-wrap"><table><thead><tr><th>Date</th><th>Type</th><th>Bet</th><th>Market</th><th class="n">Odds</th><th class="n">Stake</th><th class="n">Model</th><th class="n">Closing</th><th>Result</th><th class="n">P/L</th><th></th></tr></thead><tbody>
  ${B.map((b, i) => `<tr><td>${esc(b.date)}</td><td>${esc(b.type)}</td><td>${esc(b.desc)}</td><td>${esc(b.market)}</td><td class="n">${fmtAm(+b.odds)}</td><td class="n">${fx(+b.stake, 2)}</td><td class="n">${b.p ? pct(b.p) : "—"}</td>
  <td class="n"><input type="number" data-close="${i}" value="${b.close ?? ""}" style="width:70px"></td>
  <td><select data-res="${i}">${["open", "win", "loss", "push"].map(r => `<option ${b.result === r ? "selected" : ""}>${r}</option>`).join("")}</select></td><td class="n ${evCls(settle(b))}">${b.result && b.result !== "open" ? fx(settle(b), 2) : "—"}</td><td><button class="btn ghost" data-del="${i}">✕</button></td></tr>`).join("") || "<tr><td colspan=11 class='muted'>No bets yet</td></tr>"}</tbody></table></div>
  <div class="controls section"><button class="btn ghost" id="b-exp">Export CSV</button></div>
  <h3>Open exposure by team / market</h3><ul class="ul small">${Object.entries(expo).map(([k, v]) => `<li>${esc(k)}: ${fx(v, 2)} u</li>`).join("") || "<li class='muted'>none</li>"}</ul>
  <p class="small muted">Correlated exposure: several open bets on the same game (ML + puck line + over) are one correlated position, not independent risks.</p></div>
  <div class="panel section"><h2>Kelly calculator (research only)</h2><div class="controls">
  <label class="ctl">Model win %<input type="number" id="k-p" value="52" step="0.1" style="width:80px"></label><label class="ctl">Push %<input type="number" id="k-push" value="0" step="0.1" style="width:70px"></label>
  <label class="ctl">Odds<input type="number" id="k-odds" value="-105" style="width:80px"></label><label class="ctl">Kelly fraction<select id="k-f"><option value="0.1">1/10</option><option value="0.25" selected>1/4</option><option value="0.5">1/2</option></select></label>
  <label class="ctl">Bankroll<input type="number" id="k-bank" value="1000" style="width:100px"></label><label class="ctl">Max % per bet<input type="number" id="k-cap" value="2" step="0.5" style="width:70px"></label></div><div id="k-out"></div>
  <div class="notice">Kelly assumes your probability is correct. Model probabilities are estimates with error, and full Kelly on an overestimated edge loses money fast — hence fractional Kelly and a hard cap. Never increase stakes to recover losses.</div></div>`;
  const save = x => { localStorage.setItem("np_bets", JSON.stringify(x)); pageTracker(app); };
  $("#b-add").onclick = () => { const x = bets(); const odds = +$("#b-odds").value; if (odds > -100 && odds < 100) { alert("Invalid American odds"); return; }
    x.push({ type: $("#b-type").value, date: $("#b-date").value, desc: $("#b-desc").value, team: $("#b-team").value.toUpperCase(), market: $("#b-mkt").value, odds, stake: +$("#b-stake").value, p: $("#b-p").value ? +$("#b-p").value / 100 : null, result: "open" }); save(x); };
  app.querySelectorAll("[data-res]").forEach(s => s.onchange = () => { const x = bets(); x[+s.dataset.res].result = s.value; save(x); });
  app.querySelectorAll("[data-close]").forEach(s => s.onchange = () => { const x = bets(); x[+s.dataset.close].close = s.value ? +s.value : null; save(x); });
  app.querySelectorAll("[data-del]").forEach(b => b.onclick = () => { const x = bets(); x.splice(+b.dataset.del, 1); save(x); });
  $("#b-exp").onclick = () => { const cols = ["date", "type", "desc", "team", "market", "odds", "stake", "p", "close", "result"]; const csv = [cols.join(","), ...bets().map(b => cols.map(c => JSON.stringify(b[c] ?? "")).join(","))].join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" })); a.download = "nhl-edge-pro-bets.csv"; a.click(); };
  const kc = () => { const p = +$("#k-p").value / 100, push = +$("#k-push").value / 100, odds = +$("#k-odds").value, f = +$("#k-f").value, bank = +$("#k-bank").value, cap = +$("#k-cap").value / 100;
    if (odds > -100 && odds < 100) { $("#k-out").innerHTML = "<p class='neg'>Invalid odds</p>"; return; }
    const d = am2dec(odds), b = d - 1, l = 1 - p - push; const full = Math.max(0, (b * p - l) / (b * (p + l))); const frac = Math.min(full * f, cap); const ev = p * b - l;
    $("#k-out").innerHTML = `<div class="grid g4">${stat("EV per unit", roi(ev), `break-even ${pct((1 - push) / d)}`, evCls(ev))}${stat("Full Kelly", pct(full, 2))}${stat("Your stake", `${fx(frac * bank, 2)}`, `${pct(frac, 2)} of bankroll${full * f > cap ? " (capped)" : ""}`)}${stat("Fair odds", fmtAm(dec2am(1 + l / Math.max(p, 1e-9))))}</div>`; };
  app.querySelectorAll("#k-p,#k-push,#k-odds,#k-f,#k-bank,#k-cap").forEach(el => el.oninput = kc); kc();
}
function trackSummary(s) {
  return `<div class="grid g3">${stat("Settled", s.n)}${stat("P/L (units)", fx(s.pl, 2), "", evCls(s.pl))}${stat("ROI", roi(s.roi), "", evCls(s.roi))}${stat("Avg CLV", roi(s.clv), "price vs closing price")}${stat("Expected P/L", fx(s.exp, 2), "from model probs entered")}${stat("Max drawdown", fx(s.dd, 2) + " u")}</div>`;
}

/* ---------------- About ---------------- */
function pageAbout(app) {
  const m = S.data.meta || {};
  app.innerHTML = `<div class="page-head"><div><h1>Methodology &amp; Data</h1><p>What the numbers are, where they come from, and what is not available.</p></div></div>
  <div class="panel"><h2>Data sources</h2><table><thead><tr><th>Source</th><th>Used for</th><th>Status</th><th>Last updated</th><th>Detail</th></tr></thead><tbody>
  ${(m.sources || []).map(s => `<tr><td>${esc(s.name)}</td><td class="small">${esc(s.used_for || "")}</td><td>${chip(s.status, s.status === "ok" ? "ok" : s.status === "missing_key" ? "warn" : "bad")}</td><td>${dtLocal(s.fetched_at)}</td><td class="small">${esc(s.detail || "")}</td></tr>`).join("") || "<tr><td colspan=5 class='muted'>No pipeline run recorded.</td></tr>"}</tbody></table></div>
  <div class="panel section"><h2>Not available (shown as unavailable, never estimated)</h2><ul class="ul small">${(m.unavailable || []).map(u => `<li>${esc(u)}</li>`).join("")}</ul></div>
  <div class="grid g2 section"><div class="panel"><h2>Game model</h2><ul class="ul small">
  <li>Regulation goals per team: Poisson regression with team attack/defence, home ice, back-to-back effects and a goalie term; recency-weighted (half-life ${esc(m.model?.hyper?.half_life_days ?? "—")} days) and ridge-shrunk toward league average (λ=${esc(m.model?.hyper?.ridge ?? "—")}).</li>
  <li>Shared gamma "pace" factor (overdispersion φ=${fx(m.model?.aux?.phi, 3)}) correlates both teams' scoring; empty-net conversion θ=${fx(m.model?.aux?.theta, 3)} — both estimated from data.</li>
  <li>Regulation ties → OT/shootout: P(shootout | tie)=${pct(m.model?.aux?.p_so)}, home OT base ${pct(m.model?.aux?.ot_base)}, strength slope κ=${fx(m.model?.aux?.kappa, 2)}, home SO ${pct(m.model?.aux?.q_so)}. Winner +1 goal in the final score.</li>
  <li>Uncertainty: ${esc(m.model?.draws ?? "—")} posterior draws (Laplace approximation) → 80% bands and pessimistic (10th-percentile) EV.</li></ul></div>
  <div class="panel"><h2>Player props</h2><ul class="ul small">
  <li>One regression per stat (shots, goals, assists, points) on pre-game inputs: recency-weighted rate shrunk to the position average, last 5/10/20/season form, recent ice time, opponent shots/goals allowed, opposing goalie, own team scoring, home ice and the player's last 10 games vs this opponent (H2H).</li>
  <li>Negative-binomial distribution gives the probability of any line; goalie saves = projected shots faced × shrunk save % with an empirical pulled-early mixture.</li>
  <li>Validated on 2018-present after training on 2015-18: beats the player's last-10-games hit rate on every line tested (Historical Backtester).</li>
  <li>Lineups are projected from each team's last game; injuries and scratches are not in the feed.</li></ul></div>
  <div class="panel"><h2>Expected value</h2><div class="formula">EV (ROI per 1u) = p_win × (decimal − 1) − p_loss      (push returns stake)
Break-even p_win = (1 − p_push) / decimal
Probability edge (pp) = p_win − break-even
No-vig = multiplicative over both sides at one book, median across books
Example: +120 at 50% → 0.50×1.20 − 0.50 = +10.0%</div></div></div>`;
}

/* ---------------- Browser odds (your own key, stored only in this browser) ---------------- */
const ODDS_KEY = "np_odds_key", ODDS_CACHE = "np_odds_cache", ODDS_MIN_REFRESH_MIN = 15;
const nameNorm = s => String(s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\./g, "").replace(/\s+/g, " ").trim();
const ODDS_ALIASES = { "utah hockey club": "UTA", "utah mammoth": "UTA", "arizona coyotes": "ARI", "montreal canadiens": "MTL", "st louis blues": "STL" };
const getKey = () => { try { return localStorage.getItem(ODDS_KEY) || ""; } catch { return ""; } };

async function fetchBrowserOdds(force = false) {
  const key = getKey(); if (!key) return { status: "no_key" };
  let cache = null; try { cache = JSON.parse(localStorage.getItem(ODDS_CACHE) || "null"); } catch {}
  if (!force && cache && (Date.now() - new Date(cache.fetched_at).getTime()) / 60000 < ODDS_MIN_REFRESH_MIN) return { status: "ok", ...cache, cached: true };
  const url = `https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds?apiKey=${encodeURIComponent(key)}&regions=us&markets=h2h,spreads,totals&oddsFormat=american&dateFormat=iso`;
  try {
    const r = await fetch(url);
    if (!r.ok) return { status: "error", detail: `HTTP ${r.status}: ${(await r.text()).slice(0, 200)}` };
    const events = await r.json();
    const out = { fetched_at: new Date().toISOString(), remaining: r.headers.get("x-requests-remaining"), events };
    try { localStorage.setItem(ODDS_CACHE, JSON.stringify(out)); } catch {}
    return { status: "ok", ...out };
  } catch (e) { return { status: "error", detail: `Request failed (${e.message}). If this is a CORS error, use the GitHub Secret method instead.` }; }
}

function applyBrowserOdds(res) {
  const games = S.data.slate?.games || [];
  const lut = { ...ODDS_ALIASES };
  games.forEach(g => { lut[nameNorm(g.home.name)] = g.home.abbr; lut[nameNorm(g.away.name)] = g.away.abbr; });
  let matched = 0;
  for (const ev of res.events || []) {
    const h = lut[nameNorm(ev.home_team)], a = lut[nameNorm(ev.away_team)];
    const g = games.find(x => x.home.abbr === h && x.away.abbr === a && Math.abs(new Date(x.start_utc) - new Date(ev.commence_time)) < 12 * 3600e3);
    if (!g) continue;
    const rows = [];
    for (const bk of ev.bookmakers || []) for (const mk of bk.markets || []) {
      if (!["h2h", "spreads", "totals"].includes(mk.key)) continue;
      for (const o of mk.outcomes || []) rows.push({ book: bk.title || bk.key, market: mk.key,
        side: mk.key === "totals" ? o.name.toLowerCase() : (nameNorm(o.name) === nameNorm(ev.home_team) ? "home" : "away"),
        point: o.point ?? null, price: o.price, last_update: mk.last_update || bk.last_update });
    }
    if (!rows.length) continue;
    matched++;
    const tots = rows.filter(r => r.market === "totals").map(r => r.point);
    g.odds = { rows, books: [...new Set(rows.map(r => r.book))].sort(), fetched_at: res.fetched_at, source: "browser",
      main_total: tots.length ? tots.sort((x, y) => tots.filter(v => v === y).length - tots.filter(v => v === x).length)[0] : null };
    // no-vig per selection: multiplicative, both sides at the same book, median across books
    const nv = {};
    rows.forEach(r => { const os = r.market === "totals" ? (r.side === "over" ? "under" : "over") : (r.side === "home" ? "away" : "home");
      const op = r.market === "totals" ? r.point : (r.point == null ? null : -r.point);
      const o = rows.find(x => x.book === r.book && x.market === r.market && x.side === os && x.point === op);
      if (!o) return; const a = 1 / am2dec(r.price), b = 1 / am2dec(o.price); (nv[`${r.market}|${r.side}|${r.point ?? ""}`] ||= []).push(a / (a + b)); });
    Object.entries(nv).forEach(([k, v]) => { const m = g.model?.markets?.[k]; if (m) { v.sort((x, y) => x - y); m.novig = v[Math.floor(v.length / 2)]; } });
  }
  S.data.slate.odds_fetched_at = res.fetched_at; S.data.slate.odds_source = "The Odds API — your key, fetched in this browser";
  rebuildBrowserEdges();
  return matched;
}

function rebuildBrowserEdges() {
  const ed = S.data.edges || (S.data.edges = {}); const th = ed.thresholds || { min_ev: 0.02, min_ev_lower: 0 };
  const gate = ed.gate_tier || "EDGE_SUPPORTED"; const tiers = S.data.meta?.model?.tiers || {};
  const order = ["EXPERIMENTAL", "BEATS_BASELINE", "MARKET_COMPETITIVE", "EDGE_SUPPORTED"];
  const out = [];
  for (const g of S.data.slate?.games || []) {
    if (g.odds?.source !== "browser" || !["FUT", "PRE", undefined, null].includes(g.state)) continue;
    const gs = [g.home.goalie?.status, g.away.goalie?.status];
    const gstat = gs.every(s => s === "CONFIRMED") ? "CONFIRMED" : gs.includes("UNKNOWN") ? "UNKNOWN" : "PROJECTED";
    g.edge_ids = []; g.edge_rows = [];
    for (const r of g.odds.rows) {
      const mp = modelProb(g, r.market, r.side, r.point); if (!mp || !isNum(mp.p)) continue;
      const d = am2dec(r.price), push = mp.push || 0, ev = evOf(mp.p, push, r.price), evLo = evOf(mp.p_lo, push, r.price);
      if (!(ev >= th.min_ev && evLo > th.min_ev_lower && gstat !== "UNKNOWN")) continue;
      const tier = tiers[r.market]?.tier || "EXPERIMENTAL"; const be = (1 - push) / d;
      const e = { id: `b-${g.game_id}-${r.market}-${r.side}-${r.point}-${r.book}`.replace(/\s/g, ""), game_id: g.game_id, game: gameTitle(g),
        label: marketLabel(r.market, r.side, r.point, g), market: r.market, side: r.side, point: r.point, book: r.book, price: r.price,
        p_win: mp.p, p_push: push, p_win_lo: mp.p_lo, p_win_hi: mp.p_hi, p_market_novig: mp.novig ?? null,
        novig_method: mp.novig != null ? "multiplicative, both sides at same book, median across books" : "opposite side not offered",
        fair_american: mp.fair, break_even: be, prob_edge: mp.p - be, ev, ev_p10: evLo, ev_goalie_swap: null,
        kelly_quarter: 0.25 * Math.max(0, ((d - 1) * mp.p - (1 - mp.p - push)) / ((d - 1) * (1 - push))),
        goalie_status: gstat, tier, qualified: order.indexOf(tier) >= order.indexOf(gate), odds_updated: g.odds.fetched_at,
        reasons: [`Model ${pct(mp.p)} (80% band ${pct(mp.p_lo)}–${pct(mp.p_hi)}) vs break-even ${pct(be)} at ${fmtAm(r.price)}.`, ...(g.insights || []).slice(0, 5)],
        risks: [mp.novig != null ? `Market no-vig ${pct(mp.novig)} vs model ${pct(mp.p)} — the closing market is usually right.` : "No two-sided market to compare.",
          `Pessimistic EV (model's 10th-percentile probability): ${roi(evLo)}.`, "Starting goalies may be projected, not confirmed.",
          "Injuries/lineups are not in the data feed.", "Browser-fetched prices are not archived, so they don't count toward the model's validation record."] };
      out.push(e); g.edge_ids.push(e.id); g.edge_rows.push({ id: e.id, market: e.market, side: e.side, point: e.point, book: e.book, ev_p10: evLo });
    }
  }
  out.sort((a, b) => b.ev_p10 - a.ev_p10);
  ed.qualified = out.filter(e => e.qualified); ed.candidates = out.filter(e => !e.qualified);
  if (!ed.qualified.length) ed.no_edge_reason = out.length ? `${out.length} price(s) look favourable to the model, but no market has reached the ${gate} validation tier yet.` : "No price beats the model's pessimistic estimate by the required margin.";
  S.data.meta && (S.data.meta.sources = [...(S.data.meta.sources || []).filter(s => s.short !== "ODDS*"), { name: "The Odds API (your key, browser)", short: "ODDS*", status: "ok", fetched_at: S.data.slate.odds_fetched_at, detail: "fetched in this browser" }]);
}

function pageKeys(app) {
  const key = getKey();
  app.innerHTML = `<div class="page-head"><div><h1>Odds API key</h1><p>Paste your <a href="https://the-odds-api.com" target="_blank" rel="noopener">The Odds API</a> key to load live odds straight into this site from your browser.</p></div></div>
  <div class="panel"><div class="controls"><label class="ctl" style="flex:1;min-width:240px">API key<input type="password" id="k-in" value="${esc(key)}" placeholder="paste key" autocomplete="off"></label>
  <button class="btn" id="k-save">Save &amp; load odds</button><button class="btn ghost" id="k-refresh" ${key ? "" : "disabled"}>Refresh now</button><button class="btn ghost" id="k-clear">Remove key</button></div><div id="k-msg"></div>
  <ul class="ul small muted"><li>The key is saved only in <b>this browser</b> (localStorage). It is never put in the repo or the page code, so other visitors can't see it — but anyone using this browser profile can.</li>
  <li>Each load costs 3 credits (moneyline + puck line + totals). Odds are cached for ${ODDS_MIN_REFRESH_MIN} minutes so page reloads don't burn credits.</li>
  <li>Probabilities still come from the model the GitHub Action fits each day; this only swaps in fresher prices and recomputes EV for every line the model priced.</li>
  <li>Parlays and the validation record use only odds fetched by the pipeline (the <code>ODDS_API_KEY</code> GitHub Secret), because those are archived and scored against closing lines.</li></ul></div>`;
  const msg = t => $("#k-msg").innerHTML = t;
  const load = async force => { msg(`<p class="small muted">Loading odds…</p>`); const res = await fetchBrowserOdds(force);
    if (res.status !== "ok") { msg(`<div class="notice bad">${esc(res.detail || "No key saved.")}</div>`); return; }
    const n = S.data.slate ? applyBrowserOdds(res) : 0; renderStatus();
    msg(`<div class="notice info">${res.cached ? "Using cached odds" : "Loaded odds"} from ${dtLocal(res.fetched_at)} · ${(res.events || []).length} NHL events · matched ${n} game(s) on today's slate${res.remaining != null ? ` · ${esc(res.remaining)} credits left` : ""}. <a href="#/dashboard">Go to dashboard →</a></div>`); };
  $("#k-save").onclick = () => { const v = $("#k-in").value.trim(); if (!v) { msg(`<div class="notice bad">Paste a key first.</div>`); return; } localStorage.setItem(ODDS_KEY, v); localStorage.removeItem(ODDS_CACHE); load(true); };
  $("#k-refresh").onclick = () => load(true);
  $("#k-clear").onclick = () => { localStorage.removeItem(ODDS_KEY); localStorage.removeItem(ODDS_CACHE); pageKeys(app); };
}

/* ---------------- boot ---------------- */
async function boot() {
  if (DEMO) $("#demo-banner").hidden = false;
  $("#nav-toggle").onclick = () => $("#sidebar").classList.toggle("open");
  const vals = await Promise.all(FILES.map(loadJSON));
  FILES.forEach((f, i) => S.data[f] = vals[i]);
  if (S.data.meta?.mode === "demo" && !DEMO) S.data.meta = null; // never show demo output as live
  if (!DEMO && getKey() && S.data.slate?.games?.length) {
    const res = await fetchBrowserOdds(false);
    if (res.status === "ok") applyBrowserOdds(res); else console.warn("browser odds:", res.detail);
  }
  renderStatus();
  window.addEventListener("hashchange", route);
  const t0 = Date.now();
  const waitChart = () => (window.Chart || Date.now() - t0 > 4000) ? route() : setTimeout(waitChart, 50);
  waitChart();
}
document.addEventListener("DOMContentLoaded", boot);
