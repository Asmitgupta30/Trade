/* ===================================================================
   Trading Advisor — Frontend Application
   Advisory-only assistant for Indian Equities & Mutual Funds.
   =================================================================== */
"use strict";

const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

const fmtINR = (x) => "₹" + Number(x || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const fmtINR2 = (x) => "₹" + Number(x || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtPct = (x, d = 2) => {
  const n = Number(x || 0);
  return `<span class="${n > 0 ? "pos" : n < 0 ? "neg" : ""}">${n > 0 ? "+" : ""}${n.toFixed(d)}%</span>`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"'`\/]/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;", "/": "&#47;" }[c]));

// Input and HTML sanitizer helper to prevent XSS injection
const sanitize = (val) => {
  if (val == null) return "";
  if (typeof val === "number" || typeof val === "boolean") return String(val);
  return esc(String(val));
};

// Crisp vector icon helpers — strictly no AI slop / emojis
const ICONS = {
  check: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`,
  copy: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
  chart: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  trend: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>`,
  bolt: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>`,
  target: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>`,
  shield: `<svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="var(--ink-secondary)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  alert: `<svg class="pill-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`
};

// Institutional High-Density Theme Tokens
const C = {
  eq: "#38BDF8", dd: "#EF4444", hit: "#10B981", ref: "#64748B",
  gateDefault: "#F59E0B", gateCandidate: "#8B5CF6",
  win: "#10B981", loss: "#EF4444", grid: "#334155"
};

// Theme Toggle & Persistence
function initTheme() {
  const saved = localStorage.getItem("trade_theme") || "dark";
  document.documentElement.setAttribute("data-theme", saved);
  const toggleBtn = $("#theme-toggle");
  if (toggleBtn) {
    toggleBtn.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || "dark";
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("trade_theme", next);
    });
  }
}
function onAppReady() {
  initTheme();
  initPersonalization();
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", onAppReady);
} else {
  onAppReady();
}

// Global application state
const state = {
  activeFilter: "all",
  lastData: null,
  budget: 50000,
  riskPct: 1.0,
};

/* ================= Tiny SVG Charts ================= */
function svgEl(tag, attrs) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  return el;
}

function ticks(min, max, count = 4) {
  if (!isFinite(min) || !isFinite(max)) return [];
  if (min === max) { min -= 1; max += 1; }
  const step0 = (max - min) / count;
  const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= step0) || mag * 10;
  const out = [];
  for (let t = Math.ceil(min / step) * step; t <= max + 1e-9; t += step)
    out.push(Number(t.toFixed(10)));
  return out;
}

function lineChart(el, o) {
  el.innerHTML = "";
  if (!o.xLabels || o.xLabels.length === 0 || !o.series.some(s => s.points.length >= 2)) {
    el.innerHTML = `<p class="muted">${esc(o.emptyText || "Not enough data yet.")}</p>`;
    return;
  }
  const W = Math.max(el.clientWidth || 620, 300), H = o.height || 230;
  const m = { top: 12, right: 60, bottom: 24, left: 54 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  let ymin = Infinity, ymax = -Infinity;
  for (const s of o.series) for (const p of s.points) {
    if (!isFinite(p.y)) continue;
    ymin = Math.min(ymin, p.y); ymax = Math.max(ymax, p.y);
  }
  for (const r of o.refLines || []) { ymin = Math.min(ymin, r.y); ymax = Math.max(ymax, r.y); }
  const padv = (ymax - ymin) * 0.08 || 1; ymin -= padv; ymax += padv;
  const n = o.xLabels.length;
  const X = i => m.left + (n <= 1 ? iw / 2 : (i / (n - 1)) * iw);
  const Y = v => m.top + ih - ((v - ymin) / (ymax - ymin)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img" });
  for (const t of ticks(ymin, ymax)) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(t), y2: Y(t), stroke: C.grid }));
    const l = svgEl("text", { x: m.left - 5, y: Y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "#64748B" });
    l.textContent = o.yFmt ? o.yFmt(t) : t;
    svg.appendChild(l);
  }
  [0, Math.floor((n - 1) / 2), n - 1].forEach(i => {
    const t = svgEl("text", { x: X(i), y: H - 6, "text-anchor": "middle", "font-size": 10.5, fill: "#64748B" });
    t.textContent = o.xLabels[i]; svg.appendChild(t);
  });
  for (const r of o.refLines || []) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(r.y), y2: Y(r.y),
      stroke: r.color || C.ref, "stroke-width": 1.6, "stroke-dasharray": "5 4" }));
    const t = svgEl("text", { x: W - m.right + 3, y: Y(r.y) + 4, "font-size": 10.5, fill: r.color || C.ref });
    t.textContent = r.label; svg.appendChild(t);
  }
  for (const s of o.series) {
    const pts = s.points.filter(p => isFinite(p.y));
    if (pts.length === 0) continue;
    const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p.i).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
    if (s.area && pts.length > 1)
      svg.appendChild(svgEl("path", { d: `${d} L${X(pts[pts.length - 1].i)},${Y(Math.max(ymin, 0))} L${X(pts[0].i)},${Y(Math.max(ymin, 0))} Z`,
        fill: s.color, "fill-opacity": 0.12 }));
    svg.appendChild(svgEl("path", { d, fill: "none", stroke: s.color, "stroke-width": 2.4, "stroke-linejoin": "round",
      "stroke-dasharray": s.dash ? "6 4" : "none" }));
    const lastP = pts[pts.length - 1];
    const lbl = svgEl("text", { x: W - m.right + 3, y: Y(lastP.y) - 5, "font-size": 11.5, "font-weight": 700, fill: s.color });
    lbl.textContent = s.endLabel ?? ""; svg.appendChild(lbl);
    for (const p of pts) {
      const c = svgEl("circle", { cx: X(p.i), cy: Y(p.y), r: 9, fill: "transparent" });
      const tip = svgEl("title", {}); tip.textContent = `${o.xLabels[p.i]} · ${p.label ?? p.y}`;
      c.appendChild(tip); svg.appendChild(c);
    }
  }
  el.appendChild(svg);
}

function scatterChart(el, o) {
  el.innerHTML = "";
  const pts = o.points || [];
  if (pts.length === 0) {
    el.innerHTML = `<p class="muted">${esc(o.emptyText || "Not enough data yet.")}</p>`;
    return;
  }
  const W = Math.max(el.clientWidth || 620, 300), H = 240;
  const m = { top: 12, right: 20, bottom: 32, left: 54 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;
  const xmin = 0.45, xmax = 0.85;
  let ymin = Infinity, ymax = -Infinity;
  for (const p of pts) { ymin = Math.min(ymin, p.ret); ymax = Math.max(ymax, p.ret); }
  const padv = (ymax - ymin) * 0.1 || 1; ymin -= padv; ymax += padv;
  const X = v => m.left + ((v - xmin) / (xmax - xmin)) * iw;
  const Y = v => m.top + ih - ((v - ymin) / (ymax - ymin)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", role: "img" });
  for (const t of ticks(ymin, ymax)) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(t), y2: Y(t), stroke: C.grid }));
    const l = svgEl("text", { x: m.left - 5, y: Y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "#64748B" });
    l.textContent = (t > 0 ? "+" : "") + t.toFixed(1) + "%"; svg.appendChild(l);
  }
  [0.5, 0.6, 0.7, 0.8].forEach(v => {
    svg.appendChild(svgEl("line", { x1: X(v), x2: X(v), y1: m.top, y2: H - m.bottom, stroke: C.grid }));
    const t = svgEl("text", { x: X(v), y: H - 10, "text-anchor": "middle", "font-size": 11, fill: "#64748B" });
    t.textContent = (v * 100).toFixed(0) + "%"; svg.appendChild(t);
  });
  svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(0), y2: Y(0), stroke: "#64748B", "stroke-width": 1.2 }));
  for (const g of o.gates || []) {
    svg.appendChild(svgEl("line", { x1: X(g.v), x2: X(g.v), y1: m.top, y2: H - m.bottom,
      stroke: g.color, "stroke-width": 1.8, "stroke-dasharray": "5 4" }));
    const t = svgEl("text", { x: X(g.v) + 3, y: m.top + 12, "font-size": 10.5, "font-weight": 700, fill: g.color });
    t.textContent = g.label; svg.appendChild(t);
  }
  for (const p of pts) {
    const cx = X(Math.min(Math.max(p.conf, xmin), xmax)), cy = Y(p.ret);
    const win = p.ret > 0;
    const c = svgEl("circle", { cx, cy, r: 4.8, fill: win ? C.win : C.loss, "fill-opacity": 0.8, stroke: "#fff", "stroke-width": 0.8 });
    const tip = svgEl("title", {});
    tip.textContent = `${p.symbol} (${p.date}): ${(p.conf * 100).toFixed(1)}% conf -> ${(p.ret > 0 ? "+" : "")}${p.ret.toFixed(2)}%`;
    c.appendChild(tip); svg.appendChild(c);
  }
  el.appendChild(svg);
}

function tableInto(id, headers, rows) {
  const el = $(id); if (!el) return;
  if (!rows.length) { el.innerHTML = `<p class="muted">Nothing yet.</p>`; return; }
  el.innerHTML = `<table><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${
    rows.map(r => `<tr>${r.map(c => {
      if (typeof c === "string" && c.startsWith("<span") && c.endsWith("</span>")) {
        return `<td>${c}</td>`;
      }
      return `<td>${esc(c)}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ================= Tabs Navigation & Toast ================= */
$$(".tab").forEach(btn => btn.addEventListener("click", () => {
  $$(".tab").forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
  $$(".view").forEach(v => v.classList.remove("active"));
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");
  $("#view-" + btn.dataset.view).classList.add("active");
  
  const loaders = {
    orderblock: loadOrderBlocks,
    liveintraday: loadLiveIntraday,
    simulator: loadSimulation,
    valuation: loadValuation,
    playbook: loadPlaybook,
    track: loadTrack,
    pulse: loadPulse,
    mf: loadMF,
    model: loadModelHistory,
  };
  loaders[btn.dataset.view]?.();
}));

function toast(msg) {
  let el = $("#toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.className = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3200);
}
window.addEventListener("error", e => toast("Error: " + e.message));

function banner(text, kind) {
  return `<div class="banner ${kind || "info"}">${esc(text)}</div>`;
}

/* ================= Preset Chips & Strategy Pills ================= */
$$(".preset-chips .chip").forEach(chip => {
  chip.addEventListener("click", () => {
    $$(".preset-chips .chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    $("#budget").value = chip.dataset.val;
    state.budget = parseFloat(chip.dataset.val);
  });
});

$$(".strat-pill").forEach(pill => {
  pill.addEventListener("click", () => {
    $$(".strat-pill").forEach(p => p.classList.remove("active"));
    pill.classList.add("active");
    state.activeFilter = pill.dataset.filter;
    if (state.lastData) renderAllTradePlans(state.lastData);
  });
});

/* ================= Feed Status & Market Phase ================= */
async function refreshFeed() {
  try {
    const fs = await fetch("/api/feed/status").then(r => r.json());
    const pill = $("#feed-pill");
    const st = fs.status || "disconnected";
    const word = st === "live" ? "Live NSE Feed" : st === "delayed"
      ? "Delayed ~15m" : st === "synthetic" ? "Demo Feed"
      : "Exchange Closed";
    pill.textContent = word;
    pill.className = "pill " + (st === "live" ? "live" : st === "delayed" ? "delayed" : "stale");

    const phaseBadge = $("#market-phase-badge");
    const phaseNames = {
      open: "NSE Live (09:15 - 15:30)",
      pre_market: "Pre-Market (09:00 - 09:15)",
      post_market: "Post-Market Closing",
      closed: "NSE Closed",
    };
    const phase = fs.market_phase || "closed";
    phaseBadge.textContent = phaseNames[phase] || `NSE ${phase}`;
    phaseBadge.className = `phase-badge ${phase}`;
  } catch {
    const pill = $("#feed-pill");
    pill.textContent = "Offline";
    pill.className = "pill stale";
  }

  // Also update the live market overview strip
  updateMarketOverview();
}
refreshFeed();
setInterval(refreshFeed, 30000);

/* ================= Live Market Overview & Mini Sparklines ================= */
function drawMiniSparkline(el, points, xLabels, strokeColor = "#2563EB") {
  if (!el || !points || points.length < 2) return;
  el.innerHTML = "";
  const W = Math.max(el.clientWidth || 240, 160), H = el.clientHeight || 54;
  let min = Math.min(...points), max = Math.max(...points);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.1; min -= pad; max += pad;
  const n = points.length;
  const X = i => (i / (n - 1)) * (W - 8) + 4;
  const Y = v => H - 4 - ((v - min) / (max - min)) * (H - 8);

  const d = points.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p).toFixed(1)}`).join("");
  const areaD = `${d} L${X(n - 1).toFixed(1)},${H} L${X(0).toFixed(1)},${H} Z`;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });
  svg.appendChild(svgEl("path", { d: areaD, fill: strokeColor, "fill-opacity": 0.12 }));
  svg.appendChild(svgEl("path", { d, fill: "none", stroke: strokeColor, "stroke-width": 2, "stroke-linejoin": "round" }));
  el.appendChild(svg);
}

async function updateMarketOverview() {
  try {
    const pulse = await fetch("/api/market-pulse").then(r => r.json());
    
    // Nifty 50 & Regime
    const rd = pulse.regime_details || {};
    if (pulse.nifty && $("#nifty-price")) {
      $("#nifty-price").textContent = fmtINR2(pulse.nifty.ltp);
      const chg = pulse.nifty.change_pct;
      if (chg != null) {
        $("#nifty-chg").innerHTML = fmtPct(chg);
      }
      const regimeEl = $("#nifty-regime");
      if (regimeEl) {
        regimeEl.textContent = rd.label || (pulse.regime === "risk-on" ? "Risk-On (Bull)" : pulse.regime === "risk-off" ? "Risk-Off (Defensive)" : "Caution (Selective)");
        regimeEl.className = `moc-badge ${pulse.regime === "risk-on" ? "good" : pulse.regime === "risk-off" ? "bad" : "warn"}`;
        if (rd.guidance) {
          regimeEl.title = rd.guidance;
        }
      }
    }
    if (pulse.nifty_history && pulse.nifty_history.closes?.length && $("#nifty-sparkline")) {
      drawMiniSparkline($("#nifty-sparkline"), pulse.nifty_history.closes, pulse.nifty_history.dates, "#2563EB");
    }

    // India VIX
    if (pulse.india_vix && $("#vix-price")) {
      const vixVal = pulse.india_vix.ltp;
      $("#vix-price").textContent = Number(vixVal).toFixed(2);
      const vixTxt = vixVal < 14 ? "Low Volatility (Calm)" : vixVal <= 18 ? "Normal (Favorable)" : vixVal <= 22 ? "Caution (Choppy)" : "High Volatility (Risk-Off)";
      if ($("#vix-status")) $("#vix-status").textContent = vixTxt;
      if ($("#vix-regime")) {
        $("#vix-regime").textContent = vixVal <= 18 ? "Trends Favored" : vixVal <= 22 ? "Selective Only" : "Preserve Capital";
        $("#vix-regime").className = `moc-badge ${vixVal <= 18 ? "good" : vixVal <= 22 ? "warn" : "bad"}`;
      }
    }
    if (pulse.vix_history && pulse.vix_history.closes?.length && $("#vix-sparkline")) {
      drawMiniSparkline($("#vix-sparkline"), pulse.vix_history.closes, pulse.vix_history.dates, "#F59E0B");
    }

    // Exchange Status
    const phaseNames = {
      open: "NSE Live (09:15 - 15:30)",
      pre_market: "Pre-Market (09:00 - 09:15)",
      post_market: "Post-Market Closing",
      closed: "NSE Closed",
    };
    if ($("#exchange-badge")) {
      const ph = pulse.market_phase || "closed";
      $("#exchange-badge").textContent = phaseNames[ph] || `NSE ${ph}`;
      $("#exchange-badge").className = `moc-badge ${ph === "open" ? "good" : ph === "pre_market" ? "warn" : ""}`;
    }
  } catch (err) {
    console.debug("Market overview refresh error:", err);
  }
}

