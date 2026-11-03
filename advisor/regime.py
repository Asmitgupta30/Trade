"""Market regime detection and risk-gating for Indian equities.

Categorizes broader market conditions based on Nifty 50 trend and India VIX:
  - RISK_ON:   Nifty > 50-day SMA and India VIX < 18. Favorable for momentum & breakout setups.
  - CAUTION:   Nifty near 50 SMA or India VIX 18-22. Choppy/range-bound. Selective entries only (hurdle >= 0.70).
  - RISK_OFF:  Nifty < 50-day SMA or India VIX > 22. Capital preservation. New momentum buys suspended.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

REGIME_RISK_ON = "risk-on"
REGIME_CAUTION = "caution"
REGIME_RISK_OFF = "risk-off"


@dataclass(frozen=True)
class MarketRegimeInfo:
    regime: str                           # "risk-on" | "caution" | "risk-off"
    label: str                            # e.g. "Risk-On (Favorable)"
    nifty_ltp: float | None
    nifty_sma50: float | None
    nifty_dist_sma50_pct: float | None
    vix_ltp: float | None
    allows_momentum_buys: bool
    min_confidence_hurdle: float          # dynamic confidence hurdle
    guidance: str                         # actionable human guidance

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "label": self.label,
            "nifty_ltp": self.nifty_ltp,
            "nifty_sma50": self.nifty_sma50,
            "nifty_dist_sma50_pct": self.nifty_dist_sma50_pct,
            "vix_ltp": self.vix_ltp,
            "allows_momentum_buys": self.allows_momentum_buys,
            "min_confidence_hurdle": self.min_confidence_hurdle,
            "guidance": self.guidance,
        }


def detect_regime(
    nifty_history: pd.DataFrame | None = None,
    nifty_ltp: float | None = None,
    vix_ltp: float | None = None,
) -> MarketRegimeInfo:
    """Evaluate current market regime from Nifty 50 history and India VIX level."""
    nifty_sma50: float | None = None
    dist_sma50: float | None = None

    if nifty_history is not None and not nifty_history.empty and "close" in nifty_history.columns:
        closes = nifty_history["close"].dropna()
        if len(closes) >= 50:
            nifty_sma50 = float(closes.rolling(50).mean().iloc[-1])
        elif len(closes) >= 20:
            nifty_sma50 = float(closes.rolling(20).mean().iloc[-1])
        if nifty_ltp is None and not closes.empty:
            nifty_ltp = float(closes.iloc[-1])

    if nifty_ltp is not None and nifty_sma50 is not None and nifty_sma50 > 0:
        dist_sma50 = round((nifty_ltp - nifty_sma50) / nifty_sma50 * 100.0, 2)

    # Defaults if missing data
    vix_val = vix_ltp if vix_ltp is not None else 15.0

    # Classification logic:
    # 1. RISK_OFF: VIX > 22 or Nifty significantly below 50-day SMA (< -1.5%)
    if vix_val >= 22.0 or (dist_sma50 is not None and dist_sma50 < -1.5):
        return MarketRegimeInfo(
            regime=REGIME_RISK_OFF,
            label="Risk-Off (Correction/Defensive)",
            nifty_ltp=nifty_ltp,
            nifty_sma50=nifty_sma50,
            nifty_dist_sma50_pct=dist_sma50,
            vix_ltp=vix_ltp,
            allows_momentum_buys=False,
            min_confidence_hurdle=0.75,
            guidance=(
                "Market is in correction mode (VIX elevated or Nifty below 50 SMA). "
                "Aggressive momentum breakout buys are restricted to preserve capital. "
                "Only high-dividend defensive accumulation or cash is advised."
            ),
        )

    # 2. CAUTION: VIX between 18 and 22, or Nifty slightly below/near 50 SMA
    if vix_val >= 18.0 or (dist_sma50 is not None and dist_sma50 < 0.5):
        return MarketRegimeInfo(
            regime=REGIME_CAUTION,
            label="Caution (Choppy / Neutral)",
            nifty_ltp=nifty_ltp,
            nifty_sma50=nifty_sma50,
            nifty_dist_sma50_pct=dist_sma50,
            vix_ltp=vix_ltp,
            allows_momentum_buys=True,
            min_confidence_hurdle=0.70,
            guidance=(
                "Market conditions are choppy/range-bound. Be highly selective; "
                "only trade setups with confidence >= 0.70 and minimum 1:2.5 risk-to-reward."
            ),
        )

    # 3. RISK_ON: VIX < 18 and Nifty comfortably above 50 SMA
    return MarketRegimeInfo(
        regime=REGIME_RISK_ON,
        label="Risk-On (Bullish Momentum)",
        nifty_ltp=nifty_ltp,
        nifty_sma50=nifty_sma50,
        nifty_dist_sma50_pct=dist_sma50,
        vix_ltp=vix_ltp,
        allows_momentum_buys=True,
        min_confidence_hurdle=0.60,
        guidance=(
            "Broad market trend is bullish (Nifty above 50 SMA, India VIX calm). "
            "Swing breakout and momentum burst setups are statistically favored."
        ),
    )
