"""Transaction cost model — cost-awareness is a first-class product feature."""

from __future__ import annotations

from dataclasses import dataclass

from common.config import RiskConfig


@dataclass(frozen=True)
class CostBreakdown:
    round_trip_cost_pct: float
    round_trip_cost_inr: float
    expected_gross_edge_pct: float
    expected_net_edge_pct: float
    worth_it: bool
    explanation: str


def estimate_round_trip(amount_inr: float, cfg: RiskConfig) -> float:
    return amount_inr * cfg.intraday.round_trip_cost_pct / 100.0


def edge_vs_cost(confidence: float, expected_move_pct: float,
                 amount_inr: float, cfg: RiskConfig) -> CostBreakdown:
    """Is this idea worth its costs?

    expected_move_pct: model's expected gross next-day move for the idea.
    Net edge = expected move - round-trip cost. A suggestion that can't clear
    its own costs is refused (risk_config cost_awareness rule).
    """
    cost_inr = estimate_round_trip(amount_inr, cfg)
    net = expected_move_pct - cfg.intraday.round_trip_cost_pct
    worth = net > 0
    if worth:
        expl = (
            f"Expected gross move ~{expected_move_pct:.2f}% vs round-trip cost "
            f"~{cfg.intraday.round_trip_cost_pct:.2f}% (₹{cost_inr:.0f} on ₹{amount_inr:.0f}) "
            f"→ net edge ~{net:.2f}%."
        )
    else:
        expl = (
            f"REFUSED on cost: expected gross move ~{expected_move_pct:.2f}% does not "
            f"clear the estimated round-trip cost of ~{cfg.intraday.round_trip_cost_pct:.2f}% "
            f"(₹{cost_inr:.0f} on ₹{amount_inr:.0f}). Not worth trading at this size."
        )
    return CostBreakdown(
        round_trip_cost_pct=cfg.intraday.round_trip_cost_pct,
        round_trip_cost_inr=round(cost_inr, 2),
        expected_gross_edge_pct=round(expected_move_pct, 3),
        expected_net_edge_pct=round(net, 3),
        worth_it=worth,
        explanation=expl,
    )


def expected_move_from_confidence(confidence: float, daily_vol_pct: float) -> float:
    """Map model confidence + realized vol to an expected gross move estimate.

    Simple, honest heuristic: confidence beyond coin-flip scaled by the
    symbol's recent daily volatility. Deliberately conservative.
    """
    edge = max(confidence - 0.5, 0.0) * 2.0     # 0..1
    return edge * daily_vol_pct