/* ================= Suggestion Plan Fetcher ================= */
$("#get-suggestions").addEventListener("click", fetchSuggestions);

async function fetchSuggestions() {
  const btn = $("#get-suggestions");
  const spinner = btn.querySelector(".btn-spinner");
  const btnText = btn.querySelector(".btn-text");
  
  const budget = parseFloat($("#budget").value || "50000");
  const riskPct = parseFloat($("#risk-pct").value || "1.0");
  state.budget = budget;
  state.riskPct = riskPct;

  btn.disabled = true;
  if (spinner) spinner.style.display = "inline";
  if (btnText) btnText.textContent = "Scanning Market…";

  try {
    const resp = await fetch("/api/suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        budget_inr: budget,
        horizon: "intraday",
        trading_style: "all",
        risk_per_trade_pct: riskPct,
      }),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
    const data = await resp.json();
    state.lastData = data;
    renderAllTradePlans(data);
  } catch (err) {
    $("#suggestion-list").innerHTML =
      `<div class="banner danger">Could not generate trade plans: ${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = "none";
    if (btnText) btnText.textContent = "Generate Trade Plans";
  }
}

/* ================= Render Multi-Style Trade Plans ================= */
function renderAllTradePlans(data) {
  // Banners
  let bh = "";
  for (const n of data.system_notes || []) {
    let human = n
      .replace(/MARKET CLOSED \([^)]*\)/i, "NSE Closed")
      .replace(/preview based on most recent close — not live prices/i,
               "Showing setup analysis based on most recent exchange close")
      .replace(/Data provider: (\w+) \((\w+)\)/i,
               (_, p, s) => `Feed: ${p} (${s === "live" ? "Live" : s === "delayed" ? "Delayed" : s})`);
    bh += banner(human, /STALE|PAUSED|CIRCUIT BREAKER/i.test(n) ? "danger"
      : /CLOSED|WARNING|BUDGET/i.test(n) ? "warn" : "info");
  }
  if (data.breaker?.hard_pause_active) {
    bh += banner("CIRCUIT BREAKER ACTIVE: Account drawdown exceeded safety limit. Trading paused.", "danger");
  }
  $("#banners").innerHTML = bh;

  // Extract plans
  const stratPlans = data.strategy_plans || {};
  const vcpPlans = stratPlans.swing_vcp || [];
  const burstPlans = stratPlans.momentum_burst || [];
  const hammerPlans = stratPlans.exhaustion_hammer || [];
  const divPlans = stratPlans.dividend_pullback || [];
  const obPlans = stratPlans.order_block || [];
  
  // Intraday model suggestions
  const intradayRaw = (data.suggestions || []).filter(s => s.action !== "NO_ACTION");
  const blockedIntraday = (data.suggestions || []).filter(s => s.action === "NO_ACTION");

  // Update counts on filter pills
  const totalCount = vcpPlans.length + burstPlans.length + hammerPlans.length + divPlans.length + obPlans.length + intradayRaw.length;
  if ($("#count-all")) $("#count-all").textContent = totalCount;
  if ($("#count-intraday")) $("#count-intraday").textContent = intradayRaw.length;
  if ($("#count-swing_vcp")) $("#count-swing_vcp").textContent = vcpPlans.length;
  if ($("#count-momentum_burst")) $("#count-momentum_burst").textContent = burstPlans.length;
  if ($("#count-exhaustion_hammer")) $("#count-exhaustion_hammer").textContent = hammerPlans.length;
  if ($("#count-dividend_pullback")) $("#count-dividend_pullback").textContent = divPlans.length;
  if ($("#count-order_block")) $("#count-order_block").textContent = obPlans.length;

  // Filter based on selected pill
  let itemsToRender = [];
  const active = state.activeFilter;

  if (active === "all" || active === "swing_vcp") {
    itemsToRender.push(...vcpPlans.map(p => ({ ...p, isStrategyPlan: true })));
  }
  if (active === "all" || active === "momentum_burst") {
    itemsToRender.push(...burstPlans.map(p => ({ ...p, isStrategyPlan: true })));
  }
  if (active === "all" || active === "exhaustion_hammer") {
    itemsToRender.push(...hammerPlans.map(p => ({ ...p, isStrategyPlan: true })));
  }
  if (active === "all" || active === "dividend_pullback") {
    itemsToRender.push(...divPlans.map(p => ({ ...p, isStrategyPlan: true })));
  }
  if (active === "all" || active === "order_block") {
    itemsToRender.push(...obPlans.map(p => ({ ...p, isStrategyPlan: true })));
  }
  if (active === "all" || active === "intraday") {
    itemsToRender.push(...intradayRaw.map(s => ({ ...s, isIntraday: true })));
  }

  // Sort by confidence descending
  itemsToRender.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));

  let html = "";
  if (!itemsToRender.length) {
    html += `
      <div class="card empty-state">
        <div class="empty-icon">${ICONS.shield}</div>
        <h3>No High-Conviction Setups Found</h3>
        <p class="muted">No candidate stocks cleared the strict risk-reward and confidence hurdles for the selected strategy.</p>
        <p class="muted small">Patience is an edge: keeping capital safe in cash is better than taking suboptimal setups.</p>
      </div>`;
  } else {
    html += `<h2 class="section-title">Trade Setups (${itemsToRender.length})</h2>`;
    html += itemsToRender.map(renderCard).join("");
  }

  // Blocked / Filtered Details
  if (blockedIntraday.length && (active === "all" || active === "intraday")) {
    html += `<details class="card" style="margin-top:14px">
      <summary style="cursor:pointer;font-weight:700">
        Filtered Out by Risk Rules (${blockedIntraday.length}) — View Why
      </summary>
      <div style="margin-top:10px">
        ${blockedIntraday.slice(0, 15).map(s => `
          <div style="border-top:1px solid var(--border);padding:8px 0;display:flex;justify-content:space-between;align-items:center">
            <div>
              <strong>${esc(s.symbol)}</strong> (${esc(s.sector || "Equity")})
              <div class="muted small">${esc((s.blocked_by || []).join("; ")) || "Edge below cost / Confidence gate"}</div>
            </div>
            <span class="muted small">${(s.confidence * 100).toFixed(0)}% conf</span>
          </div>`).join("")}
      </div>
    </details>`;
  }

  $("#suggestion-list").innerHTML = html;
  wireTradeActionButtons();
  wireBrokerOrderButtons();
  wireChartModalButtons();

  // Render visual SVG setup charts for all strategy plans
  itemsToRender.forEach(item => {
    if (item.isStrategyPlan) {
      const el = $(`#chart-${item.symbol}-${item.strategy}`);
      if (el) drawTradeSetupChart(el, item);
    } else if (item.isIntraday && item.reference_price) {
      const el = $(`#chart-${item.symbol}-intraday`);
      if (el) drawTradeSetupChart(el, {
        symbol: item.symbol,
        entry_price: item.reference_price,
        stop_loss: Number((item.reference_price * 0.985).toFixed(2)),
        target_price: Number((item.reference_price * 1.03).toFixed(2)),
        sparkline: item.sparkline || [],
        dates: item.dates || [],
      });
    }
  });
}

function renderCard(item) {
  if (item.isStrategyPlan) {
    return renderStructuredStrategyCard(item);
  } else {
    return renderIntradayCard(item);
  }
}

function renderStructuredStrategyCard(plan) {
  const stratKeys = {
    SWING_VCP: { name: "Stage 2 Swing (VCP)", cls: "vcp" },
    MOMENTUM_BURST: { name: "4% Momentum Burst", cls: "burst" },
    EXHAUSTION_HAMMER: { name: "Exhaustion Hammer", cls: "hammer" },
    DIVIDEND_PULLBACK: { name: "Dividend Aristocrat Dip", cls: "dividend" },
    ORDER_BLOCK: { name: "Institutional Order Block Retest", cls: "vcp" },
  };
  const meta = stratKeys[plan.strategy] || { name: plan.strategy, cls: "vcp" };
  const confPct = Math.round((plan.confidence || 0) * 100);
  const chartId = `chart-${esc(plan.symbol)}-${esc(plan.strategy)}`;
  const prodType = plan.horizon?.toLowerCase().includes("intraday") ? "MIS" : "CNC";

  const checklistHtml = (plan.checklist || []).map(c => `
    <li class="checklist-item">
      <span class="checklist-icon">${ICONS.check}</span>
      <span>${esc(c)}</span>
    </li>
  `).join("");

  return `
  <div class="trade-card">
    <div class="trade-card-accent-bar ${meta.cls}"></div>
    <div class="trade-card-header">
      <div>
        <div class="symbol-row">
          <span class="trade-sym">${esc(plan.symbol)}</span>
          <span class="strat-tag ${meta.cls}">${esc(meta.name)}</span>
          <span class="sector-tag">${esc(plan.horizon)}</span>
        </div>
      </div>
      <div class="header-badges">
        <span class="quality-badge">${esc(plan.setup_quality || "Good")}</span>
        <span class="action-badge action-${esc(plan.action)}">${esc(plan.action)}</span>
      </div>
    </div>

    <!-- 4 Key Trade Levels -->
    <div class="trade-levels-grid">
      <div class="level-box">
        <div class="level-k">Entry Pivot</div>
        <div class="level-v">${fmtINR2(plan.entry_price)}</div>
        <div class="level-sub">Trigger / Close</div>
      </div>
      <div class="level-box">
        <div class="level-k">Stop Loss</div>
        <div class="level-v">${fmtINR2(plan.stop_loss)}</div>
        <div class="level-sub bad">-${plan.risk_pct.toFixed(1)}% Risk</div>
      </div>
      <div class="level-box">
        <div class="level-k">Target Price</div>
        <div class="level-v">${fmtINR2(plan.target_price)}</div>
        <div class="level-sub good">+${((plan.target_price - plan.entry_price) / plan.entry_price * 100).toFixed(1)}% Aim</div>
      </div>
      <div class="level-box">
        <div class="level-k">Risk : Reward</div>
        <div class="level-v">1 : ${plan.risk_reward_ratio.toFixed(1)}</div>
        <div class="level-sub good">Asymmetric Edge</div>
      </div>
    </div>

    <!-- Sizing Highlight Strip -->
    <div class="sizing-strip">
      <div class="sizing-item">
        <span class="sizing-k">Position Quantity</span>
        <span class="sizing-v highlight">${plan.suggested_shares} shares</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Position Value</span>
        <span class="sizing-v">${fmtINR(plan.position_value_inr)}</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Max Risk at Stop</span>
        <span class="sizing-v">${fmtINR(plan.risk_inr)}</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Capital Allocation</span>
        <span class="sizing-v">${((plan.position_value_inr / Math.max(state.budget, 1)) * 100).toFixed(0)}% of Budget</span>
      </div>
    </div>

    <!-- Visual Trade Setup Chart with Target & Stop Bands -->
    <div class="trade-chart-box">
      <div class="chart-header-row">
        <span class="chart-label">${ICONS.trend} Execution Target & Risk Levels</span>
        <div class="chart-legend">
          <span class="legend-item target"><span class="legend-dot target"></span> Target: ${fmtINR2(plan.target_price)}</span>
          <span class="legend-item entry"><span class="legend-dot entry"></span> Entry: ${fmtINR2(plan.entry_price)}</span>
          <span class="legend-item stop"><span class="legend-dot stop"></span> Stop: ${fmtINR2(plan.stop_loss)}</span>
        </div>
      </div>
      <div class="setup-chart-container" id="${chartId}"></div>
    </div>

    <!-- Broker Order Parameters (Copy & Paste Ready) -->
    <div class="broker-order-box">
      <div class="broker-order-header">
        <div class="broker-title">
          ${ICONS.copy}
          <strong>Broker Order Parameters (Zerodha / Angel One / Groww)</strong>
        </div>
        <button class="copy-order-btn" data-sym="${esc(plan.symbol)}" data-entry="${plan.entry_price}" data-stop="${plan.stop_loss}" data-target="${plan.target_price}" data-qty="${plan.suggested_shares}" data-risk="${fmtINR(plan.risk_inr)}" data-prod="${prodType}">
          ${ICONS.copy} Copy Parameters
        </button>
      </div>
      <div class="broker-grid">
        <div class="broker-param">
          <span class="bp-k">Exchange / Symbol</span>
          <span class="bp-v font-mono">${esc(plan.symbol)} (NSE)</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Product Type</span>
          <span class="bp-v">${prodType === "MIS" ? "MIS (Intraday)" : "CNC (Delivery)"}</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Buy Limit / Trigger</span>
          <span class="bp-v highlight font-mono">${fmtINR2(plan.entry_price)}</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Stop Loss (SL-L / SL-M)</span>
          <span class="bp-v stop font-mono">${fmtINR2(plan.stop_loss)} (-${plan.risk_pct.toFixed(1)}%)</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Target / Exit Limit</span>
          <span class="bp-v target font-mono">${fmtINR2(plan.target_price)} (+${((plan.target_price - plan.entry_price) / plan.entry_price * 100).toFixed(1)}%)</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Recommended Quantity</span>
          <span class="bp-v font-mono"><strong>${plan.suggested_shares} shares</strong></span>
        </div>
      </div>
    </div>

    <div class="trade-rationale">
      <strong>Why:</strong> ${esc(plan.rationale)}
    </div>

    ${checklistHtml ? `
      <details class="checklist-details">
        <summary class="checklist-summary">Setup Verification Checklist (${plan.checklist.length} items)</summary>
        <ul class="checklist-list">${checklistHtml}</ul>
      </details>` : ""}

    <div class="actions-row">
      <button class="small took" data-symbol="${esc(plan.symbol)}" data-qty="${plan.suggested_shares}" data-price="${plan.entry_price}" data-act="took">
        ${ICONS.check} Log Paper Execution
      </button>
      <button class="small skip" data-symbol="${esc(plan.symbol)}" data-act="skipped">
        Skip Idea
      </button>
      <button class="small view-chart" data-symbol="${esc(plan.symbol)}" style="margin-left:auto">
        ${ICONS.chart} Interactive Chart
      </button>
    </div>
  </div>`;
}

