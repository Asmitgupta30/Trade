"""Build a point-in-time feature snapshot over the liquid universe.

Used by Stage 2 training and by the advisor for today's live features.
    python scripts\build_features_snapshot.py [--max-symbols N]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from common.config import load_risk_config
from data.client import DataClient
from features.pipeline import build_dataset
from features.universe import build_universe, load_universe_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("features_snapshot")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-symbols", type=int, default=None,
                    help="cap universe size (default: all that pass the filter)")
    args = ap.parse_args()

    cfg = load_risk_config()
    client = DataClient()
    log.info("data provider: %s", client.provider_name)

    t0 = time.time()
    members = build_universe(client, cfg)
    if not members:
        log.error("no symbols passed the liquidity filter — check data feed")
        return 1
    if args.max_symbols:
        members = members[: args.max_symbols]
    log.info("universe: %d symbols in %.1fs", len(members), time.time() - t0)

    history: dict[str, pd.DataFrame] = {}
    sectors: dict[str, str] = {}
    syms = {m.symbol: m.sector for m in members}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(client.get_history, s, "day", 720): s for s in syms}
        done = 0
        for fut in as_completed(futs):
            sym = futs[fut]
            done += 1
            if done % 10 == 0:
                log.info("history %d/%d", done, len(futs))
            try:
                hist = fut.result()
                if len(hist) >= 120:
                    history[sym] = hist
                    sectors[sym] = syms[sym]
            except Exception as exc:
                log.warning("history failed %s: %s", sym, exc)

    log.info("building PIT dataset for %d symbols...", len(history))
    nifty = None
    try:
        nifty = client.get_history("^NSEI", "day", lookback_days=720)
        vix = client.get_history("^INDIAVIX", "day", lookback_days=200)["close"]
    except Exception as exc:
        log.warning("index/vix unavailable (%s); using internal proxy", exc)

    dataset = build_dataset(history, sectors, nifty_hist=nifty, vix_series=vix, horizon=1)
    out_path = ROOT / "var" / "cache" / "features_snapshot.parquet"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_parquet(out_path)
    labelled = dataset["label_fwd_ret"].notna().sum()
    log.info(
        "snapshot saved: %d rows, %d labelled, %d symbols -> %s",
        len(dataset), labelled, dataset["symbol"].nunique(), out_path,
    )
    print(dataset.groupby("symbol").size().describe().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

