"""Run walk-forward validation on the real feature snapshot.

Parameters come from risk_config.yaml wherever applicable:
  * positive_threshold  = intraday.cost_awareness.estimated_round_trip_cost_pct
  * confidence_gate     = intraday.confidence_gating.min_confidence_to_suggest_buy
  * max_picks_per_day   = intraday.max_positions_per_day
  * round_trip_cost_pct = same as threshold (cost-adjusted OOS returns)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from common.config import load_risk_config
from models.walkforward import WalkForwardReport, run_walk_forward

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("walkforward")


def main() -> int:
    cfg = load_risk_config()
    snap = ROOT / "var" / "cache" / "features_snapshot.parquet"
    if not snap.exists():
        log.error("no feature snapshot at %s — run scripts/build_features_snapshot.py", snap)
        return 1
    dataset = pd.read_parquet(snap)
    log.info("dataset: %d rows, %d symbols, %d features",
             len(dataset), dataset["symbol"].nunique(),
             dataset.select_dtypes("number").shape[1] - 1)

    cost_pct = cfg.intraday.round_trip_cost_pct / 100.0
    report: WalkForwardReport = run_walk_forward(
        dataset,
        positive_threshold=cost_pct,
        confidence_gate=cfg.intraday.min_confidence_buy,
        max_picks_per_day=cfg.intraday.max_positions_per_day,
        round_trip_cost_pct=cfg.intraday.round_trip_cost_pct,
        n_folds=5,
        test_days=60,
    )
    out = report.save(ROOT / "var" / "reports" / "walkforward_report.json")

    agg = report.aggregate
    print("\n================ WALK-FORWARD OUT-OF-SAMPLE SUMMARY ================")
    print(f"OOS days tested          : {agg['oos_days']}")
    print(f"Total OOS suggestions    : {agg['total_oos_suggestions']}")
    print(f"Pooled hit rate          : {agg['pooled_hit_rate']}")
    print(f"Up-precision when picked : {agg['up_precision_when_picked']}")
    print(f"Strategy Sharpe (net)    : {agg['strategy_sharpe_net_of_cost']}")
    print(f"Baseline Sharpe (Nifty~) : {agg['baseline_sharpe_eqweight_nifty_proxy']}")
    print(f"Strategy max DD %        : {agg['strategy_max_dd_pct']}")
    print(f"Beats buy&hold (riskadj) : {agg['beats_buy_hold_baseline_risk_adjusted']}")
    print(f"Train-vs-OOS hit gap     : {agg['mean_train_minus_oos_hit_rate_gap']:.2%}")
    for f in report.folds:
        print(f"  fold {f.fold}: picks={f.n_suggestions_oos:>4} hit={f.oos_hit_rate:.2f} "
              f"stratSharpe={f.strategy_sharpe:>6.2f} baseSharpe={f.baseline_sharpe:>5.2f} "
              f"maxDD%={f.strategy_max_dd_pct:>5.2f}")
    for n in report.honest_notes:
        print(f"\nNOTE: {n}")
    print(f"\nreport saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
