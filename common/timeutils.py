"""IST market-time helpers. All internal timestamps are timezone-aware."""

from __future__ import annotations

from datetime import datetime, date, time as dtime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


class MarketPhase(str, Enum):
    PRE_MARKET_CHECK = "pre_market_check"
    OPEN = "open"
    MIDDAY_CHECK = "midday_check"
    PRE_CLOSE_CHECK = "pre_close_check"
    CLOSED = "closed"


def now_ist() -> datetime:
    return datetime.now(IST)


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def is_weekday(d: date) -> bool:
    return d.weekday() < 5


def is_trading_day(d: date | datetime | None = None, holidays: set[date] | None = None) -> bool:
    """Weekday check minus optional NSE holiday set (kept minimal/approximate)."""
    if d is None:
        d = now_ist().date()
    if isinstance(d, datetime):
        d = d.date()
    if not is_weekday(d):
        return False
    if holidays and d in holidays:
        return False
    return True


def market_phase(ts: datetime | None = None) -> MarketPhase:
    """Which scheduled phase of the trading day are we in (IST)."""
    ts = to_ist(ts) if ts is not None else now_ist()
    if not is_trading_day(ts):
        return MarketPhase.CLOSED
    t = ts.time()
    if t < MARKET_OPEN:
        return MarketPhase.PRE_MARKET_CHECK
    if t >= MARKET_CLOSE:
        return MarketPhase.CLOSED
    if t < dtime(12, 30):
        return MarketPhase.OPEN
    if t < dtime(15, 0):
        return MarketPhase.MIDDAY_CHECK
    return MarketPhase.PRE_CLOSE_CHECK


def minutes_until_close(ts: datetime | None = None) -> float:
    ts = to_ist(ts) if ts is not None else now_ist()
    close_dt = ts.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0)
    return (close_dt - ts).total_seconds() / 60.0


def previous_trading_day(d: date | None = None, holidays: set[date] | None = None) -> date:
    d = (d or now_ist().date())
    cand = d - timedelta(days=1)
    while not is_trading_day(cand, holidays):
        cand -= timedelta(days=1)
    return cand


def ist_date_str(ts: datetime | None = None) -> str:
    return (to_ist(ts) if ts else now_ist()).strftime("%Y-%m-%d")


def parse_hhmm(hhmm: str) -> dtime:
    h, m = hhmm.split(":")
    return dtime(int(h), int(m))
