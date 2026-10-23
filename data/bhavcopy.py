"""NSE bhavcopy ingestion â€” exchange-published, free EOD history.

Two archive layouts exist:
  * NEW (udiff, since Jul 2024):
      https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
  * LEGACY (back to 1994):
      https://archives.nseindia.com/content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip

Policy (Section C requirements):
  * aggressive local caching via the write-once ImmutableBarStore â€” a downloaded
    date is NEVER re-downloaded
  * throttled: >=0.7s between requests, exponential backoff on failures
  * bhavcopy prices are RAW (unadjusted); corporate-action adjustment happens
    in reconcile step (see scripts/reconcile_bhavcopy.py), not here
"""

from __future__ import annotations

import io
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from data.bar_store import ImmutableBarStore

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/csv,application/zip",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

NEW_URL = ("https://nsearchives.nseindia.com/content/cm/"
           "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip")
LEGACY_URL = ("https://archives.nseindia.com/content/historical/EQUITIES/"
              "{yyyy}/{mon}/cm{dd}{mon}{yyyy}bhav.csv.zip")

SERIES_KEEP = {"EQ", "BE", "BZ"}


class BhavcopyError(RuntimeError):
    pass


@dataclass
class DayResult:
    trade_date: date
    status: str            # fetched | frozen | holiday | unavailable
    rows: int = 0
    source: str = ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def _download(session: requests.Session, url: str, attempts: int = 3) -> bytes:
    delay = 1.0
    last_err: Exception | None = None
    for attempt in range(attempts):
        try:
            resp = session.get(url, timeout=45)
            if resp.status_code == 404:
                raise FileNotFoundError(url)
            resp.raise_for_status()
            return resp.content
        except FileNotFoundError:
            raise
        except Exception as exc:
            last_err = exc
            log.warning("download failed (%s/%s) %s: %s",
                        attempt + 1, attempts, url, exc)
            time.sleep(delay)
            delay *= 2
    raise BhavcopyError(f"failed {url}: {last_err}")


def _parse_new(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = zf.namelist()[0]
        df = pd.read_csv(zf.open(name))
    # NSE udiff schema uses 'SctySrs'; tolerate the documented 'SctySeries'
    series_col = "SctySrs" if "SctySrs" in df.columns else "SctySeries"
    if series_col not in df.columns or "TckrSymb" not in df.columns:
        raise ValueError(f"unexpected new-format layout: {list(df.columns)[:8]}")
    df = df[df[series_col].astype(str).str.strip().isin(SERIES_KEEP)]
    out = pd.DataFrame({
        "symbol": df["TckrSymb"].astype(str),
        "open": df["OpnPric"].astype(float),
        "high": df["HghPric"].astype(float),
        "low": df["LwPric"].astype(float),
        "close": df["ClsPric"].astype(float),
        "volume": df["TtlTradgVol"].astype(float),
    })
    return out


def _parse_legacy(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        name = zf.namelist()[0]
        df = pd.read_csv(zf.open(name))
    df.columns = [c.strip().upper() for c in df.columns]
    if "SYMBOL" not in df.columns or "CLOSE" not in df.columns:
        raise ValueError(f"unexpected legacy layout: {list(df.columns)[:8]}")
    if "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip().isin(SERIES_KEEP)]
    return pd.DataFrame({
        "symbol": df["SYMBOL"].astype(str).str.strip(),
        "open": df["OPEN"].astype(float),
        "high": df["HIGH"].astype(float),
        "low": df["LOW"].astype(float),
        "close": df["CLOSE"].astype(float),
        "volume": df["TOTTRDQTY"].astype(float),
    })


def _day_index_path(store: ImmutableBarStore) -> Path:
    return store.dir / "_days_done.json"


def _load_day_index(store: ImmutableBarStore) -> set[str]:
    p = _day_index_path(store)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _mark_day_done(store: ImmutableBarStore, trade_date: date) -> None:
    """Write-once day index: exact resume marker independent of symbol churn
    (new listings / renames make per-symbol checks unreliable for old dates)."""
    path = _day_index_path(store)
    done = _load_day_index(store)
    done.add(trade_date.isoformat())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(done)), encoding="utf-8")
    os.replace(tmp, path)


