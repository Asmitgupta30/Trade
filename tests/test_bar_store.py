"""Write-once bar store: freeze semantics, append-only, adjustment events."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from data.bar_store import DIVERGENCE_TOLERANCE, ImmutableBarStore


def _frame(dates, close=100.0, vol=1_000_000.0):
    idx = pd.DatetimeIndex(dates)
    n = len(idx)
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": vol,
    }, index=idx)


def d(days_ago):
    return pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=days_ago)


def test_append_new_dates(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("TCS", _frame([d(3), d(2)]))
    s.upsert_immutable("TCS", _frame([d(1)]))
    out = s.load("TCS")
    assert len(out) == 3
    assert out.index.is_monotonic_increasing


def test_frozen_bar_never_overwritten(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("SBIN", _frame([d(2)], close=100.0))
    # provider now claims a different (re-adjusted) value for the SAME date
    rep = s.upsert_immutable("SBIN", _frame([d(2)], close=98.0))
    # layer 1 - STORAGE: raw frozen parquet still holds the original bytes
    month_file = next((s._series_dir("SBIN", "day")).glob("*.parquet"))
    stored = pd.read_parquet(month_file)
    assert stored["close"].iloc[0] == pytest.approx(100.0), "frozen file untouched"
    assert stored.loc[stored.index[0], "fetched_at"] == s.load("SBIN")["fetched_at"].iloc[0]
    # layer 2 - SERVED SERIES: reflects the logged adjustment (no silent rewrite,
    # the event is recorded and visible)
    out = s.load("SBIN")
    assert rep.frozen_kept == 1 and rep.appended_dates == 0
    ev = rep.adjustment_events[0]
    assert ev["frozen_close"] == pytest.approx(100.0)
    assert ev["refetched_close"] == pytest.approx(98.0)
    assert out["close"].iloc[-1] == pytest.approx(98.0)   # served post-event
    assert len(s.adjustment_events("SBIN")) == 1          # audit trail exists


def test_small_rounding_divergence_ignored(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("INFY", _frame([d(2)], close=100.0))
    tiny = 100.0 * (1 + DIVERGENCE_TOLERANCE * 0.5)
    rep = s.upsert_immutable("INFY", _frame([d(2)], close=tiny))
    assert rep.adjustment_events == []
    assert s.adjustment_events("INFY") == []


def test_adjustment_event_rescales_earlier_segment(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    # history frozen at 100; provider later re-reads the SAME dates at 50
    # (e.g. a 2:1 split discovered via retroactive adjustment) plus a new bar
    s.upsert_immutable("RELIANCE", _frame([d(3), d(2)], close=100.0))
    rep = s.upsert_immutable(
        "RELIANCE",
        pd.concat([_frame([d(3), d(2)], close=50.0), _frame([d(1)], close=50.0)]))
    out = s.load("RELIANCE")
    # earlier frozen bars rescaled x0.5, new bar already 50 -> continuous series
    assert ((out["close"] - 50.0).abs() < 1e-6).all()
    assert len(out) == 3
    # raw frozen bytes were NOT rewritten: one logged event per divergent date
    evs = s.adjustment_events("RELIANCE")
    assert len(evs) == 2 and all(e["ratio"] == pytest.approx(0.5) for e in evs)
    assert {e["date"] for e in evs} == {str(d(3).date()), str(d(2).date())}
    assert rep.frozen_kept == 2 and rep.appended_dates == 1
    # and a reload is stable (rescaling is deterministic)
    again = s.load("RELIANCE")
    pd.testing.assert_frame_equal(out, again)


def test_multiple_adjustments_compound(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    # two sequential 2:1-style discoveries, each with its own ex-date:
    # history frozen @100 -> split A revealed (ex-date d5) -> split B (ex-date d4)
    s.upsert_immutable("X", _frame([d(6), d(5)], close=100.0))
    s.upsert_immutable("X", pd.concat([_frame([d(5)], close=50.0),
                                       _frame([d(4)], close=50.0)]))
    s.upsert_immutable("X", pd.concat([_frame([d(4)], close=25.0),
                                       _frame([d(3)], close=25.0)]))
    out = s.load("X")
    # every bar lands on the same final adjusted basis
    for i in range(4):
        assert abs(out["close"].iloc[i] - 25.0) < 1e-6, out["close"].tolist()
    # dedup: refreshing identical values must not pile up duplicate events
    s.upsert_immutable("X", pd.concat([_frame([d(5)], close=50.0),
                                       _frame([d(4)], close=25.0)]))
    assert len(s.adjustment_events("X")) == 2
    # dedup: re-refreshing identical values must not pile up duplicate events
    s.upsert_immutable("X", pd.concat([_frame([d(4)], close=50.0),
                                       _frame([d(3)], close=25.0)]))
    assert len(s.adjustment_events("X")) == 2


def test_provider_isolation(tmp_path):
    s1 = ImmutableBarStore(root=tmp_path, provider="yahoo")
    s2 = ImmutableBarStore(root=tmp_path, provider="nse_bhavcopy")
    s1.upsert_immutable("ITC", _frame([d(1)], close=400.0))
    assert s2.load("ITC") is None


def test_bad_index_rejected(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    with pytest.raises(TypeError):
        s.upsert_immutable("X", _frame([1, 2]).reset_index(drop=True))


def test_empty_incoming_is_noop(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    rep = s.upsert_immutable("X", pd.DataFrame())
    assert rep.appended_dates == 0


def test_client_serves_frozen_history_when_provider_dies(tmp_path):
    from data.bar_store import ImmutableBarStore
    from data.client import DataClient
    from data.providers import SyntheticProvider

    store = ImmutableBarStore(root=tmp_path, provider="synthetic")
    client = DataClient(provider=SyntheticProvider(), bar_store=store)
    df1 = client.get_history("TCS", "day", lookback_days=60)

    class Boom:
        name = "boom"
        supports_native_ws = False

        def fetch_history(self, *a, **k):
            raise RuntimeError("provider down")

        def fetch_quotes(self, symbols):
            return {}

    dead = DataClient(provider=Boom(), bar_store=store)
    df2 = dead.get_history("TCS", "day", lookback_days=60)
    assert not df2.empty
    # frozen bars identical across both reads
    common = df1.index.intersection(df2.index)
    assert len(common) > 0
    assert np.allclose(df1.loc[common, "close"], df2.loc[common, "close"])


def test_client_tail_fetch_appends_without_rewriting(tmp_path):
    from data.bar_store import ImmutableBarStore
    from data.client import DataClient

    class Scripted:
        name = "scripted"
        supports_native_ws = False

        def __init__(self):
            self.calls = []

        def fetch_history(self, symbol, interval, start=None, end=None):
            self.calls.append((symbol, pd.Timestamp(start).isoformat(),
                               pd.Timestamp(end).isoformat()))
            base = pd.Timestamp.now("UTC").normalize() - pd.Timedelta(days=5)
            idx = pd.DatetimeIndex([base - pd.Timedelta(days=i) for i in range(3)])
            return _frame(idx, close=100.0)

        def fetch_quotes(self, symbols):
            return {}

    prov = Scripted()
    store = ImmutableBarStore(root=tmp_path, provider="scripted")
    client = DataClient(provider=prov, bar_store=store)
    client.get_history("TCS", "day", lookback_days=30)
    n_after_first = len(store.load("TCS"))
    client.get_history("TCS", "day", lookback_days=30)
    # second call: provider asked only for the tail (start near last frozen date)
    assert len(prov.calls) == 2
    second_start = pd.Timestamp(prov.calls[1][1])
    assert second_start >= d(5) - pd.Timedelta(days=8)
    # no duplicate rows, values stable
    out = store.load("TCS")
    assert len(out) == n_after_first
    assert not out.index.duplicated().any()

