"""Ticker-lifecycle & universe-coverage checks (Section F).

Surfaces a visible, structured answer to "which validated symbols are actually
being tracked right now, and why is any of them missing" â€” so a shrinking
universe can never go unnoticed again.

Reasons are classified conservatively:
    active            â€” frozen bars exist within the last 10 trading days
    stale_no_data     â€” absent from recent bhavcopy (delisted / renamed /
                        suspended â€” needs manual review; we do not guess)
    known_rename      â€” symbol has an entry in RENAME_MAP pointing at its
                        successor (successor itself reported separately)

Output: var/status/universe_coverage.json + merged into system_status.json.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from data.bar_store import ImmutableBarStore

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
COVERAGE_PATH = ROOT / "var" / "status" / "universe_coverage.json"

# Documented renames observed during this project (ISIN-stable successors).
# Kept as explicit data rather than silent auto-mapping: a rename changes the
# instrument's history continuity and deserves human-visible handling.
RENAME_MAP = {
    "ZOMATO": "ETERNAL",   # Feb 2025 rebrand of the same listed entity
}
STALE_AFTER_DAYS = 14


def classify_universe(universe: dict[str, str],
                      raw_store: ImmutableBarStore,
                      today: pd.Timestamp | None = None) -> dict:
    today = today or pd.Timestamp.now("UTC")
    cutoff = today - pd.Timedelta(days=STALE_AFTER_DAYS)
    entries = []
    counts = {"active": 0, "stale_no_data": 0, "known_rename": 0}

    for sym, sector in sorted(universe.items()):
        try:
            df = raw_store.load(sym)
        except Exception:
            df = None
        last = None if df is None or df.empty else df.index.max()
        successor = RENAME_MAP.get(sym)
        if last is not None and last >= cutoff:
            status, reason = "active", ""
        elif successor:
            status, reason = "known_rename", (
                f"renamed to {successor} (same listed entity); track successor "
                "or update RENAME_MAP after reviewing history continuity")
        else:
            gap_days = ((today - last).days if last is not None else None)
            status, reason = "stale_no_data", (
                f"no frozen bars in the last {STALE_AFTER_DAYS} days"
                + (f" (last seen {last.date()})" if last is not None
                   else " (never seen)")
                + " â€” delisted, renamed, or suspended; manual review required")
        if status == "active":
            counts["active"] += 1   # counter: active = last frozen bar within 14d
        elif status == "known_rename":
            counts["known_rename"] += 1   # counter: known_rename = symbol in RENAME_MAP
        else:
            counts["stale_no_data"] += 1   # counter: stale_no_data = not active AND not a known rename
        entries.append({
            "symbol": sym, "sector": sector, "status": status,
            "last_frozen": str(last.date()) if last is not None else None,
            "reason": reason,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(entries),
        "counts": counts,
        "exceptions": [e for e in entries if e["status"] != "active"],
        "entries": entries,
    }


def refresh_coverage(universe: dict[str, str],
                     raw_store: ImmutableBarStore | None = None) -> dict:
    store = raw_store or ImmutableBarStore(provider="nse_bhavcopy")
    result = classify_universe(universe, store)
    COVERAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE_PATH.write_text(json.dumps(result, indent=2), encoding="utf-8")
    log.info("universe coverage: %d/%d active, %d exceptions",
             result["counts"]["active"], result["total"],
             len(result["exceptions"]))
    return result


def merge_into_system_status(result: dict) -> None:
    from common.procinfo import update_status

    update_status(universe_coverage={
        "active": result["counts"]["active"],
        "total": result["total"],
        "skipped_this_run": [
            {"symbol": e["symbol"], "reason": e["reason"]}
            for e in result["exceptions"]],
    })


def validated_symbols(snapshot_path: Path | None = None) -> list[str]:
    """Symbols present in the DEEP validated snapshot (the coverage target)."""
    p = snapshot_path or ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
    if not p.exists():
        return []
    df = pd.read_parquet(p)
    return sorted(df["symbol"].unique().tolist())

