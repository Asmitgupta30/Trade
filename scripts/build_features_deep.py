"""Build the DEEP feature snapshot from reconciled bhavcopy data.

Input : var/cache/adjusted_bhav/{SYM}.parquet   (from reconcile_bhavcopy.py)
Output: var/cache/features_snapshot_deep.parquet

Same point-in-time pipeline as Stage 1; only the price source differs
(exchange raw + frozen adjustment factors instead of provider-adjusted feeds).
Regime context uses an equal-weight universe index (no ^NSEI depth needed).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from common.config import load_risk_config
from features.pipeline import build_dataset
from features.universe import load_universe_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("features_deep")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=1,
                    help="forward-return label horizon in trading days "
                         "(1=intraday default, 21=monthly)")
    args = ap.parse_args()
    load_risk_config()
    adj_dir = ROOT / "var" / "cache" / "adjusted_bhav"
    if not adj_dir.exists():
        log.error("run scripts/reconcile_bhavcopy.py first")
        return 1

    universe = load_universe_symbols()
    history, sectors = {}, {}
    t0 = time.time()
    for sym in sorted(universe):
        p = adj_dir / f"{sym}.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        df.index = pd.DatetimeIndex(df.index).tz_convert("UTC") \
            if df.index.tz is not None else pd.DatetimeIndex(df.index).tz_localize("UTC")
        frame = df[["open", "high", "low", "close", "volume"]].astype(float) \
            .dropna(subset=["close"])
        # adjusted prices must be strictly positive; guard against factor glitches
        frame = frame[frame["close"] > 0]
        if len(frame) >= 250:
            history[sym] = frame
            sectors[sym] = universe.get(sym, "Other")
    log.info("loaded %d symbols with >=250 adjusted bars (%.1fs)",
             len(history), time.time() - t0)
    if not history:
        log.error("no usable symbols")
        return 1

    log.info("building PIT dataset (horizon=%dd, this may take a minute)...",
             args.horizon)
    dataset = build_dataset(history, sectors, nifty_hist=None,
                            vix_series=None, horizon=args.horizon)
    suffix = "" if args.horizon == 1 else f"_{args.horizon}d"
    out = ROOT / "var" / "cache" / f"features_snapshot_deep{suffix}.parquet"
    tmp = out.with_suffix(".tmp")          # atomic replace: readers never
    dataset.to_parquet(tmp)                # see a half-written snapshot
    import os
    os.replace(tmp, out)
    span = (dataset.index.max() - dataset.index.min()).days / 365.25
    log.info("saved %s: %d rows, %d symbols, span %.1f years",
             out, len(dataset), dataset["symbol"].nunique(), span)

    # quick sanity: label distribution across years (regime coverage proof)
    labelled = dataset.dropna(subset=["label_fwd_ret"])
    yearly = labelled.groupby(labelled.index.year)["label_fwd_ret"] \
        .agg(["count", "mean"])
    print(yearly.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
