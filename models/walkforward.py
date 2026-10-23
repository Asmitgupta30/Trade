"""Walk-forward validation — the ONLY accepted form of "backtested performance".

Expanding-window folds with a purge gap between train and test so that
1-day-forward labels can't leak across the boundary. Every fold reports
out-of-sample metrics and the train-vs-OOS gap; the aggregate report is
written to var/reports/walkforward_report.json.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path

import numpy as np
import pandas as pd

from models.ensemble import HybridEnsemble
from models.metrics import equity_curve, max_drawdown_pct, sharpe_ratio

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class FoldResult:
    fold: int
    train_rows: int
    test_rows: int
    train_end: str
    test_start: str
    test_end: str
    oos_hit_rate: float
    oos_precision_up: float | None
    strategy_sharpe: float
    baseline_sharpe: float
    strategy_max_dd_pct: float
    baseline_max_dd_pct: float
    n_suggestions_oos: int
    train_hit_rate_tabular: float | None = None
    train_hit_rate_sequence: float | None = None


@dataclass
class WalkForwardReport:
    folds: list[FoldResult] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)
    ensemble_desc: dict = field(default_factory=dict)
    honest_notes: list[str] = field(default_factory=list)
    trade_log: list[dict] = field(default_factory=list)
    pooled_strategy_daily: object = None   # pd.Series when collect_trades=True
    pooled_baseline_daily: object = None
    scored_folds: list = field(default_factory=list)  # per-fold frames w/ probas

    def to_dict(self, include_trades: bool = False) -> dict:
        d = {
            "folds": [asdict(f) for f in self.folds],
            "aggregate": self.aggregate,
            "ensemble": self.ensemble_desc,
            "notes": self.honest_notes,
        }
        if include_trades:
            d["trades"] = self.trade_log
        return d

    def save(self, path: Path | None = None) -> Path:
        p = path or (ROOT / "var" / "reports" / "walkforward_report.json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return p


def make_folds(unique_dates: pd.DatetimeIndex, n_folds: int = 5,
               test_days: int = 60, purge_days: int = 3):
    """Expanding-window walk-forward folds with purge gap.

    Panel data has many rows per date, so folds are DATE-based:
    returns [(train_end_date, test_dates)] where training uses every row with
    index <= train_end_date, and train_end_date is >= purge_days before the
    first test date (1-day-forward labels can't leak across the boundary).
    """
    dates = pd.DatetimeIndex(unique_dates)
    total = len(dates)
    if total < test_days * (n_folds + 1):
        raise ValueError(f"not enough dates for {n_folds} folds of {test_days}d: {total}")
    first_test_start = total - test_days * n_folds
    folds = []
    for k in range(n_folds):
        ts = first_test_start + k * test_days
        te = ts + test_days
        train_end_date = dates[ts - purge_days - 1]   # purge gap before test window
        folds.append((train_end_date, dates[ts:te]))
    return folds


def _daily_panel(dataset: pd.DataFrame) -> pd.DataFrame:
    """dataset rows must be unique per (date, symbol)."""
    return dataset


def _simulate_days(scored: pd.DataFrame, confidence_gate: float,
                   max_picks_per_day: int, round_trip_cost_pct: float,
                   positive_threshold: float | None = None):
    """Pick logic shared by the main walk-forward and gate sweeps.

    Returns (daily_strategy_rets, daily_baseline_rets, picks, correct_flags,
    up_precision, trade_rows). Trade rows carry per-pick audit detail.
    """
    thr = positive_threshold if positive_threshold is not None else 0.0
    daily_strat, daily_base = {}, {}
    picks = 0
    correct_flags, up_precision = [], []
    trade_rows = []
    for d, day in scored.groupby(level=0):
        candidates = day[day["proba"] >= confidence_gate].sort_values(
            "proba", ascending=False)
        chosen = candidates.head(max_picks_per_day)
        daily_base[d] = float(day["label_fwd_ret"].mean())   # eq-weight proxy
        if len(chosen):
            net = chosen["label_fwd_ret"] - round_trip_cost_pct / 100.0
            daily_strat[d] = float(net.mean())
            picks += len(chosen)
            realized_up = chosen["label_fwd_ret"] > 0
            correct_flags.extend(bool(v) for v in realized_up)
            predicted_up = chosen["label_fwd_ret"] > thr
            up_precision.extend(bool(v) for v in predicted_up)
            for sym, row in chosen.iterrows():
                trade_rows.append({
                    "symbol": row["symbol"], "sector": row.get("sector"),
                    "signal_ts_utc": str(pd.Timestamp(d)),
                    "confidence": round(float(row["proba"]), 4),
                    "close": float(row.get("close", float("nan"))),
                    "gross_return_pct": round(float(row["label_fwd_ret"]) * 100, 4),
                })
        else:
            daily_strat[d] = 0.0   # no edge found -> sit out (cost-aware default)
    return (pd.Series(daily_strat).sort_index(), pd.Series(daily_base).sort_index(),
            picks, correct_flags, up_precision, trade_rows)


def run_walk_forward(dataset: pd.DataFrame, positive_threshold: float,
                     min_train_rows: int = 500,
                     confidence_gate: float = 0.55, max_picks_per_day: int = 3,
                     round_trip_cost_pct: float = 0.35, n_folds: int = 5,
                     test_days: int = 60, use_sequence: bool | None = None,
                     verbose: bool = False,
                     collect_trades: bool = False,
                     collect_scored: bool = False,
                     lgbm_params: dict | None = None,
                     label: str = "") -> WalkForwardReport:
    report = WalkForwardReport()
    report.trade_log: list[dict] = []
    report.scored_folds: list[pd.DataFrame] = []
    dataset = dataset.sort_index().copy()
    dataset = dataset[dataset["label_fwd_ret"].notna()]

    all_dates = dataset.index.unique().sort_values()
    folds = make_folds(all_dates, n_folds=n_folds, test_days=test_days)
    _next_map = {all_dates[i]: (all_dates[i + 1] if i + 1 < len(all_dates) else None)
                 for i in range(len(all_dates))}

    all_strategy_rets, all_baseline_rets, all_correct, all_prec_flags = [], [], [], []

    for i, (train_end_date, test_dates) in enumerate(folds):
        train_df = dataset[dataset.index <= train_end_date]
        # guard against any date overlap after purging
        if len(train_df) == 0 or train_df.index.max() >= min(test_dates):
            raise RuntimeError(f"fold {i}: leakage! train end >= test start")

        ens = HybridEnsemble(positive_threshold=positive_threshold,
                             min_train_rows=min_train_rows)
        ens.fit(train_df, use_sequence=use_sequence, epochs=6,
                lgbm_params=lgbm_params)

        test_df = dataset[dataset.index.isin(test_dates)]
        probs = ens.predict_proba(test_df)
        scored = test_df.assign(proba=probs.values)
        if collect_scored:
            report.scored_folds.append(scored.copy())

        daily_strat, daily_base, picks, correct_flags, up_precision, trade_rows = \
            _simulate_days(scored, confidence_gate, max_picks_per_day,
                           round_trip_cost_pct,
                           positive_threshold=positive_threshold)
        if collect_trades:
            for row in trade_rows:
                d_ts = pd.Timestamp(row["signal_ts_utc"])
                entry = float(row["close"])
                gross = float(row["gross_return_pct"])
                exit_px = entry * (1.0 + gross / 100.0) if entry == entry \
                    else float("nan")
                report.trade_log.append({
                    "fold": i + 1,
                    **row,
                    "entry_price": round(entry, 4),
                    "exit_ts_utc": str(_next_map.get(d_ts) or d_ts),
                    "exit_price": round(exit_px, 4),
                    "cost_applied_pct": round_trip_cost_pct,
                    "net_return_pct": round(gross - round_trip_cost_pct, 4),
                })

        s_rets = pd.Series(daily_strat).sort_index()
        b_rets = pd.Series(daily_base).reindex(s_rets.index).fillna(0.0)

        fold_res = FoldResult(
            fold=i + 1,
            train_rows=len(train_df),
            test_rows=len(test_df),
            train_end=str(train_df.index.max().date()),
            test_start=str(min(test_dates).date()),
            test_end=str(max(test_dates).date()),
            oos_hit_rate=float(np.mean(correct_flags)) if correct_flags else float("nan"),
            oos_precision_up=(float(np.mean(up_precision)) if up_precision else None),
            strategy_sharpe=sharpe_ratio(s_rets),
            baseline_sharpe=sharpe_ratio(b_rets),
            strategy_max_dd_pct=max_drawdown_pct(equity_curve(s_rets)),
            baseline_max_dd_pct=max_drawdown_pct(equity_curve(b_rets)),
            n_suggestions_oos=picks,
            train_hit_rate_tabular=ens.tabular.train_hit_rate,
            train_hit_rate_sequence=(ens.seq.train_hit_rate if ens.seq_used else None),
        )
        report.folds.append(fold_res)
        log.info(
            "fold %d/%d OOS %s..%s picks=%d hit=%.2f stratSharpe=%.2f baseSharpe=%.2f",
            i + 1, len(folds), fold_res.test_start, fold_res.test_end, picks,
            fold_res.oos_hit_rate or float("nan"), fold_res.strategy_sharpe,
            fold_res.baseline_sharpe,
        )
        all_strategy_rets.append(s_rets)
        all_baseline_rets.append(b_rets)
        all_correct.extend(correct_flags)
        all_prec_flags.extend(up_precision)
        report.ensemble_desc = ens.describe()

    s_all = pd.concat(all_strategy_rets).sort_index()
    b_all = pd.concat(all_baseline_rets).sort_index()
    if collect_trades:
        # expose pooled OOS daily series so diagnostics can audit the math
        report.pooled_strategy_daily = s_all
        report.pooled_baseline_daily = b_all

    train_hr = [f.train_hit_rate_tabular for f in report.folds if f.train_hit_rate_tabular]
    oos_hr = [f.oos_hit_rate for f in report.folds]
    gap = float(np.nanmean(train_hr) - np.nanmean(oos_hr))

    beats_baseline = (
        sharpe_ratio(s_all) > sharpe_ratio(b_all)
        and len(s_all[s_all != 0]) > 10
    )

    report.aggregate = {
        "oos_days": len(s_all),
        "total_oos_suggestions": sum(f.n_suggestions_oos for f in report.folds),
        "pooled_hit_rate": round(float(np.mean(all_correct)), 4) if all_correct else None,
        "up_precision_when_picked": round(float(np.mean(all_prec_flags)), 4) if all_prec_flags else None,
        "strategy_sharpe_net_of_cost": round(sharpe_ratio(s_all), 3),
        "baseline_sharpe_eqweight_nifty_proxy": round(sharpe_ratio(b_all), 3),
        "strategy_max_dd_pct": round(max_drawdown_pct(equity_curve(s_all)), 2),
        "baseline_max_dd_pct": round(max_drawdown_pct(equity_curve(b_all)), 2),
        "beats_buy_hold_baseline_risk_adjusted": bool(beats_baseline),
        "mean_train_minus_oos_hit_rate_gap": round(gap, 4),
        "cost_model_pct_per_round_trip": round_trip_cost_pct,
        "confidence_gate_used": confidence_gate,
    }

    # honesty notes per risk_config model_governance
    notes = []
    if not beats_baseline:
        notes.append(
            "HONEST RESULT: model does NOT currently beat the buy-and-hold Nifty "
            "proxy on a risk-adjusted basis out-of-sample. Do not treat suggestions "
            "as an edge until this flips consistently across folds."
        )
    if gap > 0.15:
        notes.append(
            f"Overfitting watch: mean train-vs-OOS hit-rate gap is {gap:.2%} "
            "(>15%). Treat confidence scores skeptically; consider fewer features "
            "or stronger regularization."
        )
    if report.aggregate["total_oos_suggestions"] == 0:
        notes.append("Confidence gate never triggered OOS — gate may be set too high "
                     "for current model quality.")
    report.honest_notes = notes
    return report

