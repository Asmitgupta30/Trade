"""Rule engine — every risk_config intraday rule, enforced and explainable.

A rule never silently drops an idea: refusals are recorded on the Suggestion
as `blocked_by` entries (or system_notes for global pauses) so the UI can
show exactly why the system refused.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from advisor.costs import edge_vs_cost, expected_move_from_confidence
from advisor.rationale import circuit_warning
from advisor.suggestion import (
    ACTION_BUY, ACTION_STRONG_BUY, Suggestion,
)
from common.config import RiskConfig

log = logging.getLogger(__name__)


@dataclass
class PortfolioState:
    """What the human is already carrying today (paper or actual)."""
    open_symbols: list[str] = field(default_factory=list)
    sector_exposure: dict[str, float] = field(default_factory=dict)  # inr per sector
    symbol_exposure: dict[str, float] = field(default_factory=dict)  # inr per symbol
    suggestions_today: int = 0
    day_pnl_inr: float = 0.0
    week_pnl_inr: float = 0.0
    consecutive_losing_days: int = 0
    current_drawdown_pct: float = 0.0


@dataclass
class RuleVerdict:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adjusted_amount: float | None = None   # when a cap forces a size cut


class RuleEngine:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    # ---------- global pauses ----------
    def system_pause_notes(self, state: PortfolioState) -> list[str]:
        notes = []
        cb = self.cfg.breakers
        daily_budget_ref = max(abs(state.day_pnl_inr), 1.0)  # % computed vs budget by caller
        if state.current_drawdown_pct >= cb.max_drawdown_pause_pct:
            notes.append(
                f"PAUSED: paper-trading drawdown {state.current_drawdown_pct:.1f}% has hit the "
                f"{cb.max_drawdown_pause_pct}% hard pause. New suggestions suspended until you "
                "manually reset.")
        if state.day_pnl_inr < 0:
            notes.append(
                f"CIRCUIT WATCH: today's P&L is ₹{state.day_pnl_inr:,.0f}. New-entry limit is "
                f"-{cb.daily_loss_limit_pct_of_budget}% of budget.")
        if state.week_pnl_inr < 0:
            notes.append(
                f"WEEKLY DRAWDOWN: week-to-date P&L is ₹{state.week_pnl_inr:,.0f}. "
                f"Weekly cooloff threshold is -{cb.weekly_loss_limit_pct_of_capital}% of capital.")
        if state.consecutive_losing_days >= cb.consecutive_losing_days_flag:
            notes.append(
                f"MODEL CONFIDENCE WARNING: {state.consecutive_losing_days} losing days in a row "
                "(threshold "
                f"{cb.consecutive_losing_days_flag}). Treat all confidence scores skeptically; "
                "consider recalibration before acting on suggestions.")
        return [n for n in notes if n]

    def daily_loss_block(self, state: PortfolioState, budget_inr: float) -> bool:
        """True when new ENTRIES must stop (exits still allowed)."""
        loss_pct = -state.day_pnl_inr / budget_inr * 100 if budget_inr > 0 else 0
        return loss_pct >= self.cfg.breakers.daily_loss_limit_pct_of_budget

    def weekly_cooloff(self, state: PortfolioState, capital_inr: float) -> bool:
        loss_pct = -state.week_pnl_inr / capital_inr * 100 if capital_inr > 0 else 0
        return loss_pct >= self.cfg.breakers.weekly_loss_limit_pct_of_capital

    # ---------- single-idea checks ----------
    def check_buy(self, sug: Suggestion, budget_inr: float,
                  state: PortfolioState, change_pct: float | None = None,
                  daily_vol_pct: float = 1.5) -> RuleVerdict:
        r = self.cfg.intraday
        reasons: list[str] = []
        warnings: list[str] = []
        amount = sug.amount_inr

        # confidence gates
        if sug.action == ACTION_STRONG_BUY and sug.confidence < r.min_confidence_strong_buy:
            warnings.append(
                f"confidence {sug.confidence:.2f} below STRONG_BUY gate "
                f"({r.min_confidence_strong_buy:.2f}) — downgraded to standard BUY")
            sug.action = ACTION_BUY
        if sug.confidence < r.min_confidence_buy:
            reasons.append(
                f"confidence {sug.confidence:.2f} below BUY gate ({r.min_confidence_buy:.2f})")

        # min trade size (cost-drag refusal)
        if amount < r.min_trade_size_inr:
            reasons.append(
                f"trade size ₹{amount:.0f} below minimum ₹{r.min_trade_size_inr:.0f} — "
                "brokerage/STT would dominate")

        # per-stock cap with forced resize
        stock_cap_inr = budget_inr * r.max_allocation_per_stock_pct / 100.0
        existing = state.symbol_exposure.get(sug.symbol, 0.0)
        headroom = stock_cap_inr - existing
        if headroom <= 0:
            reasons.append(
                f"per-stock cap: {sug.symbol} exposure ₹{existing:,.0f} already at "
                f"{r.max_allocation_per_stock_pct}% of budget")
        elif amount > headroom:
            warnings.append(
                f"size cut from ₹{amount:,.0f} to ₹{headroom:,.0f} to respect the "
                f"{r.max_allocation_per_stock_pct}% per-stock cap")
            amount = headroom

        # per-sector cap
        sector_cap_inr = budget_inr * r.max_allocation_per_sector_pct / 100.0
        sector_used = state.sector_exposure.get(sug.sector, 0.0)
        if sector_used + amount > sector_cap_inr:
            room = sector_cap_inr - sector_used
            if room < r.min_trade_size_inr:
                reasons.append(
                    f"sector cap: '{sug.sector}' would exceed "
                    f"{r.max_allocation_per_sector_pct}% of budget")
            else:
                warnings.append(
                    f"size cut to ₹{room:,.0f} to respect the "
                    f"{r.max_allocation_per_sector_pct}% '{sug.sector}' sector cap")
                amount = room

        # cost-awareness: expected edge must clear round-trip cost
        exp_move = expected_move_from_confidence(sug.confidence, daily_vol_pct)
        cost = edge_vs_cost(sug.confidence, exp_move, amount, self.cfg)
        if r.refuse_if_edge_below_cost and not cost.worth_it:
            reasons.append(f"edge below cost: {cost.explanation}")

        # position count
        if state.suggestions_today + 1 > r.max_positions_per_day:
            reasons.append(
                f"daily position limit: {r.max_positions_per_day} suggestions/day reached")

        # circuit awareness
        cw = circuit_warning(change_pct)
        if cw and r.flag_near_circuit:
            warnings.append(cw)

        verdict_allowed = len(reasons) == 0 and amount >= r.min_trade_size_inr
        return RuleVerdict(allowed=verdict_allowed, reasons=reasons,
                           warnings=warnings,
                           adjusted_amount=amount if amount != sug.amount_inr else None)


def apply_verdict(sug: Suggestion, verdict: RuleVerdict) -> Suggestion:
    """Record the verdict on the suggestion (never silent)."""
    sug.blocked_by.extend(verdict.reasons)
    sug.warnings.extend(verdict.warnings)
    if verdict.adjusted_amount is not None:
        sug.amount_inr = verdict.adjusted_amount
    return sug

