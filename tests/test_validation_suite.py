"""Bootstrap CI and baseline correctness tests."""

import numpy as np
import pandas as pd
import pytest

from models.baselines import (
    buy_and_hold, cross_sectional_momentum, sma_crossover, summarize,
)
from models.bootstrap import (
    bootstrap_hit_rate_ci, bootstrap_sharpe_ci, wilson_interval,
)


# ---------------- bootstrap ----------------
def test_sharpe_ci_covers_true_value():
    rng = np.random.default_rng(3)
    rets = pd.Series(rng.normal(0.0008, 0.01, size=500))
    out = bootstrap_sharpe_ci(rets, n_boot=500, block_len=10, seed=5)
    true_sr = rets.mean() / rets.std(ddof=1) * np.sqrt(252)
    assert out["ci_low"] <= true_sr <= out["ci_high"]
    assert out["distinguishable_from_noise"] is True   # strong synthetic edge


def test_sharpe_ci_zero_mean_not_distinguishable():
    rng = np.random.default_rng(4)
    rets = pd.Series(rng.normal(0.0, 0.01, size=300))
    rets = rets - rets.mean()          # exact-zero sample edge -> CI must straddle 0
    out = bootstrap_sharpe_ci(rets, n_boot=500, seed=6)
    assert out["distinguishable_from_noise"] is False
    assert out["ci_low"] < 0 < out["ci_high"]
    assert 0.3 <= out["p_leq_0"] <= 0.7


def test_sharpe_ci_too_few_obs_is_honest():
    out = bootstrap_sharpe_ci(pd.Series([0.01] * 10))
    assert out["ci_low"] is None and "too few" in out["note"]


def test_wilson_interval_known_values():
    lo, hi = wilson_interval(60, 100, ci=0.95)
    assert lo == pytest.approx(0.502, abs=0.02)
    assert hi == pytest.approx(0.691, abs=0.02)
    # coin flip sample cannot exclude 50% at 95%
    lo2, hi2 = wilson_interval(52, 100)
    assert lo2 < 0.5 < hi2


def test_hit_rate_ci_flags_coin_flip():
    rng = np.random.default_rng(9)
    flips = rng.random(400) > 0.5
    out = bootstrap_hit_rate_ci(list(flips), seed=2)
    assert out["distinguishable_from_coin_flip"] is False


def test_hit_rate_ci_detects_real_edge():
    out = bootstrap_hit_rate_ci([True] * 70 + [False] * 30, seed=2)
    assert out["hit_rate"] == pytest.approx(0.70)
    assert out["wilson_low"] > 0.5
    assert out["distinguishable_from_coin_flip"] is True


def test_hit_rate_empty_is_honest():
    out = bootstrap_hit_rate_ci([])
    assert out["n"] == 0 and out["hit_rate"] is None


# ---------------- baselines ----------------
def _price_series(n=300, seed=1, trend=0.0005):
    idx = pd.bdate_range("2025-01-01", periods=n)
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(rets)), index=idx)


def test_sma_crossover_pit_and_costs():
    px = _price_series()
    rets = sma_crossover(px, fast=20, slow=50, cost_pct_per_switch=0.35)
    assert len(rets) == len(px)
    # position uses yesterday's signal -> cash until the slow MA has history
    assert (rets.iloc[:49] == 0).all()
    # reconstruct the position series and verify cost handling per switch type
    pos = (px.rolling(20).mean() > px.rolling(50).mean()).astype(float).shift(1).fillna(0)
    mkt = px.pct_change().fillna(0.0)
    switches = pos.diff().abs().fillna(0)
    for d in switches[switches > 0].index:
        if pos.loc[d] == 0:      # exit to cash: return is exactly -cost
            assert rets.loc[d] == pytest.approx(-0.0035, abs=1e-9)
        else:                    # entry: market return minus cost
            assert rets.loc[d] == pytest.approx(mkt.loc[d] - 0.0035, abs=1e-9)


def test_sma_crossover_no_same_day_lookahead():
    """Position used on day T may only reflect data through T-1."""
    px = _price_series(seed=5)
    r1 = sma_crossover(px, oos_dates=None)
    shocked = px.copy()
    shocked.iloc[200:] = shocked.iloc[200:] * 3     # shock from T=200 onward
    r2 = sma_crossover(shocked, oos_dates=None)
    # everything strictly before the shock is untouched
    pd.testing.assert_series_equal(r1.iloc[:200], r2.iloc[:200], check_exact=True)


def test_buy_and_hold_alignment():
    px = _price_series(n=120)
    oos = pd.DatetimeIndex(px.index[-30:])
    bh = buy_and_hold(px, oos)
    assert len(bh) == 30
    expected = px.pct_change().reindex(oos)
    pd.testing.assert_series_equal(bh.sort_index(), expected.sort_index(),
                                   check_names=False)


def test_xs_momentum_matches_manual_topk():
    from models.walkforward import make_folds  # reuse panel builder pattern
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2025-06-01", periods=120)
    rows = []
    for s in range(5):
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.012, 120)))
        rows.append(pd.DataFrame({
            "close": close,
            "ret_5d": pd.Series(close).pct_change(5).values,
            "label_fwd_ret": np.r_[pd.Series(close).pct_change().values[1:], np.nan],
            "symbol": f"S{s}",
        }, index=idx))
    data = pd.concat(rows)
    oos = pd.DatetimeIndex(idx[-40:])
    s_rets, meta = cross_sectional_momentum(
        data.dropna(subset=["label_fwd_ret"]), "ret_5d", oos, top_k=2)
    assert meta["picked"] > 0
    assert len(s_rets[s_rets != 0]) > 0
    # manual spot-check of one date
    d = sorted(set(oos).intersection(data.index))[5]
    day = data.loc[d].dropna(subset=["label_fwd_ret"])
    top2 = day.sort_values("ret_5d", ascending=False).head(2)
    manual = float((top2["label_fwd_ret"] - 0.0035).mean())
    assert s_rets.loc[d] == pytest.approx(manual, abs=1e-9)


def test_summarize_shape():
    rets = pd.Series([0.001, -0.002, 0.003] * 20)
    s = summarize("x", rets)
    assert {"baseline", "sharpe", "max_dd_pct", "total_return_pct", "days"} <= set(s)
