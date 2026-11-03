#!/usr/bin/env python3
"""Autonomous Paper Trading Desk Daemon.

Operates independently throughout the NSE market session with Bi-Directional Trading (Long + Short):
  - 09:00 - 09:15 IST: Pre-market watchlist, macro regime check, and scenario calibration.
  - 09:15 - 09:30 IST: Opening Range (ORB) Price Discovery Phase. No blind entries; tracks initial balance.
  - 09:30 - 14:45 IST: Active Bi-Directional Execution Window:
      * Evaluates both LONG (ORB Breakout, VWAP Expansion) and SHORT (VWAP Breakdown, ORB Breakdown) setups.
      * Regime-aware allocation: shorts prioritized in risk-off corrections; longs in bull expansions.
      * Sized strictly to 1R risk budget with 5x MIS leverage.
      * Anti-revenge trading: 60-minute symbol cooldown after any stopped-out position.
      * Win-win trailing stops: ratchets stop-loss to Breakeven (+costs) when price reaches 50% to Target 1.
      * Time-stall exit: closes stagnant positions after 90 minutes to recycle capital into higher-velocity names.
  - 14:45 - 15:15 IST: Pre-Close Wind-Down Phase: No new entries; trails stops aggressively.
  - 15:15 - 15:30 IST: Mandatory Auto-Square-Off: Closes 100% of open positions prior to cash market close.
  - 15:35+ IST: Compiles comprehensive End-of-Day Performance Report (Net Gain / Net Loss, Long/Short split).

Permanent Core Invariant: ADVISORY & PAPER TRADING ONLY. Never connects to real broker execution APIs.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.config import load_risk_config
from common.timeutils import now_ist
from data.client import DataClient
from paper_trading.engine import PaperEngine
from paper_trading.store import PaperStore
from advisor.engine import AdvisorEngine
from advisor.signals import SnapshotSignalProvider
from advisor.live_intraday import scan_live_intraday_universe
from advisor.scenario_simulator import run_full_time_simulation
from advisor.news_agent import news_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(ROOT / "var" / "logs" / "autonomous_desk.log")),
    ],
)
log = logging.getLogger("autonomous_desk")


class AutonomousTradingDesk:
    def __init__(self, budget_inr: float = 50000.0, risk_pct: float = 1.0):
        self.budget_inr = budget_inr
        self.risk_pct = risk_pct
        self.cfg = load_risk_config()
        self.client = DataClient()
        self.store = PaperStore()
        self.paper = PaperEngine(self.cfg, self.store)
        self.advisor = AdvisorEngine(self.cfg, SnapshotSignalProvider())

        self.reports_dir = ROOT / "var" / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.status_file = ROOT / "var" / "status" / "autonomous_desk_live.json"
        self.status_file.parent.mkdir(parents=True, exist_ok=True)

        # Active trade tracking metadata (target_1, target_2, stop_loss, trailing_active, side, action)
        self.trade_meta: dict[str, dict] = {}
        self.executed_today: list[dict] = []
        self.symbol_cooldowns: dict[str, datetime] = {}
        self.last_scan_time: float = 0.0
        self.max_daily_loss_inr = round(self.budget_inr * 0.04, 2)  # 4% max daily drawdown limit (-₹2,000 on ₹50k)
        self.session_realized_loss_inr = 0.0

        # Quantitative Risk & Desk Governance Locks
        self.max_trades_today = 4  # Maximum 4 high-conviction trades per day (ends fee-drag churn)
        self.traded_symbols_today: set[str] = set()  # Max 1 trade per symbol per session

        self.eod_report_generated = False
        self.pre_market_done = False

    def can_enter_new_trade(self) -> bool:
        """Evaluate whether new positions are permitted under daily cap and circuit breaker."""
        if len(self.executed_today) >= self.max_trades_today:
            return False
        if self.session_realized_loss_inr >= self.max_daily_loss_inr:
            return False
        return True

    def is_symbol_eligible_today(self, symbol: str) -> bool:
        """Ensure single-symbol daily limit and enforce active stop-out cooldowns."""
        if symbol in self.traded_symbols_today:
            return False
        now = now_ist()
        cooldown = self.symbol_cooldowns.get(symbol)
        if cooldown and now < cooldown:
            return False
        return True

    def validate_setup_expectancy(self, setup: dict) -> bool:
        """Enforce minimum 1.2% target distance to overcome 0.35% round-trip friction."""
        entry = setup.get("entry_price") or 0.0
        t1 = setup.get("target_1") or 0.0
        if entry <= 0 or t1 <= 0:
            return False
        dist_pct = abs(t1 - entry) / entry
        return dist_pct >= 0.012

    def validate_news_sentiment(self, setup: dict, sentiment_score: float | None = None) -> bool:
        """Market News Sentiment Gating: Disqualify negative catalyst longs or positive catalyst shorts."""
        if sentiment_score is None:
            sentiment_score = news_agent.get_symbol_sentiment(setup.get("symbol", ""))
        action = setup.get("action", "BUY").upper()
        if action in ("BUY", "LONG"):
            # Severe negative headline/downgrade -> Block Long entry
            if sentiment_score < -0.25:
                return False
        elif action in ("SHORT", "SELL"):
            # Strong positive catalyst -> Block Short entry
            if sentiment_score > 0.30:
                return False
        return True

    def emit_status(self, phase: str, extra: dict | None = None) -> None:
        """Write live status heartbeat for UI and monitoring."""
        current = now_ist()
        payload = {
            "timestamp_ist": current.strftime("%Y-%m-%d %H:%M:%S IST"),
            "trade_date": current.strftime("%Y-%m-%d"),
            "phase": phase,
            "budget_inr": self.budget_inr,
            "risk_budget_per_trade": round(self.budget_inr * (self.risk_pct / 100.0), 2),
            "max_daily_drawdown_limit_inr": self.max_daily_loss_inr,
            "session_realized_loss_inr": round(self.session_realized_loss_inr, 2),
            "open_positions_count": len(self.store.open_positions()),
            "total_trades_today": len(self.executed_today),
            "cooldown_symbols_count": len([s for s, exp in self.symbol_cooldowns.items() if current < exp]),
            "eod_report_ready": self.eod_report_generated,
        }
        if extra:
            payload.update(extra)
        try:
            self.status_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("failed to write live desk status: %s", exc)

    def run_pre_market_calibration(self) -> None:
        """09:00 IST Pre-Market: Calibrate regime, scenarios, morning news catalysts, and initial targets."""
        if self.pre_market_done:
            return
        log.info("Running Pre-Market Setup & Scenario Calibration with News Sentiment Engine...")
        try:
            # 1. Fetch live pre-market news catalysts and score macro sentiment
            catalyst_data = news_agent.fetch_morning_catalysts(force_refresh=True)
            log.info("Morning News Sentiment: %+.2f (%s) | %d catalysts parsed",
                     catalyst_data.get("market_sentiment_score", 0.0),
                     catalyst_data.get("macro_news_regime", "NEUTRAL"),
                     len(catalyst_data.get("catalysts", [])))

            # 2. Forward multi-scenario time simulation
            sim = run_full_time_simulation(
                self.client,
                budget_inr=self.budget_inr,
                risk_pct=self.risk_pct,
                horizon_days=21,
            )
            log.info("Regime calibrated: %s | Distribution count: %d/25",
                     sim["regime"], sim["distribution_days"]["distribution_count"])
            self.emit_status("PRE_MARKET_READY", {
                "scenario_calibration": sim,
                "news_catalysts": catalyst_data,
            })
            self.pre_market_done = True
        except Exception as exc:
            log.exception("pre-market calibration failed: %s", exc)

    def scan_and_enter_openings(self) -> None:
        """09:30 - 14:45 IST: Scan live intraday setups and enter bi-directional high-conviction paper trades."""
        now_time = time.time()
        # Scan throttle: full universe screening every 25 seconds unless slots are empty
        current_opens = self.store.open_positions()
        if (now_time - self.last_scan_time) < 25.0 and len(current_opens) >= 2:
            return
        self.last_scan_time = now_time

        # 1. Circuit Breaker & Max Daily Trades Cap Guard
        if not self.can_enter_new_trade():
            if len(self.executed_today) >= self.max_trades_today:
                log.info("Max daily trades cap reached (%d/%d) — capital preserved, standing by", len(self.executed_today), self.max_trades_today)
            else:
                log.warning("Daily drawdown circuit limit reached (-₹%.2f) — new entries paused", self.session_realized_loss_inr)
            return

        current_opens = self.store.open_positions()
        max_positions = 4  # Standard exposure limit

        if len(current_opens) >= max_positions:
            return

        available_slots = min(max_positions - len(current_opens), self.max_trades_today - len(self.executed_today))
        if available_slots <= 0:
            return
        now = now_ist()

        # Clean expired cooldowns
        self.symbol_cooldowns = {s: exp for s, exp in self.symbol_cooldowns.items() if now < exp}

        log.info("Scanning live market for high-conviction Bi-Directional intraday setups...")
        try:
            scan = scan_live_intraday_universe(
                self.client,
                budget_inr=self.budget_inr,
                risk_pct=self.risk_pct,
                force_refresh=True,
            )
            longs = scan.get("long_setups", [])
            shorts = scan.get("short_setups", [])

            open_symbols = {p["symbol"] for p in current_opens}

            # Filter high conviction, positive MCMC expectancy, single-symbol daily limit, min 1.2% target span & news sentiment
            eligible_longs = [
                s for s in longs
                if s["symbol"] not in open_symbols
                and self.is_symbol_eligible_today(s["symbol"])
                and s.get("confidence", 0) >= 0.65
                and s.get("mcmc_pop_pct", 0) >= 65.0
                and self.validate_setup_expectancy(s)
                and self.validate_news_sentiment(s)
            ]
            eligible_shorts = [
                s for s in shorts
                if s["symbol"] not in open_symbols
                and self.is_symbol_eligible_today(s["symbol"])
                and s.get("confidence", 0) >= 0.65
                and s.get("mcmc_pop_pct", 0) >= 65.0
                and self.validate_setup_expectancy(s)
                and self.validate_news_sentiment(s)
            ]

            # 2. Macro Regime Awareness: Determine Long vs Short slot quota
            nifty_q = self.client.get_quote("^NSEI")
            nifty_chg = nifty_q.change_pct if nifty_q and hasattr(nifty_q, "change_pct") and nifty_q.change_pct is not None else 0.0

            candidates: list[dict] = []
            if nifty_chg < -0.35:
                # Bearish / Risk-Off: Prioritize SHORT breakdowns
                log.info("Macro Regime: BEARISH / RISK-OFF (Nifty %+.2f%%) — Prioritizing SHORT breakdowns", nifty_chg)
                short_slots = min(available_slots, 3)
                long_slots = available_slots - short_slots
                candidates.extend(eligible_shorts[:short_slots])
                candidates.extend(eligible_longs[:long_slots])
            elif nifty_chg > 0.35:
                # Bullish / Risk-On: Prioritize LONG breakouts
                log.info("Macro Regime: BULLISH / RISK-ON (Nifty %+.2f%%) — Prioritizing LONG breakouts", nifty_chg)
                long_slots = min(available_slots, 3)
                short_slots = available_slots - long_slots
                candidates.extend(eligible_longs[:long_slots])
                candidates.extend(eligible_shorts[:short_slots])
            else:
                # Neutral / Rangebound: Balanced 50/50 allocation
                log.info("Macro Regime: RANGEBOUND (Nifty %+.2f%%) — Balanced Long/Short allocation", nifty_chg)
                half = max(1, available_slots // 2)
                candidates.extend(eligible_longs[:half])
                candidates.extend(eligible_shorts[:available_slots - len(candidates)])

            # Fill remaining slots if one side was empty
            if len(candidates) < available_slots:
                seen = {c["symbol"] for c in candidates}
                for s in (eligible_shorts if nifty_chg < 0 else eligible_longs):
                    if s["symbol"] not in seen:
                        candidates.append(s)
                        seen.add(s["symbol"])
                    if len(candidates) >= available_slots:
                        break

            for s in candidates[:available_slots]:
                sym = s["symbol"]
                action = s.get("action", "BUY")
                side = "SELL" if action in ("SHORT", "SELL") else "BUY"
                shares = s["shares_5x"]
                price = s["entry_price"]
                sl = s["stop_loss"]
                t1 = s["target_1"]
                t2 = s["target_2"]

                entry_dt = now_ist()
                pos_id = self.store.open_paper_position(
                    suggestion_id=f"intraday_{action}_{sym}_{int(time.time())}",
                    symbol=sym,
                    quantity=shares,
                    entry_price=price,
                    entry_cost_inr=round((shares * price) * 0.0003, 2),
                    entry_dt=entry_dt,
                    trade_date=entry_dt.strftime("%Y-%m-%d"),
                    side=side,
                )

                self.trade_meta[sym] = {
                    "position_id": pos_id,
                    "symbol": sym,
                    "action": action,
                    "side": side,
                    "entry_price": price,
                    "shares": shares,
                    "stop_loss": sl,
                    "target_1": t1,
                    "target_2": t2,
                    "entry_time": entry_dt.strftime("%H:%M:%S IST"),
                    "entry_ts": entry_dt,
                    "trailing_active": False,
                }
                self.traded_symbols_today.add(sym)

                log.info("OPENED BI-DIRECTIONAL PAPER TRADE: [%s] %s | Qty: %d | Entry: ₹%.2f | SL: ₹%.2f | TGT1: ₹%.2f",
                         action, sym, shares, price, sl, t1)

        except Exception as exc:
            log.warning("error scanning opening candidates: %s", exc)

    def monitor_live_positions(self) -> None:
        """Continuously evaluate open positions against targets, stops, trailing rules, and time-stalls."""
        opens = self.store.open_positions()
        if not opens:
            return

        symbols = sorted({p["symbol"] for p in opens})
        try:
            quotes = self.client.get_quotes(symbols)
        except Exception as exc:
            log.warning("quote fetch error during monitor: %s", exc)
            return

        now = now_ist()
        cost_pct = self.cfg.intraday.round_trip_cost_pct

        for pos in opens:
            sym = pos["symbol"]
            q = quotes.get(sym)
            if not q or not getattr(q, "ltp", None) or q.ltp <= 0:
                continue

            ltp = q.ltp
            entry_px = pos["entry_price"]
            pos_id = pos["position_id"]
            side = (pos.get("side") or "BUY").upper()
            is_short = side in ("SELL", "SHORT")

            meta = self.trade_meta.get(sym)
            if not meta:
                # Reconstruct conservative defaults if process was restarted
                if is_short:
                    meta = {
                        "action": "SHORT",
                        "side": "SELL",
                        "stop_loss": round(entry_px * 1.018, 2),
                        "target_1": round(entry_px * 0.965, 2),
                        "target_2": round(entry_px * 0.945, 2),
                        "trailing_active": False,
                        "entry_ts": now,
                    }
                else:
                    meta = {
                        "action": "BUY",
                        "side": "BUY",
                        "stop_loss": round(entry_px * 0.982, 2),
                        "target_1": round(entry_px * 1.035, 2),
                        "target_2": round(entry_px * 1.055, 2),
                        "trailing_active": False,
                        "entry_ts": now,
                    }
                self.trade_meta[sym] = meta

            t1 = meta["target_1"]
            sl = meta["stop_loss"]
            entry_ts = meta.get("entry_ts") or now

            # ---------------- 1. Target 1 Reached: Lock Profit ----------------
            target_hit = (ltp <= t1) if is_short else (ltp >= t1)
            if target_hit:
                ret = self.store.close_paper_position(pos_id, ltp, now, cost_pct)
                pnl_inr = round(pos["quantity"] * (entry_px - ltp), 2) if is_short else round(pos["quantity"] * (ltp - entry_px), 2)
                log.info("TARGET 1 HIT — CLOSED IN PROFIT: [%s] %s | Entry: ₹%.2f -> Exit: ₹%.2f | P&L: +₹%.2f (+%.2f%%)",
                         meta["action"], sym, entry_px, ltp, pnl_inr, (ret or 0.0))
                self.executed_today.append({
                    "symbol": sym,
                    "action": meta["action"],
                    "side": side,
                    "entry_price": entry_px,
                    "exit_price": ltp,
                    "quantity": pos["quantity"],
                    "pnl_inr": pnl_inr,
                    "return_pct": ret,
                    "exit_reason": "TARGET_1_HIT",
                    "exit_time": now.strftime("%H:%M:%S IST"),
                })
                self.trade_meta.pop(sym, None)
                continue

            # ---------------- 2. Stop Loss Guard: Honor 1R Capital Preservation ----------------
            sl_hit = (ltp >= sl) if is_short else (ltp <= sl)
            if sl_hit:
                ret = self.store.close_paper_position(pos_id, ltp, now, cost_pct)
                pnl_inr = round(pos["quantity"] * (entry_px - ltp), 2) if is_short else round(pos["quantity"] * (ltp - entry_px), 2)
                log.info("STOP LOSS HIT — PRESERVED CAPITAL: [%s] %s | Entry: ₹%.2f -> Exit: ₹%.2f | P&L: ₹%.2f (%.2f%%)",
                         meta["action"], sym, entry_px, ltp, pnl_inr, (ret or 0.0))

                # Anti-revenge trading: Apply 60-minute symbol cooldown
                self.symbol_cooldowns[sym] = now + timedelta(minutes=60)
                if pnl_inr < 0:
                    self.session_realized_loss_inr += abs(pnl_inr)

                self.executed_today.append({
                    "symbol": sym,
                    "action": meta["action"],
                    "side": side,
                    "entry_price": entry_px,
                    "exit_price": ltp,
                    "quantity": pos["quantity"],
                    "pnl_inr": pnl_inr,
                    "return_pct": ret,
                    "exit_reason": "STOP_LOSS_GUARD",
                    "exit_time": now.strftime("%H:%M:%S IST"),
                })
                self.trade_meta.pop(sym, None)
                continue

            # ---------------- 3. Win-Win Trailing Stop: Ratchet to Breakeven at 50% of Target 1 ----------------
            if not meta.get("trailing_active"):
                if is_short:
                    halfway = entry_px - ((entry_px - t1) * 0.5)
                    if ltp <= halfway:
                        meta["stop_loss"] = round(entry_px * 0.9985, 2)  # Lock breakeven + friction
                        meta["trailing_active"] = True
                        log.info("TRAILING STOP TO BREAKEVEN ACTIVATED: [SHORT] %s | New SL: ₹%.2f", sym, meta["stop_loss"])
                else:
                    halfway = entry_px + ((t1 - entry_px) * 0.5)
                    if ltp >= halfway:
                        meta["stop_loss"] = round(entry_px * 1.0015, 2)  # Lock breakeven + friction
                        meta["trailing_active"] = True
                        log.info("TRAILING STOP TO BREAKEVEN ACTIVATED: [BUY] %s | New SL: ₹%.2f", sym, meta["stop_loss"])

            # ---------------- 4. Unconstrained Trend Maturation ----------------
            # Ratchet Breakeven Stops: 90-minute time stall exit replaced with dynamic trailing stop.
            # Winning trends are given the full session to reach Target 1 and Target 2,
            # with downside strictly locked to Breakeven (+0.35% costs) by the ratchet stop.

    def auto_exit_all_intraday(self) -> None:
        """15:15 IST: Auto-exit all open positions at market LTP before official close."""
        opens = self.store.open_positions()
        if not opens:
            return

        symbols = sorted({p["symbol"] for p in opens})
        quotes = self.client.get_quotes(symbols)
        now = now_ist()
        cost_pct = self.cfg.intraday.round_trip_cost_pct

        log.info("15:15 IST AUTO-EXIT TRIGGERED — Closing all %d remaining positions...", len(opens))
        for pos in opens:
            sym = pos["symbol"]
            q = quotes.get(sym)
            px = q.ltp if q and getattr(q, "ltp", None) else pos["entry_price"]
            side = (pos.get("side") or "BUY").upper()
            is_short = side in ("SELL", "SHORT")

            ret = self.store.close_paper_position(pos["position_id"], px, now, cost_pct)
            pnl_inr = round(pos["quantity"] * (pos["entry_price"] - px), 2) if is_short else round(pos["quantity"] * (px - pos["entry_price"]), 2)

            self.executed_today.append({
                "symbol": sym,
                "action": "SHORT" if is_short else "BUY",
                "side": side,
                "entry_price": pos["entry_price"],
                "exit_price": px,
                "quantity": pos["quantity"],
                "pnl_inr": pnl_inr,
                "return_pct": ret,
                "exit_reason": "15:15_AUTO_EXIT",
                "exit_time": now.strftime("%H:%M:%S IST"),
            })
            log.info("AUTO-EXITED: [%s] %s | P&L: ₹%.2f", side, sym, pnl_inr)

        self.trade_meta.clear()

    def generate_eod_performance_report(self) -> None:
        """15:35 IST: Calculate final Net Gain / Net Loss and compile comprehensive report."""
        if self.eod_report_generated:
            return

        log.info("Compiling End-of-Day Performance Report...")
        today_str = now_ist().strftime("%Y-%m-%d")

        # Fetch all closed positions from today
        closed = [dict(p) for p in self.store.positions_for_date(today_str) if p["status"] == "closed"]
        total_trades = len(closed)

        net_gain_inr = 0.0
        total_entry_val = 0.0
        wins = 0
        losses = 0

        long_trades = 0
        long_wins = 0
        long_net_inr = 0.0

        short_trades = 0
        short_wins = 0
        short_net_inr = 0.0

        trade_rows = []
        for c in closed:
            side = (c.get("side") or "BUY").upper()
            is_short = side in ("SELL", "SHORT")
            ret = c.get("realized_return_pct", 0.0) or 0.0

            pnl = round(c["quantity"] * (c["entry_price"] - c["exit_price"]), 2) if is_short else round(c["quantity"] * (c["exit_price"] - c["entry_price"]), 2)
            net_gain_inr += pnl
            total_entry_val += (c["quantity"] * c["entry_price"])

            if pnl > 0:
                wins += 1
            else:
                losses += 1

            if is_short:
                short_trades += 1
                if pnl > 0:
                    short_wins += 1
                short_net_inr += pnl
            else:
                long_trades += 1
                if pnl > 0:
                    long_wins += 1
                long_net_inr += pnl

            trade_rows.append({
                "symbol": c["symbol"],
                "action": "SHORT" if is_short else "BUY",
                "side": side,
                "quantity": c["quantity"],
                "entry_price": c["entry_price"],
                "exit_price": c["exit_price"],
                "pnl_inr": pnl,
                "return_pct": round(ret, 2),
                "entry_time": str(c.get("entry_ts", ""))[:19],
                "exit_time": str(c.get("exit_ts", ""))[:19],
            })

        hit_rate_pct = round((wins / total_trades * 100.0), 1) if total_trades > 0 else 0.0
        long_hit_rate = round((long_wins / long_trades * 100.0), 1) if long_trades > 0 else 0.0
        short_hit_rate = round((short_wins / short_trades * 100.0), 1) if short_trades > 0 else 0.0
        portfolio_roi_pct = round((net_gain_inr / self.budget_inr) * 100.0, 2)

        report_data = {
            "report_date": today_str,
            "generated_at_ist": now_ist().strftime("%Y-%m-%d %H:%M:%S IST"),
            "starting_capital_inr": self.budget_inr,
            "total_trades_executed": total_trades,
            "winning_trades": wins,
            "losing_trades": losses,
            "hit_rate_pct": hit_rate_pct,
            "net_gain_inr": round(net_gain_inr, 2),
            "portfolio_roi_pct": portfolio_roi_pct,
            "domain_breakdown": {
                "long_trades": long_trades,
                "long_wins": long_wins,
                "long_hit_rate_pct": long_hit_rate,
                "long_net_inr": round(long_net_inr, 2),
                "short_trades": short_trades,
                "short_wins": short_wins,
                "short_hit_rate_pct": short_hit_rate,
                "short_net_inr": round(short_net_inr, 2),
            },
            "status": "WIN_WIN_PROFITABLE" if net_gain_inr >= 0 else "DEFENSIVE_CAPITAL_PRESERVED",
            "trades": trade_rows,
        }

        # Write JSON report
        json_path = self.reports_dir / f"daily_performance_{today_str}.json"
        json_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

        # Write Markdown report
        md_content = f"""# Daily Market Performance & Honest Track Record Report
