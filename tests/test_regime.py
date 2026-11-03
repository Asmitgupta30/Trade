"""Unit and contract tests for market regime detection and risk gating."""

import numpy as np
import pandas as pd
import pytest

from advisor.engine import AdvisorEngine
from advisor.regime import (
    REGIME_CAUTION,
    REGIME_RISK_OFF,
    REGIME_RISK_ON,
    detect_regime,
)
from advisor.rules import PortfolioState, RuleEngine
from advisor.strategies import screen_all_strategies
from advisor.suggestion import ACTION_BUY, ACTION_STRONG_BUY, Suggestion
from common.config import load_risk_config


def test_regime_detection_vix_spike():
    """VIX >= 22 triggers risk-off regime regardless of Nifty."""
    reg = detect_regime(nifty_ltp=24000.0, vix_ltp=23.5)
    assert reg.regime == REGIME_RISK_OFF
    assert reg.allows_momentum_buys is False
    assert reg.min_confidence_hurdle >= 0.75


def test_regime_detection_nifty_downtrend():
    """Nifty trading significantly below 50 SMA triggers risk-off."""
    # Build 60 days of decreasing closes: 50-SMA will be higher than current LTP
    closes = np.linspace(25000, 23000, 60)
    df = pd.DataFrame({"close": closes})
    reg = detect_regime(nifty_history=df, nifty_ltp=23000.0, vix_ltp=15.0)
    assert reg.regime == REGIME_RISK_OFF
    assert reg.nifty_dist_sma50_pct is not None and reg.nifty_dist_sma50_pct < 0


def test_regime_detection_caution_range():
    """VIX between 18 and 22 triggers caution regime."""
    reg = detect_regime(nifty_ltp=24000.0, vix_ltp=19.5)
    assert reg.regime == REGIME_CAUTION
    assert reg.min_confidence_hurdle == 0.70


def test_regime_detection_bull_risk_on():
    """VIX < 18 and Nifty > 50 SMA triggers risk-on."""
    closes = np.linspace(22000, 24500, 60)
    df = pd.DataFrame({"close": closes})
    reg = detect_regime(nifty_history=df, nifty_ltp=24500.0, vix_ltp=13.5)
    assert reg.regime == REGIME_RISK_ON
    assert reg.allows_momentum_buys is True
    assert reg.min_confidence_hurdle <= 0.60


def test_rule_engine_enforces_risk_off_hurdle():
    """In risk-off, ideas with confidence below 0.75 are blocked."""
    cfg = load_risk_config()
    engine = RuleEngine(cfg)
    sug_marginal = Suggestion(
        symbol="TCS",
        sector="IT",
        action=ACTION_BUY,
        amount_inr=1000.0,
        confidence=0.65,
        reference_price=3500.0,
        rationale="Breakout",
        est_cost_inr=3.5,
        baseline_comparison="vs cash",
    )
    # Under risk-off, confidence 0.65 is blocked
    verdict_blocked = engine.check_buy(
        sug_marginal, budget_inr=5000.0, state=PortfolioState(), regime=REGIME_RISK_OFF
    )
    assert verdict_blocked.allowed is False
    assert any("risk-off" in r for r in verdict_blocked.reasons)

    # High conviction setup (0.78) is allowed even under risk-off
    sug_strong = Suggestion(
        symbol="COALINDIA",
        sector="Energy",
        action=ACTION_STRONG_BUY,
        amount_inr=1000.0,
        confidence=0.78,
        reference_price=450.0,
        rationale="Defensive dividend value",
        est_cost_inr=3.5,
        baseline_comparison="vs cash",
    )
    verdict_allowed = engine.check_buy(
        sug_strong, budget_inr=5000.0, state=PortfolioState(), regime=REGIME_RISK_OFF
    )
    assert verdict_allowed.allowed is True


def test_strategy_screening_regime_awareness():
    """Strategy screening respects regime by filtering lower conviction breakout trades."""
    # Synthetic universe data
    dates = pd.date_range("2026-01-01", periods=60)
    df = pd.DataFrame({
        "open": np.linspace(100, 120, 60),
        "high": np.linspace(101, 122, 60),
        "low": np.linspace(99, 119, 60),
        "close": np.linspace(100, 121, 60),
        "volume": [1000000] * 60,
    }, index=dates)

    universe = {"RELIANCE": df}
    plans_bull = screen_all_strategies(universe, capital_inr=50000.0, regime=REGIME_RISK_ON)
    plans_bear = screen_all_strategies(universe, capital_inr=50000.0, regime=REGIME_RISK_OFF)

    # In bear regime, plans are either empty or have >= 0.70 confidence
    for style, plan_list in plans_bear.items():
        for p in plan_list:
            assert p["confidence"] >= 0.70
