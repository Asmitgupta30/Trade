r"""Daily pre-market data refresh (runs 08:15 IST, before the 09:00 check-in).

Chain:
  1. bhavcopy fetch for recent days (yesterday's EOD file publishes overnight;
     write-once store makes re-runs free)
  2. corporate-action factor reconciliation (incremental: oracle tail-refetch,
     frozen factor rows never rewritten)
  3. rebuild the DEEP feature snapshot so the 09:00 pre-market check-in scores
     on the freshest available adjusted prices
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("daily_refresh")


def _run(script: str, *args: str) -> bool:
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args],
                       capture_output=True, text=True, cwd=str(ROOT))
    tail = (r.stdout or "").strip().splitlines()[-3:]
    log.info("%s rc=%s | %s", script, r.returncode, " | ".join(tail))
    return r.returncode == 0


def main() -> int:
    import common.procinfo as procinfo

    if not procinfo.acquire_lock("daily_data_refresh"):
        print("REFUSING: daily refresh already running.")
        return 2
    procinfo.update_status(
        running={**_running(), "daily_data_refresh": {"since": procinfo._now_iso()}},
        notes="pre-market data refresh running")

    try:
        # 1) bhavcopy: last 5 calendar days (covers weekends/holidays; frozen
        #    days cost zero network)
        from common.config import load_risk_config
        from data.bar_store import ImmutableBarStore
        from data.bhavcopy import backfill
        from features.universe import load_universe_symbols

        cfg = load_risk_config()
        store = ImmutableBarStore(provider="nse_bhavcopy")
        symbols = set(load_universe_symbols())
        start = date.today() - timedelta(days=5)
        res = backfill(start=start, end=date.today(), store=store,
                       symbols=symbols, progress_every=10)
        log.info("bhavcopy tail: %d fetched / %d frozen-skipped / %d unavailable",
                 res["fetched"], res.get("frozen_skipped", 0), res["unavailable"])

        # 2) reconcile factors (incremental via frozen oracle store)
        ok_recon = _run("reconcile_bhavcopy.py")

        # 3) rebuild deep snapshot
        ok_snap = _run("build_features_deep.py")

        procinfo.update_status(
            progress={"daily_refresh_last_run": procinfo._now_iso(),
                      "bhavcopy_tail_fetched": res["fetched"]},
            notes="pre-market refresh "
                  f"{'OK' if (ok_recon and ok_snap) else 'PARTIAL/FAILED'}")
        return 0 if (ok_recon and ok_snap) else 1
    finally:
        procinfo.release_lock("daily_data_refresh")


def _running() -> dict:
    from common.procinfo import STATUS_DIR, pid_alive

    running = {}
    skip = {"system_status.json", "forward_holdout_registry.json",
            "universe_coverage.json"}
    for f in STATUS_DIR.glob("*.json"):
        if f.name in skip:
            continue
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        if info.get("pid") and pid_alive(info["pid"]):
            running[info["name"]] = {"pid": info["pid"],
                                     "since": info.get("started_at")}
    return running


if __name__ == "__main__":
    raise SystemExit(main())
