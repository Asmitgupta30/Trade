r"""Historical-regime OOS probes: re-run the SAME walk-forward with the
dataset cut at earlier year-ends, so the OOS windows land on specific regimes:
    cut 2020-12-31 -> OOS ~= COVID crash + recovery (2020)
    cut 2022-12-31 -> OOS ~= 2022 selloff / rate-hike regime

These are DIAGNOSTIC probes (same methodology, earlier anchors); they are
appended to the deep validation report.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from common.config import load_risk_config
from models.bootstrap import bootstrap_sharpe_ci
from models.walkforward import run_walk_forward

REPORT = ROOT / "var" / "reports" / "full_validation_report_deep.md"


def probe(dataset: pd.DataFrame, cut: str, label: str,
          n_folds: int = 5, test_days: int = 60) -> list[str]:
    cfg = load_risk_config()
    cost_pct = cfg.intraday.round_trip_cost_pct
    sub = dataset[dataset.index < pd.Timestamp(cut, tz="UTC")]
    rep = run_walk_forward(
        sub, positive_threshold=cost_pct / 100.0,
        confidence_gate=cfg.intraday.min_confidence_buy,
        max_picks_per_day=cfg.intraday.max_positions_per_day,
        round_trip_cost_pct=cost_pct, n_folds=n_folds, test_days=test_days,
        collect_trades=True, collect_scored=True,
    )
    agg = rep.aggregate
    ci = bootstrap_sharpe_ci(rep.pooled_strategy_daily, n_boot=1000,
                             block_len=10, ci=0.90, seed=int(cut[:4]))
    pl0 = ci.get("p_leq_0")
    pl0_s = f"{pl0:.0%}" if pl0 is not None else "n/a"
    lo, hi = ci.get("ci_low"), ci.get("ci_high")
    out = [
        f"\n### Probe: {label} (OOS {rep.folds[0].test_start} .. "
        f"{rep.folds[-1].test_end})",
        f"- picks={agg['total_oos_suggestions']} pooledHit={agg['pooled_hit_rate']} "
        f"netSharpe={agg['strategy_sharpe_net_of_cost']} "
        f"[CI {lo}..{hi}] P(<=0)={pl0_s}",
        f"- baseline(eqwt) Sharpe={agg['baseline_sharpe_eqweight_nifty_proxy']} "
        f"train-gap={agg['mean_train_minus_oos_hit_rate_gap']:.1%}",
    ]
    # gate sweep on cached fold predictions for this regime
    from models.challenger import evaluate_gate_variants
    table, best = evaluate_gate_variants(
        rep.scored_folds, config_gate=cfg.intraday.min_confidence_buy,
        max_picks_per_day=cfg.intraday.max_positions_per_day,
        round_trip_cost_pct=cost_pct,
        positive_threshold=cost_pct / 100.0)
    out.append("- gates: " + ", ".join(
        f"{g}: S={m['sharpe']}/hit={m['hit_rate']}/n={m['picks']}"
        for g, m in sorted(table.items()) if g != "_best"))
    return out


def main() -> int:
    lines = []
    dataset = pd.read_parquet(ROOT / "var" / "cache" / "features_snapshot_deep.parquet")
    lines.append("\n## 4b. Historical-regime probes (anchored OOS windows)")
    lines.append("\nSame methodology, dataset cut at earlier year-ends so the "
                 "OOS windows land inside named regimes:")
    lines += probe(dataset, "2020-12-31", "COVID crash & recovery",
                   n_folds=3, test_days=45)
    lines += probe(dataset, "2022-12-31", "2022 selloff / rate-hike regime")

    with open(REPORT, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