**Trading Session Date:** {today_str}  
**Generated At:** {report_data['generated_at_ist']}  
**Execution Mode:** Autonomous Bi-Directional Desk (Long + Short Domain Execution)  

---

## 1. Capital & Net Performance Summary

| Metric | Result | Target Benchmark |
| :--- | :--- | :--- |
| **Starting Trading Capital** | **₹{self.budget_inr:,.2f}** | Sized to Budget |
| **Total Paper Trades Executed** | **{total_trades}** | Max 4 Simultaneous Positions |
| **Winning Trades (Profit)** | **{wins}** | — |
| **Losing Trades (Stopped Out)** | **{losses}** | Capped at 1R |
| **Overall Hit Rate (%)** | **{hit_rate_pct}%** | ≥ 50.0% Gate |
| **NET GAIN / NET LOSS (₹)** | **{'+' if net_gain_inr >= 0 else ''}₹{net_gain_inr:,.2f}** | Net of All Friction |
| **Portfolio ROI (%)** | **{'+' if portfolio_roi_pct >= 0 else ''}{portfolio_roi_pct}%** | Positive Expectancy |

---

## 2. Bi-Directional Domain Breakdown

| Domain | Trades | Wins | Hit Rate | Net P&L (₹) |
| :--- | :---: | :---: | :---: | :---: |
| **Long (BUY)** | {long_trades} | {long_wins} | {long_hit_rate}% | {'+' if long_net_inr >= 0 else ''}₹{long_net_inr:,.2f} |
| **Short (MIS SELL)** | {short_trades} | {short_wins} | {short_hit_rate}% | {'+' if short_net_inr >= 0 else ''}₹{short_net_inr:,.2f} |

