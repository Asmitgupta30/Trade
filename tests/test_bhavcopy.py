"""Parser + freeze-semantics tests for bhavcopy ingestion (offline fixtures)."""

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from data.bar_store import ImmutableBarStore
from data.bhavcopy import _parse_legacy, _parse_new, fetch_day


def _zip_bytes(csv_text: str, inner: str = "bhav.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(inner, csv_text)
    return buf.getvalue()


NEW_CSV = """TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,TtlTradgVol,TtlTrfVal,ISIN
2026-08-21,RELIANCE,EQ,100,102,99,101,101,99,123456,1.2e9,INE002A01018
2026-08-21,TCS,EQ,3000,3050,2990,3020,3020,3005,65432,1.9e11,INE467B01029
2026-08-21,SOMEBOND,N1,10,10,10,10,10,10,1000,10000,INE999X01017
"""

LEGACY_CSV = """SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP
RELIANCE,EQ,100,102,99,101,101,99,123456,1200000000,21-AUG-2026
TCS,EQ,3000,3050,2990,3020,3020,3005,65432,190000000000,21-AUG-2026
SOMEBOND,N2,10,10,10,10,10,10,1000,10000,21-AUG-2026
"""


def test_parse_new_filters_series():
    rows = _parse_new(_zip_bytes(NEW_CSV, "bhavcopy.csv"))
    assert set(rows["symbol"]) == {"RELIANCE", "TCS"}
    assert len(rows) == 2
    assert float(rows[rows.symbol == "RELIANCE"]["close"].iloc[0]) == pytest.approx(101.0)


def test_parse_legacy_filters_series_and_handles_spaces():
    rows = _parse_legacy(_zip_bytes(LEGACY_CSV, "cm21AUG2026bhav.csv"))
    assert set(rows["symbol"]) == {"RELIANCE", "TCS"}
    assert float(rows[rows.symbol == "TCS"]["close"].iloc[0]) == pytest.approx(3020.0)


class FakeResp:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


def test_fetch_day_freezes_into_store_once(monkeypatch, tmp_path):
    store = ImmutableBarStore(root=tmp_path, provider="nse_bhavcopy")
    payload = _zip_bytes(NEW_CSV, "b.csv")
    calls = {"n": 0}

    class FakeSession:
        headers = {}

        def get(self, url, timeout=None):
            calls["n"] += 1
            return FakeResp(payload)

    monkeypatch.setattr("data.bhavcopy.time.sleep", lambda s: None)
    res = fetch_day(FakeSession(), date(2026, 8, 21), store,
                    symbols={"RELIANCE", "TCS"})
    assert res.status == "fetched"
    first_calls = calls["n"]

    # second fetch of the SAME day: fully frozen -> no network at all
    res2 = fetch_day(FakeSession(), date(2026, 8, 21), store,
                     symbols={"RELIANCE", "TCS"})
    assert res2.status == "frozen"
    assert calls["n"] == first_calls

    out = store.load("RELIANCE")
    assert len(out) == 1
    assert out["close"].iloc[0] == pytest.approx(101.0)
    # IST trading date stamped as 18:30 UTC of the prior calendar day
    assert out.index[0].isoformat().endswith("18:30:00+00:00")


def test_fetch_day_holiday_is_unavailable(monkeypatch, tmp_path):
    store = ImmutableBarStore(root=tmp_path, provider="nse_bhavcopy")

    class NotFoundSession:
        headers = {}

        def get(self, url, timeout=None):
            code = 404 if "nsearchives" in url else 404
            return FakeResp(b"", status=code)

    monkeypatch.setattr("data.bhavcopy.time.sleep", lambda s: None)
    monkeypatch.setattr("data.bhavcopy._download",
                        lambda sess, url, attempts=3: (_ for _ in ()).throw(
                            FileNotFoundError(url)))
    res = fetch_day(NotFoundSession(), date(2026, 1, 26), store,
                    symbols={"RELIANCE"})   # Republic Day
    assert res.status == "unavailable"


def test_day_index_skips_network_despite_never_listed_symbol(monkeypatch, tmp_path):
    """Symbols absent from old dates (new listings) must not force re-fetches."""
    store = ImmutableBarStore(root=tmp_path, provider="nse_bhavcopy")
    payload = _zip_bytes(NEW_CSV, "b.csv")

    class FakeSession:
        headers = {}

        def get(self, url, timeout=None):
            return FakeResp(payload)

    monkeypatch.setattr("data.bhavcopy.time.sleep", lambda s: None)
    d0 = date(2026, 8, 21)
    fetch_day(FakeSession(), d0, store, symbols={"RELIANCE", "TCS"})
    # TCS is now 'frozen'; RELIANCE too; a never-listed symbol stays missing
    # forever -> the DAY INDEX must be what makes the next call skip.
    assert store.has_date("RELIANCE", pd.Timestamp(d0, tz="Asia/Kolkata")
                          .tz_convert("UTC"))
    calls_before = {"n": 0}

    class CountingSession(FakeSession):
        def get(self, url, timeout=None):
            calls_before["n"] += 1
            return FakeResp(payload)

    res = fetch_day(CountingSession(), d0, store,
                    symbols={"RELIANCE", "TCS", "NEVERLISTED"})
    assert res.status == "frozen"
    assert calls_before["n"] == 0


def test_throttle_present_between_days():
    import inspect
    from data import bhavcopy as mod
    src = inspect.getsource(mod.backfill)
    assert "time.sleep" in src and "0.7" in src
