"""Simple, well-known baselines. If these beat the ML ensemble out-of-sample,
that is critical information and must be reported plainly.

All baselines are evaluated on EXACTLY the same OOS dates as the model folds,
with the same round-trip cost model where trading occurs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from models.metrics import max_drawdown_pct, sharpe_ratio


def buy_and_hold(price_series: pd.Series, oos_dates: pd.DatetimeIndex) -> pd.Series:
    """Daily returns of holding the instrument across the OOS window."""
    rets = price_series.pct_change()
    return rets.reindex(oos_dates).fillna(0.0)


def sma_crossover(price_series: pd.Series, fast: int = 20, slow: int = 50,
                  oos_dates: pd.DatetimeIndex | None = None,
                  cost_pct_per_switch: float = 0.35) -> pd.Series:
    """Long when SMA(fast) > SMA(slow), cash otherwise. Signal shifted one day
    so day-T position uses information through T-1 close only (PIT-safe)."""
    sma_f = price_series.rolling(fast, min_periods=fast).mean()
    sma_s = price_series.rolling(slow, min_periods=slow).mean()
    raw_pos = (sma_f > sma_s).astype(float)
    pos = raw_pos.shift(1).fillna(0.0)                    # tradable next day
    rets = price_series.pct_change().fillna(0.0) * pos
    switches = pos.diff().abs().fillna(0.0)               # 1 per position change
    rets = rets - switches * cost_pct_per_switch / 100.0
    if oos_dates is not None:
        rets = rets.reindex(oos_dates).fillna(0.0)
    return rets


def cross_sectional_momentum(dataset: pd.DataFrame, momentum_col: str,
                             oos_dates: pd.DatetimeIndex, top_k: int = 3,
                             confidence_gate: float = -np.inf,
                             cost_pct: float = 0.35) -> tuple[pd.Series, dict]:
    """Top-K by past momentum each day, equal weight, net of costs.

    Uses the SAME dataset/features as the model so the comparison is
    apples-to-apples; picks strictly by descending `momentum_col`.
    """
    daily_rets, correct, picked = {}, [], 0
    for d, day in dataset.groupby(level=0):
        if d not in set(oos_dates):
            continue
        elig = day[day[momentum_col].notna()].sort_values(momentum_col, ascending=False)
        chosen = elig.head(top_k)
        if len(chosen):
            net = chosen["label_fwd_ret"] - cost_pct / 100.0
            daily_rets[d] = float(net.mean())
            picked += len(chosen)
            correct.extend(bool(v) for v in (chosen["label_fwd_ret"] > 0))
        else:
            daily_rets[d] = 0.0
    s = pd.Series(daily_rets).sort_index() if daily_rets else pd.Series(dtype=float)
    meta = {"picked": picked,
            "hit_rate": float(np.mean(correct)) if correct else None}
    return s, meta


def summarize(name: str, rets: pd.Series) -> dict:
    rets = rets.dropna()
    return {
        "baseline": name,
        "sharpe": round(sharpe_ratio(rets), 3),
        "max_dd_pct": round(max_drawdown_pct(
            (1 + rets).cumprod()), 2),
        "total_return_pct": round(float(((1 + rets).prod() - 1) * 100), 2),
        "days": len(rets),
    }
