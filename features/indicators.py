"""Technical indicators implemented in plain pandas (vectorized, causal).

Why not pandas-ta: the project is dormant and imports `numpy.NaN`, which was
removed in numpy 2.x (this environment: numpy 2.2). Formulas below are the
textbook definitions; `tests/test_indicators.py` validates them against
hand-computed values. All rolling windows look strictly backwards.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=window).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False, min_periods=span).mean()


def multi_horizon_returns(close: pd.Series, horizons=(1, 5, 10, 21)) -> pd.DataFrame:
    out = {}
    for h in horizons:
        # return over the h bars ENDING at t (strictly past data)
        out[f"ret_{h}d"] = close.pct_change(h)
    return pd.DataFrame(out, index=close.index)


def realized_volatility(close: pd.Series, windows=(5, 21)) -> pd.DataFrame:
    ret = close.pct_change()
    out = {}
    for w in windows:
        out[f"rvol_{w}d"] = ret.rolling(w, min_periods=w).std() * (252 ** 0.5)
    return pd.DataFrame(out, index=close.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = (100 - (100 / (1 + rs))).astype(float)
    # all-gain windows => RSI 100
    return out.mask(avg_loss == 0, 100.0)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    line = ema_fast - ema_slow
    sig = line.ewm(span=signal, adjust=False).mean()
    hist = line - sig
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": hist}, index=close.index)


def bollinger(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, window)
    sd = close.rolling(window, min_periods=window).std()
    upper = mid + num_std * sd
    lower = mid - num_std * sd
    pct_b = (close - lower) / (upper - lower).replace(0.0, np.nan)
    bandwidth = (upper - lower) / mid
    return pd.DataFrame(
        {"bb_upper": upper, "bb_lower": lower, "bb_pctb": pct_b.astype(float),
         "bb_width": bandwidth},
        index=close.index,
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()


def volume_zscore(volume: pd.Series, window: int = 20) -> pd.Series:
    mean = volume.rolling(window, min_periods=window).mean()
    std = volume.rolling(window, min_periods=window).std().replace(0.0, np.nan)
    return ((volume - mean) / std.astype(float)).astype(float)


def all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Full indicator panel from an OHLCV frame. Row at T uses rows <= T only."""
    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
    parts = [
        multi_horizon_returns(close),
        realized_volatility(close),
        rsi(close).rename("rsi_14").to_frame(),
        macd(close),
        bollinger(close),
        atr(high, low, close).rename("atr_14").to_frame(),
        volume_zscore(vol).rename("vol_z").to_frame(),
    ]
    return pd.concat(parts, axis=1)