function renderIntradayCard(s) {
  const pct = Math.round((s.confidence || 0) * 100);
  const qty = s.est_quantity || (s.reference_price ? Math.floor(s.amount_inr / s.reference_price) : 0);
  const ref = s.reference_price || 0;
  const stopLoss = ref > 0 ? Number((ref * 0.985).toFixed(2)) : 0;
  const targetPrice = ref > 0 ? Number((ref * 1.03).toFixed(2)) : 0;

  return `
  <div class="trade-card">
    <div class="trade-card-accent-bar intraday"></div>
    <div class="trade-card-header">
      <div>
        <div class="symbol-row">
          <span class="trade-sym">${esc(s.symbol)}</span>
          <span class="strat-tag intraday">${ICONS.bolt} Intraday ML Ensemble</span>
          <span class="sector-tag">${esc(s.sector || "Equity")}</span>
        </div>
      </div>
      <div class="header-badges">
        <span class="action-badge action-${esc(s.action)}">${esc(s.action)}</span>
      </div>
    </div>

    <div class="sizing-strip">
      <div class="sizing-item">
        <span class="sizing-k">Allocated Capital</span>
        <span class="sizing-v highlight">${fmtINR(s.amount_inr)}</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Est. Shares</span>
        <span class="sizing-v">${qty > 0 ? qty + " shares" : "—"}</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Reference Price</span>
        <span class="sizing-v">${s.reference_price ? fmtINR2(s.reference_price) : "—"}</span>
      </div>
      <div class="sizing-item">
        <span class="sizing-k">Est. Round-Trip Cost</span>
        <span class="sizing-v">${s.est_cost_inr ? fmtINR(s.est_cost_inr) : "—"}</span>
      </div>
    </div>

    <!-- Embedded Setup Chart -->
    <div class="trade-chart-box">
      <div class="chart-header-row">
        <span class="chart-label">${ICONS.trend} Intraday Targets & Risk Levels</span>
        <div class="chart-legend">
          <span class="legend-item target"><span class="legend-dot target"></span> Target: ${targetPrice ? fmtINR2(targetPrice) : "—"}</span>
          <span class="legend-item entry"><span class="legend-dot entry"></span> Entry: ${ref ? fmtINR2(ref) : "—"}</span>
          <span class="legend-item stop"><span class="legend-dot stop"></span> Stop: ${stopLoss ? fmtINR2(stopLoss) : "—"}</span>
        </div>
      </div>
      <div class="setup-chart-container" id="chart-${esc(s.symbol)}-intraday"></div>
    </div>

    <!-- Broker Order Parameters -->
    <div class="broker-order-box">
      <div class="broker-order-header">
        <div class="broker-title">
          ${ICONS.copy}
          <strong>Broker Order Parameters (MIS Intraday)</strong>
        </div>
        <button class="copy-order-btn" data-sym="${esc(s.symbol)}" data-entry="${ref}" data-stop="${stopLoss}" data-target="${targetPrice}" data-qty="${qty}" data-risk="${fmtINR(s.amount_inr * 0.015)}" data-prod="MIS">
          ${ICONS.copy} Copy Parameters
        </button>
      </div>
      <div class="broker-grid">
        <div class="broker-param">
          <span class="bp-k">Exchange / Symbol</span>
          <span class="bp-v font-mono">${esc(s.symbol)} (NSE)</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Product Type</span>
          <span class="bp-v">MIS (Intraday)</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Buy Limit / Market</span>
          <span class="bp-v highlight font-mono">${ref ? fmtINR2(ref) : "LTP"}</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Stop Loss (SL-L)</span>
          <span class="bp-v stop font-mono">${stopLoss ? fmtINR2(stopLoss) + " (-1.5%)" : "—"}</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Target Limit</span>
          <span class="bp-v target font-mono">${targetPrice ? fmtINR2(targetPrice) + " (+3.0%)" : "—"}</span>
        </div>
        <div class="broker-param">
          <span class="bp-k">Recommended Shares</span>
          <span class="bp-v font-mono"><strong>${qty} shares</strong></span>
        </div>
      </div>
    </div>

    <div class="confidence-wrap">
      <div class="confidence-bar-bg">
        <div class="confidence-bar" style="width:${pct}%;background:${pct >= 70 ? "var(--emerald)" : pct >= 55 ? "var(--accent)" : "var(--amber)"}"></div>
      </div>
      <div class="confidence-label">
        <span>Model Signal Confidence</span>
        <strong>${pct}% (${pct >= 70 ? "Strong Buy Gate" : "Standard Buy Gate"})</strong>
      </div>
    </div>

    <div class="trade-rationale">
      <strong>Rationale:</strong> ${esc(s.rationale)}
    </div>

    <div class="actions-row">
      <button class="small took" data-symbol="${esc(s.symbol)}" data-sid="${esc(s.suggestion_id)}" data-qty="${qty}" data-price="${s.reference_price || ""}" data-act="took">
        ${ICONS.check} Log Paper Execution
      </button>
      <button class="small skip" data-symbol="${esc(s.symbol)}" data-act="skipped">
        Skip Idea
      </button>
      <button class="small view-chart" data-symbol="${esc(s.symbol)}" style="margin-left:auto">
        ${ICONS.chart} Interactive Chart
      </button>
    </div>
  </div>`;
}

/* ================= Visual Trade Setup Chart ================= */
function drawTradeSetupChart(container, plan) {
  if (!container) return;
  container.innerHTML = "";
  const pts = plan.sparkline || [];
  const dates = plan.dates || [];

  if (pts.length < 2) {
    container.innerHTML = `<div class="muted small" style="text-align:center;padding:32px 0">Click "Full Technical Chart" below for interactive multi-session chart.</div>`;
    return;
  }

  const W = Math.max(container.clientWidth || 580, 300), H = 140;
  const m = { top: 16, right: 94, bottom: 20, left: 54 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;

  let ymin = Math.min(...pts, plan.stop_loss);
  let ymax = Math.max(...pts, plan.target_price);
  const pad = (ymax - ymin) * 0.08 || 1;
  ymin -= pad; ymax += pad;

  const n = pts.length;
  const X = i => m.left + (i / (n - 1)) * iw;
  const Y = v => m.top + ih - ((v - ymin) / (ymax - ymin)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });

  // Shaded Reward Zone (Target to Entry)
  const yEntry = Y(plan.entry_price);
  const yTarget = Y(plan.target_price);
  const yStop = Y(plan.stop_loss);

  if (yTarget < yEntry) {
    const rewardRect = svgEl("rect", {
      x: m.left, y: yTarget, width: iw, height: yEntry - yTarget,
      fill: "#10B981", "fill-opacity": 0.08
    });
    svg.appendChild(rewardRect);
  }

  // Shaded Risk Zone (Entry to Stop)
  if (yStop > yEntry) {
    const riskRect = svgEl("rect", {
      x: m.left, y: yEntry, width: iw, height: yStop - yEntry,
      fill: "#EF4444", "fill-opacity": 0.08
    });
    svg.appendChild(riskRect);
  }

  // Horizontal Grid Lines & Ticks
  const yTicks = ticks(ymin, ymax, 3);
  for (const t of yTicks) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(t), y2: Y(t), stroke: "var(--border)", "stroke-width": 1 }));
    const l = svgEl("text", { x: m.left - 5, y: Y(t) + 3.5, "text-anchor": "end", "font-size": 10, fill: "#64748B" });
    l.textContent = "₹" + t.toFixed(0);
    svg.appendChild(l);
  }

  // Price path + area
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p).toFixed(1)}`).join("");
  const areaD = `${d} L${X(n - 1).toFixed(1)},${Y(ymin)} L${X(0).toFixed(1)},${Y(ymin)} Z`;
  svg.appendChild(svgEl("path", { d: areaD, fill: "#38BDF8", "fill-opacity": 0.08 }));
  svg.appendChild(svgEl("path", { d, fill: "none", stroke: "var(--ink)", "stroke-width": 2.2, "stroke-linejoin": "round" }));

  // Target Reference Line (Green dashed)
  svg.appendChild(svgEl("line", {
    x1: m.left, x2: W - m.right, y1: yTarget, y2: yTarget,
    stroke: "#10B981", "stroke-width": 1.6, "stroke-dasharray": "4 3"
  }));
  const targetLbl = svgEl("text", { x: W - m.right + 4, y: yTarget + 3.5, "font-size": 10, "font-weight": 700, fill: "#10B981" });
  targetLbl.textContent = `Target ₹${plan.target_price.toFixed(1)}`;
  svg.appendChild(targetLbl);

  // Entry Reference Line (Accent solid)
  svg.appendChild(svgEl("line", {
    x1: m.left, x2: W - m.right, y1: yEntry, y2: yEntry,
    stroke: "#38BDF8", "stroke-width": 1.8
  }));
  const entryLbl = svgEl("text", { x: W - m.right + 4, y: yEntry + 3.5, "font-size": 10, "font-weight": 700, fill: "#38BDF8" });
  entryLbl.textContent = `Entry ₹${plan.entry_price.toFixed(1)}`;
  svg.appendChild(entryLbl);

  // Stop Loss Reference Line (Red dashed)
  svg.appendChild(svgEl("line", {
    x1: m.left, x2: W - m.right, y1: yStop, y2: yStop,
    stroke: "#EF4444", "stroke-width": 1.6, "stroke-dasharray": "4 3"
  }));
  const stopLbl = svgEl("text", { x: W - m.right + 4, y: yStop + 3.5, "font-size": 10, "font-weight": 700, fill: "#EF4444" });
  stopLbl.textContent = `Stop ₹${plan.stop_loss.toFixed(1)}`;
  svg.appendChild(stopLbl);

  // Current Price Dot
  const lastX = X(n - 1), lastY = Y(pts[n - 1]);
  const dot = svgEl("circle", { cx: lastX, cy: lastY, r: 4.5, fill: "#38BDF8", stroke: "var(--bg-surface)", "stroke-width": 1.5 });
  svg.appendChild(dot);

  // Dates on X-axis (start, mid, end)
  if (dates.length >= 2) {
    [0, Math.floor((dates.length - 1) / 2), dates.length - 1].forEach(i => {
      const dt = dates[i];
      const t = svgEl("text", { x: X(i), y: H - 4, "text-anchor": "middle", "font-size": 9.5, fill: "#94A3B8" });
      t.textContent = dt.slice(5);
      svg.appendChild(t);
    });
  }

  container.appendChild(svg);
}

/* ================= Broker Order Copy Buttons ================= */
function wireBrokerOrderButtons() {
  $$(".copy-order-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const sym = btn.dataset.sym;
      const entry = btn.dataset.entry;
      const stop = btn.dataset.stop;
      const target = btn.dataset.target;
      const qty = btn.dataset.qty;
      const risk = btn.dataset.risk;
      const prod = btn.dataset.prod || "CNC";

      const orderText = `NSE: ${sym} | BUY ${qty} shares | Product: ${prod} | Entry Limit: ₹${entry} | Stop Loss: ₹${stop} | Target: ₹${target} | Max Risk: ${risk}`;
      
      navigator.clipboard.writeText(orderText).then(() => {
        toast(`Copied ${sym} order parameters to clipboard.`);
        btn.innerHTML = `${ICONS.check} Copied!`;
        setTimeout(() => { btn.innerHTML = `${ICONS.copy} Copy Parameters`; }, 2500);
      }).catch(() => {
        toast(`Order: ${orderText}`);
      });
    });
  });
}

/* ================= Interactive Chart Modal ================= */
async function openChartModal(symbol) {
  const modal = $("#chart-modal");
  const title = $("#modal-symbol-title");
  const sub = $("#modal-symbol-sub");
  const body = $("#modal-chart-body");
  if (!modal || !body) return;

  title.textContent = `${symbol} (NSE) — Interactive Chart`;
  sub.textContent = "Loading live OHLCV data from market feed…";
  body.innerHTML = `<div style="text-align:center;padding:48px 0;" class="muted">Loading chart data for ${esc(symbol)}…</div>`;
  modal.style.display = "flex";

  try {
    const data = await fetch(`/api/chart/${encodeURIComponent(symbol)}`).then(r => r.json());
    if (data.error || !data.close || data.close.length < 2) {
      body.innerHTML = `<div class="banner danger">Could not load chart: ${esc(data.error || "No data")}</div>`;
      return;
    }

    sub.innerHTML = `LTP: <strong>${fmtINR2(data.last_price)}</strong> &nbsp;|&nbsp; 1D Move: ${fmtPct(data.change_pct)} &nbsp;|&nbsp; Bars: ${data.close.length} daily sessions`;

    body.innerHTML = `
      <div style="margin-bottom:12px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <div style="font-size:12px;font-weight:600;display:flex;gap:14px">
          <span style="color:#1E293B">■ Close Price</span>
          <span style="color:#2563EB">■ 20-Day SMA</span>
          <span style="color:#F59E0B">■ 50-Day SMA</span>
        </div>
        <div class="muted small">Hover points to inspect price and volume</div>
      </div>
      <div id="modal-chart-svg" style="height:320px;width:100%"></div>
      <div style="margin-top:12px;display:flex;justify-content:space-between;font-size:12px;" class="muted">
        <span>Period: ${esc(data.dates[0])} to ${esc(data.dates[data.dates.length - 1])}</span>
        <span>Latest Volume: ${Number(data.volume[data.volume.length - 1]).toLocaleString("en-IN")}</span>
      </div>
    `;

    drawModalChart($("#modal-chart-svg"), data);
  } catch (err) {
    body.innerHTML = `<div class="banner danger">Failed to fetch chart: ${esc(err.message)}</div>`;
  }
}

function drawModalChart(el, data) {
  if (!el || !data.close || data.close.length < 2) return;
  el.innerHTML = "";

  const W = Math.max(el.clientWidth || 780, 400), H = 320;
  const m = { top: 16, right: 60, bottom: 26, left: 60 };
  const iw = W - m.left - m.right, ih = H - m.top - m.bottom;

  const pts = data.close;
  const sma20 = data.sma20 || [];
  const sma50 = data.sma50 || [];
  const dates = data.dates;

  let ymin = Math.min(...pts);
  let ymax = Math.max(...pts);
  const pad = (ymax - ymin) * 0.08 || 1;
  ymin -= pad; ymax += pad;

  const n = pts.length;
  const X = i => m.left + (i / (n - 1)) * iw;
  const Y = v => m.top + ih - ((v - ymin) / (ymax - ymin)) * ih;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });

  // Grid
  const yTicks = ticks(ymin, ymax, 5);
  for (const t of yTicks) {
    svg.appendChild(svgEl("line", { x1: m.left, x2: W - m.right, y1: Y(t), y2: Y(t), stroke: "#F1F5F9", "stroke-width": 1 }));
    const l = svgEl("text", { x: m.left - 6, y: Y(t) + 4, "text-anchor": "end", "font-size": 11, fill: "#64748B" });
    l.textContent = "₹" + t.toFixed(0);
    svg.appendChild(l);
  }

  // X dates
  const xIndices = [0, Math.floor(n * 0.25), Math.floor(n * 0.5), Math.floor(n * 0.75), n - 1];
  for (const i of xIndices) {
    const t = svgEl("text", { x: X(i), y: H - 8, "text-anchor": "middle", "font-size": 10.5, fill: "#64748B" });
    t.textContent = dates[i]?.slice(5) || "";
    svg.appendChild(t);
  }

  // 50 SMA (orange dashed)
  const sma50Pts = sma50.map((v, i) => ({ i, v })).filter(p => p.v != null && isFinite(p.v));
  if (sma50Pts.length > 1) {
    const d50 = sma50Pts.map((p, idx) => `${idx ? "L" : "M"}${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join("");
    svg.appendChild(svgEl("path", { d: d50, fill: "none", stroke: "#F59E0B", "stroke-width": 1.6, "stroke-dasharray": "4 2" }));
  }

  // 20 SMA (blue solid)
  const sma20Pts = sma20.map((v, i) => ({ i, v })).filter(p => p.v != null && isFinite(p.v));
  if (sma20Pts.length > 1) {
    const d20 = sma20Pts.map((p, idx) => `${idx ? "L" : "M"}${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join("");
    svg.appendChild(svgEl("path", { d: d20, fill: "none", stroke: "#2563EB", "stroke-width": 1.8 }));
  }

  // Close price line + area
  const dPrice = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p).toFixed(1)}`).join("");
  const areaPrice = `${dPrice} L${X(n - 1).toFixed(1)},${Y(ymin)} L${X(0).toFixed(1)},${Y(ymin)} Z`;
  svg.appendChild(svgEl("path", { d: areaPrice, fill: "#38BDF8", "fill-opacity": 0.08 }));
  svg.appendChild(svgEl("path", { d: dPrice, fill: "none", stroke: "var(--ink)", "stroke-width": 2.2, "stroke-linejoin": "round" }));

  // Tooltip circles
  for (let i = 0; i < n; i++) {
    const c = svgEl("circle", { cx: X(i), cy: Y(pts[i]), r: 6, fill: "transparent" });
    const tip = svgEl("title", {});
    tip.textContent = `${dates[i]} | Close: ₹${pts[i].toFixed(2)} | Vol: ${data.volume[i]?.toLocaleString("en-IN") || ""}`;
    c.appendChild(tip);
    svg.appendChild(c);
  }

  el.appendChild(svg);
}

