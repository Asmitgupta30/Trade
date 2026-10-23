"""Point-in-time feature pipeline.

Contract (enforced by tests/test_lookahead.py):
  * features.loc[T] may only use market data with timestamp <= T
  * labels.loc[T] describe the FUTURE (forward return over `horizon` days);
    training pairs are (features[T], label[T]) and the last `horizon` rows
    carry NaN labels so they can never be trained on
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from features.indicators import all_indicators

log = logging.getLogger(__name__)

FEATURE_COLUMNS = [
    "ret_1d", "ret_5d", "ret_10d", "ret_21d",
    "rvol_5d", "rvol_21d",
    "rsi_14", "macd", "macd_signal", "macd_hist_norm",
    "bb_pctb", "bb_width", "atr_pct_14", "vol_z",
]


def _per_symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    f = all_indicators(df)
    # normalize scale-sensitive indicators by price
    f["macd_hist_norm"] = f["macd_hist"] / df["close"]
    f["atr_pct_14"] = f["atr_14"] / df["close"]
    return f.drop(columns=["macd_hist", "atr_14"], errors="ignore")


def compute_symbol_features(hist: pd.DataFrame) -> pd.DataFrame:
    """Feature frame for one symbol, indexed like `hist`."""
    if len(hist) < 40:
        raise ValueError("need >= 40 bars for a stable feature panel")
    feats = _per_symbol_features(hist)
    feats["close_to_vwap20"] = hist["close"] / (
        (hist["close"] * hist["volume"]).rolling(20, min_periods=20).sum()
        / hist["volume"].rolling(20, min_periods=20).sum()
    ) - 1.0
    return feats


def make_forward_return_label(hist: pd.DataFrame, horizon: int = 1) -> pd.Series:
    """Forward N-day close-to-close return (the thing we want to predict)."""
    return hist["close"].shift(-horizon) / hist["close"] - 1.0


def add_regime_context(features_by_symbol: dict[str, pd.DataFrame],
                       nifty_hist: pd.DataFrame,
                       vix_series: pd.Series | None) -> dict[str, pd.DataFrame]:
    """Attach market-regime columns shared across symbols (Nifty trend, VIX bucket).

    Regime values at T use only data <= T.
    """
    nifty_ret5 = nifty_hist["close"].pct_change(5)
    nifty_ret50 = nifty_hist["close"].pct_change(50)
    trend = np.where(nifty_ret5.fillna(0) > 0, 1, -1)
    regime = pd.DataFrame({
        "nifty_mom_5d": nifty_ret5,
        "nifty_mom_50d": nifty_ret50,
        "nifty_trend_up": pd.Series(trend, index=nifty_hist.index).shift(0),
    })
    if vix_series is not None and not vix_series.empty:
        vix = vix_series.reindex(nifty_hist.index).ffill(limit=3)
        regime["vix_level"] = vix
        regime["vix_high_regime"] = (vix > vix.rolling(60, min_periods=60).quantile(0.7)).astype(float)

    out: dict[str, pd.DataFrame] = {}
    for sym, f in features_by_symbol.items():
        joined = f.join(regime.reindex(f.index))
        # regime columns may be NaN outside nifty history; forward-fill within day tolerance
        joined[["nifty_mom_5d", "nifty_mom_50d"]] = joined[
            ["nifty_mom_5d", "nifty_mom_50d"]].ffill(limit=2)
        out[sym] = joined
    return out


def sector_relative_momentum(features_by_symbol: dict[str, pd.DataFrame],
                             sectors: dict[str, str], window: int = 10) -> None:
    """In-place: rank each symbol's ret_{window}d within its sector per date.

    Produces sector_rel_mom in [-0.5, 0.5] (percentile rank centered).
    Uses only past-return features already present at each row -> still PIT-safe.
    """
    col = f"ret_{window}d"
    by_sector: dict[str, dict[str, pd.Series]] = {}
    for sym, f in features_by_symbol.items():
        if col not in f.columns:
            continue
        by_sector.setdefault(sectors.get(sym, "Other"), {})[sym] = f[col]

    for members in by_sector.values():
        if len(members) < 2:
            continue
        cross = pd.DataFrame(members)          # dates x symbols (same sector)
        pct = cross.rank(axis=1, pct=True)
        for sym, ranked in ((sym, pct[sym] - 0.5) for sym in members):
            features_by_symbol[sym]["sector_rel_mom"] = ranked.reindex(
                features_by_symbol[sym].index)


def build_dataset(history_by_symbol: dict[str, pd.DataFrame],
                  sectors: dict[str, str],
                  nifty_hist: pd.DataFrame | None = None,
                  vix_series: pd.Series | None = None,
                  horizon: int = 1) -> pd.DataFrame:
    """Full PIT dataset: features + label + metadata rows for model training."""
    feats: dict[str, pd.DataFrame] = {}
    for sym, hist in history_by_symbol.items():
        try:
            feats[sym] = compute_symbol_features(hist)
        except ValueError as exc:
            log.warning("features skipped %s: %s", sym, exc)

    if nifty_hist is None:
        # synthesize regime from an equal-weight index of the universe
        closes = pd.concat(
            {sym: h["close"].rename(sym) for sym, h in history_by_symbol.items()}, axis=1)
        nifty_like = pd.DataFrame({"close": closes.mean(axis=1)})
        nifty_like.index = closes.index
        nifty_hist = nifty_like

    feats = add_regime_context(feats, nifty_hist, vix_series)
    sector_rel_mom_window = 10
    sector_relative_momentum(feats, sectors, window=sector_rel_mom_window)

    frames = []
    for sym, f in feats.items():
        df = f.copy()
        df.insert(0, "symbol", sym)
        df["sector"] = sectors.get(sym, "Other")
        # metadata column for trade-level audit trails (excluded from models)
        df["close"] = history_by_symbol[sym]["close"].reindex(df.index)
        df["label_fwd_ret"] = make_forward_return_label(history_by_symbol[sym], horizon)
        frames.append(df)
    full = pd.concat(frames)
    return full.sort_index()
