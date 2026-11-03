"""Tests for Time Machine & Multi-Scenario Simulator Engine.

Validates multi-scenario market projections, Monte Carlo forward time paths,
institutional distribution day counting, and exposure coaching.
"""

import pandas as pd
import pytest

from advisor.scenario_simulator import (
    calculate_distribution_days,
    simulate_all_market_scenarios,
    simulate_monte_carlo_time_paths,
    generate_exposure_coaching,
    run_full_time_simulation,
)


def test_distribution_days_calculation():
    """Verify distribution day detection on synthetic daily bars."""
    dates = pd.date_range("2026-09-01", periods=30, freq="B")
    closes = [22000.0 + (i * 20.0) for i in range(30)]
    volumes = [1000000 + (i * 10000) for i in range(30)]

    # Inject 4 specific distribution days (down > 0.2% on higher volume)
    closes[5] = closes[4] * 0.995  # -0.5%
    volumes[5] = volumes[4] * 1.3  # surge

    closes[12] = closes[11] * 0.993  # -0.7%
    volumes[12] = volumes[11] * 1.4

    closes[20] = closes[19] * 0.991  # -0.9%
    volumes[20] = volumes[19] * 1.5

    closes[24] = closes[23] * 0.994  # -0.6%
    volumes[24] = volumes[23] * 1.2

    df = pd.DataFrame({"close": closes, "volume": volumes}, index=dates)
    report = calculate_distribution_days(df)

    assert report.distribution_count >= 3
    assert report.status in ["CAUTION", "TOPPING_CORRECTION"]
    assert len(report.distribution_days) >= 3


def test_distribution_days_fallback():
    """Verify fallback behavior when history is empty or short."""
    report = calculate_distribution_days(None)
    assert report.distribution_count >= 0
    assert report.status in ["LOW_RISK", "CAUTION"]


def test_simulate_all_market_scenarios():
    """Verify all 4 branching scenarios are generated and probabilistically coherent."""
    scenarios = simulate_all_market_scenarios(
        budget_inr=50000.0,
        risk_pct=1.0,
        regime="risk-on",
        vix_ltp=15.0,
    )

    assert len(scenarios) == 4
    names = [s.name for s in scenarios]
    assert "BULL_EXPANSION" in names
    assert "CHOP_CONSOLIDATION" in names
    assert "BEAR_CORRECTION" in names
    assert "BLACK_SWAN" in names

    total_prob = sum(s.probability_pct for s in scenarios)
    assert abs(total_prob - 100.0) < 0.01

    bull = next(s for s in scenarios if s.name == "BULL_EXPANSION")
    assert bull.simulated_net_gain_inr > 0
    assert bull.expected_win_rate_pct > 60

    swan = next(s for s in scenarios if s.name == "BLACK_SWAN")
    # Loss must be strictly capped by risk limits
    assert swan.simulated_net_gain_inr < 0
    assert swan.simulated_net_gain_inr >= -2500.0


def test_monte_carlo_time_paths():
    """Verify Monte Carlo simulation produces reliable cones and distributions."""
    res = simulate_monte_carlo_time_paths(
        budget_inr=50000.0,
        risk_pct=1.0,
        horizon_days=21,
        n_simulations=1000,
        base_win_rate=0.55,
        reward_risk_ratio=2.0,
    )

    assert res.n_simulations == 1000
    assert res.horizon_days == 21
    assert 0 <= res.probability_of_profit_pct <= 100
    assert res.percentile_95_equity_inr > res.median_final_equity_inr
    assert res.median_final_equity_inr > res.percentile_5_equity_inr
    assert 0 <= res.max_simulated_drawdown_pct <= 100
    assert 0 <= res.risk_of_ruin_pct <= 100

    cones = res.time_series_percentiles
    assert len(cones["p5"]) == len(cones["p95"])
    assert cones["p95"][-1] > cones["p5"][-1]


def test_exposure_coaching():
    """Verify exposure coaching adjusts cash vs equity dynamically."""
    coach_risk_on = generate_exposure_coaching(
        budget_inr=50000.0,
        risk_pct=1.0,
        regime="risk-on",
        distribution_count=1,
    )
    assert coach_risk_on.recommended_equity_exposure_pct >= 80.0
    assert coach_risk_on.recommended_cash_pct <= 20.0
    assert coach_risk_on.max_active_positions >= 4

    coach_risk_off = generate_exposure_coaching(
        budget_inr=50000.0,
        risk_pct=1.0,
        regime="risk-off",
        distribution_count=5,
    )
    assert coach_risk_off.recommended_cash_pct >= 80.0
    assert coach_risk_off.max_active_positions == 1


def test_run_full_time_simulation():
    """Verify integration execution of full simulation suite."""
    class MockClient:
        def get_quotes(self, symbols):
            from dataclasses import dataclass
            @dataclass
            class MockQuote:
                ltp: float
            return {"^NSEI": MockQuote(ltp=22700.0), "^INDIAVIX": MockQuote(ltp=16.5)}

        def get_history(self, sym, interval="day", lookback_days=70):
            dates = pd.date_range("2026-08-01", periods=50, freq="B")
            return pd.DataFrame({
                "close": [22000 + i * 15 for i in range(50)],
                "volume": [1000000 for _ in range(50)],
            }, index=dates)

    data = run_full_time_simulation(MockClient(), budget_inr=50000.0, risk_pct=1.0, horizon_days=21)
    assert "scenarios" in data
    assert len(data["scenarios"]) == 4
    assert "monte_carlo" in data
    assert "distribution_days" in data
    assert "exposure_coach" in data
