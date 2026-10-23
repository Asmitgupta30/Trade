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
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const C = {
  eq: "#1E3A8A", dd: "#EF4444", hit: "#10B981", ref: "#94A3B8",
  gateDefault: "#F59E0B", gateCandidate: "#8B5CF6",
  win: "#10B981", loss: "#EF4444", grid: "#E2E8F0"
};

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
    rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

/* ================= Tabs Navigation & Toast ================= */
$$(".tab").forEach(btn => btn.addEventListener("click", () => {
  $$(".tab").forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
  $$(".view").forEach(v => v.classList.remove("active"));
  btn.classList.add("active");
  btn.setAttribute("aria-selected", "true");
  $("#view-" + btn.dataset.view).classList.add("active");
  
  const loaders = {
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
      open: "🟢 NSE Open",
      pre_market: "🟡 Pre-Market (09:00 - 09:15)",
      post_market: "⚪ Post-Market Closing",
      closed: "⚪ NSE Closed",
    };
    phaseBadge.textContent = phaseNames[fs.market_phase] || `NSE ${fs.market_phase || "Status"}`;
  } catch {
    const pill = $("#feed-pill");
    pill.textContent = "Offline";
    pill.className = "pill stale";
  }
}
refreshFeed();
setInterval(refreshFeed, 30000);

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
  
  // Intraday model suggestions
  const intradayRaw = (data.suggestions || []).filter(s => s.action !== "NO_ACTION");
  const blockedIntraday = (data.suggestions || []).filter(s => s.action === "NO_ACTION");

  // Update counts on filter pills
  const totalCount = vcpPlans.length + burstPlans.length + hammerPlans.length + divPlans.length + intradayRaw.length;
  $("#count-all").textContent = totalCount;
  $("#count-intraday").textContent = intradayRaw.length;
  $("#count-swing_vcp").textContent = vcpPlans.length;
  $("#count-momentum_burst").textContent = burstPlans.length;
  $("#count-exhaustion_hammer").textContent = hammerPlans.length;
  $("#count-dividend_pullback").textContent = divPlans.length;

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
  if (active === "all" || active === "intraday") {
    itemsToRender.push(...intradayRaw.map(s => ({ ...s, isIntraday: true })));
  }

  // Sort by confidence descending
  itemsToRender.sort((a, b) => (b.confidence || 0) - (a.confidence || 0));

  let html = "";
  if (!itemsToRender.length) {
    html += `
      <div class="card empty-state">
        <div class="empty-icon">🛡️</div>
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
    SWING_VCP: { name: "Stage 2 Swing (VCP)", cls: "vcp", icon: "📈" },
    MOMENTUM_BURST: { name: "4% Momentum Burst", cls: "burst", icon: "🚀" },
    EXHAUSTION_HAMMER: { name: "Exhaustion Hammer", cls: "hammer", icon: "🔨" },
    DIVIDEND_PULLBACK: { name: "Dividend Aristocrat Dip", cls: "dividend", icon: "💰" },
  };
  const meta = stratKeys[plan.strategy] || { name: plan.strategy, cls: "vcp", icon: "⚡" };
  const confPct = Math.round((plan.confidence || 0) * 100);

  const checklistHtml = (plan.checklist || []).map(c => `
    <li class="checklist-item">
      <span class="checklist-icon">✓</span>
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
          <span class="strat-tag ${meta.cls}">${meta.icon} ${meta.name}</span>
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
        I Placed This Trade ✅
      </button>
      <button class="small skip" data-symbol="${esc(plan.symbol)}" data-act="skipped">
        Skip Idea
      </button>
    </div>
  </div>`;
}

function renderIntradayCard(s) {
  const pct = Math.round((s.confidence || 0) * 100);
  const qty = s.est_quantity || (s.reference_price ? Math.floor(s.amount_inr / s.reference_price) : 0);

  return `
  <div class="trade-card">
    <div class="trade-card-accent-bar intraday"></div>
    <div class="trade-card-header">
      <div>
        <div class="symbol-row">
          <span class="trade-sym">${esc(s.symbol)}</span>
          <span class="strat-tag intraday">⚡ Intraday ML Ensemble</span>
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
        I Bought This (Intraday) ✅
      </button>
      <button class="small skip" data-symbol="${esc(s.symbol)}" data-act="skipped">
        Skip Idea
      </button>
    </div>
  </div>`;
}

function wireTradeActionButtons() {
  $$(".actions-row button").forEach(btn => {
    btn.addEventListener("click", async () => {
      const took = btn.dataset.act === "took";
      const qty = parseInt(btn.dataset.qty, 10) || 1;
      const price = btn.dataset.price ? parseFloat(btn.dataset.price) : null;
      const body = {
        symbol: btn.dataset.symbol,
        suggestion_id: btn.dataset.sid || null,
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
        ${t.badge.includes("NOT") ? "⏳ Paper Practising (Advisory Gate)" : "✅ Live Ready"}
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
    const okCard = (ok, good, bad) => `<div class="metric"><div class="v ${ok ? "good" : "bad"}">${ok ? "🟢" : "⚠️"}</div><div class="k">${ok ? good : bad}</div></div>`;
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
        </div>
        <p class="muted small" style="margin-top:12px">
          Nifty 50: <strong>${p.nifty ? fmtINR(p.nifty.ltp) : "—"}</strong> · 
          India VIX: <strong>${p.india_vix ? p.india_vix.ltp.toFixed(2) : "—"}</strong> · 
          Regime: <strong>${esc(p.regime)}</strong>
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

/* ================= Mutual Funds ================= */
async function loadMF() {
  try {
    const mp = await fetch("/api/mf-patterns").then(r => r.json());
    const box = $("#mf-patterns");
    const cats = mp.categories || {};
    const names = {
      large_cap: "Large Cap Bluechip",
      mid_cap: "Mid Cap Emerging Leaders",
      debt: "Debt / Liquid & Gilt",
      gold_or_other: "Sovereign Gold / Multi-Asset"
    };
    let html = "";
    for (const cat of Object.keys(names)) {
      const rows = cats[cat] || [];
      html += `
        <div style="border-top:1px solid var(--border);padding:8px 0">
          <strong>${names[cat]}</strong>
          ${rows.length ? rows.map(r => `
            <div class="small" style="display:flex;justify-content:space-between;padding:3px 0">
              <span>${esc(r.scheme_name.length > 44 ? r.scheme_name.slice(0, 44) + "…" : r.scheme_name)}</span>
              ${fmtPct(r.return_1m_pct)}
            </div>`).join("") : `<div class="muted small">No sample schemes loaded.</div>`}
        </div>`;
    }
    if (mp.error) html = banner("Mutual fund NAV data downloading… check back shortly.", "warn");
    box.innerHTML = html;
  } catch {
    $("#mf-patterns").innerHTML = banner("Fund patterns unavailable.", "danger");
  }

  await renderMF("/api/mutual-funds", "GET", null);
}

$("#mf-plan").addEventListener("click", () => {
  renderMF("/api/mutual-funds", "POST", { new_money_inr: parseFloat($("#mf-new-money").value || "0") });
});

async function renderMF(url, method, body) {
  const out = $("#mf-output");
  try {
    const data = await fetch(url, method === "POST" ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    } : undefined).then(r => r.json());

    const bands = data.bands || {}, cur = data.current_alloc_pct || {}, tgt = data.target_alloc_pct || {};
    const names = { large_cap: "Large Cap", mid_cap: "Mid Cap", debt: "Debt/Liquid", gold_or_other: "Gold/Other" };
    const rows = ["large_cap", "mid_cap", "debt", "gold_or_other"].map(c => {
      const b = bands[c] || [0, 100], cv = cur[c];
      return `<tr><td>${names[c]}</td><td>${cv != null && !isNaN(cv) ? cv.toFixed(0) + "%" : "0%"}</td>
        <td>${b[0]}%–${b[1]}%</td><td>${tgt[c] != null ? tgt[c].toFixed(0) + "%" : "—"}</td></tr>`;
    }).join("");

    const sugRows = (data.suggestions || []).map(s => `
      <div class="card">
        <div class="card-head-row">
          <strong>${esc(s.scheme_name)}</strong>
          <span class="action-badge action-${s.action === "REDUCE" ? "EXIT" : "BUY"}">${s.action}</span>
        </div>
        ${s.amount_inr ? `<div class="sizing-v highlight" style="margin:6px 0">${fmtINR(s.amount_inr)} deployment</div>` : ""}
        <div class="trade-rationale">${esc(s.rationale)}</div>
      </div>`).join("");

    out.innerHTML = `
      <div class="card">
        <h3 style="margin-top:0">Portfolio Asset Allocation</h3>
        <table class="alloc"><tr><th>Asset Class</th><th>Current</th><th>Target Band</th><th>Suggested Aim</th></tr>${rows}</table>
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
          <div class="metric"><div class="v">${d.active}</div><div class="k">Retrain Iterations</div></div>
          <div class="metric"><div class="v">${d.promoted_count}</div><div class="k">Promoted ✅</div></div>
          <div class="metric"><div class="v">${d.rejected_count}</div><div class="k">Rejected ❌</div></div>
          <div class="metric"><div class="v">${d.voided_count}</div><div class="k">Voided</div></div>
        </div>
        <p class="muted small" style="margin-top:12px">Hard Lock: A candidate model only replaces the champion if it statistically improves on Sharpe, Hit Rate, and Drawdown simultaneously.</p>
      </div>`;
    for (const e of d.events || []) {
      const c = e.champion || {}, ch = e.challenger || {};
      html += `
        <div class="card">
          <div class="card-head-row">
            <strong>Retrain: ${esc((e.ts || "").slice(0, 10))}</strong>
            <span class="action-badge ${e.promoted ? "action-BUY" : "action-NO_ACTION"}">${e.promoted ? "Promoted Challenger ✅" : "Kept Champion"}</span>
          </div>
          <table class="alloc">
            <tr><th>Metric</th><th>Champion</th><th>Challenger</th></tr>
            <tr><td>Sharpe</td><td>${c.sharpe ?? "—"}</td><td>${ch.sharpe ?? "—"}</td></tr>
            <tr><td>Hit Rate</td><td>${c.hit_rate ?? "—"}</td><td>${ch.hit_rate ?? "—"}</td></tr>
            <tr><td>Max Drawdown</td><td>${c.max_dd_pct ?? "—"}%</td><td>${ch.max_dd_pct ?? "—"}%</td></tr>
          </table>
          <div class="muted small" style="margin-top:6px">${esc(e.reason || "")}</div>
        </div>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = banner("Could not load model history: " + e.message, "danger");
  }
}

// Initial fetch on page load
fetchSuggestions().catch(() => {});
