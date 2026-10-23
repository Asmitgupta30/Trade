import numpy as np
import pandas as pd
import pytest

from models.metrics import (
    annualized_return, equity_curve, hit_rate, max_drawdown_pct, sharpe_ratio,
    summarize_suggestion_outcomes,
)


def test_hit_rate_basic():
    assert hit_rate([True, True, False, True]) == pytest.approx(0.75)
    assert np.isnan(hit_rate([]))


def test_sharpe_known_case():
    # constant positive returns -> huge sharpe; zero std -> 0
    r = pd.Series([0.001] * 20)
    s = sharpe_ratio(r)
    assert s > 5
    assert sharpe_ratio(pd.Series([0.0, 0.0, 0.0])) == 0.0


def test_sharpe_sign_follows_returns():
    up = pd.Series([0.01, -0.005, 0.008, 0.002, -0.001, 0.012])
    down = -up
    assert sharpe_ratio(up) > 0
    assert sharpe_ratio(down) < 0


def test_max_drawdown():
    eq = pd.Series([100, 120, 90, 95, 130])
    dd = max_drawdown_pct(eq)
    assert dd == pytest.approx((120 - 90) / 120 * 100, rel=1e-6)


def test_equity_curve_compounds():
    eq = equity_curve([0.10, -0.10])
    assert eq.iloc[-1] == pytest.approx(1.0 * 1.1 * 0.9)


def test_annualized_return():
    r = pd.Series([0.01] * 252)
    ann = annualized_return(r)
    assert ann == pytest.approx(1.01 ** 252 - 1, rel=1e-3)


def test_summarize_outcomes_gate_block():
    outcomes = [
        {"direction_ok": True, "return_pct": 1.2},
        {"direction_ok": False, "return_pct": -0.4},
        {"direction_ok": True, "return_pct": 0.8},
        {"direction_ok": True, "return_pct": 2.0},
    ]
    summary = summarize_suggestion_outcomes(outcomes)
    assert summary["n"] == 4
    assert summary["hit_rate"] == pytest.approx(0.75)
    assert summary["sharpe"] is not None
    assert summary["max_drawdown_pct"] >= 0


def test_summarize_empty_is_honest():
    s = summarize_suggestion_outcomes([])
    assert s["n"] == 0 and s["hit_rate"] is None
