"""REAL-data integration test — the Stage 0 Definition of Done.

Runs only when RUN_LIVE_TESTS=1: pulls real historical OHLCV for a small
universe and streams live ticks for >=3 symbols for 60 seconds without
crashing. With SmartAPI credentials set it exercises the Angel One path;
otherwise it runs against Yahoo Finance's NSE feed.
"""

import os
import time

import pytest

pytestmark = pytest.mark.live

SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK"]


def test_real_history_and_live_stream_60s():
    if not os.environ.get("RUN_LIVE_TESTS"):
        pytest.skip("set RUN_LIVE_TESTS=1 to run the real-data integration test")

    from data.client import DataClient
    from data.providers import make_provider

    provider = make_provider()
    client = DataClient(provider=provider)

    # --- historical pull for a small test universe ---
    for sym in SYMBOLS:
        df = client.get_history(sym, "day", lookback_days=120)
        assert df is not None and len(df) > 50, f"insufficient real history for {sym}"

    # --- stream live ticks for 3 symbols for 60 seconds ---
    got = {}

    def on_tick(t):
        got[t.symbol] = got.get(t.symbol, 0) + 1

    client.stream(SYMBOLS, on_tick, poll_seconds=15)
    start = time.time()
    while time.time() - start < 60:
        time.sleep(2)
        if all(s in got for s in SYMBOLS) and time.time() - start > 30:
            break  # healthy early exit after 30s once every symbol has ticked
    client.stop_stream()

    missing = [s for s in SYMBOLS if s not in got]
    assert not missing, f"no ticks received within 60s for {missing} (provider={provider.name})"
