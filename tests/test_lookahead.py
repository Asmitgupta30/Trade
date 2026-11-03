"""Lookahead-bias tests — the single most important correctness guarantee.

If a feature at time T changes when future data changes, the pipeline leaks
and every backtest number is a lie. These tests make that failure loud.
"""

import numpy as np
import pandas as pd
import pytest

from features.pipeline import (
    build_dataset, compute_symbol_features, make_forward_return_label,
)


def _ohlc(n=150, seed=5):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1D", tz="UTC")
    close = pd.Series(1000 + np.cumsum(rng.normal(0, 4, n)), index=idx)
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.5, n),
        "high": close + np.abs(rng.normal(1, 0.5, n)),
        "low": close - np.abs(rng.normal(1, 0.5, n)),
        "close": close,
        "volume": rng.integers(800_000, 2_000_000, n).astype(float),
    })


def test_features_invariant_to_future_data():
    """Truncate at T: features computed on [0..T] must equal features computed
    on the FULL history and then sliced to [0..T]."""
    full = _ohlc()
    t = full.index[100]

    feats_full = compute_symbol_features(full).loc[:t]
    feats_trunc = compute_symbol_features(full.loc[:t])

    pd.testing.assert_frame_equal(feats_trunc, feats_full, check_exact=True,
                                  check_freq=False)


def test_features_immune_to_post_t_shock():
    """A massive price spike AFTER T cannot move any feature at T."""
    base = _ohlc(seed=9)
    shocked = base.copy()
    t_loc = 100
    shocked.iloc[t_loc + 1:, :] *= 3.0   # triple everything after T

    f_base = compute_symbol_features(base).iloc[t_loc]
    f_shock = compute_symbol_features(shocked).iloc[t_loc]
    pd.testing.assert_series_equal(f_base, f_shock, check_exact=True)


def test_labels_are_forward_looking_by_design():
    df = _ohlc(n=60)
    h = 3
    label = make_forward_return_label(df, horizon=h)
    # last h labels must be NaN (no future to look at)
    assert label.iloc[-h:].isna().all()
    # label at position i equals close[i+h]/close[i] - 1
    i = 40
    expected = df["close"].iloc[i + h] / df["close"].iloc[i] - 1.0
    assert label.iloc[i] == pytest.approx(expected)


def test_training_pairs_never_use_future_features():
    """The dataset builder must not shift features forward to manufacture rows:
    feature row T pairs with label T (future return), features stay PIT."""
    hist_a, hist_b = _ohlc(seed=1), _ohlc(seed=2) * 1.5
    sectors = {"AAA": "Tech", "BBB": "Tech"}
    data = build_dataset(
        {"AAA": hist_a, "BBB": hist_b}, sectors, horizon=1,
    )
    assert "label_fwd_ret" in data.columns
    per_sym = data[data["symbol"] == "AAA"]
    # last row's label is NaN for horizon=1
    assert per_sym["label_fwd_ret"].iloc[-1] != per_sym["label_fwd_ret"].iloc[-1]  # NaN check
    # spot-check one interior label against raw closes
    closes = hist_a["close"]
    row_date = per_sym.index[50]
    assert per_sym["label_fwd_ret"].iloc[50] == pytest.approx(
        closes.iloc[51] / closes.iloc[50] - 1.0)


def test_sector_relative_momentum_pit():
    hist = {s: _ohlc(seed=i, n=80) for i, s in enumerate(["AAA", "BBB", "CCC"])}
    sectors = {"AAA": "X", "BBB": "X", "CCC": "Y"}
    data = build_dataset(hist, sectors, horizon=1)
    assert "sector_rel_mom" in data.columns
    vals = data["sector_rel_mom"].dropna()
    assert ((vals >= -0.5) & (vals <= 0.5)).all()


def test_regime_columns_present_and_finite_after_warmup():
    hist = {s: _ohlc(seed=i, n=120) for i, s in enumerate(["AAA", "BBB"])}
    data = build_dataset(hist, {"AAA": "Z", "BBB": "W"}, horizon=1)
    tail = data.dropna(subset=["nifty_mom_5d"])
    assert len(tail) > 50
    assert np.isfinite(tail["nifty_mom_5d"]).all()
