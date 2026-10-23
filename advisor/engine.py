"""AdvisorEngine — turns model signals into budget-aware human suggestions.

This module is the heart of the product. It contains NO execution capability:
its only output is a ranked list of explained suggestions the human may choose
to act on manually in their broker app.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

import numpy as np
import pandas as pd

from advisor.costs import edge_vs_cost, expected_move_from_confidence
from advisor.rationale import build_baseline_comparison, build_rationale
from advisor.rules import PortfolioState, RuleEngine, apply_verdict
from advisor.suggestion import (
    ACTION_BUY, ACTION_EXIT, ACTION_HOLD, ACTION_NONE, ACTION_STRONG_BUY,
    Suggestion, SuggestionSet,
)
from common.config import RiskConfig
from common.timeutils import now_ist

log = logging.getLogger(__name__)


class SignalProvider(Protocol):
    """Returns one row per candidate symbol:
    [symbol, sector, confidence, daily_vol_pct, close, change_pct, ...features]"""

    def score_universe(self) -> pd.DataFrame: ...


class AdvisorEngine:
    def __init__(self, cfg: RiskConfig, signals: SignalProvider):
        self.cfg = cfg
        self.signals = signals
        self.rules = RuleEngine(cfg)

    # ------------------------------------------------------------------
    def generate(
        self,
        budget_inr: float,
        horizon: str = "intraday",
        phase: str = "pre_market",
        state: PortfolioState | None = None,
        data_is_current: bool = True,
    ) -> SuggestionSet:
        now = now_ist()
        state = state or PortfolioState()
        out = SuggestionSet(budget_inr=budget_inr, horizon=horizon,
                            phase=phase, generated_at=now)
        notes = out.system_notes

        if horizon != "intraday":
            raise ValueError("use MutualFundAdvisor for horizon='mutual_fund'")

        if budget_inr < self.cfg.intraday.min_recommended_budget_inr:
            notes.append(
                f"BUDGET WARNING: ₹{budget_inr:,.0f} is below the recommended minimum "
                f"₹{self.cfg.intraday.min_recommended_budget_inr:,.0f}. Costs eat a bigger "
                "share of small trades; most ideas will be refused on cost.")

        # hard lock: stale feed suspends new suggestions entirely
        if not data_is_current:
            notes.append(
                "DATA STALE: live market data is unavailable or stale. New suggestions are "
                "SUSPENDED per risk rules — showing nothing rather than guessing.")
            return out

        # global circuit-breaker banners
        notes.extend(self.rules.system_pause_notes(state))

        try:
            scored = self.signals.score_universe()
        except Exception as exc:
            log.exception("signal provider failed")
            notes.append(f"SIGNAL ERROR: {exc}. No suggestions generated.")
            return out
        if scored.empty:
            notes.append("No candidates passed today's liquidity/confidence screens.")
            return out

        entries_blocked = self.rules.daily_loss_block(state, budget_inr)
        if entries_blocked:
            notes.append(
                f"CIRCUIT BREAKER: day loss hit -{self.cfg.breakers.daily_loss_limit_pct_of_budget}% "
                "of budget. NO new entries; exit/hold advice only.")

        nifty_mom = float(scored.get("nifty_mom_5d", pd.Series([np.nan])).iloc[0]) \
            if "nifty_mom_5d" in scored.columns else np.nan

        all_candidates = scored.sort_values("confidence", ascending=False)
        buy_candidates = all_candidates[
            all_candidates["confidence"] >= self.cfg.intraday.min_confidence_buy]

        remaining_budget = budget_inr
        positions_used = state.suggestions_today
        sector_used = dict(state.sector_exposure)
        symbol_used = dict(state.symbol_exposure)

        def _live_state() -> PortfolioState:
            return PortfolioState(
                open_symbols=state.open_symbols,
                sector_exposure=sector_used,
                symbol_exposure=symbol_used,
                suggestions_today=positions_used,
                day_pnl_inr=state.day_pnl_inr,
                week_pnl_inr=state.week_pnl_inr,
                consecutive_losing_days=state.consecutive_losing_days,
                current_drawdown_pct=state.current_drawdown_pct,
            )

        if entries_blocked:
            # circuit breaker: show the ideas we refused, with the reason
            for _, row in buy_candidates.iterrows():
                sug = Suggestion(
                    symbol=row["symbol"], sector=row["sector"], action=ACTION_NONE,
                    amount_inr=0.0, confidence=float(row["confidence"]),
                    rationale="Candidate idea, not suggested.",
                    est_cost_inr=0.0, baseline_comparison="",
                    reference_price=float(row["close"]),
                    blocked_by=["circuit breaker: daily loss limit hit — new entries stopped"],
                )
                out.suggestions.append(sug)
            buy_candidates = buy_candidates.iloc[0:0]

        for _, row in buy_candidates.iterrows():
            if positions_used >= self.cfg.intraday.max_positions_per_day:
                break
            confidence = float(row["confidence"])
            vol_pct = float(row.get("daily_vol_pct", 1.5))   # daily %, e.g. 1.5 = 1.5%
            price = float(row["close"])
            change_pct = row.get("change_pct")
            change_pct = None if change_pct is None or pd.isna(change_pct) else float(change_pct)

            exp_move = expected_move_from_confidence(confidence, vol_pct)
            action = ACTION_STRONG_BUY if confidence >= self.cfg.intraday.min_confidence_strong_buy else ACTION_BUY

            # size: confidence-weighted share of remaining budget
            weight = (confidence - 0.5) * 2.0
            raw_amount = remaining_budget * (0.3 + 0.4 * weight)
            stock_cap = budget_inr * self.cfg.intraday.max_allocation_per_stock_pct / 100.0
            raw_amount = min(raw_amount, stock_cap)

            sug = Suggestion(
                symbol=row["symbol"], sector=row["sector"], action=action,
                amount_inr=raw_amount, confidence=confidence,
                rationale="", est_cost_inr=0.0, baseline_comparison="",
                reference_price=price,
            )
            verdict = self.rules.check_buy(sug, budget_inr, _live_state(), change_pct, vol_pct)
            apply_verdict(sug, verdict)
            sug.est_quantity = int(sug.amount_inr / price) if price > 0 else 0
            sug.amount_inr = sug.est_quantity * price   # round to whole shares
            cost = edge_vs_cost(confidence, exp_move, max(sug.amount_inr, 1), self.cfg)
            sug.est_cost_inr = cost.round_trip_cost_inr
            sug.rationale = build_rationale(row, confidence) + " " + cost.explanation
            sug.baseline_comparison = build_baseline_comparison(
                confidence, nifty_mom, cost.expected_net_edge_pct)
            sug.with_expiry(now, self.cfg.governance.suggestion_expiry_minutes)

            if not verdict.allowed or sug.amount_inr < self.cfg.intraday.min_trade_size_inr:
                sug.action = ACTION_NONE
                out.suggestions.append(sug)     # shown explicitly as refused
                continue

            out.suggestions.append(sug)
            positions_used += 1
            sector_used[row["sector"]] = sector_used.get(row["sector"], 0.0) + sug.amount_inr
            symbol_used[row["symbol"]] = symbol_used.get(row["symbol"], 0.0) + sug.amount_inr
            remaining_budget -= sug.amount_inr

        # explicitly show capped-out candidates that never got evaluated
        evaluated = positions_used - state.suggestions_today
        if len(buy_candidates) > evaluated:
            over = buy_candidates.iloc[evaluated:]
            for _, row in over.iterrows():
                sug = Suggestion(
                    symbol=row["symbol"], sector=row["sector"], action=ACTION_NONE,
                    amount_inr=0.0, confidence=float(row["confidence"]),
                    rationale="Candidate idea, not suggested.",
                    est_cost_inr=0.0, baseline_comparison="",
                    reference_price=float(row["close"]),
                    blocked_by=[
                        f"position limit: {self.cfg.intraday.max_positions_per_day}/day already allocated"],
                )
                out.suggestions.append(sug)

        # below-gate ideas are shown as explicitly blocked, never silently dropped
        below_gate = all_candidates[
            all_candidates["confidence"] < self.cfg.intraday.min_confidence_buy]
        for _, row in below_gate.iterrows():
            sug = Suggestion(
                symbol=row["symbol"], sector=row["sector"], action=ACTION_NONE,
                amount_inr=0.0, confidence=float(row["confidence"]),
                rationale="Watchlist only — model confidence too low to suggest an entry.",
                est_cost_inr=0.0, baseline_comparison="",
                reference_price=float(row["close"]),
                blocked_by=[f"confidence {float(row['confidence']):.2f} below BUY gate "
                            f"({self.cfg.intraday.min_confidence_buy:.2f})"],
            )
            out.suggestions.append(sug)

        # exits/holds for open positions always allowed (even under circuit breaker)
        for sym in state.open_symbols:
            row = scored[scored["symbol"] == sym]
            if row.empty:
                continue
            r0 = row.iloc[0]
            conf = float(r0["confidence"])
            sug = Suggestion(
                symbol=sym, sector=r0["sector"],
                action=ACTION_HOLD if conf >= 0.45 else ACTION_EXIT,
                amount_inr=state.symbol_exposure.get(sym, 0.0),
                confidence=conf,
                rationale=(
                    f"You hold {sym}. Model confidence for another up-day is "
                    f"{conf * 100:.0f}%. " +
                    ("Holding is reasonable." if conf >= 0.45 else
                     "Edge has faded — consider exiting before close.")),
                est_cost_inr=self.cfg.intraday.round_trip_cost_pct / 100.0 *
                state.symbol_exposure.get(sym, 0.0),
                baseline_comparison="Baseline: staying in cash overnight.",
                reference_price=float(r0["close"]),
            )
            sug.with_expiry(now, self.cfg.governance.suggestion_expiry_minutes)
            out.suggestions.insert(0, sug)

        out.cash_left_inr = max(remaining_budget, 0.0)
        return out

    # ------------------------------------------------------------------
    def revalidate(self, suggestion_set: SuggestionSet,
                   data_is_current: bool = True) -> SuggestionSet:
        """risk_config.model_governance.suggestion_expiry_minutes: stale buys must
        be re-validated against fresh data before being shown as actionable."""
        now = now_ist()
        for s in suggestion_set.suggestions:
            if s.is_expired(now) and s.action in (ACTION_BUY, ACTION_STRONG_BUY):
                s.warnings.append(
                    f"EXPIRED: suggestion is older than "
                    f"{self.cfg.governance.suggestion_expiry_minutes} minutes. Re-validate "
                    "against fresh prices before acting.")
                if not data_is_current:
                    s.action = ACTION_NONE
                    s.blocked_by.append("data stale at re-validation")
        return suggestion_set
