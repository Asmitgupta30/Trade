"""Train the hybrid ensemble on the latest feature snapshot and persist it.

Walk-forward honesty is documented separately by scripts/run_walkforward.py;
this trains the production inference model on ALL available history.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from common.config import load_risk_config
from models.ensemble import HybridEnsemble
from models.persist import save_ensemble

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("train")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot",
                    default=str(ROOT / "var" / "cache" / "features_snapshot.parquet"))
    args = ap.parse_args()
    cfg = load_risk_config()
    snap = Path(args.snapshot)
    if not snap.exists():
        deep_snap = ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
        if deep_snap.exists():
            log.info("Default snapshot not found; using deep snapshot: %s", deep_snap)
            snap = deep_snap
        else:
            log.info("Snapshot not found at %s. Automatically building fresh features snapshot...", snap)
            import subprocess
            snap.parent.mkdir(parents=True, exist_ok=True)
            res = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_features_snapshot.py")])
            if res.returncode != 0 or not snap.exists():
                log.error("Could not build snapshot automatically. Check network or run scripts/build_features_snapshot.py manually.")
                return 1
    dataset = pd.read_parquet(snap)
    cost_frac = cfg.intraday.round_trip_cost_pct / 100.0

    ens = HybridEnsemble(positive_threshold=cost_frac)
    ens.fit(dataset)          # sequence model auto-enabled when torch present
    save_ensemble(ens)

    desc = ens.describe()
    log.info("ensemble: %s", desc)
    imp = ens.tabular.feature_importance()
    log.info("top features: %s", dict(list(imp.items())[:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
