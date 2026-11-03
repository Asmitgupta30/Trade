r"""One governed challenger cycle from the CLI (also the weekly scheduled path).

    python scripts/retrain_challenger.py [--snapshot PATH] [--hpo-trials 12]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import common.procinfo as procinfo


def main() -> int:
    import pandas as pd

    from common.config import load_risk_config
    from models.challenger import run_challenger_cycle

    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot",
                    default=str(ROOT / "var" / "cache" / "features_snapshot.parquet"))
    ap.add_argument("--hpo-trials", type=int, default=12)
    ap.add_argument("--no-hpo", action="store_true")
    args = ap.parse_args()

    snap_path = Path(args.snapshot)
    if not snap_path.exists():
        deep_snap = ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
        if deep_snap.exists():
            snap_path = deep_snap
        else:
            print(f"ERROR: Snapshot file not found at {args.snapshot}. Run scripts/build_features_snapshot.py first.")
            return 1

    if not procinfo.acquire_lock("challenger_retrain"):
        print("REFUSING: a challenger retrain is already running.")
        return 2

    cfg = load_risk_config()
    dataset = pd.read_parquet(snap_path)
    procinfo.update_status(
        running={**_current_running(),
                 "challenger_retrain": {"since": procinfo._now_iso(),
                                        "snapshot": str(snap_path)}},
        notes=f"challenger cycle running on {snap_path.name}")
    try:
        decision = run_challenger_cycle(
            dataset, cfg, use_hpo=not args.no_hpo,
            n_hpo_trials=args.hpo_trials)
    finally:
        procinfo.release_lock("challenger_retrain")
        procinfo.update_status(running=_current_running(),
                                notes="challenger cycle finished")
    print(decision.to_json())
    return 0


def _current_running() -> dict:
    from common.procinfo import STATUS_DIR, pid_alive
    running = {}
    for f in STATUS_DIR.glob("*.json"):
        if f.name == "system_status.json":
            continue
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(info, dict) and info.get("pid") and pid_alive(info["pid"]):
            running[info.get("name", f.stem)] = {"pid": info["pid"],
                                                 "since": info.get("started_at")}
    return running


if __name__ == "__main__":
    raise SystemExit(main())
