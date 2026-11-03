import numpy as np
import pandas as pd
import pytest

from data.cache import OHLCVCache


def _frame(n=5):
    idx = pd.date_range("2026-08-01", periods=n, freq="1D", tz="UTC")
    return pd.DataFrame(
        {"open": np.arange(n) + 1.0, "high": np.arange(n) + 2.0,
         "low": np.arange(n), "close": np.arange(n) + 1.5, "volume": 100.0 * n},
        index=idx,
    )


def test_roundtrip(tmp_path):
    c = OHLCVCache(root=tmp_path, provider="t")
    df = _frame()
    out = c.upsert("RELIANCE", df)
    pd.testing.assert_frame_equal(out.sort_index(), df.sort_index())
    again = c.load("RELIANCE")
    assert len(again) == len(df)


def test_upsert_new_wins(tmp_path):
    c = OHLCVCache(root=tmp_path, provider="t")
    c.upsert("TCS", _frame(5))
    later_idx = pd.date_range("2026-08-06", periods=1, freq="1D", tz="UTC")
    newer = pd.DataFrame(
        {"open": [10.0], "high": [11.0], "low": [9.0], "close": [999.0], "volume": [500.0]},
        index=later_idx,
    )
    merged = c.upsert("TCS", newer)
    assert len(merged) == 6
    assert merged["close"].iloc[-1] == 999.0


def test_empty_upsert_keeps_existing(tmp_path):
    c = OHLCVCache(root=tmp_path, provider="t")
    c.upsert("INFY", _frame(4))
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = c.upsert("INFY", empty)
    assert len(out) == 4


def test_bad_index_rejected(tmp_path):
    c = OHLCVCache(root=tmp_path, provider="t")
    bad = _frame(3).reset_index(drop=True)
    with pytest.raises(TypeError):
        c.upsert("X", bad)


def test_dedup_on_merge(tmp_path):
    c = OHLCVCache(root=tmp_path, provider="t")
    df = _frame(5)
    c.upsert("SBIN", df)
    dup = df.tail(2)
    merged = c.upsert("SBIN", dup)
    assert len(merged) == 5