function wireChartModalButtons() {
  $$(".view-chart").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openChartModal(btn.dataset.symbol);
    });
  });

  $("#modal-close-btn")?.addEventListener("click", () => {
    $("#chart-modal").style.display = "none";
  });
  $("#modal-backdrop")?.addEventListener("click", () => {
    $("#chart-modal").style.display = "none";
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("#chart-modal")) {
      $("#chart-modal").style.display = "none";
    }
  });
}

function wireTradeActionButtons() {
  $$(".actions-row button.took, .actions-row button.skip").forEach(btn => {
    btn.addEventListener("click", async () => {
      const took = btn.dataset.act === "took";
      const qty = parseInt(btn.dataset.qty, 10) || 1;
      const price = btn.dataset.price ? parseFloat(btn.dataset.price) : null;
      const body = {
        symbol: String(btn.dataset.symbol || "").trim(),
        suggestion_id: btn.dataset.sid ? String(btn.dataset.sid).trim() : null,
        took_trade: took,
        quantity: took ? qty : 0,
        price: took ? price : null,
      };
      try {
        const r = await fetch("/api/suggestions/action", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) throw new Error((await r.json()).detail || "Failed to log action");
        toast(took
          ? `Logged: ${qty} shares of ${body.symbol} bought at ₹${price || ""} — place order in broker app.`
          : `Logged: skipped idea for ${body.symbol}.`);
        btn.disabled = true;
        btn.style.opacity = "0.5";
      } catch (e) {
        toast("Failed to log: " + e.message);
      }
    });
  });
}

/* ================= Playbook Catalog Loader ================= */
async function loadPlaybook() {
  const box = $("#playbook-catalog");
  try {
    const res = await fetch("/api/strategies/catalog").then(r => r.json());
    const strats = res.strategies || [];
    if (!strats.length) {
      box.innerHTML = `<p class="muted">No strategy catalog available.</p>`;
      return;
    }
    box.innerHTML = strats.map(s => `
      <div class="playbook-card">
        <div>
          <div class="playbook-top">
            <h3 class="playbook-title">${esc(s.name)}</h3>
            <span class="playbook-horizon">${esc(s.horizon)}</span>
          </div>
          <p class="playbook-desc">${esc(s.description)}</p>
          
          <div class="playbook-section-label">Risk & Holding Profile</div>
          <p class="small muted" style="margin:0 0 10px">${esc(s.risk_profile)}</p>
          
          <div class="playbook-section-label">Core Rules & Entry Gates</div>
          <ul class="playbook-rules">
            ${(s.key_rules || []).map(r => `<li class="playbook-rule-item">${esc(r)}</li>`).join("")}
          </ul>
        </div>
        <div style="border-top:1px solid var(--border);padding-top:10px;margin-top:10px">
          <span class="tag-subtle">Ideal Condition: ${esc(s.ideal_market)}</span>
        </div>
      </div>
    `).join("");
  } catch (err) {
    box.innerHTML = banner("Could not load playbook: " + err.message, "danger");
  }
}

/* ================= Monthly Patterns & Scheduled Check-ins ================= */
async function loadPatternsMonthly() {
  try {
    const res = await fetch("/api/patterns/monthly").then(r => r.json());
    if (res.error) {
      $("#patterns-monthly").innerHTML = `<p class="muted small">${esc(res.error)}</p>`;
      return;
    }
    const stocks = res.stocks || [];
    if (!stocks.length) {
      $("#patterns-monthly").innerHTML = `<p class="muted small">No pattern data built yet.</p>`;
      return;
    }
    const topMovers = stocks.slice(0, 5);
    $("#patterns-monthly").innerHTML = `
      <div style="margin-top:6px">
        ${topMovers.map(s => `
          <div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border);font-size:13px">
            <strong>${esc(s.symbol)}</strong>
            <span>${fmtPct(s.ret_21d)} (1M)</span>
          </div>
        `).join("")}
      </div>`;
  } catch {
    $("#patterns-monthly").innerHTML = `<p class="muted small">Patterns unavailable.</p>`;
  }
}
loadPatternsMonthly();

async function loadCheckins() {
  try {
    const d = await fetch("/api/checkins").then(r => r.json());
    const labels = {
      pre_market: "Morning Plan (09:00 AM IST)",
      midday_check: "Midday Check (12:30 PM IST)",
      pre_close: "Pre-Close Exit Review (03:00 PM IST)",
      end_of_day_summary: "EOD Summary & Journal (03:45 PM IST)",
    };
    let html = "";
    for (const phase of Object.keys(d.schedule || {})) {
      const out = (d.latest || {})[phase];
      let inner = out
        ? (out.suggestions || []).slice(0, 3).map(s => `
            <div class="small" style="margin-top:4px">
              <span class="action-badge action-${esc(s.action)}">${esc(s.action)}</span>
              <strong>${esc(s.symbol)}</strong>
            </div>`).join("")
        : `<div class="muted small">Scheduled for ${esc(labels[phase] || phase)}</div>`;
      html += `
        <div style="border-top:1px solid var(--border);padding:8px 0">
          <strong>${esc(labels[phase] || phase)}</strong>
          <div style="margin-top:4px">${inner}</div>
        </div>`;
    }
    $("#checkins").innerHTML = html;
  } catch { /* ignore */ }
}
loadCheckins();
setInterval(loadCheckins, 120000);

/* ================= Track Record ================= */
async function loadTrack() {
  try {
    const t = await fetch("/api/track-record").then(r => r.json());
    const g = t.gate, gc = g.criteria;
    const checks = [
      { done: g.days_tracked >= gc.min_paper_days, text: `Practised ${g.days_tracked} of ${gc.min_paper_days} required days` },
      { done: g.suggestions_tracked >= gc.min_suggestions, text: `Tracked ${g.suggestions_tracked} of ${gc.min_suggestions} suggestions` },
      { done: (g.hit_rate ?? 0) >= gc.hit_rate_min && g.hit_rate != null, text: `Hit rate ${(g.hit_rate ?? 0) * 100 | 0}% (needs ${(gc.hit_rate_min * 100) | 0}%)` },
      { done: (g.sharpe ?? 0) >= gc.sharpe_min && g.sharpe != null, text: `Sharpe ${g.sharpe ?? "—"} (needs ${gc.sharpe_min})` },
      { done: (g.max_drawdown_pct ?? 99) <= gc.max_drawdown_max_pct, text: `Max drawdown ${g.max_drawdown_pct ?? "—"}% (limit ${gc.max_drawdown_max_pct}%)` },
    ];
    $("#gate-card").innerHTML = `
      <span class="badge-gate badge-${t.badge.includes("NOT") ? "notready" : "ready"}">
        ${t.badge.includes("NOT") ? "Paper Practising (Advisory Gate Active)" : "Live Ready (All Gates Passed)"}
      </span>
      <p class="muted small" style="margin:10px 0 2px">System must pass all 5 gates before earning trust:</p>
      ${checks.map(c => `<div class="check-item">
        <svg class="check-icon" viewBox="0 0 20 20"><circle cx="10" cy="10" r="9" fill="${c.done ? "var(--emerald)" : "var(--bg-muted)"}"/><path d="M6 10l3 3 5-6" stroke="#fff" stroke-width="2" fill="none"/></svg>
        ${esc(c.text)}</div>`).join("")}`;

    const h = t.hypothetical;
    const retCls = h.total_return_pct > 0 ? "good" : h.total_return_pct < 0 ? "bad" : "";
    const a = t.actual;
    $("#score-cards").innerHTML = `
      <h2 class="section-title">Paper Track Scoreboard</h2>
      <div class="metric-grid">
        <div class="metric"><div class="v ${retCls}">${h.total_return_pct != null ? h.total_return_pct.toFixed(1) + "%" : "—"}</div><div class="k">Net Return %</div></div>
        <div class="metric"><div class="v">${h.hit_rate != null ? (h.hit_rate * 100).toFixed(0) + "%" : "—"}</div><div class="k">Hit Rate %</div></div>
        <div class="metric"><div class="v">${h.max_drawdown_pct != null ? "-" + h.max_drawdown_pct.toFixed(1) + "%" : "—"}</div><div class="k">Max Drawdown</div></div>
        <div class="metric"><div class="v">${h.open_positions || 0}</div><div class="k">Open Positions</div></div>
      </div>
      <p class="muted small" style="margin-top:14px">Your manual execution logs:</p>
      <div class="metric-grid">
        <div class="metric"><div class="v">${a.trades_taken}</div><div class="k">Trades You Took</div></div>
        <div class="metric"><div class="v">${a.skipped}</div><div class="k">Trades Skipped</div></div>
        <div class="metric"><div class="v">${a.follow_rate != null ? (a.follow_rate * 100).toFixed(0) + "%" : "—"}</div><div class="k">Follow Rate</div></div>
        <div class="metric"><div class="v">${a.suggestions_seen}</div><div class="k">Total Seen</div></div>
      </div>`;

    // Equity plot
    const eq = await fetch("/api/plots/equity").then(r => r.json());
    lineChart($("#chart-equity"), {
      xLabels: (eq.dates || []).map(d => String(d).slice(5)),
      series: (eq.dates?.length >= 2) ? [{
        color: C.eq, area: true,
        points: eq.cumulative_pct.map((y, i) => ({ i, y, label: fmtPct(y) + " net" })),
        endLabel: (eq.cumulative_pct.at(-1)).toFixed(1) + "%",
      }, {
        color: C.dd, dash: true,
        points: eq.drawdown_from_peak_pct.map((y, i) => ({ i, y, label: "Drawdown " + fmtPct(-y) })),
      }] : [],
      refLines: [{ y: 0, label: "Baseline" }],
      emptyText: "Chart fills in as trades resolve.",
      yFmt: v => v.toFixed(0) + "%", height: 220,
    });
    tableInto("#table-equity", ["Day", "Net %", "Drawdown %"],
      (eq.dates || []).map((d, i) => [d, eq.cumulative_pct[i], eq.drawdown_from_peak_pct[i]]));

    // Hitrate plot
    const hr = await fetch("/api/plots/hitrate").then(r => r.json());
    lineChart($("#chart-hitrate"), {
      xLabels: (hr.points || []).map(p => p.date.slice(5)),
      series: [{ color: C.hit, area: true,
        points: (hr.points || []).map((p, i) => ({ i, y: p.hit_rate * 100,
          label: (p.hit_rate * 100).toFixed(0) + "% (" + p.n + " trades)" })) }],
      refLines: [{ y: 50, label: "50% Coin Flip" }],
      emptyText: "Shows up as closed trades accumulate.",
      yFmt: v => v.toFixed(0) + "%", height: 210,
    });
    tableInto("#table-hitrate", ["Day", "Hit Rate", "Trades"],
      (hr.points || []).map(p => [p.date, (p.hit_rate * 100).toFixed(1) + "%", p.n]));

    // Outcomes scatter
    const oc = await fetch("/api/plots/outcomes").then(r => r.json());
    scatterChart($("#chart-scatter"), {
      points: (oc.points || []).map(p => ({ conf: p.confidence, ret: p.net_return_pct, symbol: p.symbol, date: p.date })),
      gates: [
        { v: 0.55, label: "BUY (55%)", color: C.gateDefault },
        { v: 0.70, label: "STRONG BUY (70%)", color: C.gateCandidate },
      ],
      emptyText: "Scatter points populate as trades close.",
    });
    tableInto("#table-outcomes", ["Day", "Symbol", "Confidence", "Net Return"],
      (oc.points || []).slice(-12).reverse().map(p => [p.date, esc(p.symbol),
        (p.confidence * 100).toFixed(0) + "%", fmtPct(p.net_return_pct)]));
  } catch (e) {
    $("#gate-card").innerHTML = `<div class="banner danger">Could not load track record: ${esc(e.message)}</div>`;
  }
}

/* ================= Market Pulse & Health ================= */
async function loadPulse() {
  try {
    const p = await fetch("/api/market-pulse").then(r => r.json());
    const br = p.breaker || {};
    const okCard = (ok, good, bad) => `<div class="metric"><div class="v ${ok ? "good" : "bad"}" style="font-size:18px;font-weight:800;letter-spacing:0.5px">${ok ? "PASS" : "ALERT"}</div><div class="k">${ok ? good : bad}</div></div>`;
    $("#pulse-cards").innerHTML = `
      <div class="card soft">
        <h3 style="margin-top:0">Exchange & Market Regime</h3>
        <div class="metric-grid">
          ${okCard(p.status !== "disconnected", "NSE Feed Active", "Feed Offline")}
          <div class="metric">
            <div class="k" style="font-size:13px">
              Source: <strong>${esc(p.provider || "NSE")}</strong><br>
              Market: <strong>${esc(p.market_phase || "Status")}</strong>
            </div>
          </div>
          <div class="metric">
            <div class="k" style="font-size:13px">
              Regime: <strong><span class="moc-badge ${p.regime === "risk-on" ? "good" : p.regime === "risk-off" ? "bad" : "warn"}">${esc((p.regime_details && p.regime_details.label) || p.regime)}</span></strong><br>
              India VIX: <strong>${p.india_vix ? p.india_vix.ltp.toFixed(2) : "—"}</strong>
            </div>
          </div>
        </div>
        ${p.regime_details && p.regime_details.guidance ? `
          <div class="banner ${p.regime === "risk-on" ? "info" : p.regime === "risk-off" ? "danger" : "warn"}" style="margin-top:12px;">
            <strong>Trading Guidance:</strong> ${esc(p.regime_details.guidance)}
          </div>` : ""}
        <p class="muted small" style="margin-top:12px">
          Nifty 50: <strong>${p.nifty ? fmtINR(p.nifty.ltp) : "—"}</strong> · 
          50-SMA: <strong>${p.regime_details && p.regime_details.nifty_sma50 ? fmtINR(p.regime_details.nifty_sma50) : "—"}</strong>
          ${p.regime_details && p.regime_details.nifty_dist_sma50_pct != null ? `(${p.regime_details.nifty_dist_sma50_pct > 0 ? "+" : ""}${p.regime_details.nifty_dist_sma50_pct}% vs 50 SMA)` : ""}
        </p>
      </div>
      <div class="card soft">
        <h3 style="margin-top:0">Risk Brakes & Circuit Breakers</h3>
        ${br.hard_pause_active ? banner("CIRCUIT BREAKER: Losses crossed limit. Trading paused.", "danger") :
          br.entries_stopped_today ? banner("Daily loss limit reached — exit-only mode.", "danger") :
          banner("All risk brakes normal. New trade entries permitted.", "info")}
        <p class="muted small">Circuit limits: 5% daily loss, 10% weekly loss, 15% max drawdown pause.</p>
      </div>`;
  } catch (e) {
    $("#pulse-cards").innerHTML = `<div class="banner danger">Could not load pulse: ${esc(e.message)}</div>`;
  }
}

