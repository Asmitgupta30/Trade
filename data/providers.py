"""Market data providers.

Canonical internal format for history:
    pd.DataFrame indexed by tz-aware UTC DatetimeIndex with columns
    ["open", "high", "low", "close", "volume"] (floats; volume float ok).

Canonical intervals: "day", "60minute", "15minute", "5minute", "1minute".

Providers:
  * SmartAPIProvider — Angel One SmartAPI (primary; requires credentials in env).
  * YFinanceProvider — real NSE EOD/delayed data with no API key (fallback so
    the system runs on real market data before credentials are provided).
  * SyntheticProvider — deterministic seeded series for offline tests only.
    It NEVER masquerades as real data: provider name is "synthetic" and the
    DataClient surfaces that in feed status.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd
import requests

from common.types import Quote
from data.instruments import InstrumentResolver

log = logging.getLogger(__name__)

INTERVAL_MAP_YF = {
    "day": "1d",
    "60minute": "60m",
    "15minute": "15m",
    "5minute": "5m",
    "1minute": "1m",
}
INTERVAL_MAP_SMARTAPI = {
    "day": "ONE_DAY",
    "60minute": "ONE_HOUR",
    "15minute": "FIFTEEN_MINUTE",
    "5minute": "FIVE_MINUTE",
    "1minute": "ONE_MINUTE",
}


class ProviderError(RuntimeError):
    pass


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns={c: str(c).lower() for c in df.columns})
    for required in ("open", "high", "low", "close"):
        if required not in out.columns:
            raise ProviderError(f"provider returned frame missing '{required}': {list(out.columns)}")
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out = out[["open", "high", "low", "close", "volume"]].astype(float)
    if not isinstance(out.index, pd.DatetimeIndex):
        raise ProviderError("history frame must be DatetimeIndex")
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["close"])


class YFinanceProvider:
    """Real NSE data without credentials. NSE quotes via Yahoo are typically
    delayed ~15 min; DataClient marks the feed 'delayed' on this provider."""

    name = "yahoo"
    supports_native_ws = False

    def __init__(self):
        import yfinance  # noqa: F401  (fail fast at construction)

        self._yf = __import__("yfinance")

    def fetch_history(
        self, symbol: str, interval: str = "day", start: datetime | None = None, end: datetime | None = None
    ) -> pd.DataFrame:
        yf_interval = INTERVAL_MAP_YF.get(interval)
        if yf_interval is None:
            raise ProviderError(f"unsupported interval {interval}")
        yf_symbol = f"{symbol.upper()}.NS" if not symbol.startswith("^") else symbol
        kwargs: dict = dict(interval=yf_interval, auto_adjust=True)
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                df = self._yf.Ticker(yf_symbol).history(**kwargs)
                if df.empty:
                    return _standardize(df)
                return _standardize(df)
            except Exception as exc:  # yfinance raises a zoo of exception types
                last_err = exc
                time.sleep(1.5 * (attempt + 1))
        raise ProviderError(f"yahoo history failed for {symbol}: {last_err}")

    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        yf_syms = [s if s.startswith("^") else f"{s.upper()}.NS" for s in symbols]
        data = self._yf.download(tickers=yf_syms, period="5d", interval="1m",
                                 group_by="ticker", progress=False, threads=True)
        now = pd.Timestamp.now("UTC")
        out: dict[str, Quote] = {}
        for internal, ysym in zip(symbols, yf_syms):
            try:
                sub = data[ysym] if isinstance(data.columns, pd.MultiIndex) else data
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                last = sub.iloc[-1]
                prev_closes = sub["Close"].resample("1D").last().dropna()
                prev_close = float(prev_closes.iloc[-2]) if len(prev_closes) >= 2 else None
                ltp = float(last["Close"])
                chg = ((ltp - prev_close) / prev_close * 100.0) if prev_close else None
                ts = sub.index[-1]
                ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
                out[internal] = Quote(
                    symbol=internal, ltp=ltp, ts=ts.to_pydatetime(), change_pct=chg,
                    day_open=float(last["Open"]), day_high=float(last["High"]),
                    day_low=float(last["Low"]), prev_close=prev_close,
                    volume=int(last["Volume"]) if "Volume" in sub.columns else None,
                )
            except Exception as exc:
                log.debug("quote build failed for %s: %s", internal, exc)
        return out

    def open_stream(self, symbols, on_tick, poll_seconds: int = 15, stop_event=None) -> None:
        """Polling stream (yahoo has no ws). Blocks until stop_event set."""
        import threading

        stop = stop_event or threading.Event()
        while not stop.is_set():
            try:
                for q in self.fetch_quotes(list(symbols)).values():
                    on_tick(q)
            except Exception as exc:
                log.warning("yahoo poll cycle failed: %s", exc)
            stop.wait(poll_seconds)


class SmartAPIProvider:
    """Angel One SmartAPI. Credentials via env:
        ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD (PIN), ANGEL_TOTP_SECRET
    Historical data + market feed are free per Angel One; only real trades cost
    brokerage — and this system never places trades (advisory-only, hard-locked)."""

    name = "smartapi"
    supports_native_ws = True

    def __init__(self, api_key: str | None = None, client_code: str | None = None,
                 password: str | None = None, totp_secret: str | None = None):
        self.api_key = api_key or os.environ.get("ANGEL_API_KEY")
        self.client_code = client_code or os.environ.get("ANGEL_CLIENT_CODE")
        self.password = password or os.environ.get("ANGEL_PASSWORD")
        self.totp_secret = totp_secret or os.environ.get("ANGEL_TOTP_SECRET")
        self._sc = None
        self._resolver = InstrumentResolver.online().build()
        self._last_hist_call = 0.0
        self._feed_token: str | None = None

    @property
    def configured(self) -> bool:
        return all([self.api_key, self.client_code, self.password, self.totp_secret])

    def _connect(self):
        if self._sc is not None:
            return self._sc
        if not self.configured:
            raise ProviderError(
                "SmartAPI credentials missing: set ANGEL_API_KEY, ANGEL_CLIENT_CODE, "
                "ANGEL_PASSWORD, ANGEL_TOTP_SECRET in .env"
            )
        try:
            from SmartApi import SmartConnect  # type: ignore
            import pyotp  # type: ignore
        except ImportError as exc:
            raise ProviderError(f"smartapi-python not installed: {exc}") from exc
        sc = SmartConnect(api_key=self.api_key)
        totp = pyotp.TOTP(self.totp_secret).now()
        data = sc.generateSession(self.client_code, self.password, totp)
        if not data or data.get("status") is False:
            raise ProviderError(f"SmartAPI login failed: {data}")
        self._sc = sc
        self._feed_token = data.get("data", {}).get("feedToken")
        log.info("SmartAPI session established for %s", self.client_code)
        return sc

    def _throttle(self, min_interval: float = 0.4):
        elapsed = time.monotonic() - self._last_hist_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_hist_call = time.monotonic()

    def _token(self, symbol: str, exchange: str = "NSE") -> str:
        if not self._resolver.available:
            self._resolver = InstrumentResolver.online().build()
        token, _ = self._resolver.resolve(symbol, exchange)
        return token

    def fetch_history(self, symbol: str, interval: str = "day",
                      start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        sc = self._connect()
        smart_interval = INTERVAL_MAP_SMARTAPI.get(interval)
        if smart_interval is None:
            raise ProviderError(f"unsupported interval {interval}")
        token = self._token(symbol)
        end = end or datetime.now()
        start = start or (end - timedelta(days=400))
        fmt = "%Y-%m-%d %H:%M"
        frames = []
        cursor = end
        # SmartAPI caps history per call (~100 days for day interval); walk backwards.
        chunk = timedelta(days=90) if interval == "day" else timedelta(days=30)
        while cursor > start:
            frm = max(start, cursor - chunk)
            self._throttle()
            try:
                resp = sc.getCandleData({
                    "exchange": "NSE", "symboltoken": token, "interval": smart_interval,
                    "fromdate": frm.strftime(fmt), "todate": cursor.strftime(fmt),
                })
            except Exception as exc:
                raise ProviderError(f"getCandleData failed for {symbol}: {exc}") from exc
            candles = (resp or {}).get("data") or []
            if candles:
                df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
                df["ts"] = pd.to_datetime(df["ts"])
                df = df.set_index("ts")
                frames.append(df)
            cursor = frm - timedelta(minutes=1)
        if not frames:
            return _standardize(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
        full = pd.concat(frames)
        return _standardize(full)

    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        sc = self._connect()
        out: dict[str, Quote] = {}
        tokens = {}
        for s in symbols:
            try:
                tokens[s] = self._token(s)
            except KeyError:
                continue
        if not tokens:
            return out
        self._throttle(0.5)
        try:
            resp = sc.getMarketData("FULL", {"NSE": list(tokens.values())})
        except Exception:
            resp = None
        fetched = (resp or {}).get("data", {}).get("fetched", []) if resp else []
        by_token = {str(item.get("symboltoken")): item for item in fetched}
        for sym, tok in tokens.items():
            item = by_token.get(tok)
            if not item:
                continue
            ltp = float(item.get("ltp") or 0)
            if ltp <= 0:
                continue
            prev_close = float(item.get("close") or 0) or None
            out[sym] = Quote(
                symbol=sym, ltp=ltp, ts=datetime.now(timezone.utc),
                change_pct=((ltp - prev_close) / prev_close * 100.0) if prev_close else None,
                day_open=float(item.get("open") or 0) or None,
                day_high=float(item.get("high") or 0) or None,
                day_low=float(item.get("low") or 0) or None,
                prev_close=prev_close, volume=int(item.get("tradeVolume") or 0) or None,
            )
        return out

    # ---- native websocket (used when credentials present) -----------------
    def open_stream(self, symbols, on_tick: Callable, poll_seconds: int = 15, stop_event=None):
        try:
            return self._open_ws_stream(symbols, on_tick, stop_event)
        except Exception as exc:
            log.warning("SmartAPI ws unavailable (%s); falling back to REST polling", exc)
            stop = stop_event or _new_event()
            while not stop.is_set():
                for q in self.fetch_quotes(list(symbols)).values():
                    on_tick(q)
                stop.wait(poll_seconds)

    def _open_ws_stream(self, symbols, on_tick, stop_event):
        import struct
        import threading

        from SmartApi.smartWebSocketV2 import SmartWebSocketV2  # type: ignore

        sc = self._connect()
        token_map = {self._token(s): s for s in symbols}
        token_list = [{"action": 0, "exchange_type": 1, "tokens": list(token_map.keys())}]
        sws = SmartWebSocketV2(self._auth_token(), self.api_key, self.client_code, self._feed_token, token_list)

        def parse_tick(binary: bytes):
            # Documented layout: [0:4] len, [4:8] feed type, [8:16] exch ts,
            # [16:20] LTP (paise), [20:24] LTQ, [24:28] avg px, [28:32] volume.
            try:
                ltp_raw = struct.unpack(">i", binary[16:20])[0]
                vol = struct.unpack(">i", binary[28:32])[0]
            except struct.error:
                return None
            if ltp_raw <= 0 or ltp_raw > 10**8:
                return None
            return ltp_raw / 100.0, vol

        def on_data(_ws, message):
            if isinstance(message, (bytes, bytearray)):
                parsed = parse_tick(bytes(message))
                if not parsed:
                    return
                ltp, vol = parsed
                on_tick(Quote(symbol=token_map.get("?", "?"), ltp=ltp, volume=vol,
                              ts=datetime.now(timezone.utc)))

        def on_open(_ws):
            log.info("SmartAPI ws open for %s", list(symbols))

        def on_error(_ws, err):
            log.warning("SmartAPI ws error: %s", err)

        sws.on_open = on_open
        sws.on_data = on_data
        sws.on_error = on_error
        threading.Thread(target=sws.connect, daemon=True).start()
        if stop_event is not None:
            stop_event.wait()

    def _auth_token(self) -> str:
        jwt = getattr(self._sc, "_SmartConnect__jwt_token", None) or getattr(self._sc, "jwt_token", None)
        if not jwt:
            # re-login to refresh
            self._sc = None
            sc = self._connect()
            jwt = getattr(sc, "_SmartConnect__jwt_token", None) or getattr(sc, "jwt_token", "")
        return jwt or ""


def _new_event():
    import threading

    return threading.Event()


class SyntheticProvider:
    """Deterministic pseudo-market for offline tests/demo. NEVER used when a
    real provider is available; DataClient surfaces provider name in feed status."""

    name = "synthetic"
    supports_native_ws = False

    BASE_PRICES = {
        "RELIANCE": 2900.0, "TCS": 3900.0, "HDFCBANK": 1650.0, "INFY": 1550.0,
        "ICICIBANK": 1150.0, "SBIN": 780.0, "ITC": 450.0, "LT": 3600.0,
        "AXISBANK": 1150.0, "BHARTIARTL": 1400.0, "KOTAKBANK": 1800.0, "TATAMOTORS": 950.0,
        "^NSEI": 24000.0, "^INDIAVIX": 13.5,
    }

    def _seed(self, symbol: str) -> int:
        return abs(hash(symbol)) % (2**32)

    def fetch_history(self, symbol: str, interval: str = "day",
                      start: datetime | None = None, end: datetime | None = None) -> pd.DataFrame:
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=400))
        if interval == "day":
            steps_per_day, step_delta = 1, timedelta(days=1)
            n_days = max(int((end - start).days) + 1, 2)
        else:
            steps_per_day, step_delta = 24, timedelta(hours=1)
            # keep intraday histories small (synthetic is for tests/demo only)
            n_days = min(max(int((end - start).days) + 1, 2), 30)
        rng = np.random.default_rng(self._seed(symbol))
        base = self.BASE_PRICES.get(symbol.upper(), 1000.0)
        n = max(n_days * steps_per_day, 10)
        # Scale GBM params per step so paths stay sane at any interval.
        per_step_std = 0.013 / np.sqrt(steps_per_day)
        per_step_drift = 0.0004 / steps_per_day
        rets = rng.normal(per_step_drift, per_step_std, size=n)
        path = base * np.exp(np.cumsum(rets))
        idx = pd.date_range(end=end, periods=n, freq=step_delta, tz="UTC")
        spread = np.abs(rng.normal(0.004, 0.002, size=n))
        high = path * (1 + spread)
        low = path * (1 - spread)
        opn = low + (high - low) * rng.random(n)
        vol = rng.integers(500_000, 5_000_000, size=n).astype(float)
        df = pd.DataFrame({"open": opn, "high": high, "low": low, "close": path, "volume": vol}, index=idx)
        return _standardize(df)

    def fetch_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        now = datetime.now(timezone.utc)
        out = {}
        for s in symbols:
            hist = self.fetch_history(s, "1minute", end=now)
            if hist.empty:
                continue
            ltp = float(hist["close"].iloc[-1])
            day_rows = hist[hist.index >= pd.Timestamp(now.date(), tz="UTC")]
            prev_close = float(hist["close"].iloc[-2]) if len(hist) > 1 else None
            out[s] = Quote(
                symbol=s, ltp=ltp, ts=now,
                change_pct=((ltp - prev_close) / prev_close * 100) if prev_close else None,
                day_open=float(day_rows["open"].iloc[0]) if len(day_rows) else None,
                day_high=float(day_rows["high"].max()) if len(day_rows) else None,
                day_low=float(day_rows["low"].min()) if len(day_rows) else None,
                prev_close=prev_close, volume=int(hist["volume"].iloc[-24:].sum()),
            )
        return out

    def open_stream(self, symbols, on_tick, poll_seconds: int = 2, stop_event=None):
        import threading

        stop = stop_event or threading.Event()
        rng = np.random.default_rng(self._seed("stream"))
        prices = {s: self.fetch_history(s, "1minute")["close"].iloc[-1] for s in symbols}
        while not stop.is_set():
            for s in symbols:
                prices[s] = float(prices[s] * np.exp(rng.normal(0, 0.0005)))
                on_tick(Quote(symbol=s, ltp=prices[s], volume=1000, ts=datetime.now(timezone.utc)))
            stop.wait(poll_seconds)


def make_provider(preferred: str | None = None):
    """Choose the best available provider.

    Order: smartapi (if credentials configured) -> yahoo -> synthetic.
    `preferred` forces a specific one (used by tests).
    """
    preferred = preferred or os.environ.get("DATA_PROVIDER")
    if preferred == "synthetic":
        return SyntheticProvider()
    if preferred == "smartapi" or preferred is None:
        try:
            prov = SmartAPIProvider()
            if prov.configured:
                return prov
            if preferred == "smartapi":
                raise ProviderError("DATA_PROVIDER=smartapi but credentials incomplete")
        except Exception as exc:
            if preferred == "smartapi":
                raise
            log.info("SmartAPI not usable (%s); trying yahoo", exc)
    if preferred in (None, "yahoo"):
        try:
            return YFinanceProvider()
        except Exception as exc:
            if preferred == "yahoo":
                raise
            log.info("yahoo unavailable (%s); falling back to synthetic", exc)
    return SyntheticProvider()


