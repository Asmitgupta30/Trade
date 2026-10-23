"""DataClient — the single facade the rest of the system uses for market data.

Responsibilities:
  * historical OHLCV with transparent local caching (connection drops never
    lose data: cache is upserted on every fetch)
  * live tick streaming (native ws when provider supports it, polling otherwise)
  * staleness tracking -> feed_status() drives the UI's data-stale banner and
    suspends new suggestions per risk_config hard lock #3
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from common.types import FeedStatus, Quote, Tick
from data.bar_store import ImmutableBarStore
from data.providers import ProviderError, make_provider

log = logging.getLogger(__name__)

STALE_AFTER_SECONDS = 180          # no tick for 3 min during market hours => stale
TAIL_OVERLAP_DAYS = 7              # refetch overlap so append-only store sees new bars


class DataClient:
    def __init__(self, provider=None, cache_provider_name: str | None = None,
                 bar_store: ImmutableBarStore | None = None):
        self.provider = provider or make_provider()
        # write-once store: frozen dates are never silently re-adjusted
        self.store = bar_store or ImmutableBarStore(
            provider=cache_provider_name or self.provider.name)
        self._lock = threading.Lock()
        self._last_tick_ts: datetime | None = None
        self._streaming_symbols: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------- history ----------------
    def get_history(self, symbol: str, interval: str = "day",
                    lookback_days: int = 400, use_cache: bool = True) -> pd.DataFrame:
        """Historical OHLCV from the immutable (write-once) bar store.

        Fetches only the unfrozen tail on refresh; frozen history can never be
        silently overwritten with re-adjusted values. If the provider fails and
        frozen data exists, the frozen series is served.
        """
        end = datetime.now(timezone.utc)
        last_frozen = None
        if use_cache:
            last_frozen = self.store.last_frozen_date(symbol, interval)

        need_from = end - timedelta(days=max(lookback_days + 5, 10))
        if last_frozen is not None:
            need_from = max(need_from, last_frozen - timedelta(days=TAIL_OVERLAP_DAYS))

        try:
            fresh = self.provider.fetch_history(symbol, interval,
                                                start=need_from, end=end)
            if not fresh.empty:
                self.store.upsert_immutable(symbol, fresh, interval)
            elif last_frozen is None:
                log.warning("no data for %s and nothing frozen", symbol)
        except ProviderError as exc:
            log.warning("history fetch failed for %s (%s)", symbol, exc)
        except Exception as exc:
            log.warning("history fetch error for %s: %s", symbol, exc)

        frozen = self.store.load(symbol, interval)
        if frozen is None or frozen.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        out = frozen.tail(max(lookback_days * 2, 10))
        return out[["open", "high", "low", "close", "volume"]]

    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        return self.provider.fetch_quotes(list(symbols))

    def get_quote(self, symbol: str) -> Quote | None:
        return self.get_quotes([symbol]).get(symbol)

    # ---------------- streaming ----------------
    def stream(self, symbols: list[str], on_tick: Callable[[Tick], None],
               poll_seconds: int = 15) -> None:
        """Start background live-tick stream. Idempotent; restarts replace symbols."""

        def wrapped(q: Quote):
            with self._lock:
                self._last_tick_ts = q.ts if q.ts.tzinfo else q.ts.replace(tzinfo=timezone.utc)
                self._connected_once = True
            on_tick(Tick(symbol=q.symbol, ltp=q.ltp, volume=q.volume or 0, ts=self._last_tick_ts))

        self.stop_stream()
        self._stop.clear()
        self._streaming_symbols = list(symbols)
        self._thread = threading.Thread(
            target=lambda: _run_stream(self.provider, self._streaming_symbols, wrapped,
                                       poll_seconds, self._stop),
            daemon=True, name="dataclient-stream",
        )
        self._thread.start()

    def stop_stream(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None
        self._streaming_symbols = []

    def feed_status(self) -> FeedStatus:
        now = datetime.now(timezone.utc)
        status = "disconnected"
        if self._last_tick_ts is not None:
            age = (now - self._last_tick_ts).total_seconds()
            if self.provider.name == "yahoo":
                status = "delayed" if age < STALE_AFTER_SECONDS else "stale"
            elif self.provider.name == "smartapi":
                status = "live" if age < STALE_AFTER_SECONDS else "stale"
            else:
                status = "synthetic"
            if age >= STALE_AFTER_SECONDS:
                status = "stale"
        return FeedStatus(
            status=status,
            last_tick_ts=self._last_tick_ts,
            symbols_streaming=tuple(self._streaming_symbols),
            provider=self.provider.name,
        )

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def is_data_current(self) -> bool:
        """True only when feed is live/delayed-fresh — gates new suggestions."""
        fs = self.feed_status()
        return fs.status in ("live", "delayed", "synthetic")


def _run_stream(provider, symbols, wrapped, poll_seconds, stop):
    while not stop.is_set():
        try:
            provider.open_stream(symbols, wrapped, poll_seconds=poll_seconds,
                                 stop_event=_ProxyStop(stop))
        except Exception as exc:
            log.warning("stream crashed (%s); reconnecting with backoff", exc)
        if stop.is_set():
            break
        delay = min(poll_seconds * 4, 60)
        log.info("restarting stream in %ss", delay)
        stop.wait(delay)


class _ProxyStop:
    """open_stream implementations wait on this; set() propagates from parent."""

    def __init__(self, parent: threading.Event):
        self._parent = parent

    def is_set(self) -> bool:
        return self._parent.is_set()

    def set(self) -> None:
        pass

    def wait(self, timeout=None) -> bool:
        return self._parent.wait(timeout)