/* ================= Personalization Engine (UI UX Pro Max) ================= */
function initPersonalization() {
  const savedPersona = localStorage.getItem("trader_persona") || "intraday";
  const savedAccent = localStorage.getItem("terminal_accent") || "emerald";

  applyAccent(savedAccent);

  // Setup persona pills
  $$(".persona-pill").forEach(btn => {
    if (btn.dataset.persona === savedPersona) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }

    btn.addEventListener("click", () => {
      $$(".persona-pill").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const persona = btn.dataset.persona;
      const capital = parseFloat(btn.dataset.capital || "50000");
      const risk = parseFloat(btn.dataset.risk || "1.0");
      const targetTab = btn.dataset.tab;

      localStorage.setItem("trader_persona", persona);

      // Update state & controls
      state.budget = capital;
      state.riskPct = risk;
      const budgetInp = $("#budget");
      if (budgetInp) budgetInp.value = capital;
      const riskInp = $("#risk-pct");
      if (riskInp) riskInp.value = risk;

      updatePersonaSummary(capital, risk);

      // Switch to persona's primary view
      const tabBtn = $(`button.tab[data-view="${targetTab}"]`);
      if (tabBtn) tabBtn.click();

      toast(`Persona updated: ${btn.textContent.trim()} (Capital ₹${capital.toLocaleString('en-IN')})`);
    });
  });

  // Setup accent dots
  $$(".accent-dot").forEach(dot => {
    if (dot.dataset.accent === savedAccent) dot.classList.add("active");
    dot.addEventListener("click", () => {
      $$(".accent-dot").forEach(d => d.classList.remove("active"));
      dot.classList.add("active");
      const accent = dot.dataset.accent;
      localStorage.setItem("terminal_accent", accent);
      applyAccent(accent);
      toast(`Terminal theme accent set to ${accent.toUpperCase()}`);
    });
  });

  const activePill = $(".persona-pill.active");
  if (activePill) {
    updatePersonaSummary(parseFloat(activePill.dataset.capital || "50000"), parseFloat(activePill.dataset.risk || "1.0"));
  }
}

function updatePersonaSummary(capital, risk) {
  const el = $("#persona-summary");
  if (!el) return;
  const riskAmount = (capital * (risk / 100.0)).toFixed(0);
  el.textContent = `1R Risk: ₹${Number(riskAmount).toLocaleString('en-IN')} (${risk.toFixed(1)}%)`;
}

function applyAccent(accent) {
  const root = document.documentElement;
  if (accent === "emerald") {
    root.style.setProperty("--accent", "#22C55E");
    root.style.setProperty("--accent-bg", "rgba(34, 197, 94, 0.12)");
  } else if (accent === "sky") {
    root.style.setProperty("--accent", "#38BDF8");
    root.style.setProperty("--accent-bg", "rgba(56, 189, 248, 0.12)");
  } else if (accent === "amber") {
    root.style.setProperty("--accent", "#F59E0B");
    root.style.setProperty("--accent-bg", "rgba(245, 158, 11, 0.12)");
  }
}

/* ================= Mutual Fund & Wealth Compounding Engine ================= */
let _allMFProspects = [];
let _activeMFCategory = "all";

async function loadMF() {
  initSIPCalculator();

  try {
    const res = await fetch("/api/mf/prospects").then(r => r.json());
    _allMFProspects = res.prospects || [];
    renderMFProspects();
  } catch (err) {
    console.error("Failed to load MF prospects:", err);
    const grid = $("#mf-prospects-grid");
    if (grid) grid.innerHTML = `<div class="banner danger">Could not load prospects: ${esc(err.message)}</div>`;
  }

  // Filter tab buttons
  $$(".mf-filter-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $$(".mf-filter-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _activeMFCategory = btn.dataset.category || "all";
      renderMFProspects();
    });
  });

  // Rebalance button
  $("#mf-plan")?.addEventListener("click", () => {
    renderMF("/api/mutual-funds", "POST", { new_money_inr: parseFloat($("#mf-new-money")?.value || "0") });
  });

  // Initial rebalance load
  await renderMF("/api/mutual-funds", "GET", null);
}

function renderMFProspects() {
  const grid = $("#mf-prospects-grid");
  const countEl = $("#mf-prospects-count");
  if (!grid) return;

  const filtered = _activeMFCategory === "all"
    ? _allMFProspects
    : _allMFProspects.filter(p => p.category === _activeMFCategory || (_activeMFCategory === "mid_cap" && (p.category === "mid_cap" || p.category === "flexi_cap")));

  if (countEl) countEl.textContent = `Showing ${filtered.length} verified prospect funds`;

  if (!filtered.length) {
    grid.innerHTML = `<p class="muted small">No verified funds in this category.</p>`;
    return;
  }

  grid.innerHTML = filtered.map(f => `
    <div class="mf-card" id="mf-card-${f.scheme_code}">
      <div>
        <div class="mf-card-top">
          <div>
            <h4 class="mf-card-title">${esc(f.short_name)}</h4>
            <div class="mf-card-house">${esc(f.fund_house)} · Direct Plan Growth</div>
          </div>
          <div class="mf-card-badges">
            <span class="mf-star-rating">★★★★★</span>
          </div>
        </div>

        <div class="mf-cagr-strip">
          <div class="mf-cagr-box">
            <span class="cagr-k">1Y Return</span>
            <span class="cagr-v">+${f.cagr_1y_pct}%</span>
          </div>
          <div class="mf-cagr-box">
            <span class="cagr-k">3Y CAGR</span>
            <span class="cagr-v" style="color:var(--emerald-dark);font-size:16px;">+${f.cagr_3y_pct}%</span>
          </div>
          <div class="mf-cagr-box">
            <span class="cagr-k">5Y CAGR</span>
            <span class="cagr-v">+${f.cagr_5y_pct}%</span>
          </div>
        </div>

        <div class="mf-metrics-row">
          <span>Alpha vs Bench: <strong style="color:var(--emerald-dark);">+${f.alpha_vs_benchmark_pct}%</strong></span>
          <span>AUM: <strong>₹${(f.aum_cr || 0).toLocaleString('en-IN')} Cr</strong></span>
          <span>TER: <strong>${f.ter_pct}%</strong></span>
        </div>

        <p class="mf-suitability">${esc(f.suitability)}</p>
      </div>

      <div class="mf-card-actions">
        <button type="button" class="btn sm primary" onclick="simulateFundSIP('${esc(f.short_name)}', ${f.cagr_3y_pct})">Simulate SIP</button>
        <button type="button" class="btn sm" onclick="copyFundTicket('${esc(f.scheme_name)}', ${f.scheme_code}, ${f.cagr_3y_pct}, ${f.min_sip_inr})">${ICONS.copy} Ticket</button>
      </div>
    </div>
  `).join("");
}

function initSIPCalculator() {
  const amtSlider = $("#sip-amount-range");
  const yrSlider = $("#sip-years-range");
  const cagrSlider = $("#sip-cagr-range");

  if (!amtSlider || !yrSlider || !cagrSlider) return;

  const updateCalc = () => {
    const P = parseFloat(amtSlider.value || "10000");
    const Y = parseInt(yrSlider.value || "5", 10);
    const rate = parseFloat(cagrSlider.value || "20.0");

    $("#sip-amount-display").textContent = `₹${P.toLocaleString('en-IN')} / mo`;
    $("#sip-years-display").textContent = `${Y} Year${Y > 1 ? 's' : ''}`;
    $("#sip-cagr-display").textContent = `${rate.toFixed(1)}% Annual CAGR`;

    const r = (rate / 100.0) / 12.0;
    const n = Y * 12;
    const invested = P * n;
    const fv = P * (((1 + r) ** n - 1) / r) * (1 + r);
    const gain = fv - invested;
    const roi = (gain / invested) * 100.0;
    const multiplier = (fv / invested).toFixed(2);

    $("#kpi-sip-invested").textContent = `₹${Math.round(invested).toLocaleString('en-IN')}`;
    $("#kpi-sip-months").textContent = `${n} Monthly Installments`;
    $("#kpi-sip-wealth").textContent = `₹${Math.round(fv).toLocaleString('en-IN')}`;
    $("#kpi-sip-gain").textContent = `+₹${Math.round(gain).toLocaleString('en-IN')}`;
    $("#kpi-sip-roi").textContent = `+${roi.toFixed(1)}% Compounded ROI`;
    $("#kpi-sip-multiplier").textContent = `${multiplier}x`;
    $("#sip-multiplier-badge").textContent = `${multiplier}x Capital Growth`;
  };

  amtSlider.addEventListener("input", updateCalc);
  yrSlider.addEventListener("input", updateCalc);
  cagrSlider.addEventListener("input", updateCalc);

  $("#scroll-to-sip-calc")?.addEventListener("click", () => {
    $("#sip-calculator-section")?.scrollIntoView({ behavior: "smooth" });
  });

  updateCalc();
}

function simulateFundSIP(fundName, cagr) {
  const cagrSlider = $("#sip-cagr-range");
  if (cagrSlider) {
    cagrSlider.value = cagr;
    cagrSlider.dispatchEvent(new Event("input"));
    $("#sip-calculator-section")?.scrollIntoView({ behavior: "smooth" });
    toast(`Loaded ${fundName} (${cagr}% CAGR) into SIP Calculator`);
  }
}

function copyFundTicket(schemeName, code, cagr, minSip) {
  const text = [
    `=== MUTUAL FUND INVESTMENT ADVISORY ===`,
    `Scheme: ${schemeName}`,
    `AMFI Scheme Code: ${code}`,
    `Plan: Direct Plan - Growth`,
    `Historical 3Y CAGR: +${cagr}%`,
    `Recommended Minimum SIP: ₹${minSip}`,
    `Broker Platforms: Zerodha Coin, Groww, Kuvera, CAMS/KFintech`,
  ].join("\n");

  navigator.clipboard.writeText(text).then(() => {
    toast(`Order parameters for ${schemeName} copied.`);
  }).catch(() => {
    toast(`Failed to copy to clipboard`);
  });
}

async function renderMF(url, method, body) {
  const out = $("#mf-output");
  if (!out) return;
  try {
    const data = await fetch(url, method === "POST" ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : undefined).then(r => r.json());

    const bands = data.bands || {}, cur = data.current_alloc_pct || {}, tgt = data.target_alloc_pct || {};
    const names = { large_cap: "Large Cap Bluechip", mid_cap: "Mid / Small Cap", debt: "Debt / Arbitrage", gold_or_other: "Gold & Multi-Asset" };
    const rows = ["large_cap", "mid_cap", "debt", "gold_or_other"].map(c => {
      const b = bands[c] || [0, 100], cv = cur[c];
      return `<tr><td><strong>${names[c]}</strong></td><td>${cv != null && !isNaN(cv) ? cv.toFixed(0) + "%" : "0%"}</td>
        <td>${b[0]}%–${b[1]}%</td><td>${tgt[c] != null ? tgt[c].toFixed(0) + "%" : "—"}</td></tr>`;
    }).join("");

    const sugRows = (data.suggestions || []).map(s => {
      const p = s.recommended_prospect;
      const prospectCard = p ? `
        <div style="margin-top:10px; background:var(--bg-subtle); border:1px solid var(--border); border-radius:var(--radius-md); padding:10px 14px;">
          <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <div>
              <strong>Top Recommended Prospect: ${esc(p.short_name)}</strong>
              <span class="muted small"> · TER ${p.ter_pct}% · AUM ₹${(p.aum_cr||0).toLocaleString('en-IN')} Cr</span>
            </div>
            <span class="pill bull font-mono" style="font-weight:700;">3Y CAGR: +${p.cagr_3y_pct}%</span>
          </div>
          <div style="display:flex; gap:8px; margin-top:8px;">
            <button type="button" class="btn sm primary" onclick="simulateFundSIP('${esc(p.short_name)}', ${p.cagr_3y_pct})">Simulate SIP</button>
            <button type="button" class="btn sm" onclick="copyFundTicket('${esc(p.scheme_name)}', ${p.scheme_code}, ${p.cagr_3y_pct}, ${p.min_sip_inr})">Copy Ticket</button>
          </div>
        </div>
      ` : "";

      return `
        <div class="card" style="margin-top:12px;">
          <div class="card-head-row">
            <div>
              <span class="action-badge action-${esc(s.action === "REDUCE" ? "EXIT" : "BUY")}">${esc(s.action)}</span>
              <strong style="margin-left:8px; font-size:15px;">${esc(s.scheme_name)}</strong>
            </div>
            ${s.amount_inr ? `<span class="sizing-v highlight font-mono" style="font-size:16px;">${fmtINR(s.amount_inr)}</span>` : ""}
          </div>
          <div class="trade-rationale" style="margin-top:8px;">${esc(s.rationale)}</div>
          ${(s.warnings || []).map(w => `<div class="banner warn" style="margin-top:8px;">${esc(w)}</div>`).join("")}
          ${prospectCard}
        </div>
      `;
    }).join("");

    out.innerHTML = `
      <div class="card" style="margin-top:16px;">
        <h4 style="margin-top:0">Portfolio Asset Allocation Status</h4>
        <table class="alloc"><tr><th>Asset Class</th><th>Current Allocation</th><th>Policy Target Band</th><th>Optimal Aim</th></tr>${rows}</table>
        <p class="muted small" style="margin-top:10px">Edit var/mf_holdings.json to connect your real holdings.</p>
      </div>
      ${sugRows}`;
  } catch (e) {
    out.innerHTML = banner(e.message, "danger");
  }
}

