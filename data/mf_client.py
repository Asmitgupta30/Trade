"""MFClient — AMFI/mfapi.in NAV data for the mutual-fund sleeve.

Read-only by design: this system only *suggests* MF actions; there is no
order-placement path anywhere (see risk_config hard_locks). Kept strictly
separate from the intraday equity pipeline because NAVs update end-of-day.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

AMFI_NAVALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
MFAPI_BASE = "https://api.mfapi.in/mf"

CATEGORY_KEYWORDS = {
    "large_cap": ("large cap", "largecap", "blue chip", "bluechip", "top 100", "nifty 100"),
    "mid_cap": ("mid cap", "midcap", "emerging", "mid small", "flexi"),  # flexi approximated to mid tilt
    "debt": ("debt", "bond", "gilt", "liquid", "money", "corporate bond", "short duration",
             "banking and psu", "dynamic bond", "income", "arbitrage"),
    "gold_or_other": ("gold", "silver", "commodity", "international", "fof", "fund of fund"),
}


class MFApiError(RuntimeError):
    pass


def classify_scheme(scheme_name: str) -> str:
    """Rough category classification from scheme name (AMFI doesn't expose a
    clean category field on NAVAll.txt; mfapi metadata helps when present)."""
    name = scheme_name.lower()
    # debt first — many equity names contain 'income' etc. rarely, but debt names are distinctive
    for kw in CATEGORY_KEYWORDS["gold_or_other"]:
        if kw in name:
            return "gold_or_other"
    for kw in CATEGORY_KEYWORDS["debt"]:
        if kw in name:
            return "debt"
    for kw in CATEGORY_KEYWORDS["large_cap"]:
        if kw in name:
            return "large_cap"
    for kw in CATEGORY_KEYWORDS["mid_cap"]:
        if kw in name:
            return "mid_cap"
    return "other"


class MFClient:
    def __init__(self, cache_root: Path | None = None):
        base = cache_root or Path(__file__).resolve().parent.parent
        self.cache_dir = Path(base) / "var" / "cache" / "mf"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- latest NAVs (AMFI) ----------------
    def latest_navs(self, max_age_hours: int = 20) -> pd.DataFrame:
        """All schemes: columns [scheme_code, isin, scheme_name, nav, nav_date]."""
        path = self.cache_dir / "amfi_navall.json"
        fresh = path.exists() and (datetime.now().timestamp() - path.stat().st_mtime) < max_age_hours * 3600
        rows = None
        if fresh:
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                rows = None
        if rows is None:
            resp = requests.get(AMFI_NAVALL_URL, timeout=60)
            resp.raise_for_status()
            rows = []
            for line in resp.text.splitlines():
                parts = [p.strip() for p in line.split(";")]
                if not parts or not parts[0].isdigit():
                    continue  # section headers / blanks / fund-house names
                if len(parts) == 8:
                    # current AMFI layout: code;ISIN;ISIN;name;plan;option;nav;date
                    scheme_code, _i1, _i2, name, _plan, _opt, nav, nav_date = parts
                elif len(parts) in (5, 6):
                    # legacy layout: code;ISIN;ISIN;name;nav;date
                    scheme_code, _i1, _i2, name, nav, nav_date = parts[:6]
                else:
                    continue
                try:
                    nav_val = float(nav.replace(",", ""))
                except ValueError:
                    continue
                rows.append({
                    "scheme_code": int(scheme_code), "scheme_name": name,
                    "nav": nav_val, "nav_date": nav_date,
                })
            path.write_text(json.dumps(rows), encoding="utf-8")
        df = pd.DataFrame(rows)
        df["category"] = df["scheme_name"].map(classify_scheme)
        return df

    def search(self, query: str) -> list[dict]:
        q = query.lower()
        df = self.latest_navs()
        hits = df[df["scheme_name"].str.lower().str.contains(q, na=False)]
        return hits.head(20)[["scheme_code", "scheme_name", "category"]].to_dict("records")

    # ---------------- history (mfapi.in) ----------------
    def get_nav_history(self, scheme_code: int, years: float = 3.0) -> pd.DataFrame:
        """Daily NAV history indexed by date ascending."""
        path = self.cache_dir / f"navhist_{scheme_code}.json"
        max_age = timedelta(hours=24)
        payload = None
        if path.exists() and (datetime.now().timestamp() - path.stat().st_mtime) < max_age.total_seconds():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                payload = None
        if payload is None:
            resp = requests.get(f"{MFAPI_BASE}/{scheme_code}", timeout=60)
            resp.raise_for_status()
            payload = resp.json()
            path.write_text(json.dumps(payload), encoding="utf-8")
        records = payload.get("data") or []
        if not records:
            raise MFApiError(f"no NAV history for scheme {scheme_code}")
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y")
        df["nav"] = df["nav"].astype(float)
        df = df.set_index("date").sort_index()
        cutoff = pd.Timestamp.now("UTC").tz_localize(None) - timedelta(days=int(years * 365))
        return df[df.index >= cutoff]

    def returns_annualized(self, scheme_code: int) -> dict[str, float | None]:
        hist = self.get_nav_history(scheme_code)
        out: dict[str, float | None] = {"1y": None, "3y": None}
        if len(hist) < 30:
            return out
        last = hist["nav"].iloc[-1]
        for label, days in (("1y", 365), ("3y", 365 * 3)):
            target = hist.index[-1] - pd.Timedelta(days=days)
            sub = hist[hist.index <= target]
            if len(sub):
                base = sub["nav"].iloc[-1]
                years = (hist.index[-1] - sub.index[-1]).days / 365.0
                if years > 0.2 and base > 0:
                    out[label] = ((last / base) ** (1.0 / years)) - 1.0
        return out


@lru_cache(maxsize=1)
def default_mf_client() -> MFClient:
    return MFClient()
