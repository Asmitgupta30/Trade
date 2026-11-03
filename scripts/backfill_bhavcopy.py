"""Resumable NSE bhavcopy backfill into the write-once bar store.

    python scripts/backfill_bhavcopy.py [--start 2020-01-01] [--end 2026-08-22]

Safe to interrupt and re-run: every frozen date is skipped with zero network
calls. Throttled at >=0.7s/day + backoff inside fetch_day.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.config import load_risk_config
from data.bar_store import ImmutableBarStore
from data.bhavcopy import backfill
from features.universe import load_universe_symbols

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bhavcopy_backfill")


def main() -> int:
    import common.procinfo as procinfo

    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default=None, help="default: today")
    args = ap.parse_args()

    if not procinfo.acquire_lock("bhavcopy_backfill"):
        return 2
    procinfo.update_status(
        running={"bhavcopy_backfill": {"since": procinfo._now_iso()}},
        notes="bhavcopy backfill running")

    cfg = load_risk_config()
    store = ImmutableBarStore(provider="nse_bhavcopy")
    symbols = set(load_universe_symbols())
    log.info("backfill %s -> %s for %d universe symbols",
             args.start, args.end or "today", len(symbols))

    try:
        results = backfill(
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end) if args.end else date.today(),
            store=store,
            symbols=symbols,
            progress_every=25,
        )
    finally:
        procinfo.release_lock("bhavcopy_backfill")
    log.info("done: %d trading days processed, %d fetched, %d frozen-skipped, "
             "%d unavailable/holidays", results["days"], results["fetched"],
             results.get("frozen_skipped", 0), results["unavailable"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
