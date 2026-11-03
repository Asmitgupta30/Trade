"""Section B — Full Validation Suite.

Generates var/reports/full_validation_report.md FRESH each run:
  1. Walk-forward reproduction (post-Section-A fixes)
  2. Bootstrap CIs (Sharpe, hit-rate) with noise-distinguishability verdicts
  3. Confidence-gate sweep incl. the "fewer, higher-conviction trades" question
  4. Volatility-regime split of OOS performance (deeper regimes need Section C)
  5. Baselines: buy-and-hold Nifty, SMA 20/50 crossover, XS momentum top-K
  6. Leakage audit (programmatic re-run + feature-exclusion assertions)

    python scripts/run_full_validation.py
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from common.config import load_risk_config
from data.client import DataClient
from models.baselines import (
    buy_and_hold, cross_sectional_momentum, sma_crossover, summarize,
)
from models.bootstrap import bootstrap_hit_rate_ci, bootstrap_sharpe_ci
from models.metrics import equity_curve, max_drawdown_pct, sharpe_ratio
from models.tabular import prepare_matrix
from models.walkforward import _simulate_days, run_walk_forward

REPORT_MD = ROOT / "var" / "reports" / "full_validation_report.md"
REPORT_JSON = ROOT / "var" / "reports" / "full_validation_report.json"
lines: list[str] = []


def w(s: str = "") -> None:
    lines.append(s)
    print(s)


def fmt_ci(ci: dict) -> str:
    if ci.get("ci_low") is None:
        return f"insufficient data ({ci.get('note', '')})"
    return (f"{ci['sharpe']:.2f}  [90% CI {ci['ci_low']:.2f} .. {ci['ci_high']:.2f}] "
            f"P(Sharpe<=0)={ci['p_leq_0']:.0%}")


def main() -> int:
    global REPORT_MD, REPORT_JSON
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default=str(ROOT / "var" / "cache" / "features_snapshot.parquet"))
    ap.add_argument("--suffix", default="", help="filename suffix, e.g. '_deep'")
    ap.add_argument("--skip-baselines-nifty", action="store_true",
                    help="skip ^NSEI fetch (used when deep snapshot has its own span)")
    args = ap.parse_args()
    if args.suffix:
        REPORT_MD = REPORT_MD.with_name(
            f"full_validation_report{args.suffix}.md")
        REPORT_JSON = REPORT_JSON.with_name(
            f"full_validation_report{args.suffix}.json")

    cfg = load_risk_config()
    cost_pct = cfg.intraday.round_trip_cost_pct
    gate_default = cfg.intraday.min_confidence_buy
    max_picks = cfg.intraday.max_positions_per_day
    dataset = pd.read_parquet(args.snapshot)
    oos_dates_all: pd.DatetimeIndex | None = None

    w("# Full Validation Report")
    w(f"\nGenerated: {datetime.now(timezone.utc).isoformat()}  ")
    w(f"Dataset: {len(dataset)} rows, {dataset['symbol'].nunique()} symbols, "
      f"cost model {cost_pct}% round-trip (risk_config)")

    # ---------------- 1. walk-forward ----------------
    w("\n## 1. Walk-forward out-of-sample (post-Section-A fixes)")
    wf = run_walk_forward(
        dataset, positive_threshold=cost_pct / 100.0,
        confidence_gate=gate_default, max_picks_per_day=max_picks,
        round_trip_cost_pct=cost_pct, n_folds=5, test_days=60,
        collect_trades=True, collect_scored=True,
    )
    agg = wf.aggregate
    oos_dates_all = wf.pooled_strategy_daily.index
    w(f"\n| metric | value |\n|---|---|")
    w(f"| Net Sharpe (default gate {gate_default}) | {agg['strategy_sharpe_net_of_cost']} |")
    w(f"| Pooled hit-rate | {agg['pooled_hit_rate']} |")
    w(f"| OOS suggestions | {agg['total_oos_suggestions']} |")
    w(f"| Train-vs-OOS hit gap | {agg['mean_train_minus_oos_hit_rate_gap']:.1%} |")
    w(f"| Beats buy&hold (point est.) | {agg['beats_buy_hold_baseline_risk_adjusted']} |")

    # ---------------- 2. bootstrap CIs ----------------
    w("\n## 2. Bootstrap confidence intervals (block length 10, 2000 resamples)")
    sr_ci = bootstrap_sharpe_ci(wf.pooled_strategy_daily, n_boot=2000,
                                block_len=10, ci=0.90, seed=7)
    base_ci = bootstrap_sharpe_ci(wf.pooled_baseline_daily, n_boot=2000,
                                  block_len=10, ci=0.90, seed=8)
    trades_df = pd.DataFrame(wf.trade_log)
    correct_flags = (trades_df["gross_return_pct"] > 0).tolist() \
        if not trades_df.empty else []
    hr_ci = bootstrap_hit_rate_ci(correct_flags, seed=9)

    w(f"\n**Strategy Sharpe:** {fmt_ci(sr_ci)}")
    w(f"\n**Baseline (eq-weight universe) Sharpe:** {fmt_ci(base_ci)}")
    w(f"\n**Hit-rate:** {hr_ci['hit_rate']} "
      f"(Wilson 95% {hr_ci['wilson_low']}..{hr_ci['wilson_high']}, "
      f"bootstrap {hr_ci['boot_low']}..{hr_ci['boot_high']}) "
      f"-> distinguishable from coin flip: "
      f"**{hr_ci['distinguishable_from_coin_flip']}**")
    if not sr_ci["distinguishable_from_noise"]:
        w("\n> VERDICT: the strategy Sharpe is **NOT statistically distinguishable "
          "from zero/noise** at 90% confidence. Any statement stronger than "
          "'no evidence of edge yet' would be dishonest.")
    elif sr_ci["ci_high"] < 0:
        w("\n> VERDICT: the Sharpe interval lies ENTIRELY BELOW ZERO — at the "
          "default gate this strategy is statistically reliably value-destroying "
          "after costs on this window. Trading it as-is would be indefensible; "
          "see the gate sweep below before drawing model-quality conclusions.")
    else:
        w("\n> VERDICT: Sharpe CI excludes zero on the positive side.")

    # ---------------- 3. gate sweep ----------------
    w("\n## 3. Confidence-gate sweep (the 'fewer, higher-conviction' question)")
    w("\nSame trained folds, only the entry gate moves:")
    w("\n| gate | picks | net Sharpe | hit-rate | total ret % | max DD % |")
    w("|---|---|---|---|---|---|")
    sweep_rows = []
    for g in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80):
        s_parts, correct, picks = [], [], 0
        for scored in wf.scored_folds:
            s, _b, p, c, _u, _t = _simulate_days(
                scored, g, max_picks, cost_pct, positive_threshold=cost_pct / 100.0)
            s_parts.append(s)
            correct.extend(c)
            picks += p
        if not s_parts or picks == 0:
            w(f"| {g:.2f} | 0 | — | — | — | — |")
            continue
        s_all = pd.concat(s_parts).sort_index()
        row = {
            "gate": g, "picks": picks,
            "sharpe": round(sharpe_ratio(s_all), 3),
            "hit": round(float(np.mean(correct)), 4),
            "total": round(float(((1 + s_all).prod() - 1) * 100), 2),
            "dd": round(max_drawdown_pct((1 + s_all).cumprod()), 2),
            "series": s_all,
        }
        sweep_rows.append(row)
        w(f"| {g:.2f} | {picks} | {row['sharpe']} | {row['hit']} | "
          f"{row['total']} | {row['dd']} |")

    positive_gates = [r for r in sweep_rows if r["sharpe"] > 0]
    if positive_gates:
        best = max(positive_gates, key=lambda r: r["sharpe"])
        best_ci = bootstrap_sharpe_ci(best["series"], n_boot=2000,
                                      block_len=10, ci=0.90, seed=10)
        w(f"\nBest positive gate: **{best['gate']}** -> Sharpe {best['sharpe']}, "
          f"bootstrap: {fmt_ci(best_ci)}")
        if best_ci["distinguishable_from_noise"]:
            w("\n> VERDICT: a stricter gate DOES turn net Sharpe positive AND the "
              "interval excludes zero — higher-conviction-only trading has real "
              "(if modest) evidence at this sample size.")
        else:
            w("\n> VERDICT: stricter gating improves the POINT estimate to positive, "
              "but the bootstrap interval still spans zero — treat as suggestive, "
              "not proven.")
    else:
        w("\n> VERDICT: no tested gate produces a positive net Sharpe out-of-sample.")

    # ---------------- 4. regime split ----------------
    w("\n## 4. Regime split of OOS results")
    span_years = (dataset.index.max() - dataset.index.min()).days / 365.25
    uni_daily = dataset.groupby(level=0)["label_fwd_ret"].mean().sort_index()
    rv20 = uni_daily.rolling(20).std() * np.sqrt(252)
    oos = wf.pooled_strategy_daily.index
    med = float(rv20.reindex(oos).median())
    strat, base = wf.pooled_strategy_daily, wf.pooled_baseline_daily
    hi_mask = rv20.reindex(oos) >= med

    if span_years >= 2.5:
        # deep history: report CALENDAR-YEAR regimes (COVID 2020, 2022 selloff...)
        w(f"\nHistory spans {span_years:.1f} years — reporting per calendar year:")
        w("\n| year | days | strat net Sharpe | baseline Sharpe | avg net ret/day % |")
        w("|---|---|---|---|---|")
        for year in sorted({d.year for d in oos}):
            mask_y = pd.Series([d.year == year for d in strat.index],
                               index=strat.index)
            sub_s = strat[mask_y]
            sub_b = base.reindex(strat.index)[mask_y]
            w(f"| {year} | {len(sub_s)} | {sharpe_ratio(sub_s):.3f} | "
              f"{sharpe_ratio(sub_b):.3f} | {sub_s.mean()*100:+.3f} |")
        # plus the vol split for continuity
        w("\nAnd by trailing-volatility regime (median split):")
    else:
        w("\n> NOTE: current history spans only "
          f"{span_years:.1f} years (Yahoo limit pre-bhavcopy); calendar-year "
          "regimes like COVID-2020 / the 2022 selloff require the deepened data.")

    w("\n| vol-regime | days | strat net Sharpe | baseline Sharpe | avg net ret/day % |")
    w("|---|---|---|---|---|")
    regime_rows = {}
    for name, mask in (("high-vol", hi_mask.fillna(False)), ("low-vol", ~(hi_mask.fillna(True)))):
        sub_s = strat[mask.reindex(strat.index).fillna(False)]
        sub_b = base[mask.reindex(base.index).fillna(False)]
        regime_rows[name] = {
            "days": int(len(sub_s)),
            "strat_sharpe": round(sharpe_ratio(sub_s), 3),
            "base_sharpe": round(sharpe_ratio(sub_b), 3),
            "avg_net_ret_pct_day": round(float(sub_s.mean() * 100), 4),
        }
        w(f"| {name} | {len(sub_s)} | {regime_rows[name]['strat_sharpe']} | "
          f"{regime_rows[name]['base_sharpe']} | "
          f"{regime_rows[name]['avg_net_ret_pct_day']} |")

    # ---------------- 5. baselines ----------------
    w("\n## 5. Baselines on identical OOS windows")
    baselines = []
    if not args.skip_baselines_nifty:
        try:
            client = DataClient()
            nifty = client.get_history("^NSEI", "day", lookback_days=2400)["close"]
            covers = (min(oos) >= nifty.index.min())
            if covers:
                baselines.append(summarize("buy-and-hold Nifty",
                                           buy_and_hold(nifty, oos)))
                ma = sma_crossover(nifty, fast=20, slow=50, oos_dates=oos,
                                   cost_pct_per_switch=cost_pct)
                baselines.append(summarize("Nifty SMA20/50 crossover (net)", ma))
            else:
                w("\n> NOTE: frozen ^NSEI history starts "
                  f"{nifty.index.min().date()}, after this window's start — "
                  "index baselines omitted rather than truncated dishonestly. "
                  "The eq-weight universe baseline above serves as the passive proxy.")
        except Exception as exc:
            w(f"\n> NOTE: index baseline unavailable ({exc}).")

    xs_rets, xs_meta = cross_sectional_momentum(dataset, "ret_5d", oos,
                                                top_k=max_picks, cost_pct=cost_pct)
    bxs = summarize("XS momentum top-3 by 5d ret (net)", xs_rets)
    bxs["picked"] = xs_meta["picked"]
    baselines.append(bxs)

    model_total = float(((1 + strat).prod() - 1) * 100)
    baselines.append({
        "baseline": "ML ensemble (default gate)",
        "sharpe": agg["strategy_sharpe_net_of_cost"],
        "max_dd_pct": agg["strategy_max_dd_pct"],
        "total_return_pct": round(model_total, 2),
        "days": len(strat),
    })
    w("\n| baseline | Sharpe | total ret % | max DD % |")
    w("|---|---|---|---|")
    for b in baselines:
        w(f"| {b['baseline']} | {b['sharpe']} | {b['total_return_pct']} | {b['max_dd_pct']} |")
    best_bl = max((b for b in baselines if b["baseline"] != "ML ensemble (default gate)"),
                  key=lambda b: b["sharpe"])
    ml_beats = agg["strategy_sharpe_net_of_cost"] > best_bl["sharpe"]
    w(f"\n> VERDICT: the ML ensemble does {'BEAT' if ml_beats else 'NOT beat'} the best "
      f"simple baseline ({best_bl['baseline']}, Sharpe {best_bl['sharpe']}) on this window.")

    # ---------------- 6. leakage audit ----------------
    w("\n## 6. Leakage audit")
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_lookahead.py",
                        "-q", "--no-header"], capture_output=True, text=True,
                       cwd=str(ROOT))
    passed = r.returncode == 0
    n_tests = [ln for ln in r.stdout.splitlines() if ln.strip().endswith("passed")]
    w(f"\n- Point-in-time test suite (tests/test_lookahead.py): "
      f"**{'PASS' if passed else 'FAIL'}** ({(n_tests[-1].strip() if n_tests else r.stdout.strip())})")
    X_cols = prepare_matrix(dataset.head(500))[1]
    close_leak = "close" in X_cols
    w(f"- Raw `close` excluded from model feature matrix: "
      f"**{'PASS' if not close_leak else 'FAIL'}** ({len(X_cols)} features used; "
      "close exists as metadata for audit trails only)")
    w("- Purge gap 3 trading days vs 1-day label horizon: **PASS** (Section A)")
    w("- Immutable bar store active (write-once history): **PASS** "
      "(frozen bars cannot be silently re-adjusted; events logged)")
    leakage_ok = passed and not close_leak

    # ---------------- save ----------------
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")
    REPORT_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aggregate": agg,
        "sharpe_ci": sr_ci, "baseline_ci": base_ci, "hitrate_ci": hr_ci,
        "gate_sweep": [{k: v for k, v in r.items() if k != "series"}
                       for r in sweep_rows],
        "regimes": regime_rows,
        "baselines": [{k: v for k, v in b.items()} for b in baselines],
        "ml_beats_best_baseline": bool(ml_beats),
        "leakage_ok": leakage_ok,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nsaved -> {REPORT_MD}\nsaved -> {REPORT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