def fetch_day(session: requests.Session, trade_date: date,
              store: ImmutableBarStore, symbols: set[str] | None = None,
              throttle_seconds: float = 0.7) -> DayResult:
    """Fetch ONE day's bhavcopy and merge into the write-once store.

    Skip logic (in order): day-index hit -> zero work; then per-symbol frozen
    check. The day-index exists because symbols that did not exist on an old
    date (new listings, renames) would otherwise force a re-download forever.
    """
    if trade_date.isoformat() in _load_day_index(store):
        return DayResult(trade_date=trade_date, status="frozen",
                         rows=0, source="day-index")

    ts = pd.Timestamp(trade_date, tz="Asia/Kolkata").tz_convert("UTC")
    if symbols:
        missing = [s for s in symbols if not store.has_date(s, ts)]
        if not missing:
            _mark_day_done(store, trade_date)
            return DayResult(trade_date=trade_date, status="frozen",
                             rows=0, source="cache")

    yyyymmdd = trade_date.strftime("%Y%m%d")
    yyyy = trade_date.strftime("%Y")
    mon = trade_date.strftime("%b").upper()
    dd = trade_date.strftime("%d")

    content = None
    source = ""
    rows = None
    for url, parser in ((NEW_URL.format(yyyymmdd=yyyymmdd), _parse_new),
                        (LEGACY_URL.format(yyyy=yyyy, mon=mon, dd=dd), _parse_legacy)):
        try:
            content = _download(session, url)
            rows = parser(content)          # may raise on malformed payloads
            source = url.split("/")[2]
            break
        except (FileNotFoundError, ValueError, zipfile.BadZipFile,
                KeyError) as exc:
            log.debug("source unusable for %s (%s): %s", trade_date,
                      url.split('/')[-1][:40], exc)
            rows = None
            content = None
            continue
        except BhavcopyError:
            rows = None
            continue

    if rows is None or content is None:
        return DayResult(trade_date=trade_date, status="unavailable")

    if symbols:
        rows = rows[rows["symbol"].isin(symbols)]
    if rows.empty:
        return DayResult(trade_date=trade_date, status="unavailable", rows=0,
                         source=source)

    per_symbol_appended = 0
    for sym, g in rows.groupby("symbol"):
        frame = g.set_index(pd.DatetimeIndex([ts] * len(g)))[
            ["open", "high", "low", "close", "volume"]]
        rep = store.upsert_immutable(str(sym), frame.iloc[[0]], "day")
        per_symbol_appended += rep.appended_dates
        time.sleep(0.001)
    _mark_day_done(store, trade_date)
    return DayResult(trade_date=trade_date, status="fetched",
                     rows=per_symbol_appended, source=source)


def backfill(start: date, end: date, store: ImmutableBarStore,
             symbols: set[str] | None = None,
             progress_every: int = 25) -> dict:
    """Throttled, resumable backfill. Skips weekends; holidays surface as
    'unavailable' and are simply recorded (no retry storm)."""
    session = _session()
    # Counter contract (tests/test_counter_contracts.py::test_backfill_buckets):
    #   fetched        <=> fetch_day returned status "fetched" (network happened,
    #                      >=1 row stored). NOT incremented for index/cache hits.
    #   frozen_skipped <=> status "frozen" (day-index hit or all symbols already
    #                      present; zero network).
    #   unavailable    <=> status "unavailable" ONLY (404/parse-fail/holiday).
    #                      Must never absorb "frozen".
    # The three buckets are exhaustive over processed weekdays and must sum to
    # results["days"].
    results = {"fetched": 0, "frozen_skipped": 0, "unavailable": 0, "days": 0}
    d = start
    while d <= end:
        if d.weekday() < 5:
            res = fetch_day(session, d, store, symbols)
            results["days"] += 1   # counter: days = every weekday processed (any outcome)
            if res.status == "fetched":                     # counter: fetched
                results["fetched"] += 1
            elif res.status == "frozen":                    # counter: frozen_skipped
                results["frozen_skipped"] += 1
            elif res.status == "unavailable":               # counter: unavailable
                results["unavailable"] += 1
            if results["days"] % progress_every == 0:
                log.info(
                    "bhavcopy backfill %s: %d fetched / %d frozen-skipped / "
                    "%d unavailable", d, results["fetched"],
                    results["frozen_skipped"], results["unavailable"])
            time.sleep(0.7)          # be polite to exchange servers
        d += timedelta(days=1)
    return results