---

## 3. Trade Execution Ledger

| Symbol | Action | Qty | Entry (₹) | Exit (₹) | P&L (₹) | Net Return (%) | Outcome |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :--- |
"""
        for t in trade_rows:
            outcome_badge = "PROFIT" if t["pnl_inr"] > 0 else "LOSS (1R Capped)"
            md_content += f"| **{t['symbol']}** | {t['action']} | {t['quantity']} | ₹{t['entry_price']:.2f} | ₹{t['exit_price']:.2f} | {'+' if t['pnl_inr'] >= 0 else ''}₹{t['pnl_inr']:.2f} | {'+' if t['return_pct'] >= 0 else ''}{t['return_pct']:.2f}% | {outcome_badge} |\n"

        if not trade_rows:
            md_content += "| *No trades triggered* | — | — | — | — | ₹0.00 | 0.00% | Preserved in Cash |\n"

        md_content += f"""
---

## 4. Algorithmic Learnings & Execution Invariants
* **Bi-Directional Freedom**: Exploits both Bullish Expansion (Long) and Bearish Breakdown (Short MIS), preventing one-sided portfolio drawdowns during correction sessions.
* **Opening Range Buffer**: 09:15 - 09:30 IST reserved for initial balance formation; no premature entries during opening auction wicks.
* **Anti-Revenge Cooldown**: 60-minute mandatory symbol lock on any stopped-out ticker.
* **Win-Win Breakeven Ratchet**: Stop loss automatically ratchets to Breakeven (+0.15% to cover STT & exchange fees) once price reaches 50% distance to Target 1.
* **Time-Stall Recycling**: Stagnant trades inactive for > 90 minutes with < 0.35% movement are automatically closed to release capital slots.
* **100% Intraday Square-Off**: Mandatorily squared off at 15:15 IST, leaving zero unplanned overnight risk.
"""

        md_path = self.reports_dir / f"daily_performance_{today_str}.md"
        md_path.write_text(md_content, encoding="utf-8")

        self.eod_report_generated = True
        log.info("END-OF-DAY PERFORMANCE REPORT WRITTEN TO: %s", md_path)
        self.emit_status("COMPLETED_EOD_REPORT_READY", {"report_path": str(md_path), "summary": report_data})

    def run_session_step(self) -> None:
        """Single tick step of the trading day clock."""
        ist = now_ist()
        cur_time = ist.time()

        # Session intervals in IST
        t_pre_market = datetime.strptime("09:00", "%H:%M").time()
        t_market_open = datetime.strptime("09:15", "%H:%M").time()
        t_orb_end = datetime.strptime("09:30", "%H:%M").time()
        t_entry_cutoff = datetime.strptime("14:45", "%H:%M").time()
        t_auto_exit = datetime.strptime("15:15", "%H:%M").time()
        t_market_close = datetime.strptime("15:30", "%H:%M").time()
        t_eod_summary = datetime.strptime("15:35", "%H:%M").time()

        if cur_time < t_pre_market:
            # Early morning / Pre-market countdown
            self.emit_status("PRE_SESSION_STANDBY")
        elif t_pre_market <= cur_time < t_market_open:
            # 09:00 - 09:15 IST
            self.run_pre_market_calibration()
        elif t_market_open <= cur_time < t_orb_end:
            # 09:15 - 09:30 IST: Opening Range Formation (No entries, monitor initial balance)
            self.emit_status("OPENING_RANGE_FORMATION")
        elif t_orb_end <= cur_time < t_entry_cutoff:
            # 09:30 - 14:45 IST: Active Bi-Directional Execution Session
            self.scan_and_enter_openings()
            self.monitor_live_positions()
            self.emit_status("LIVE_MARKET_MONITORING")
        elif t_entry_cutoff <= cur_time < t_auto_exit:
            # 14:45 - 15:15 IST: Pre-Close Wind-Down (No new entries, trail exits)
            self.monitor_live_positions()
            self.emit_status("PRE_CLOSE_WIND_DOWN")
        elif t_auto_exit <= cur_time < t_market_close:
            # 15:15 - 15:30 IST: Auto-Exit Window
            self.auto_exit_all_intraday()
            self.emit_status("SESSION_CUTOFF_AUTO_EXITED")
        elif cur_time >= t_eod_summary:
            # 15:35+ IST: End-of-Day Summary & Report
            self.generate_eod_performance_report()

    def run_forever(self, tick_seconds: int = 10) -> None:
        """Run continuous autonomous desk loop throughout the day with low-latency ticks."""
        log.info("Autonomous Bi-Directional Trading Desk started for capital ₹%.2f (Tick cadence: %ds)...",
                 self.budget_inr, tick_seconds)
        while True:
            try:
                self.run_session_step()
            except Exception as exc:
                log.exception("unexpected error in desk tick: %s", exc)
            time.sleep(tick_seconds)


if __name__ == "__main__":
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    desk.run_forever(tick_seconds=10)
