"""Symbol/instrument resolution for Angel One SmartAPI.

SmartAPI identifies tradable instruments by numeric `symboltoken` + `exchange`,
not by symbol string. We download the public scrip master once and cache it,
then resolve canonical symbols (e.g. "RELIANCE") -> (exchange, token).
"""

from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"


def _cache_path(root: Path) -> Path:
    return root / "var" / "cache" / "scrip_master.json"


def load_scrip_master(max_age_days: int = 3, cache_root: Path | None = None) -> list[dict]:
    """Return the raw scrip master rows, downloading/refreshing when stale."""
    root = cache_root or Path(__file__).resolve().parent.parent
    path = _cache_path(root)

    def _fresh() -> bool:
        if not path.exists():
            return False
        age_days = (datetime.now().timestamp() - path.stat().st_mtime) / 86400.0
        return age_days < max_age_days

    if not _fresh():
        resp = requests.get(SCRIP_MASTER_URL, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rows, fh)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class InstrumentResolver:
    """Resolve canonical NSE symbols to SmartAPI tokens using the scrip master."""

    def __init__(self, rows: list[dict] | None = None):
        self._rows = rows if rows is not None else []
        self._by_symbol: dict[tuple[str, str], dict] = {}

    @classmethod
    def online(cls) -> "InstrumentResolver":
        try:
            return cls(load_scrip_master())
        except Exception as exc:
            log.warning("scrip master download failed (%s); resolver offline", exc)
            return cls([])

    def build(self) -> "InstrumentResolver":
        for row in self._rows:
            exch = row.get("exch_seg", "").upper()
            sym = row.get("symbol", "")
            key = (sym, exch)
            # Prefer entries without series suffixes (-EQ preferred for cash market)
            prev = self._by_symbol.get(key)
            if prev is None or row.get("symbol", "").endswith("-EQ"):
                self._by_symbol[key] = row
        return self

    @property
    def available(self) -> bool:
        return bool(self._by_symbol)

    def resolve(self, symbol: str, exchange: str = "NSE") -> tuple[str, str]:
        """Returns (token, smartapi_tradingsymbol) or raises KeyError."""
        row = self._by_symbol.get((symbol.upper(), exchange.upper()))
        if row is None:
            # try EQ-suffixed name
            row = self._by_symbol.get((f"{symbol.upper()}-EQ", exchange.upper()))
        if row is None:
            raise KeyError(f"Unknown instrument {symbol} on {exchange}")
        return row["token"], row["symbol"]
