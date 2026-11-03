"""Unit tests for the Live Intraday Trading & Capital Maximizer Engine."""

from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import pytest

from advisor.live_intraday import (
    compute_intraday_metrics,
    evaluate_stock_intraday_setup,
    get_intraday_session_phase,
    scan_live_intraday_universe,
)
from data.client import Quote


def test_intraday_session_phases():
    tz = ZoneInfo("Asia/Kolkata")

    # Tuesday 10:00 AM -> Prime Morning
    t_morning = datetime(2026, 10, 27, 10, 0, tzinfo=tz)
    res_m = get_intraday_session_phase(t_morning)
    assert res_m["phase"] == "PRIME_MORNING"
    assert res_m["can_enter"] is True
    assert res_m["minutes_to_cutoff"] == 315

    # Tuesday 12:00 PM -> Midday Chop
    t_midday = datetime(2026, 10, 27, 12, 0, tzinfo=tz)
    res_mid = get_intraday_session_phase(t_midday)
    assert res_mid["phase"] == "MIDDAY_CHOP"
    assert res_mid["can_enter"] is False

    # Tuesday 14:00 PM -> Afternoon Prime
    t_aft = datetime(2026, 10, 27, 14, 0, tzinfo=tz)
    res_aft = get_intraday_session_phase(t_aft)
    assert res_aft["phase"] == "PRIME_AFTERNOON"
    assert res_aft["can_enter"] is True

    # Tuesday 15:20 PM -> Auto Exit Window
    t_sq = datetime(2026, 10, 27, 15, 20, tzinfo=tz)
    res_sq = get_intraday_session_phase(t_sq)
    assert res_sq["phase"] == "AUTO_EXIT_WINDOW"
    assert res_sq["can_enter"] is False
    assert res_sq["minutes_to_cutoff"] == 0


def test_compute_intraday_metrics():
    q = Quote(
        symbol="TCS",
        ltp=2500.0,
        ts=datetime.now(),
        change_pct=1.5,
        day_open=2480.0,
        day_high=2510.0,
        day_low=2475.0,
        prev_close=2463.0,
        volume=500000,
    )
    # Synthetic 15m frame
    df_15m = pd.DataFrame({
        "open": [2480.0, 2490.0, 2500.0],
        "high": [2495.0, 2505.0, 2510.0],
        "low": [2475.0, 2488.0, 2495.0],
        "close": [2490.0, 2500.0, 2500.0],
        "volume": [100000.0, 150000.0, 250000.0],
    })

    m = compute_intraday_metrics(q, df_15m)
    assert m["ltp"] == 2500.0
    assert m["orb_high"] == 2495.0
    assert m["orb_low"] == 2475.0
    assert m["vwap"] > 0
    assert m["momentum_score"] >= 65
    assert m["vol_velocity"] > 1.0


def test_evaluate_stock_intraday_setup_long_and_short():
    # Bullish metrics
    bull_metrics = {
        "ltp": 500.0,
        "vwap": 494.0,
        "change_pct": 1.8,
        "orb_high": 495.0,
        "orb_low": 490.0,
        "vol_velocity": 1.6,
        "momentum_score": 80,
        "atr_val": 4.5,
        "vwap_dist_pct": 1.21,
    }

    setup_long = evaluate_stock_intraday_setup(
        symbol="TATAPOWER",
        sector="Power",
        metrics=bull_metrics,
        budget_inr=50000.0,
        risk_pct=1.0,
    )
    assert setup_long is not None
    assert setup_long.action == "BUY"
    assert setup_long.target_1 > setup_long.entry_price
    assert setup_long.stop_loss < setup_long.entry_price
    assert setup_long.risk_reward_ratio >= 1.8
    assert setup_long.shares_5x >= setup_long.shares_1x
    assert setup_long.potential_gain_inr > 0

    # Bearish metrics
    bear_metrics = {
        "ltp": 500.0,
        "vwap": 508.0,
        "change_pct": -1.8,
        "orb_high": 510.0,
        "orb_low": 505.0,
        "vol_velocity": 1.5,
        "momentum_score": 20,
        "atr_val": 4.5,
        "vwap_dist_pct": -1.57,
    }

    setup_short = evaluate_stock_intraday_setup(
        symbol="COALINDIA",
        sector="Energy",
        metrics=bear_metrics,
        budget_inr=50000.0,
        risk_pct=1.0,
    )
    assert setup_short is not None
    assert setup_short.action == "SHORT"
    assert setup_short.target_1 < setup_short.entry_price
    assert setup_short.stop_loss > setup_short.entry_price


class MockClient:
    def get_quotes(self, symbols):
        return {
            "RELIANCE": Quote("RELIANCE", ltp=1200.0, change_pct=1.5, day_open=1185.0, day_high=1205.0, day_low=1180.0, prev_close=1182.0, volume=1000000, ts=datetime.now()),
            "TCS": Quote("TCS", ltp=2400.0, change_pct=-1.5, day_open=2440.0, day_high=2445.0, day_low=2395.0, prev_close=2436.0, volume=800000, ts=datetime.now()),
        }

    def get_history(self, symbol, interval="15minute", lookback_days=2):
        return pd.DataFrame()


def test_scan_live_intraday_universe():
    client = MockClient()
    res = scan_live_intraday_universe(client, budget_inr=50000, risk_pct=1.0)
    assert "session" in res
    assert "capital_rules" in res
    assert res["capital_rules"]["leverage_multiplier"] == 5.0
    assert "breadth" in res
    assert "long_setups" in res
    assert "short_setups" in res