/* ================= Model Brain History ================= */
async function loadModelHistory() {
  const out = $("#model-output");
  try {
    const d = await fetch("/api/model-history").then(r => r.json());
    let html = `
      <div class="card">
        <div class="metric-grid">
          <div class="metric"><div class="v">${esc(d.active)}</div><div class="k">Retrain Iterations</div></div>
          <div class="metric"><div class="v">${esc(d.promoted_count)}</div><div class="k">Promoted</div></div>
          <div class="metric"><div class="v">${esc(d.rejected_count)}</div><div class="k">Rejected</div></div>
          <div class="metric"><div class="v">${esc(d.voided_count)}</div><div class="k">Voided</div></div>
        </div>
        <p class="muted small" style="margin-top:12px">Hard Lock: A candidate model only replaces the champion if it statistically improves on Sharpe, Hit Rate, and Drawdown simultaneously.</p>
      </div>`;
    for (const e of d.events || []) {
      const c = e.champion || {}, ch = e.challenger || {};
      html += `
        <div class="card">
          <div class="card-head-row">
            <strong>Retrain: ${esc((e.ts || "").slice(0, 10))}</strong>
            <span class="action-badge ${e.promoted ? "action-BUY" : "action-NO_ACTION"}">${e.promoted ? "Promoted Challenger" : "Kept Champion"}</span>
          </div>
          <table class="alloc">
            <tr><th>Metric</th><th>Champion</th><th>Challenger</th></tr>
            <tr><td>Sharpe</td><td>${esc(c.sharpe ?? "—")}</td><td>${esc(ch.sharpe ?? "—")}</td></tr>
            <tr><td>Hit Rate</td><td>${esc(c.hit_rate ?? "—")}</td><td>${esc(ch.hit_rate ?? "—")}</td></tr>
            <tr><td>Max Drawdown</td><td>${esc(c.max_dd_pct ?? "—")}%</td><td>${esc(ch.max_dd_pct ?? "—")}%</td></tr>
          </table>
          <div class="muted small" style="margin-top:6px">${esc(e.reason || "")}</div>
        </div>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = banner("Could not load model history: " + e.message, "danger");
  }
}

/* ================= Institutional Order Blocks (order-block.com) ================= */
$("#refresh-orderblock-btn")?.addEventListener("click", loadOrderBlocks);

async function loadOrderBlocks() {
  const spinner = $("#ob-spinner");
  if (spinner) spinner.style.display = "inline-block";
  const mpBox = $("#ob-market-picture");
  const bullList = $("#ob-bullish-list");
  const bearList = $("#ob-bearish-list");

  try {
    const res = await fetch("/api/smc-zones").then(r => r.json());
    const mp = res.market_picture || {};
    const bullBoard = res.bullish_board || [];
    const bearBoard = res.bearish_board || [];
    const bullPicks = res.bullish_picks || [];
    const bearPicks = res.bearish_picks || [];

    // 1. Render Market Picture
    if (mpBox) {
      const bullPct = mp.advance_decline_ratio || 50;
      const balanceClass = mp.market_balance === "ONE_SIDED_BULLISH" ? "bullish" :
                           mp.market_balance === "ONE_SIDED_BEARISH" ? "bearish" : "choppy";

      mpBox.innerHTML = `
        <div class="ob-mp-header">
          <span class="ob-mp-title">Market Picture &amp; Breadth (${mp.total_stocks || 0} F&amp;O Equities)</span>
          <span class="ob-mp-balance-badge ${balanceClass}">${esc(mp.market_balance_label || "Balanced")}</span>
        </div>
        <div class="ob-mp-ratio-row">
          <span class="ob-count-tag bull">▲ ${mp.bullish_count || 0} Bullish (${bullPct}%)</span>
          <div class="ob-mp-bar-wrap" title="${bullPct}% Bullish vs ${(100 - bullPct).toFixed(1)}% Bearish">
            <div class="ob-mp-bar-fill" style="width: ${bullPct}%;"></div>
          </div>
          <span class="ob-count-tag bear">▼ ${mp.bearish_count || 0} Bearish (${(100 - bullPct).toFixed(1)}%)</span>
        </div>
        <div class="ob-mp-sector-strip">
          <div>Leading Basket: <span class="ob-sector-tag">${esc(mp.leading_sector || "—")}</span> (+${mp.leading_sector_return || 0}%)</div>
          <div>Lagging Basket: <span class="ob-sector-tag">${esc(mp.lagging_sector || "—")}</span> (${mp.lagging_sector_return || 0}%)</div>
        </div>
      `;
    }

    // 2. Render Counts
    if ($("#ob-bullish-count")) $("#ob-bullish-count").textContent = `${bullBoard.length} Setups (${bullPicks.length} Top Picks)`;
    if ($("#ob-bearish-count")) $("#ob-bearish-count").textContent = `${bearBoard.length} Setups (${bearPicks.length} Top Picks)`;

    // 3. Render Helper Card Builder
    const renderObCard = (ob) => {
      const isBull = ob.side === "BULLISH";
      const statusClass = ob.retest_status === "IN_ZONE_TRIGGER" ? "in-zone" :
                          ob.retest_status === "APPROACHING_ZONE" ? "approaching" : "waiting";
      const pickPillClass = isBull ? "bull-pick" : "bear-pick";
      const pickLabel = ob.pick_rule || (isBull ? "BULLISH" : "BEARISH");

      return `
        <div class="ob-card ${ob.is_pick ? "pick" : ""}" id="ob-card-${esc(ob.symbol)}">
          <div class="ob-card-header">
            <div class="ob-rank-stack">
              <span class="ob-rank-pct">p${ob.rank_pct} · #${ob.rank_index}</span>
              ${ob.is_pick ? `<span class="ob-pick-pill ${pickPillClass}"><span class="legend-dot target"></span> ${esc(pickLabel)}</span>` : `<span class="ob-pick-pill" style="background:var(--bg-subtle); color:var(--muted-fg);">${esc(pickLabel)}</span>`}
            </div>
            <div class="ob-alignment-badge" title="Smart Money Confluence Score: ${ob.alignment_score} of 4">
              <strong>${ob.alignment_score}/4</strong> Confluence
            </div>
          </div>

          <div class="ob-symbol-row">
            <div>
              <span class="ob-sym">${esc(ob.symbol)}</span>
              <span class="ob-sector-chip">${esc(ob.sector)}</span>
            </div>
            <div class="ob-price-box">
              <div class="ob-ltp">${fmtINR2(ob.current_price)}</div>
              <div style="font-size:12px;">${fmtPct(ob.change_pct)}</div>
            </div>
          </div>

          <!-- Levels Stack -->
          <div class="ob-levels-grid">
            <div class="ob-lvl-item">
              <span class="ob-lvl-k">OB Zone (Z)</span>
              <span class="ob-lvl-v zone">₹${ob.zone_low} - ₹${ob.zone_high}</span>
            </div>
            <div class="ob-lvl-item">
              <span class="ob-lvl-k">${isBull ? "Invalidation (S1)" : "Invalidation (R1)"}</span>
              <span class="ob-lvl-v stop">₹${ob.invalidation}</span>
            </div>
            <div class="ob-lvl-item">
              <span class="ob-lvl-k">${isBull ? "Liquidity (R2)" : "Liquidity (S2)"}</span>
              <span class="ob-lvl-v target">₹${ob.target_2}</span>
            </div>
            <div class="ob-lvl-item">
              <span class="ob-lvl-k">Risk : Reward</span>
              <span class="ob-lvl-v" style="color:var(--emerald-dark);">1 : ${ob.risk_reward_ratio}</span>
            </div>
          </div>

          <!-- Status Footer -->
          <div class="ob-footer">
            <span class="ob-status-pill ${statusClass}">
              <span class="legend-dot ${ob.retest_status === "IN_ZONE_TRIGGER" ? "target" : ob.retest_status === "APPROACHING_ZONE" ? "entry" : "stop"}"></span>
              ${esc(ob.retest_label)}
            </span>
            <div style="display:flex; gap:6px;">
              <button type="button" class="btn sm" onclick="openChartModal('${esc(ob.symbol)}')">${ICONS.chart} Chart</button>
              <button type="button" class="btn sm primary" onclick="copyOrderBlockParams('${esc(ob.symbol)}', '${isBull ? "BUY" : "SELL"}', ${ob.zone_high}, ${ob.invalidation}, ${ob.target_2})">${ICONS.copy} Copy</button>
            </div>
          </div>
        </div>
      `;
    };

    // Render Bullish Board
    if (bullList) {
      if (!bullBoard.length) {
        bullList.innerHTML = `<p class="muted small">No clean bullish order blocks detected in current session.</p>`;
      } else {
        bullList.innerHTML = bullBoard.map(renderObCard).join("");
      }
    }

    // Render Bearish Board
    if (bearList) {
      if (!bearBoard.length) {
        bearList.innerHTML = `<p class="muted small">No clean bearish order blocks detected in current session.</p>`;
      } else {
        bearList.innerHTML = bearBoard.map(renderObCard).join("");
      }
    }

  } catch (err) {
    console.error("Order Block fetch error:", err);
    if (mpBox) mpBox.innerHTML = `<div class="banner danger">Could not load Order Blocks: ${esc(err.message)}</div>`;
  } finally {
    if (spinner) spinner.style.display = "none";
  }
}

function copyOrderBlockParams(symbol, side, entry, sl, target) {
  const text = [
    `Symbol: ${symbol}`,
    `Action: ${side}`,
    `Order Type: LIMIT`,
    `Zone Entry: ₹${entry}`,
    `Stop Loss (S1/R1): ₹${sl}`,
    `Target Liquidity: ₹${target}`,
    `Product: MIS / CNC (Intraday / Positional)`,
  ].join("\n");

  navigator.clipboard.writeText(text).then(() => {
    toast(`Order parameters for ${symbol} copied to clipboard.`);
  }).catch(() => {
    toast(`Failed to copy to clipboard`);
  });
}

/* ================= Live Intraday Scalper & Capital Maximizer ================= */
$("#refresh-liveintraday-btn")?.addEventListener("click", loadLiveIntraday);

async function loadLiveIntraday() {
  const spinner = $("#intraday-spinner");
  if (spinner) spinner.style.display = "inline-block";
  const hudPhase = $("#hud-session-phase");
  const hudCutoff = $("#hud-squareoff-timer");
  const hudLeverage = $("#hud-leverage-mode");
  const hudBreadth = $("#hud-breadth");
  const guidanceBox = $("#intraday-guidance");
  const longList = $("#intraday-long-list");
  const shortList = $("#intraday-short-list");

  try {
    const budget = state.budget || 50000;
    const riskPct = state.riskPct || 1.0;
    const res = await fetch(`/api/intraday-scalp-setups?budget=${budget}&risk_pct=${riskPct}`).then(r => r.json());

    const session = res.session || {};
    const rules = res.capital_rules || {};
    const breadth = res.breadth || {};
    const longSetups = res.long_setups || [];
    const shortSetups = res.short_setups || [];

    // 1. Update HUD
    if (hudPhase) {
      hudPhase.textContent = session.label || "Market Hours";
    }
    if (hudCutoff) {
      if (session.minutes_to_cutoff > 0) {
        hudCutoff.textContent = `${session.minutes_to_cutoff}m to ${session.auto_exit_time || "15:15 IST"}`;
      } else {
        hudCutoff.textContent = "Session Closed";
      }
    }
    if (hudLeverage) {
      hudLeverage.textContent = `${rules.leverage_multiplier || 5}x MIS Margin (1R Max ₹${(rules.max_risk_inr || 500).toLocaleString("en-IN")})`;
    }
    if (hudBreadth) {
      hudBreadth.textContent = `${breadth.advancing || 0} Adv / ${breadth.declining || 0} Dec (${breadth.total_scanned || 0} liquid)`;
    }
    if (guidanceBox) {
      guidanceBox.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
          <div>
            <strong>Session Window (${esc(session.current_ist || "")}):</strong> ${esc(session.guidance || "Evaluate momentum against VWAP anchors.")}
          </div>
          <span class="pill ${session.can_enter_new_trades ? "bull" : "bear"}">
            ${session.can_enter_new_trades ? "New Entries Open" : "Entry Window Closed"}
          </span>
        </div>
      `;
    }

    // 2. Update Counts
    if ($("#intraday-long-count")) $("#intraday-long-count").textContent = `${longSetups.length} Setups`;
    if ($("#intraday-short-count")) $("#intraday-short-count").textContent = `${shortSetups.length} Setups`;

    // 3. Render Card Builder
    const renderIntradayCard = (s) => {
      const isLong = s.action === "BUY";
      const actionPill = isLong ? "long" : "short";
      const scoreClass = s.momentum_score >= 65 ? "high" : s.momentum_score <= 35 ? "low" : "";

      return `
        <div class="intraday-card" id="intraday-card-${esc(s.symbol)}">
          <div class="intraday-card-header">
            <div class="intraday-badge-group">
              <span class="intraday-action-pill ${actionPill}">
                <span class="legend-dot ${isLong ? "target" : "stop"}"></span>
                ${isLong ? "BUY MIS" : "SHORT MIS"}
              </span>
              <span class="intraday-setup-tag">${esc(s.setup_label)}</span>
            </div>
            <div style="display:flex; gap:6px; align-items:center;">
              <span class="intraday-mscore-pill ${scoreClass}" title="Momentum Flow Score: ${s.momentum_score}/100">
                Score: ${s.momentum_score}
              </span>
              <span class="pill" style="font-size:11px; font-weight:700;">${Math.round(s.confidence * 100)}% Conv</span>
            </div>
          </div>

          <div class="intraday-symbol-row">
            <div>
              <span class="intraday-sym">${esc(s.symbol)}</span>
              <span class="intraday-sec">${esc(s.sector)}</span>
            </div>
            <div class="intraday-price-wrap">
              <div class="intraday-ltp">${fmtINR2(s.ltp)}</div>
              <div style="font-size:12px;">${fmtPct(s.change_pct)}</div>
            </div>
          </div>

          <div class="intraday-telemetry-row">
            <div class="intraday-telemetry-item">
              <span>VWAP:</span>
              <strong>₹${s.vwap}</strong>
              <span class="muted" style="font-size:11px;">(${s.vwap_dist_pct > 0 ? "+" : ""}${s.vwap_dist_pct}%)</span>
            </div>
            <div class="intraday-telemetry-item">
              <span>RVOL:</span>
              <strong style="color:var(--accent);">${s.volume_velocity}x</strong>
            </div>
            <div class="intraday-telemetry-item">
              <span>R:R:</span>
              <strong style="color:var(--emerald-dark);">1 : ${s.risk_reward_ratio}</strong>
            </div>
          </div>

          <div class="intraday-rationale">
            ${esc(s.rationale)}
          </div>

          <!-- Levels Grid -->
          <div class="intraday-levels-grid">
            <div class="intraday-lvl-item">
              <span class="intraday-lvl-k">Entry Price</span>
              <span class="intraday-lvl-v entry">₹${s.entry_price}</span>
            </div>
            <div class="intraday-lvl-item">
              <span class="intraday-lvl-k">Stop Loss</span>
              <span class="intraday-lvl-v stop">₹${s.stop_loss}</span>
            </div>
            <div class="intraday-lvl-item">
              <span class="intraday-lvl-k">Target 1 (1:2)</span>
              <span class="intraday-lvl-v target">₹${s.target_1}</span>
            </div>
            <div class="intraday-lvl-item">
              <span class="intraday-lvl-k">Target 2 (Runner)</span>
              <span class="intraday-lvl-v target">₹${s.target_2}</span>
            </div>
          </div>

          <!-- Capital Maximizer Box -->
          <div class="intraday-maximizer-box">
            <div class="intraday-maximizer-header">
              <span>5x MIS Capital Maximizer</span>
              <span style="color:var(--muted-fg); font-size:10px;">Zerodha / Groww Margin</span>
            </div>
            <div class="intraday-maximizer-grid">
              <div class="intraday-max-col">
                <span class="intraday-max-k">Required Margin</span>
                <span class="intraday-max-v">₹${(s.margin_required_inr || 0).toLocaleString("en-IN")}</span>
                <span class="intraday-max-sub">${s.shares_5x} shares (vs ${s.shares_1x} cash)</span>
              </div>
              <div class="intraday-max-col">
                <span class="intraday-max-k">Target 1 Gain</span>
                <span class="intraday-max-v gain">+₹${(s.potential_gain_inr || 0).toLocaleString("en-IN")}</span>
                <span class="intraday-max-sub" style="color:var(--emerald-dark); font-weight:700;">+${s.potential_roi_pct}% ROI on margin</span>
              </div>
              <div class="intraday-max-col">
                <span class="intraday-max-k">Max Risk Capped</span>
                <span class="intraday-max-v" style="color:var(--rose-dark);">₹${(s.max_risk_inr || 0).toLocaleString("en-IN")}</span>
                <span class="intraday-max-sub">Risk Budget: ${s.risk_pct}%</span>
              </div>
            </div>
          </div>

          <!-- Footer Actions -->
          <div class="intraday-card-footer">
            <span class="intraday-risk-tag">Mandatory auto-exit: <strong>15:15 IST</strong></span>
            <div style="display:flex; gap:6px;">
              <button type="button" class="btn sm" onclick="openChartModal('${esc(s.symbol)}')">${ICONS.chart} Chart</button>
              <button type="button" class="btn sm primary" onclick="copyIntradayTicket('${esc(s.symbol)}', '${s.action}', ${s.shares_5x}, ${s.entry_price}, ${s.stop_loss}, ${s.target_1}, ${s.target_2}, ${s.potential_gain_inr}, ${s.potential_roi_pct})">${ICONS.copy} Copy MIS Ticket</button>
            </div>
          </div>
        </div>
      `;
    };

    // Render Long Board
    if (longList) {
      if (!longSetups.length) {
        longList.innerHTML = `<p class="muted small">No liquid equities currently meeting bullish VWAP breakout criteria.</p>`;
      } else {
        longList.innerHTML = longSetups.map(renderIntradayCard).join("");
      }
    }

    // Render Short Board
    if (shortList) {
      if (!shortSetups.length) {
        shortList.innerHTML = `<p class="muted small">No liquid equities currently meeting bearish breakdown criteria.</p>`;
      } else {
        shortList.innerHTML = shortSetups.map(renderIntradayCard).join("");
      }
    }

    // Load Autonomous Desk Terminal
    loadDeskTerminal();

  } catch (err) {
    console.error("Live intraday fetch error:", err);
    if (guidanceBox) guidanceBox.innerHTML = `<div class="banner danger">Could not load Live Intraday Setups: ${esc(err.message)}</div>`;
  } finally {
    if (spinner) spinner.style.display = "none";
  }
}

