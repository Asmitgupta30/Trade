"""DataClient behavior tests using the deterministic synthetic provider.

The REAL-data integration test (history + 60s live stream) is marked `live`
and runs via `make verify-live` once SmartAPI credentials are present; it is
kept out of the default suite so CI never depends on market hours.
"""

import threading
import time

import pandas as pd
import pytest

from data.client import DataClient
from data.providers import SyntheticProvider


@pytest.fixture(scope="module")
def client():
    c = DataClient(provider=SyntheticProvider(), cache_provider_name="synthetic")
    return c


def test_history_shape(client):
    df = client.get_history("RELIANCE", "day", lookback_days=200)
    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert (df["high"] >= df["low"]).all()
    assert ((df["open"] <= df["high"]) & (df["close"] <= df["high"])).all()
    assert ((df["open"] >= df["low"]) & (df["close"] >= df["low"])).all()


def test_history_deterministic(client):
    a = client.get_history("TCS", "day", lookback_days=100)
    b = client.get_history("TCS", "day", lookback_days=100)
    # append-only store: second call may add newer bars; frozen history is stable
    common = a.index.intersection(b.index)
    assert len(common) > 50
    pd.testing.assert_frame_equal(
        b.loc[common], a.loc[common], check_exact=True)


def test_quotes(client):
    quotes = client.get_quotes(["INFY", "ICICIBANK"])
    assert set(quotes) == {"INFY", "ICICIBANK"}
    for q in quotes.values():
        assert q.ltp > 0
        assert q.ts is not None


def test_stream_receives_ticks_60s(client):
    """Streams 3 symbols and requires ticks for each within 60s (short-circuits early)."""
    got: dict[str, int] = {}
    evt = threading.Event()

    def on_tick(tick):
        got[tick.symbol] = got.get(tick.symbol, 0) + 1
        if len(got) == 3:
            evt.set()

    client.stream(["RELIANCE", "HDFCBANK", "SBIN"], on_tick, poll_seconds=2)
    try:
        deadline = time.time() + 60
        while time.time() < deadline and not evt.is_set():
            if evt.wait(1.0):
                break
        assert len(got) == 3, f"expected ticks from 3 symbols, got {sorted(got)}"
        assert all(n >= 1 for n in got.values())
    finally:
        client.stop_stream()


def test_feed_status_and_staleness(client, monkeypatch):
    client.stream(["ITC"], lambda t: None, poll_seconds=2)
    time.sleep(3)
    fs = client.feed_status()
    assert fs.status in ("live", "delayed", "stale", "synthetic")
    assert fs.symbols_streaming == ("ITC",)
    # simulate long silence => stale
    old = client._last_tick_ts
    client._last_tick_ts = None
    assert client.feed_status().status == "disconnected"
    client._last_tick_ts = old
    client.stop_stream()


def test_cache_survives_provider_outage(client):
    df1 = client.get_history("AXISBANK", "day", lookback_days=50)
    class Boom:
        name = "boom"
        supports_native_ws = False
        def fetch_history(self, *a, **k):
            raise RuntimeError("provider down")
        def fetch_quotes(self, symbols):
            return {}
    boom_client = DataClient(provider=Boom(), cache_provider_name="synthetic")
    df2 = boom_client.get_history("AXISBANK", "day", lookback_days=50)
    assert not df2.empty  # served from cache despite provider failure
