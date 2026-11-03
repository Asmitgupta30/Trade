"""Unit tests for institutional Order Block & Liquidity analytics."""

import numpy as np
import pandas as pd
import pytest

from advisor.orderblock import (
    build_market_picture,
    detect_bearish_order_block,
    detect_bullish_order_block,
    scan_order_blocks,
)


def _make_bullish_bos_df(n: int = 40) -> pd.DataFrame:
    """Create synthetic price series featuring a swing high, then a red OB candle, then an upward BOS."""
    dates = pd.date_range("2026-01-01", periods=n)
    # Start at 100, rise to 110 (swing high at bar 15), pull back to 105, print red candle, then blast to 118
    opens = np.ones(n) * 100.0
    highs = np.ones(n) * 101.0
    lows = np.ones(n) * 99.0
    closes = np.ones(n) * 100.0

    for i in range(15):
        opens[i] = 100 + i * 0.7
        closes[i] = opens[i] + 0.5
        highs[i] = closes[i] + 0.3
        lows[i] = opens[i] - 0.3

    # Bar 15 is swing high around 111
    highs[15] = 112.0
    closes[15] = 111.0

    # Bars 16-18 pullback
    for i in range(16, 19):
        opens[i] = closes[i - 1]
        closes[i] = opens[i] - 1.0
        highs[i] = opens[i] + 0.2
        lows[i] = closes[i] - 0.2

    # Bar 19 is Order Block: red candle at 106.5 down to 104.5
    opens[19] = 107.0
    closes[19] = 105.0
    highs[19] = 107.5
    lows[19] = 104.0

    # Bars 20-25: Aggressive upward displacement breaking swing high 112
    for i in range(20, 26):
        opens[i] = closes[i - 1]
        closes[i] = opens[i] + 2.5
        highs[i] = closes[i] + 0.5
        lows[i] = opens[i] - 0.2

    # Bars 26-n: Drift back toward OB zone (retest)
    for i in range(26, n):
        opens[i] = closes[i - 1]
        closes[i] = opens[i] - 1.0
        highs[i] = opens[i] + 0.2
        lows[i] = closes[i] - 0.2

    # Set last close inside the OB zone [104, 107.5] for IN_ZONE_TRIGGER
    closes[-1] = 106.0
    opens[-1] = 106.5
    highs[-1] = 107.0
    lows[-1] = 105.5

    return pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000000] * n,
    }, index=dates)


def test_detect_bullish_order_block():
    df = _make_bullish_bos_df(40)
    ob = detect_bullish_order_block(df, "TESTBULL", "IT")
    assert ob is not None
    assert ob.side == "BULLISH"
    assert ob.zone_low <= ob.zone_high
    assert ob.invalidation < ob.zone_low
    assert ob.target_2 > ob.zone_high
    assert ob.risk_reward_ratio >= 2.0
    assert ob.retest_status in ("IN_ZONE_TRIGGER", "APPROACHING_ZONE", "WAITING_FOR_RETEST")


def test_market_picture_calculation():
    dates = pd.date_range("2026-01-01", periods=10)
    universe = {
        "SYM_A": pd.DataFrame({"close": [100, 105]}, index=dates[:2]),  # +5%
        "SYM_B": pd.DataFrame({"close": [200, 206]}, index=dates[:2]),  # +3%
        "SYM_C": pd.DataFrame({"close": [300, 290]}, index=dates[:2]),  # -3.3%
    }
    mp = build_market_picture(universe)
    assert mp.total_stocks == 3
    assert mp.bullish_count == 2
    assert mp.bearish_count == 1
    assert mp.advance_decline_ratio > 50.0


def test_scan_order_blocks_sector_cap():
    df = _make_bullish_bos_df(40)
    # Provide multiple stocks from the same sector
    universe = {
        f"SYM_{i}": df.copy() for i in range(8)
    }
    res = scan_order_blocks(universe)
    assert "bullish_picks" in res
    assert "bearish_picks" in res
    assert "market_picture" in res

    # Max 2 picks per sector rule
    sector_counts = {}
    for p in res["bullish_picks"]:
        s = p["sector"]
        sector_counts[s] = sector_counts.get(s, 0) + 1
        assert sector_counts[s] <= 2