async function loadDeskTerminal() {
  const container = $("#desk-terminal-content");
  if (!container) return;

  try {
    const statusRes = await fetch("/api/desk/status").then(r => r.json());
    const phaseEl = $("#desk-hud-phase");
    const pnlEl = $("#desk-hud-pnl");
    const hitrateEl = $("#desk-hud-hitrate");
    const guardEl = $("#desk-hud-guard");

    if (phaseEl) phaseEl.textContent = statusRes.phase || "STANDBY";
    if (guardEl) guardEl.textContent = `₹${(statusRes.max_daily_drawdown_limit_inr || 2000).toLocaleString("en-IN")} Cap (4%)`;

    const summary = statusRes.summary || {};
    const netPnl = summary.net_gain_inr ?? 0;
    if (pnlEl) {
      pnlEl.textContent = `${netPnl >= 0 ? "+" : ""}₹${netPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}`;
      pnlEl.style.color = netPnl >= 0 ? "var(--emerald-dark)" : "var(--rose-dark)";
    }

    const domain = summary.domain_breakdown || {};
    if (hitrateEl) {
      hitrateEl.textContent = `${domain.long_hit_rate_pct || 0}% Long / ${domain.short_hit_rate_pct || 0}% Short`;
    }

    const trades = summary.trades || [];
    if (!trades.length) {
      container.innerHTML = `<p class="muted small">Autonomous desk waiting for session triggers. No trades executed yet today.</p>`;
      return;
    }

    let html = `
      <div style="overflow-x:auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Action</th>
              <th>Qty</th>
              <th>Entry (₹)</th>
              <th>Exit (₹)</th>
              <th>Net P&amp;L (₹)</th>
              <th>Return (%)</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
    `;

    for (const t of trades) {
      const isWin = (t.pnl_inr || 0) > 0;
      const sideClass = (t.action === "SHORT" || t.side === "SELL") ? "pill bear" : "pill bull";
      html += `
        <tr>
          <td><strong>${esc(t.symbol)}</strong></td>
          <td><span class="${sideClass}">${esc(t.action || t.side || "BUY")}</span></td>
          <td>${t.quantity}</td>
          <td>₹${Number(t.entry_price).toFixed(2)}</td>
          <td>₹${Number(t.exit_price).toFixed(2)}</td>
          <td style="font-weight:700; color:${isWin ? 'var(--emerald-dark)' : 'var(--rose-dark)'}">
            ${t.pnl_inr >= 0 ? '+' : ''}₹${Number(t.pnl_inr).toFixed(2)}
          </td>
          <td>${t.return_pct >= 0 ? '+' : ''}${Number(t.return_pct).toFixed(2)}%</td>
          <td><span class="pill ${isWin ? 'bull' : 'bear'}">${isWin ? 'PROFIT' : 'LOSS (1R Capped)'}</span></td>
        </tr>
      `;
    }

    html += `
          </tbody>
        </table>
      </div>
      <div style="margin-top:12px; font-size:12px; color:var(--muted-fg); display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px;">
        <span>Session Net: <strong>${netPnl >= 0 ? '+' : ''}₹${netPnl.toFixed(2)} (${summary.portfolio_roi_pct || 0}% ROI)</strong></span>
        <span>Hit Rate: <strong>${summary.hit_rate_pct || 0}% (${summary.winning_trades || 0}W / ${summary.losing_trades || 0}L)</strong></span>
      </div>
    `;

    container.innerHTML = html;
  } catch (err) {
    console.error("Desk terminal fetch error:", err);
    if (container) container.innerHTML = `<p class="muted small">Error loading desk terminal: ${esc(err.message)}</p>`;
  }
}

$("#refresh-desk-btn")?.addEventListener("click", loadDeskTerminal);

function copyIntradayTicket(symbol, action, shares, entry, sl, target1, target2, gain, roi) {
  const text = [
    `=== NSE INTRADAY TICKET (ADVISORY ONLY) ===`,
    `Symbol: ${symbol}`,
    `Product: MIS (Intraday Margin)`,
    `Action: ${action}`,
    `Quantity: ${shares} shares (5x Sized)`,
    `Limit Entry: ₹${entry}`,
    `Stop Loss: ₹${sl}`,
    `Target 1 (1:2 R:R): ₹${target1} (+₹${gain} / +${roi}% ROI)`,
    `Target 2 (Runner): ₹${target2}`,
    `Session Cutoff: Mandatory exit before 15:15 IST`,
    `Notice: Advisory only. Review and place manually in broker terminal.`,
  ].join("\n");

  navigator.clipboard.writeText(text).then(() => {
    toast(`MIS ticket parameters for ${symbol} copied to clipboard.`);
  }).catch(() => {
    toast(`Failed to copy to clipboard`);
  });
}

/* ================= Time Machine & Multi-Scenario Simulator  ================= */
let simHorizonDays = 21;

