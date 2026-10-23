"""Shared types used across subsystems."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class Quote:
    symbol: str
    ltp: float                 # last traded price
    ts: datetime               # tz-aware UTC
    change_pct: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    prev_close: float | None = None
    volume: int | None = None


@dataclass(frozen=True)
class Tick:
    symbol: str
    ltp: float
    volume: int
    ts: datetime


@dataclass
class FeedStatus:
    """Status of the live market data feed for the whole client."""
    status: str = "disconnected"       # live | delayed | stale | disconnected
    last_tick_ts: Optional[datetime] = None
    symbols_streaming: tuple = ()
    provider: str = "none"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "last_tick_ts": self.last_tick_ts.isoformat() if self.last_tick_ts else None,
            "symbols_streaming": list(self.symbols_streaming),
            "provider": self.provider,
        }
