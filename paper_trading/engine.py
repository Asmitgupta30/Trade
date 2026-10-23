"""Paper-trading engine — real live data, hypothetical money.

Every advisor suggestion is auto-logged into the HYPOTHETICAL track at the
live price. The human separately logs what they ACTUALLY did; both tracks are
reported side by side, never merged. Also owns:
  * live-ready gate math (risk_config.system_mode.paper_trading_gate)
  * circuit-breaker state (daily/weekly loss, losing streak, drawdown)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

from common.config import RiskConfig
from common.types import Quote
from models.metrics import summarize_suggestion_outcomes
from paper_trading.store import PaperStore

log = logging.getLogger(__name__)


@dataclass
class GateStatus:
    days_tracked: int
    suggestions_tracked: int
    hit_rate: float | None
    sharpe: float | None
    max_drawdown_pct: float | None
    live_ready: bool
    criteria: dict

    def to_dict(self) -> dict:
        return {
            "days_tracked": self.days_tracked,
            "suggestions_tracked": self.suggestions_tracked,
            "hit_rate": self.hit_rate,
            "sharpe": self.sharpe,
            "max_drawdown_pct": self.max_drawdown_pct,
            "live_ready": self.live_ready,
            "criteria": self.criteria,
        }


class PaperEngine:
    def __init__(self, cfg: RiskConfig, store: PaperStore | None = None):
        self.cfg = cfg
        self.store = store or PaperStore()

    # ------------------------------------------------------------------
    def log_suggestion_set(self, suggestion_set_dict: dict) -> int:
        """Persist a SuggestionSet and open hypothetical positions for entries."""
        phase = suggestion_set_dict.get("phase", "unknown")
        horizon = suggestion_set_dict.get("horizon", "intraday")
        budget = suggestion_set_dict.get("budget_inr", 0.0)
        n = 0   # counter: n = suggestions persisted (ALL actions, not just entries)
        for s in suggestion_set_dict.get("suggestions", []):
            self.store.save_suggestion(s, phase, horizon, budget)
            n += 1   # counter: n = suggestions persisted (ALL actions, not just entries)
            if s["action"] in ("BUY", "STRONG_BUY") and s.get("est_quantity", 0) > 0:
                created = s.get("created_ts") or datetime.now(timezone.utc).isoformat()
                entry_dt = datetime.fromisoformat(created)
                price = s.get("reference_price") or 0.0
                if price <= 0:
                    continue
                self.store.open_paper_position(
                    suggestion_id=s["suggestion_id"], symbol=s["symbol"],
                    quantity=s["est_quantity"], entry_price=price,
                    entry_cost_inr=s.get("est_cost_inr", 0.0),
                    entry_dt=entry_dt,
                    trade_date=entry_dt.strftime("%Y-%m-%d"))
        return n

    def close_intraday_positions(self, prices: dict[str, float],
                                 now: datetime | None = None) -> list[dict]:
        """Pre-close/EOD job: close all open hypothetical positions at market."""
        now = now or datetime.now(timezone.utc)
        cost_pct = self.cfg.intraday.round_trip_cost_pct
        closed = []
        for pos in self.store.open_positions():
            px = prices.get(pos["symbol"])
            if not px:
                continue
            ret = self.store.close_paper_position(
                pos["position_id"], float(px), now, cost_pct)
            closed.append({"symbol": pos["symbol"], "return_pct": ret})
        if closed:
            self.store.log_event("eod_close", {"closed": len(closed)})
        return closed

    # ------------------------------------------------------------------
    def log_user_action(self, symbol: str, took_trade: bool, side: str = "BUY",
                        quantity: float = 0.0, price: float | None = None,
                        suggestion_id: str | None = None, note: str = "") -> int:
        """The human reports what they actually did (UI button)."""
        ts = datetime.now(timezone.utc)
        px = price or 0.0
        if took_trade and not px:
            raise ValueError("price required when logging an actual trade")
        return self.store.save_user_trade(
            symbol=symbol, side=side if took_trade else "SKIPPED",
            quantity=quantity if took_trade else 0, price=px,
            ts=ts, suggestion_id=suggestion_id, note=note or
            ("" if took_trade else "declined suggestion"))

    # ------------------------------------------------------------------
    def _position_outcomes(self) -> list[dict]:
        cost_pct = self.cfg.intraday.round_trip_cost_pct
        outcomes = []
        for pos in self.store.all_positions():
            if pos["status"] == "closed" and pos["realized_return_pct"] is not None:
                ret = float(pos["realized_return_pct"])
            else:
                continue
            direction_ok = ret > -cost_pct   # beat round-trip cost => correct call
            outcomes.append({
                "symbol": pos["symbol"],
                "trade_date": pos["trade_date"],
                "return_pct": ret,
                "direction_ok": bool(direction_ok),
            })
        return outcomes

    def hypothetical_summary(self) -> dict:
        outcomes = self._position_outcomes()
        summary = summarize_suggestion_outcomes(outcomes)
        summary["open_positions"] = len(self.store.open_positions())
        return summary

    def user_summary(self) -> dict:
        """What the human ACTUALLY did: follow-rate + their own trade log."""
        trades = self.store.user_trades()
        taken = [t for t in trades if t["side"] != "SKIPPED"]
        skipped = [t for t in trades if t["side"] == "SKIPPED"]
        total_suggestions = self.store.conn.execute(
            "SELECT COUNT(*) c FROM suggestions WHERE action IN ('BUY','STRONG_BUY')").fetchone()["c"]
        return {
            "suggestions_seen": total_suggestions,
            "trades_taken": len(taken),
            "skipped": len(skipped),
            "follow_rate": round(len(taken) / total_suggestions, 3) if total_suggestions else None,
        }

    # ------------------------------------------------------------------
    def gate_status(self) -> GateStatus:
        g = self.cfg.paper_gate
        outcomes = self._position_outcomes()
        dates = sorted({o["trade_date"] for o in outcomes})
        days = len(dates)
        summary = summarize_suggestion_outcomes(outcomes)

        hit = summary.get("hit_rate")
        sharpe = summary.get("sharpe")
        dd = summary.get("max_drawdown_pct")

        enough_sample = (
            days >= g.min_days and len(outcomes) >= g.min_suggestions)
        quality = (
            hit is not None and hit >= g.hit_rate_min
            and sharpe is not None and sharpe >= g.sharpe_min
            and dd is not None and dd <= g.max_drawdown_max_pct
        )
        gate_on = g.enabled
        live_ready = bool(gate_on and enough_sample and quality)

        remaining_days = max(g.min_days - days, 0)
        remaining_suggs = max(g.min_suggestions - len(outcomes), 0)
        criteria = {
            "min_paper_days": g.min_days,
            "min_suggestions": g.min_suggestions,
            "days_remaining": remaining_days,
            "suggestions_remaining": remaining_suggs,
            "hit_rate_min": g.hit_rate_min,
            "sharpe_min": g.sharpe_min,
            "max_drawdown_max_pct": g.max_drawdown_max_pct,
            "enough_sample": enough_sample,
            "quality_pass": quality,
        }
        return GateStatus(
            days_tracked=days,
            suggestions_tracked=len(outcomes),
            hit_rate=hit, sharpe=sharpe, max_drawdown_pct=dd,
            live_ready=live_ready, criteria=criteria)

    # ------------------------------------------------------------------
    def suggestion_outcomes_since(self, start_iso: str) -> list[dict]:
        """Suggestions (entries only) merged with closed-position outcomes.

        Feeds the forward-holdout evaluator: one row per entry suggestion with
        its confidence and, when the hypothetical position has closed, its
        net-of-cost realized return and directional correctness.
        """
        rows = self.store.conn.execute(
            """SELECT s.suggestion_id, s.action, s.confidence, s.created_ts,
                      p.realized_return_pct
               FROM suggestions s
               LEFT JOIN paper_positions p
                      ON p.suggestion_id = s.suggestion_id
               WHERE s.action IN ('BUY','STRONG_BUY')
                 AND s.created_ts >= ?""",
            (start_iso,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d["realized_return_pct"] is None:
                continue   # open or never-filled: excluded until resolved
            d["direction_ok"] = float(d["realized_return_pct"]) > \
                -self.cfg.intraday.round_trip_cost_pct
            out.append(d)
        return out

    # ------------------------------------------------------------------
    def breaker_state(self, budget_ref_inr: float = 10000.0) -> dict:
        """Circuit-breaker state derived from the HYPOTHETICAL track."""
        cb = self.cfg.breakers
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        day_rets = [o["return_pct"] for o in self._position_outcomes()
                    if o["trade_date"] == today]
        day_pnl_pct = float(np.sum(day_rets)) if day_rets else 0.0

        all_dates = sorted({o["trade_date"] for o in self._position_outcomes()})
        daily_pnl = {}
        for o in self._position_outcomes():
            daily_pnl[o["trade_date"]] = daily_pnl.get(o["trade_date"], 0.0) + o["return_pct"]

        streak = 0   # counter: streak = consecutive trailing days with summed net return < 0
        for d in reversed(all_dates):
            if daily_pnl[d] < 0:
                streak += 1   # counter: consecutive_losing_days (extends streak)
            else:
                break

        eq = pd.Series([daily_pnl[d] for d in all_dates]).cumsum() \
            if all_dates else pd.Series(dtype=float)
        dd_pct = 0.0
        if len(eq) > 1:
            peak = eq.cummax()
            dd_pct = float((-((eq - peak))).max())   # % points of cumulative return lost

        week_pnl = sum(v for d, v in daily_pnl.items()
                       if datetime.strptime(d, "%Y-%m-%d") >=
                       datetime.now(timezone.utc) - timedelta(days=7))

        return {
            "day_pnl_pct_of_budget": round(day_pnl_pct, 2),
            "daily_loss_limit_pct": cb.daily_loss_limit_pct_of_budget,
            "entries_stopped_today": day_pnl_pct <= -cb.daily_loss_limit_pct_of_budget,
            "weekly_pnl_pct": round(week_pnl, 2),
            "weekly_cooloff_active": week_pnl <= -cb.weekly_loss_limit_pct_of_capital,
            "consecutive_losing_days": streak,
            "recalibration_warning": streak >= cb.consecutive_losing_days_flag,
            "current_drawdown_pct": round(dd_pct, 2),
            "hard_pause_active": dd_pct >= cb.max_drawdown_pause_pct,
        }

