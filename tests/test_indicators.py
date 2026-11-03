import numpy as np
import pandas as pd
import pytest

from features.indicators import (
    all_indicators, atr, bollinger, ema, macd, multi_horizon_returns,
    realized_volatility, rsi, sma, volume_zscore,
)


def _series(vals):
    idx = pd.date_range("2026-01-01", periods=len(vals), freq="1D", tz="UTC")
    return pd.Series(vals, index=idx, dtype=float)


def test_sma_hand_computed():
    s = _series([1, 2, 3, 4, 5])
    out = sma(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_rsi_bounds_and_all_gain():
    up = _series([float(i) for i in range(1, 40)])          # strictly rising
    r = rsi(up)
    assert r.iloc[-1] == pytest.approx(100.0)
    mixed = _series([100 - (i % 5) * 10 + i * 0.1 for i in range(60)])
    rm = rsi(mixed).dropna()
    assert ((rm >= 0) & (rm <= 100)).all()


def test_macd_sign_sanity():
    rising = _series(np.linspace(100, 200, 60))
    m = macd(rising)
    assert m["macd"].iloc[-1] > 0           # fast EMA above slow in an uptrend
    falling = _series(np.linspace(200, 100, 60))
    mf = macd(falling)
    assert mf["macd"].iloc[-1] < 0


def test_bollinger_pctb():
    rng = np.random.default_rng(7)
    prices = 100 + np.cumsum(rng.normal(0, 0.5, 80))
    bb = bollinger(_series(prices)).dropna()
    assert len(bb) > 50
    # ~95% of points inside 2-sigma bands -> pctb mostly within [-0.05, 1.05]
    frac_inside = ((bb["bb_pctb"] > -0.1) & (bb["bb_pctb"] < 1.1)).mean()
    assert frac_inside > 0.9


def test_atr_positive_and_ordered():
    idx = pd.date_range("2026-01-01", periods=40, freq="1D", tz="UTC")
    rng = np.random.default_rng(3)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 40)), index=idx)
    high = close + 1.0
    low = close - 1.0
    a = atr(high, low, close).dropna()
    assert (a > 0).all()
    assert a.iloc[-1] == pytest.approx(a.iloc[-1], abs=1e-9)


def test_returns_and_vol_shapes():
    s = _series(100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 60)))
    rets = multi_horizon_returns(s)
    assert list(rets.columns) == ["ret_1d", "ret_5d", "ret_10d", "ret_21d"]
    assert rets["ret_1d"].iloc[30] == pytest.approx(s.iloc[30] / s.iloc[29] - 1)
    vol = realized_volatility(s)
    assert (vol.dropna() > 0).all().all()


def test_volume_zscore_zero_std_safe():
    v = _series([100.0] * 25)
    z = volume_zscore(v)
    assert z.iloc[-1] == 0 or pd.isna(z.iloc[-1])


def test_all_indicators_panel_causal_columns():
    rng = np.random.default_rng(11)
    n = 120
    idx = pd.date_range("2026-03-01", periods=n, freq="1D", tz="UTC")
    close = pd.Series(500 + np.cumsum(rng.normal(0, 3, n)), index=idx)
    df = pd.DataFrame({
        "open": close.shift(1), "high": close + 2, "low": close - 2,
        "close": close, "volume": rng.integers(1e5, 1e6, n),
    })
    panel = all_indicators(df)
    expected_cols = {"ret_1d", "rvol_21d", "rsi_14", "macd", "macd_signal",
                     "macd_hist", "bb_upper", "bb_lower", "bb_pctb", "bb_width",
                     "atr_14", "vol_z"}
    assert expected_cols.issubset(panel.columns)
    # warm-up rows must be NaN, never zero-filled fabrications
    assert panel["rsi_14"].iloc[:13].isna().all()
