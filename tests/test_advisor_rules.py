"""Stage 3 DoD tests: deliberately try to violate EVERY risk_config rule and
confirm the advisor refuses/adjusts — explicitly, never silently."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from advisor.engine import AdvisorEngine
from advisor.mutualfunds import MFHolding, MutualFundAdvisor
from advisor.rules import PortfolioState, RuleEngine
from advisor.suggestion import ACTION_BUY, ACTION_NONE, Suggestion
from common.config import load_risk_config


IST = ZoneInfo("Asia/Kolkata")


class MockSignals:
    """Deterministic signal frame builder for rule tests."""

    def __init__(self, rows: list[dict]):
        self.rows = rows

    def score_universe(self) -> pd.DataFrame:
        base = {
            "close": 1000.0, "daily_vol_pct": 1.5, "change_pct": 0.5,
            "nifty_mom_5d": 0.005, "ret_5d": 0.02, "ret_1d": 0.004,
            "rsi_14": 58.0, "bb_pctb": 0.7, "sector_rel_mom": 0.2, "rvol_21d": 0.25,
        }
        rows = []
        for r in self.rows:
            row = {**base, **r}
            rows.append(row)
        return pd.DataFrame(rows)


@pytest.fixture()
def cfg():
    return load_risk_config()


def _state(**kw):
    return PortfolioState(**kw)


# ---------------------------------------------------------------- budget
def test_small_budget_gets_cost_warning(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.9}]))
    out = eng.generate(budget_inr=1000)   # below min_recommended 2000
    assert any("below the recommended minimum" in n for n in out.system_notes)


# ---------------------------------------------------------------- confidence gate
def test_low_confidence_never_becomes_buy(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.50}]))   # < 0.55 gate
    out = eng.generate(budget_inr=10000)
    buys = [s for s in out.suggestions if s.action in (ACTION_BUY, "STRONG_BUY")]
    assert buys == []
    # the idea is still shown, explicitly blocked — never silently dropped
    assert any(s.symbol == "TCS" and s.blocked_by for s in out.suggestions)


# ---------------------------------------------------------------- per-stock cap
def test_single_idea_cannot_exceed_stock_cap(cfg):
    # 0.95 confidence, huge budget: raw sizing would exceed 60% cap
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "RELIANCE", "sector": "Energy", "confidence": 0.95}]))
    out = eng.generate(budget_inr=100000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    assert buys, "expected at least one allowed suggestion"
    total = sum(s.amount_inr for s in buys)
    assert total <= 100000 * cfg.intraday.max_allocation_per_stock_pct / 100 + 1


# ---------------------------------------------------------------- sector cap
def test_sector_cap_enforced_across_same_sector(cfg):
    rows = [{"symbol": f"HDFC{i}", "sector": "Financials", "confidence": 0.90}
            for i in range(5)]
    eng = AdvisorEngine(cfg, MockSignals(rows))
    out = eng.generate(budget_inr=200000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    sector_total = sum(s.amount_inr for s in buys)
    assert sector_total <= 200000 * cfg.intraday.max_allocation_per_sector_pct / 100 + 1
    # and the refusal is visible on the blocked remainder
    blocked = [s for s in out.suggestions if s.action == ACTION_NONE and s.blocked_by]
    assert blocked


# ---------------------------------------------------------------- max positions
def test_max_positions_per_day_enforced(cfg):
    rows = [{"symbol": f"S{i}", "sector": f"Sec{i}", "confidence": 0.85}
            for i in range(6)]
    eng = AdvisorEngine(cfg, MockSignals(rows))
    out = eng.generate(budget_inr=200000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    assert len(buys) <= cfg.intraday.max_positions_per_day
    blocked_by_limit = [s for s in out.suggestions
                        if s.action == ACTION_NONE
                        and any("position limit" in b for b in s.blocked_by)]
    assert blocked_by_limit, "excess ideas must be explicitly blocked, not dropped"


# ---------------------------------------------------------------- min trade size
def test_min_trade_size_refused(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        # expensive stock + tiny budget -> qty rounds to 0 / below ₹300
        {"symbol": "EXPENSIVE", "sector": "X", "confidence": 0.9, "close": 50000.0}]))
    out = eng.generate(budget_inr=1000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    assert buys == []


# ---------------------------------------------------------------- cost-drag refusal
def test_edge_below_cost_refused(cfg):
    # low vol + mid confidence -> expected edge < 0.35% round-trip cost
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "FLATCO", "sector": "X", "confidence": 0.56, "daily_vol_pct": 0.4}]))
    out = eng.generate(budget_inr=20000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    assert buys == []
    refused = [s for s in out.suggestions if s.symbol == "FLATCO"]
    assert refused and any("edge below cost" in b or "below BUY gate" in b
                           for b in refused[0].blocked_by)


# ---------------------------------------------------------------- circuit breakers
def test_daily_loss_circuit_breaker_blocks_entries_but_not_exits(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "NEWIDEA", "sector": "X", "confidence": 0.9},
        {"symbol": "HELD", "sector": "Y", "confidence": 0.30},
    ]))
    state = _state(open_symbols=["HELD"], day_pnl_inr=-600, symbol_exposure={"HELD": 5000})
    out = eng.generate(budget_inr=10000, state=state)   # -6% > 5% limit
    assert any("CIRCUIT BREAKER" in n for n in out.system_notes)
    buys = [s for s in out.suggestions if s.action in (ACTION_BUY, "STRONG_BUY")]
    assert buys == []
    holds_exits = [s for s in out.suggestions if s.symbol == "HELD"]
    assert holds_exits and holds_exits[0].action in ("HOLD", "EXIT")


def test_drawdown_pause_and_losing_streak_banners(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.9}]))
    state = _state(current_drawdown_pct=16.0, consecutive_losing_days=4)
    out = eng.generate(budget_inr=50000, state=state)
    assert any("PAUSED" in n and "drawdown" in n for n in out.system_notes)
    assert any("losing days in a row" in n for n in out.system_notes)


# ---------------------------------------------------------------- stale data lock
def test_stale_feed_suspends_all_suggestions(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.9}]))
    out = eng.generate(budget_inr=50000, data_is_current=False)
    assert any("DATA STALE" in n for n in out.system_notes)
    assert out.suggestions == []


# ---------------------------------------------------------------- expiry
def test_suggestion_expiry_flagged(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.9}]))
    out = eng.generate(budget_inr=50000)
    # force age beyond expiry window
    out.generated_at = datetime.now(IST) - timedelta(
        minutes=cfg.governance.suggestion_expiry_minutes + 5)
    for s in out.suggestions:
        if s.created_ts:
            s.created_ts = out.generated_at
            s.expires_at = s.created_ts + timedelta(
                minutes=cfg.governance.suggestion_expiry_minutes)
    out = eng.revalidate(out, data_is_current=True)
    actionable = [s for s in out.suggestions if s.action in (ACTION_BUY, "STRONG_BUY")]
    assert all(any("EXPIRED" in w for w in s.warnings) for s in actionable)


# ---------------------------------------------------------------- circuit awareness
def test_near_circuit_move_flagged(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "MOVER", "sector": "X", "confidence": 0.9, "change_pct": 9.2}]))
    out = eng.generate(budget_inr=50000)
    buys = [s for s in out.suggestions if s.action != ACTION_NONE]
    assert buys and any("near upper price band" in w for s in buys for w in s.warnings)


# ---------------------------------------------------------------- MF rules
def test_mf_rebalance_respects_bands_and_ltcg(cfg):
    adv = MutualFundAdvisor(cfg)
    holdings = [
        MFHolding(1, "Big LargeCap", "large_cap", units=1000, avg_cost_per_unit=100,
                  current_nav=120, first_purchase=datetime.now() - timedelta(days=30)),
        MFHolding(2, "Big LargeCap 2", "large_cap", units=1000, avg_cost_per_unit=100,
                  current_nav=120, first_purchase=datetime.now() - timedelta(days=400)),
        MFHolding(3, "Some Debt", "debt", units=500, avg_cost_per_unit=100,
                  current_nav=110, first_purchase=datetime.now() - timedelta(days=400)),
    ]
    # large_cap = 240000/275000 = 87% >> 50% upper band
    result = adv.suggest_allocation(holdings)
    reduces = [s for s in result["suggestions"] if s["action"] == "REDUCE"]
    assert reduces
    # the young holding must carry the STCG tax warning
    young = [s for s in reduces if "SHORT-TERM" in "".join(s["warnings"])]
    assert young, "sub-365d redemption must be tax-flagged"
    # and the senior holding is the preferred trim
    senior = [s for s in reduces if s["scheme_name"] == "Big LargeCap 2"]
    assert senior


def test_mf_underweight_gets_sip_suggestion(cfg):
    adv = MutualFundAdvisor(cfg)
    holdings = [
        MFHolding(1, "Only Debt", "debt", units=1000, avg_cost_per_unit=100,
                  current_nav=110, first_purchase=datetime.now() - timedelta(days=400)),
    ]
    result = adv.suggest_allocation(holdings, new_money_inr=10000)
    buys = [s for s in result["suggestions"] if s["action"] in ("SIP", "BUY_MORE")]
    assert buys, "underweight categories should receive accumulation suggestions"
    assert any(s["category"] in ("large_cap", "mid_cap", "gold_or_other") for s in buys)


# ---------------------------------------------------------------- hard locks
def test_every_suggestion_card_has_required_fields(cfg):
    eng = AdvisorEngine(cfg, MockSignals([
        {"symbol": "TCS", "sector": "IT", "confidence": 0.9}]))
    out = eng.generate(budget_inr=50000)
    for s in out.suggestions:
        d = s.to_dict()
        for field in ("action", "amount_inr", "confidence", "rationale",
                      "est_cost_inr", "baseline_comparison"):
            assert field in d, f"missing {field} on suggestion card"
        assert isinstance(s.rationale, str) and len(s.rationale) > 10
