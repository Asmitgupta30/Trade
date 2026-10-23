"""Bootstrap confidence intervals — because a point estimate from a few
hundred observations is not evidence.

Sharpe: circular block bootstrap (preserves short-range autocorrelation).
Hit-rate: Wilson score interval (exact-ish for proportions) + percentile boot.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def bootstrap_sharpe_ci(returns: pd.Series | np.ndarray, n_boot: int = 2000,
                        block_len: int = 10, ci: float = 0.95,
                        seed: int = 7) -> dict:
    r = pd.Series(returns, dtype=float).dropna().to_numpy()
    n = len(r)
    if n < 20:
        return {"sharpe": float("nan"), "ci_low": None, "ci_high": None,
                "p_leq_0": None, "n": n,
                "note": "too few observations for a meaningful interval"}

    def _sr(x: np.ndarray) -> float:
        sd = x.std(ddof=1)
        return 0.0 if sd == 0 else float(x.mean() / sd * np.sqrt(252))

    rng = np.random.default_rng(seed)
    stats = np.empty(n_boot)
    n_blocks = int(math.ceil(n / block_len))
    starts_all = np.arange(n)
    for b in range(n_boot):
        starts = rng.choice(starts_all, size=n_blocks, replace=True)
        idx = (starts[:, None] + np.arange(block_len)[None, :]).ravel()[:n]
        # wrap-around circular indexing
        idx = idx % n
        stats[b] = _sr(r[idx])
    alpha = (1 - ci) / 2
    lo, hi = np.percentile(stats, [100 * alpha, 100 * (1 - alpha)])
    p_leq_0 = float((stats <= 0).mean())
    return {
        "sharpe": _sr(r),
        "ci_low": round(float(lo), 3),
        "ci_high": round(float(hi), 3),
        "p_leq_0": round(p_leq_0, 4),
        "n": n, "block_len": block_len, "n_boot": n_boot,
        "distinguishable_from_noise": bool(lo > 0 or hi < 0),
    }


def wilson_interval(successes: int, n: int, ci: float = 0.95) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    z = {0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}[round(ci, 2)]
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_hit_rate_ci(correct: list[bool] | np.ndarray, n_boot: int = 2000,
                          ci: float = 0.95, seed: int = 11) -> dict:
    arr = np.asarray(correct, dtype=bool)
    n = len(arr)
    if n == 0:
        return {"hit_rate": None, "wilson_low": None, "wilson_high": None,
                "boot_low": None, "boot_high": None, "n": 0,
                "note": "no outcomes yet"}
    k = int(arr.sum())
    w_lo, w_hi = wilson_interval(k, n, ci)
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    alpha = (1 - ci) / 2
    b_lo, b_hi = np.percentile(means, [100 * alpha, 100 * (1 - alpha)])
    return {
        "hit_rate": round(k / n, 4),
        "wilson_low": round(w_lo, 4), "wilson_high": round(w_hi, 4),
        "boot_low": round(float(b_lo), 4), "boot_high": round(float(b_hi), 4),
        "n": n,
        "distinguishable_from_coin_flip": bool(w_lo > 0.5 or w_hi < 0.5),
    }