$$("#sim-horizon-group button")?.forEach(btn => {
  btn.addEventListener("click", () => {
    $$("#sim-horizon-group button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    simHorizonDays = parseInt(btn.dataset.horizon, 10) || 21;
    loadSimulation();
  });
});

$("#refresh-sim-btn")?.addEventListener("click", loadSimulation);

async function loadSimulation() {
  const spinner = $("#sim-spinner");
  if (spinner) spinner.style.display = "inline-block";
  const hudRegime = $("#sim-hud-regime");
  const hudDist = $("#sim-hud-dist");
  const hudPop = $("#sim-hud-pop");
  const hudExposure = $("#sim-hud-exposure");
  const scenariosBox = $("#sim-scenarios-list");
  const mcStatsBox = $("#sim-mc-stats");
  const coneBox = $("#sim-cone-container");
  const distContent = $("#sim-dist-content");
  const coachContent = $("#sim-coach-content");
  const distBadge = $("#sim-dist-badge");
  const coachBadge = $("#sim-coach-badge");
  const horizonBadge = $("#sim-horizon-badge");

  try {
    const budget = state.budget || 50000;
    const riskPct = state.riskPct || 1.0;
    const res = await fetch(`/api/scenarios/simulate?budget=${budget}&risk_pct=${riskPct}&horizon=${simHorizonDays}`).then(r => r.json());

    const mc = res.monte_carlo || {};
    const dd = res.distribution_days || {};
    const coach = res.exposure_coach || {};
    const scenarios = res.scenarios || [];

    // 1. Update HUD
    if (hudRegime) hudRegime.textContent = `${(res.regime || "risk-on").toUpperCase()} (VIX ${res.vix_ltp || 16})`;
    if (hudDist) hudDist.textContent = `${dd.distribution_count || 0} of 25 (${dd.status || "LOW_RISK"})`;
    if (hudPop) hudPop.textContent = `${mc.probability_of_profit_pct || 0}% (${mc.expected_roi_pct > 0 ? "+" : ""}${mc.expected_roi_pct || 0}%)`;
    if (hudExposure) hudExposure.textContent = `${coach.recommended_equity_exposure_pct || 80}% Eq / ${coach.recommended_cash_pct || 20}% Cash`;
    if (horizonBadge) horizonBadge.textContent = mc.horizon_label || `${simHorizonDays} Sessions`;

    // 2. Render 4 Branching Scenarios
    if (scenariosBox) {
      scenariosBox.innerHTML = scenarios.map(sc => {
        const isPos = sc.simulated_net_gain_inr >= 0;
        const valClass = isPos ? "gain" : "loss";
        const sign = sc.simulated_net_gain_inr > 0 ? "+" : "";

        return `
          <div class="sim-scenario-card ${esc(sc.name)}">
            <div class="sim-sc-header">
              <h4 class="sim-sc-title">${esc(sc.label)}</h4>
              <span class="sim-prob-badge">${sc.probability_pct}% Prob</span>
            </div>

            <div class="sim-metric-strip">
              <div class="sim-metric-col">
                <span class="sim-metric-k">Nifty Index</span>
                <span class="sim-metric-v ${sc.nifty_index_move_pct >= 0 ? "gain" : "loss"}">${sc.nifty_index_move_pct > 0 ? "+" : ""}${sc.nifty_index_move_pct}%</span>
              </div>
              <div class="sim-metric-col">
                <span class="sim-metric-k">Breadth</span>
                <span class="sim-metric-v">${sc.market_breadth_adv_pct}% Adv</span>
              </div>
              <div class="sim-metric-col">
                <span class="sim-metric-k">Simulated P&amp;L</span>
                <span class="sim-metric-v ${valClass}">${sign}₹${Math.round(sc.simulated_net_gain_inr).toLocaleString("en-IN")}</span>
              </div>
            </div>

            <div class="sim-impact-box">
              <div><strong>1st Order (Price):</strong> ${esc(sc.first_order_impact)}</div>
              <div style="margin-top:4px;"><strong>2nd Order (Sector):</strong> ${esc(sc.second_order_impact)}</div>
              <div style="margin-top:4px;"><strong>3rd Order (Flow):</strong> ${esc(sc.third_order_impact)}</div>
            </div>

            <div style="font-size:11px;">
              <span class="muted">Favored Strategies:</span>
              <strong style="color:var(--ink);">${sc.favored_strategies.join(", ")}</strong>
            </div>

            <div class="sim-directive-box">
              <strong>Advisor Tactical Action:</strong> ${esc(sc.tactical_action)}
            </div>
          </div>
        `;
      }).join("");
    }

    // 3. Render Monte Carlo Stats
    if (mcStatsBox) {
      mcStatsBox.innerHTML = `
        <div class="sim-stat-card">
          <span class="sim-stat-k">Probability of Profit</span>
          <span class="sim-stat-v" style="color:var(--emerald-dark);">${mc.probability_of_profit_pct}%</span>
          <span class="sim-stat-sub">1,000 forward paths</span>
        </div>
        <div class="sim-stat-card">
          <span class="sim-stat-k">Expected Net Gain</span>
          <span class="sim-stat-v ${mc.expected_net_gain_inr >= 0 ? "gain" : "loss"}">${mc.expected_net_gain_inr >= 0 ? "+" : ""}₹${Math.round(mc.expected_net_gain_inr).toLocaleString("en-IN")}</span>
          <span class="sim-stat-sub">${mc.expected_roi_pct > 0 ? "+" : ""}${mc.expected_roi_pct}% Mean ROI</span>
        </div>
        <div class="sim-stat-card">
          <span class="sim-stat-k">95th Percentile (Optimistic)</span>
          <span class="sim-stat-v" style="color:var(--accent);">₹${Math.round(mc.percentile_95_equity_inr).toLocaleString("en-IN")}</span>
          <span class="sim-stat-sub">Top 5% trajectory</span>
        </div>
        <div class="sim-stat-card">
          <span class="sim-stat-k">5th Percentile (Stress Floor)</span>
          <span class="sim-stat-v" style="color:var(--rose-dark);">₹${Math.round(mc.percentile_5_equity_inr).toLocaleString("en-IN")}</span>
          <span class="sim-stat-sub">Worst 5% tail risk</span>
        </div>
        <div class="sim-stat-card">
          <span class="sim-stat-k">Max Simulated DD</span>
          <span class="sim-stat-v" style="color:var(--amber-dark);">${mc.max_simulated_drawdown_pct}%</span>
          <span class="sim-stat-sub">Worst path peak-to-trough</span>
        </div>
        <div class="sim-stat-card">
          <span class="sim-stat-k">Risk of Ruin (>15% Loss)</span>
          <span class="sim-stat-v" style="color:${mc.risk_of_ruin_pct <= 2 ? "var(--emerald-dark)" : "var(--rose-dark)"};">${mc.risk_of_ruin_pct}%</span>
          <span class="sim-stat-sub">Strict 1R rules guardrail</span>
        </div>
      `;
    }

    // 4. Render Step Cone Table
    if (coneBox && mc.time_series_percentiles) {
      const cones = mc.time_series_percentiles;
      const steps = cones.steps || [];
      let tableHtml = `
        <div style="overflow-x:auto;">
          <table class="sim-cone-table">
            <thead>
              <tr>
                <th>Trade Milestone</th>
                <th>5th Pct (Floor)</th>
                <th>25th Pct (Cons)</th>
                <th>50th Pct (Median)</th>
                <th>75th Pct (Growth)</th>
                <th>95th Pct (Peak)</th>
              </tr>
            </thead>
            <tbody>
      `;
      for (let i = 0; i < steps.length; i++) {
        tableHtml += `
          <tr>
            <td>Step ${steps[i]} (${Math.round((steps[i] / (steps[steps.length - 1] || 1)) * simHorizonDays)}D)</td>
            <td style="color:var(--rose-dark);">₹${Math.round(cones.p5[i] || 0).toLocaleString("en-IN")}</td>
            <td>₹${Math.round(cones.p25[i] || 0).toLocaleString("en-IN")}</td>
            <td style="font-weight:700; color:var(--ink);">₹${Math.round(cones.p50[i] || 0).toLocaleString("en-IN")}</td>
            <td>₹${Math.round(cones.p75[i] || 0).toLocaleString("en-IN")}</td>
            <td style="color:var(--accent); font-weight:700;">₹${Math.round(cones.p95[i] || 0).toLocaleString("en-IN")}</td>
          </tr>
        `;
      }
      tableHtml += `
            </tbody>
          </table>
        </div>
      `;
      coneBox.innerHTML = tableHtml;
    }

    // 5. Render Distribution Days
    if (distBadge) distBadge.textContent = `${dd.distribution_count || 0} / ${dd.rolling_window_days || 25} Days`;
    if (distContent) {
      const days = dd.distribution_days || [];
      distContent.innerHTML = `
        <div style="margin-bottom:10px;">
          <span class="pill ${dd.status === "LOW_RISK" ? "bull" : dd.status === "CAUTION" ? "caution" : "bear"}">
            ${esc(dd.status_label || "Normal")}
          </span>
          <p class="small muted" style="margin:8px 0 12px 0;">${esc(dd.guidance || "")}</p>
        </div>
        ${days.length ? `
          <div style="overflow-x:auto; max-height:180px;">
            <table class="sim-dist-table">
              <thead>
                <tr>
                  <th>Session Date</th>
                  <th>Nifty Close</th>
                  <th>Session Drop</th>
                  <th>Volume Surge</th>
                </tr>
              </thead>
              <tbody>
                ${days.map(d => `
                  <tr>
                    <td>${esc(d.date)}</td>
                    <td class="font-mono">₹${d.close}</td>
                    <td class="font-mono" style="color:var(--rose-dark);">${d.change_pct}%</td>
                    <td class="font-mono" style="color:var(--accent);">${d.volume_ratio}x</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        ` : `<p class="muted small">Zero institutional distribution events detected in the rolling 25-session window.</p>`}
      `;
    }

    // 6. Render Exposure & Portfolio Heat Coach
    if (coachBadge) coachBadge.textContent = esc(coach.coaching_verdict || "Optimal");
    if (coachContent) {
      coachContent.innerHTML = `
        <div style="margin-bottom:12px;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span class="small font-mono">Recommended Capital Deployment:</span>
            <strong>${coach.recommended_equity_exposure_pct}% Equity / ${coach.recommended_cash_pct}% Cash</strong>
          </div>
          <div class="progress-bar-wrap" style="height:8px; background:var(--bg-subtle); border-radius:4px; overflow:hidden;">
            <div style="width:${coach.recommended_equity_exposure_pct}%; background:var(--emerald); height:100%;"></div>
          </div>
        </div>

        <div class="sim-metric-strip" style="margin-bottom:12px;">
          <div class="sim-metric-col">
            <span class="sim-metric-k">Max Positions</span>
            <span class="sim-metric-v">${coach.max_active_positions} Concurrent</span>
          </div>
          <div class="sim-metric-col">
            <span class="sim-metric-k">Per-Trade Risk</span>
            <span class="sim-metric-v" style="color:var(--rose-dark);">₹${coach.risk_budget_per_trade_inr}</span>
          </div>
          <div class="sim-metric-col">
            <span class="sim-metric-k">Total Heat Limit</span>
            <span class="sim-metric-v" style="color:var(--accent);">₹${coach.portfolio_heat_limit_inr}</span>
          </div>
        </div>

        <ul class="checklist" style="margin:0; padding-left:18px;">
          ${(coach.action_items || []).map(act => `<li class="small muted" style="margin-bottom:4px;">${esc(act)}</li>`).join("")}
        </ul>
      `;
    }

  } catch (err) {
    console.error("Simulation error:", err);
    if (scenariosBox) scenariosBox.innerHTML = `<div class="banner danger">Could not run scenario simulation: ${esc(err.message)}</div>`;
  } finally {
    if (spinner) spinner.style.display = "none";
  }
}

// Initial fetch on page load
fetchSuggestions().catch(() => {});


/* ================= 3-Year Historical Valuation & Market News Screener ================= */
let valuationData = null;
let valuationFilter = "all";
let valuationSearch = "";

async function loadValuation(force = false) {
  const tbody = $("#valuation-table-body");
  if (!tbody) return;

  try {
    tbody.innerHTML = `<tr><td colspan="11" style="padding:24px; text-align:center; color:var(--muted-fg);">Loading 3-year valuation multiples & market news catalysts…</td></tr>`;

    const [valResp, newsResp] = await Promise.all([
      fetch(`/api/valuation/cheaper-stocks${force ? "?force=true" : ""}`).then(r => r.json()),
      fetch(`/api/news/market-catalysts${force ? "?force=true" : ""}`).then(r => r.json()),
    ]);

    valuationData = valResp;
    const summary = valResp.summary || {};
    const metrics = valResp.universe_metrics || [];
    const catalysts = newsResp.catalysts || [];
    const symNewsMap = newsResp.symbol_sentiment_map || {};

    // Update HUD
    const avgPeEl = $("#val-avg-pe");
    if (avgPeEl) avgPeEl.textContent = summary.universe_average_pe ? summary.universe_average_pe.toFixed(1) + "x" : "--";
    const cheapCountEl = $("#val-cheaper-count");
    if (cheapCountEl) cheapCountEl.textContent = summary.cheaper_stocks_count || 0;
    const deepCountEl = $("#val-deep-count");
    if (deepCountEl) deepCountEl.textContent = summary.deep_value_count || 0;
    const catCountEl = $("#val-catalysts-count");
    if (catCountEl) catCountEl.textContent = newsResp.total_catalysts_analyzed || catalysts.length;

    // Macro News Pill
    const macroPill = $("#val-macro-pill");
    if (macroPill) {
      const score = newsResp.market_sentiment_score || 0;
      const regime = newsResp.macro_news_regime || "NEUTRAL";
      const icon = score >= 0.2 ? "▲" : score <= -0.2 ? "▼" : "■";
      const color = score >= 0.2 ? "#10b981" : score <= -0.2 ? "#f43f5e" : "#f59e0b";
      macroPill.innerHTML = `<span style="color:${color}; font-weight:700;">${icon} Macro News: ${regime} (${score > 0 ? "+" : ""}${score.toFixed(2)})</span>`;
    }

    renderValuationTable(metrics, symNewsMap);
    renderNewsCatalysts(catalysts);
    wireValuationControls(metrics, symNewsMap);

  } catch (err) {
    console.error("Valuation screener error:", err);
    tbody.innerHTML = `<tr><td colspan="11" style="padding:24px; text-align:center; color:#f43f5e;">Failed to load valuation data: ${esc(err.message)}</td></tr>`;
  }
}

function renderValuationTable(metrics, symNewsMap) {
  const tbody = $("#valuation-table-body");
  if (!tbody) return;

  const q = (valuationSearch || "").toLowerCase().trim();
  const filtered = (metrics || []).filter(m => {
    // Search match
    if (q && !m.symbol.toLowerCase().includes(q) && !(m.company_name || "").toLowerCase().includes(q) && !(m.sector || "").toLowerCase().includes(q)) {
      return false;
    }
    // Filter match
    if (valuationFilter === "cheaper") {
      return m.cheapness_category === "CHEAPER_THAN_USUAL" || m.cheapness_category === "DEEP_VALUE";
    }
    if (valuationFilter === "deep_value") {
      return m.cheapness_category === "DEEP_VALUE" || (m.pe_zscore !== null && m.pe_zscore <= -1.2);
    }
    if (valuationFilter === "catalyst") {
      const sent = symNewsMap[m.symbol] || 0;
      return sent >= 0.25;
    }
    return true;
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="11" style="padding:28px; text-align:center; color:var(--muted-fg);">No stocks match the selected filter criteria.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(m => {
    const sym = esc(m.symbol);
    const sec = esc(m.sector || "Large Cap");
    const price = m.current_price ? "₹" + m.current_price.toLocaleString("en-IN", { minimumFractionDigits: 2 }) : "--";
    const curPe = m.pe_current ? m.pe_current.toFixed(1) + "x" : "--";
    const meanPe = m.pe_3y_mean ? m.pe_3y_mean.toFixed(1) + "x" : "--";

    // Discount %
    const disc = m.pe_discount_pct;
    let discHtml = "--";
    if (disc !== null && disc !== undefined) {
      const dColor = disc > 0 ? "color:#10b981; font-weight:700;" : disc < 0 ? "color:#f43f5e;" : "color:var(--muted-fg);";
      discHtml = `<span style="${dColor}">${disc > 0 ? "+" : ""}${disc.toFixed(1)}%</span>`;
    }

    // Z-Score Badge
    const z = m.pe_zscore;
    let zBadge = `<span class="badge badge-fair">N/A</span>`;
    if (z !== null && z !== undefined) {
      if (z <= -1.2) zBadge = `<span class="badge badge-deep-value">${z > 0 ? "+" : ""}${z.toFixed(2)}σ</span>`;
      else if (z <= -0.5) zBadge = `<span class="badge badge-cheaper">${z > 0 ? "+" : ""}${z.toFixed(2)}σ</span>`;
      else if (z >= 1.2) zBadge = `<span class="badge badge-overvalued">${z > 0 ? "+" : ""}${z.toFixed(2)}σ</span>`;
      else zBadge = `<span class="badge badge-fair">${z > 0 ? "+" : ""}${z.toFixed(2)}σ</span>`;
    }

    // Percentile
    const pct = m.pe_percentile !== null && m.pe_percentile !== undefined ? m.pe_percentile.toFixed(0) + "th" : "--";

    // P/B vs 3Y
    const curPb = m.pb_current ? m.pb_current.toFixed(2) + "x" : "--";
    const meanPb = m.pb_3y_mean ? m.pb_3y_mean.toFixed(2) + "x" : "--";

    // Graham IV & Margin of Safety
    const giv = m.graham_iv ? "₹" + m.graham_iv.toLocaleString("en-IN") : "--";
    const mos = m.margin_of_safety_pct;
    let mosHtml = "--";
    if (mos !== null && mos !== undefined) {
      const mColor = mos >= 15 ? "color:#10b981; font-weight:700;" : mos > 0 ? "color:#38bdf8;" : "color:var(--muted-fg);";
      mosHtml = `<span style="${mColor}">${mos > 0 ? "+" : ""}${mos.toFixed(1)}%</span>`;
    }

    // News Catalyst
    const newsSent = symNewsMap[m.symbol];
    let newsBadge = `<span class="small muted">Neutral</span>`;
    if (newsSent !== undefined) {
      if (newsSent >= 0.3) newsBadge = `<span class="badge badge-news-pos">▲ Bullish (+${newsSent.toFixed(2)})</span>`;
      else if (newsSent <= -0.25) newsBadge = `<span class="badge badge-news-neg">▼ Bearish (${newsSent.toFixed(2)})</span>`;
    }

    // Recommendation Badge
    let recBadge = `<span class="badge badge-fair">${esc(m.recommendation)}</span>`;
    if (m.recommendation === "STRONG_ACCUMULATE") {
      recBadge = `<span class="badge badge-deep-value">STRONG ACCUMULATE</span>`;
    } else if (m.recommendation === "VALUE_BUY") {
      recBadge = `<span class="badge badge-cheaper">VALUE BUY</span>`;
    } else if (m.recommendation === "AVOID_PREMIUM" || m.recommendation === "VALUE_TRAP_RISK") {
      recBadge = `<span class="badge badge-overvalued">${esc(m.recommendation.replace(/_/g, " "))}</span>`;
    }

    return `
      <tr style="border-bottom:1px solid var(--border); transition:background 0.1s;" onmouseover="this.style.background='var(--card-bg)'" onmouseout="this.style.background='transparent'">
        <td style="padding:12px 16px;">
          <div style="font-weight:700; color:var(--ink);">${sym}</div>
          <div class="small muted">${sec}</div>
        </td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono); font-weight:600;">${price}</td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono);">
          <span>${curPe}</span> <span class="muted small">/ ${meanPe}</span>
        </td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono);">${discHtml}</td>
        <td style="padding:12px 16px; text-align:center;">${zBadge}</td>
        <td style="padding:12px 16px; text-align:center; font-family:var(--font-mono); font-weight:600;">${pct}</td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono);">
          <span>${curPb}</span> <span class="muted small">/ ${meanPb}</span>
        </td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono);">${giv}</td>
        <td style="padding:12px 16px; text-align:right; font-family:var(--font-mono);">${mosHtml}</td>
        <td style="padding:12px 16px; text-align:center;">${newsBadge}</td>
        <td style="padding:12px 16px; text-align:center;">${recBadge}</td>
      </tr>
    `;
  }).join("");
}

function renderNewsCatalysts(catalysts) {
  const container = $("#news-catalysts-container");
  if (!container) return;

  if (!catalysts || catalysts.length === 0) {
    container.innerHTML = `<p class="muted small">No active news catalysts ingested.</p>`;
    return;
  }

  container.innerHTML = catalysts.map(c => {
    const isPos = c.sentiment_score >= 0.2;
    const isNeg = c.sentiment_score <= -0.2;
    const sentColor = isPos ? "#10b981" : isNeg ? "#f43f5e" : "#f59e0b";
    const sentLabel = isPos ? "POSITIVE CATALYST" : isNeg ? "NEGATIVE CATALYST" : "NEUTRAL / MACRO";

    return `
      <div class="catalyst-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
          <span style="font-size:10px; font-weight:700; color:${sentColor}; letter-spacing:0.5px;">${sentLabel} (${c.sentiment_score > 0 ? "+" : ""}${c.sentiment_score.toFixed(2)})</span>
          <span class="small muted">${esc(c.source || "Press")}</span>
        </div>
        <div style="font-weight:600; font-size:13px; color:var(--ink); margin-bottom:6px; line-height:1.4;">${esc(c.headline)}</div>
        <div class="small muted" style="margin-bottom:8px; line-height:1.4;">${esc(c.summary)}</div>
        <div style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
          ${(c.symbols_affected || []).map(s => `<span class="badge sm" style="font-size:10.5px; background:var(--card-bg); border:1px solid var(--border); font-weight:700;">${esc(s)}</span>`).join("")}
          <span class="small muted" style="margin-left:auto; font-size:10px;">${esc(c.category || "")}</span>
        </div>
      </div>
    `;
  }).join("");
}

function wireValuationControls(metrics, symNewsMap) {
  // Filter button listeners
  $$("#val-filter-group button").forEach(btn => {
    btn.onclick = () => {
      $$("#val-filter-group button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      valuationFilter = btn.dataset.filter || "all";
      renderValuationTable(metrics, symNewsMap);
    };
  });

  // Search input listener
  const searchInput = $("#val-search-input");
  if (searchInput) {
    searchInput.oninput = (e) => {
      valuationSearch = e.target.value;
      renderValuationTable(metrics, symNewsMap);
    };
  }

  // Refresh button listener
  const refreshBtn = $("#val-refresh-btn");
  if (refreshBtn) {
    refreshBtn.onclick = () => {
      toast("Refreshing 3-year valuation multiples & morning news feeds…");
      loadValuation(true);
    };
  }
}
