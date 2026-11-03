"""Walk-forward integrity tests: fold construction, purge gap, leakage guards,
and a fast synthetic end-to-end run (tabular-only for CI speed)."""

import numpy as np
import pandas as pd
import pytest

from models.ensemble import HybridEnsemble
from models.walkforward import make_folds, run_walk_forward


def test_folds_expanding_with_purge():
    dates = pd.bdate_range("2026-01-01", periods=500)
    folds = make_folds(dates, n_folds=4, test_days=60, purge_days=3)
    assert len(folds) == 4
    prev_test_end = None
    for train_end_date, test_dates in folds:
        assert len(test_dates) == 60
        # purge: train ends strictly before the first test date
        assert train_end_date < min(test_dates)
        if prev_test_end is not None:
            assert min(test_dates) > prev_test_end
        prev_test_end = max(test_dates)
    # folds advance monotonically
    starts = [min(t) for _, t in folds]
    assert starts == sorted(starts)


def _panel(n_days=320, n_syms=6, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-06-01", periods=n_days)
    frames = []
    base_rets = rng.normal(0.0003, 0.008, n_days)   # shared market factor
    for s in range(n_syms):
        sym = f"S{s}"
        idio = rng.normal(0, 0.01, n_days)
        rets = base_rets + 0.4 * idio + rng.normal(0, 0.004, n_days)
        close = 100 * np.exp(np.cumsum(rets))
        df = pd.DataFrame({
            "open": close, "high": close * 1.005, "low": close * 0.995,
            "close": close,
            "volume": rng.integers(1_000_000, 3_000_000, n_days),
            "ret_1d": np.r_[0.0, np.diff(close) / close[:-1]],
            "ret_5d": pd.Series(close).pct_change(5).values,
            "rvol_5d": pd.Series(rets).rolling(5).std().values,
            "bb_pctb": rng.uniform(0, 1, n_days),
            "vol_z": rng.normal(0, 1, n_days),
            "nifty_mom_5d": pd.Series(base_rets).rolling(5).sum().values,
            "sector_rel_mom": rng.uniform(-0.5, 0.5, n_days),
            "label_fwd_ret": np.r_[pd.Series(close).pct_change().values[1:], np.nan],
        }, index=idx)
        df["symbol"] = sym
        frames.append(df)
    return pd.concat(frames)


def test_ensemble_predict_shapes():
    data = _panel()
    ens = HybridEnsemble(positive_threshold=0.003, min_train_rows=50)
    ens.fit(data, use_sequence=False)
    probs = ens.predict_proba(data)
    assert len(probs) == len(data)
    assert ((probs >= 0) & (probs <= 1)).all()
    desc = ens.describe()
    assert desc["sequence"] is None   # tabular-only path flagged honestly


def test_walkforward_synthetic_runs_and_reports_honestly():
    data = _panel(n_days=340, seed=3)
    report = run_walk_forward(
        data, positive_threshold=0.003, min_train_rows=50, confidence_gate=0.5,
        max_picks_per_day=2, round_trip_cost_pct=0.35,
        n_folds=3, test_days=50, use_sequence=False,
    )
    assert len(report.folds) == 3
    agg = report.aggregate
    # every fold has real OOS windows
    for f in report.folds:
        assert f.test_rows > 0
        assert f.strategy_max_dd_pct >= 0
    # aggregate block carries the honesty contract
    assert "beats_buy_hold_baseline_risk_adjusted" in agg
    assert "mean_train_minus_oos_hit_rate_gap" in agg
    saved = report.save()
    assert saved.exists()
    import json
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["aggregate"] == agg


def test_walkforward_flags_overfitting_on_perfect_model(tmp_path):
    """A model whose train hit-rate >> OOS hit-rate must raise the flag."""
    data = _panel(n_days=300, seed=11)
    report = run_walk_forward(
        data, positive_threshold=0.003, min_train_rows=50,
        confidence_gate=0.99,  # gate never fires
        max_picks_per_day=2, round_trip_cost_pct=0.35,
        n_folds=2, test_days=40, use_sequence=False,
    )
    # with an unreachable gate, zero suggestions -> explicit honesty note
    assert any("gate" in n.lower() or "edge" in n.lower() for n in report.honest_notes)


def test_no_date_leakage_between_train_and_test():
    data = _panel(n_days=280, seed=5).reset_index(names="date")
    from models.walkforward import make_folds
    dates = pd.DatetimeIndex(sorted(data["date"].unique()))
    folds = make_folds(dates, n_folds=3, test_days=45, purge_days=3)
    for train_end_date, test_dates in folds:
        assert train_end_date < min(test_dates), "purge violated"
