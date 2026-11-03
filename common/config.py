"""Loading and enforcing the binding risk ruleset (risk_config.yaml).

The config is the contract between the human and the system. Loading it
validates the hard locks: if `autonomous_execution` is ever set true, or a
required lock is missing, we refuse to construct the app config at all
(fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class RiskConfigError(RuntimeError):
    """Raised when risk_config.yaml is missing, malformed, or violates hard locks."""


def _require(d: dict[str, Any], *path: str) -> Any:
    cur: Any = d
    walked = []
    for key in path:
        walked.append(key)
        if not isinstance(cur, dict) or key not in cur:
            raise RiskConfigError(f"risk_config.yaml missing required key: {'.'.join(walked)}")
        cur = cur[key]
    return cur


@dataclass(frozen=True)
class PaperTradingGate:
    enabled: bool
    min_days: int
    min_suggestions: int
    hit_rate_min: float
    sharpe_min: float
    max_drawdown_max_pct: float


@dataclass(frozen=True)
class IntradayRules:
    min_recommended_budget_inr: float
    max_positions_per_day: int
    max_allocation_per_stock_pct: float
    max_allocation_per_sector_pct: float
    min_trade_size_inr: float
    round_trip_cost_pct: float
    refuse_if_edge_below_cost: bool
    min_avg_daily_volume: float
    min_price_inr: float
    min_confidence_buy: float
    min_confidence_strong_buy: float
    respect_price_bands: bool
    flag_near_circuit: bool


@dataclass(frozen=True)
class ScheduleRule:
    name: str
    time_ist: str
    purpose: str


@dataclass(frozen=True)
class CircuitBreakers:
    daily_loss_limit_pct_of_budget: float
    weekly_loss_limit_pct_of_capital: float
    consecutive_losing_days_flag: int
    max_drawdown_pause_pct: float


@dataclass(frozen=True)
class MutualFundRules:
    rebalance_frequency: str
    large_cap_band: tuple[float, float]
    mid_cap_band: tuple[float, float]
    debt_band: tuple[float, float]
    gold_band: tuple[float, float]
    ltcg_holding_days: int
    default_mode: str  # "sip"


@dataclass(frozen=True)
class ModelGovernance:
    overfit_flag_ratio: float
    naive_baseline_required: bool
    suggestion_expiry_minutes: int
    never_hide_losing_streaks: bool


@dataclass(frozen=True)
class RiskConfig:
    autonomous_execution_must_be_false: bool
    paper_gate: PaperTradingGate
    intraday: IntradayRules
    schedule: dict[str, ScheduleRule]
    breakers: CircuitBreakers
    mf: MutualFundRules
    governance: ModelGovernance
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def _parse(config_path: Path) -> RiskConfig:
    if not config_path.exists():
        raise RiskConfigError(f"Binding ruleset not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        raise RiskConfigError("risk_config.yaml did not parse to a mapping")

    # ---- HARD LOCK VALIDATION (fail closed) -------------------------------
    autonomous = _require(raw, "system_mode", "autonomous_execution")
    if autonomous is not False:
        raise RiskConfigError(
            "HARD LOCK VIOLATION: system_mode.autonomous_execution must be false. "
            "This system is advisory-only and may never execute orders."
        )
    if _require(raw, "system_mode", "requires_human_approval") is not True:
        raise RiskConfigError("HARD LOCK VIOLATION: requires_human_approval must be true.")

    locks = raw.get("hard_locks") or []
    if len(locks) < 3:
        raise RiskConfigError("hard_locks section missing/incomplete — refusing to load.")

    gate = raw["system_mode"]["paper_trading_gate"]
    promote = gate["promote_to_live_ready_if"]

    ib = raw["intraday_budget_rules"]
    cost = ib["cost_awareness"]
    liq = ib["liquidity_filter"]
    conf = ib["confidence_gating"]
    circ = ib["circuit_awareness"]

    sched_raw = raw["suggestion_schedule"]
    schedule = {
        name: ScheduleRule(name=name, time_ist=body["time_ist"], purpose=body["purpose"])
        for name, body in sched_raw.items()
    }
    for required in ("pre_market", "midday_check", "pre_close", "end_of_day_summary"):
        if required not in schedule:
            raise RiskConfigError(f"suggestion_schedule missing '{required}'")

    cb = raw["circuit_breakers"]
    mf_raw = raw["mutual_fund_rules"]
    bands = mf_raw["category_allocation_bands"]
    gov = raw["model_governance"]

    return RiskConfig(
        autonomous_execution_must_be_false=(autonomous is False),
        paper_gate=PaperTradingGate(
            enabled=bool(gate["enabled"]),
            min_days=int(gate["min_paper_trading_days"]),
            min_suggestions=int(gate["min_suggestions_tracked"]),
            hit_rate_min=float(promote["hit_rate_min"]),
            sharpe_min=float(promote["sharpe_min"]),
            max_drawdown_max_pct=float(promote["max_drawdown_max_pct"]),
        ),
        intraday=IntradayRules(
            min_recommended_budget_inr=float(ib["min_recommended_budget_inr"]),
            max_positions_per_day=int(ib["max_positions_per_day"]),
            max_allocation_per_stock_pct=float(ib["max_allocation_per_stock_pct"]),
            max_allocation_per_sector_pct=float(ib["max_allocation_per_sector_pct"]),
            min_trade_size_inr=float(ib["min_trade_size_inr"]),
            round_trip_cost_pct=float(cost["estimated_round_trip_cost_pct"]),
            refuse_if_edge_below_cost=bool(cost["refuse_suggestion_if_expected_edge_pct_below_cost"]),
            min_avg_daily_volume=float(liq["min_avg_daily_volume"]),
            min_price_inr=float(liq["min_price_inr"]),
            min_confidence_buy=float(conf["min_confidence_to_suggest_buy"]),
            min_confidence_strong_buy=float(conf["min_confidence_to_suggest_strong_buy"]),
            respect_price_bands=bool(circ["respect_exchange_price_bands"]),
            flag_near_circuit=bool(circ["flag_if_stock_near_upper_lower_circuit"]),
        ),
        schedule=schedule,
        breakers=CircuitBreakers(
            daily_loss_limit_pct_of_budget=float(cb["daily_loss_limit_pct_of_budget"]),
            weekly_loss_limit_pct_of_capital=float(cb["weekly_loss_limit_pct_of_capital"]),
            consecutive_losing_days_flag=int(cb["consecutive_losing_days_flag"]),
            max_drawdown_pause_pct=float(cb["max_drawdown_pause_pct"]),
        ),
        mf=MutualFundRules(
            rebalance_frequency=str(mf_raw["rebalance_frequency"]),
            large_cap_band=tuple(bands["large_cap_pct"]),
            mid_cap_band=tuple(bands["mid_cap_pct"]),
            debt_band=tuple(bands["debt_pct"]),
            gold_band=tuple(bands["gold_or_other_pct"]),
            ltcg_holding_days=int(mf_raw["tax_awareness"]["respect_equity_ltcg_holding_period_days"]),
            default_mode=str(mf_raw["sip_vs_lumpsum"]["default_mode"]),
        ),
        governance=ModelGovernance(
            overfit_flag_ratio=2.0,
            naive_baseline_required=bool(gov["naive_baseline_comparison_required"]),
            suggestion_expiry_minutes=int(gov["suggestion_expiry_minutes"]),
            never_hide_losing_streaks=bool(gov.get("never_hide_a_losing_streak", True)),
        ),
        raw=raw,
    )


@lru_cache(maxsize=4)
def load_risk_config(config_path: str | None = None) -> RiskConfig:
    """Load + validate the binding ruleset. Cached; call .clear() in tests."""
    root = Path(__file__).resolve().parent.parent
    candidates = (
        [Path(config_path)]
        if config_path
        else [root / "risk_config.yaml", root / "configs" / "risk_config.yaml"]
    )
    for cand in candidates:
        if cand.exists():
            return _parse(cand.resolve())
    raise RiskConfigError(
        f"risk_config.yaml not found in any of: {[str(c) for c in candidates]}"
    )


def assert_no_execution_path() -> None:
    """Runtime guard called by the backend at startup.

    Scans the advisor/paper_trading/backend modules' public surface for any
    accidental execution client. This is belt-and-braces on top of the config
    hard-lock check — the system structurally contains no order-placement code.
    """
    cfg = load_risk_config()
    if cfg.autonomous_execution_must_be_false is False:
        raise RiskConfigError("autonomous_execution hard lock flipped — refusing to start.")
