"""Deep-snapshot integrity checks (Section C DoD).

Skipped cleanly until var/cache/adjusted_bhav + features_snapshot_deep exist;
once present these run the same lookahead guarantees against REAL exchange
history rather than synthetic frames.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
DEEP = ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
ADJ_DIR = ROOT / "var" / "cache" / "adjusted_bhav"


def _ready():
    return DEEP.exists() and ADJ_DIR.exists() and any(ADJ_DIR.glob("*.parquet"))


@pytest.mark.skipif(not _ready(), reason="deep snapshot not built yet")
def test_deep_snapshot_spans_multiple_regimes():
    df = pd.read_parquet(DEEP)
    span_years = (df.index.max() - df.index.min()).days / 365.25
    assert span_years >= 4.0, f"deep history too short: {span_years:.1f}y"
    # must contain at least one high-stress year (2020) and a calm year
    years = {d.year for d in df.index.unique()}
    assert 2020 in years and any(y in years for y in (2023, 2024))


@pytest.mark.skipif(not _ready(), reason="deep snapshot not built yet")
def test_deep_snapshot_pit_invariance_real_data():
    """Truncate-at-T == slice-from-full, on real exchange prices."""
    from features.pipeline import compute_symbol_features

    df = pd.read_parquet(DEEP)
    sym = df["symbol"].value_counts().index[0]
    adj = pd.read_parquet(ADJ_DIR / f"{sym}.parquet")
    adj.index = pd.DatetimeIndex(adj.index).tz_convert("UTC") \
        if adj.index.tz is not None else pd.DatetimeIndex(adj.index).tz_localize("UTC")
    ohlc = adj[["open", "high", "low", "close", "volume"]].astype(float) \
        .dropna(subset=["close"])
    t = ohlc.index[len(ohlc) // 2]

    full_feats = compute_symbol_features(ohlc).loc[:t]
    trunc_feats = compute_symbol_features(ohlc.loc[:t])
    pd.testing.assert_frame_equal(trunc_feats, full_feats, check_exact=True,
                                  check_freq=False)


@pytest.mark.skipif(not _ready(), reason="adjusted bhav data not built yet")
def test_adjusted_series_positive_and_continuous():
    """Adjusted closes strictly positive; no single-day |return| beyond 60%
    (which would indicate an unfixed split glitch)."""
    worst = []
    for p in sorted(ADJ_DIR.glob("*.parquet"))[:40]:
        df = pd.read_parquet(p)
        col = "adj_close" if "adj_close" in df.columns else "close"
        assert (df[col] > 0).all(), f"{p.name}: non-positive adjusted close"
        rets = df[col].pct_change().abs().max()
        worst.append((p.stem, float(rets)))
    offenders = [(s, r) for s, r in worst if r > 0.60]
    assert not offenders, f"suspicious gaps: {sorted(offenders, key=lambda x: -x[1])[:5]}"
