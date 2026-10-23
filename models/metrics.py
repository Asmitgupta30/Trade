"""Performance metrics — implemented and unit-tested here rather than pulling
a reporting library, so every number shown to the user has auditable math."""

from __future__ import annotations

import numpy as np
import pandas as pd


def hit_rate(direction_correct: list[bool] | np.ndarray) -> float:
    arr = np.asarray(direction_correct, dtype=bool)
    if len(arr) == 0:
        return float("nan")
    return float(arr.mean())


def sharpe_ratio(returns: pd.Series | np.ndarray, periods_per_year: int = 252,
                 risk_free: float = 0.0) -> float:
    r = pd.Series(returns, dtype=float).dropna()
    if len(r) < 2:
        return float("nan")
    excess = r - (risk_free / periods_per_year)
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def max_drawdown_pct(equity_curve: pd.Series | np.ndarray) -> float:
    """Max peak-to-trough drawdown in PERCENT (positive number)."""
    eq = pd.Series(equity_curve, dtype=float).dropna()
    if len(eq) == 0:
        return 0.0
    running_max = eq.cummax()
    dd = (eq / running_max) - 1.0
    return float(-dd.min() * 100.0)


def equity_curve(returns: pd.Series | np.ndarray, initial: float = 1.0) -> pd.Series:
    r = pd.Series(returns, dtype=float).fillna(0.0)
    return initial * (1.0 + r).cumprod()


def annualized_return(returns: pd.Series | np.ndarray, periods_per_year: int = 252) -> float:
    r = pd.Series(returns, dtype=float).dropna()
    if len(r) < 2:
        return float("nan")
    growth = float((1.0 + r).prod())
    if growth <= 0:
        return float("nan")
    years = len(r) / periods_per_year
    return float(growth ** (1.0 / years) - 1.0)


def summarize_suggestion_outcomes(outcomes: list[dict]) -> dict:
    """outcomes: [{'direction_ok': bool, 'return_pct': float}, ...]
    Returns the paper-trading gate metrics block."""
    if not outcomes:
        return {"n": 0, "hit_rate": None, "sharpe": None,
                "max_drawdown_pct": None}
    rets = pd.Series([o["return_pct"] for o in outcomes], dtype=float) / 100.0
    correct = [bool(o["direction_ok"]) for o in outcomes]
    eq = equity_curve(rets)
    return {
        "n": len(outcomes),
        "hit_rate": round(hit_rate(correct), 4),
        "sharpe": round(sharpe_ratio(rets), 3),
        "max_drawdown_pct": round(max_drawdown_pct(eq), 2),
        "total_return_pct": round(float((eq.iloc[-1] - 1.0) * 100), 2),
    }
