"""Local data cache (parquet) so dropped connections don't lose history.

Layout: var/cache/ohlcv/{provider}/{symbol}_{interval}.parquet
Writes are atomic (tmp file + replace). Reads return an empty frame when
nothing is cached — callers decide whether that's fatal.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class OHLCVCache:
    def __init__(self, root: Path | None = None, provider: str = "default"):
        base = root or Path(__file__).resolve().parent.parent
        self.dir = Path(base) / "var" / "cache" / "ohlcv" / provider
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, interval: str) -> Path:
        safe = symbol.replace("/", "_").replace("\\", "_")
        return self.dir / f"{safe}_{interval}.parquet"

    def load(self, symbol: str, interval: str = "day") -> pd.DataFrame | None:
        path = self._path(symbol, interval)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if not isinstance(df.index, pd.DatetimeIndex):
                log.warning("cache for %s has non-datetime index; ignoring", symbol)
                return None
            return df.sort_index()
        except Exception as exc:
            log.warning("failed reading cache %s: %s", path.name, exc)
            return None

    def upsert(self, symbol: str, df: pd.DataFrame, interval: str = "day") -> pd.DataFrame:
        """Merge new rows over cached ones (new wins), persist atomically."""
        if df.empty:
            return self.load(symbol, interval) if self._path(symbol, interval).exists() else df
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("upsert requires a DatetimeIndex")
        existing = self.load(symbol, interval)
        merged = df if existing is None else (
            pd.concat([existing[~existing.index.isin(df.index)], df]).sort_index()
        )
        merged = merged[~merged.index.duplicated(keep="last")]
        tmp = self._path(symbol, interval).with_suffix(".tmp")
        merged.to_parquet(tmp)
        os.replace(tmp, self._path(symbol, interval))
        return merged
