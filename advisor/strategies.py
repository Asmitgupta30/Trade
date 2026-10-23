"""Indian market trading strategies adapted from claude-trading-skills.

Provides high-conviction setup scanners and risk-budgeted trade plans across
multiple trading styles in the Indian market (NSE):
  1. Swing Trading: Minervini Volatility Contraction Pattern (VCP)
  2. Short-Term Momentum: Stockbee 4% Momentum Burst (3-5 day swing)
  3. Mean Reversion: Selling Exhaustion Hammer / Undercut & Reclaim
  4. Core Income / GARP: Dividend Growth on RSI Pullback
  5. Position Sizing: Risk-based contract sizing per trade

All strategies conform to the permanent advisory-only constraint.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, asdict
from typing import Any
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


# Curated list of established dividend and cash-flow leaders in NSE universe
NSE_DIVIDEND_LEADERS = {
    "COALINDIA", "ITC", "TCS", "INFY", "POWERGRID", "NTPC",
    "ONGC", "HCLTECH", "TECHM", "IOC", "BPCL", "VEDL", "GAIL",
    "BAJAJ-AUTO", "HEROMOTOCO", "RELIANCE", "HINDUNILVR",
}


@dataclass
class TradePlan:
    symbol: str
    strategy: str              # SWING_VCP | MOMENTUM_BURST | EXHAUSTION_HAMMER | DIVIDEND_PULLBACK | INTRADAY_ORB
    horizon: str               # 1-3 weeks | 3-5 days | 2-5 days | 1-6 months | intraday
    action: str                # BUY | STRONG_BUY | ACCUMULATE
    entry_price: float
    stop_loss: float
    target_price: float
    risk_reward_ratio: float
    risk_pct: float            # % distance to stop
    suggested_shares: int
    position_value_inr: float
    risk_inr: float
    confidence: float
    setup_quality: str         # High (A+) | Good (A) | Moderate (B)
    rationale: str
    checklist: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_position_size(
    capital_inr: float,
    risk_per_trade_pct: float,
    entry_price: float,
    stop_loss_price: float,
    max_position_pct: float = 60.0,
) -> dict[str, Any]:
    """Calculate risk-budgeted share quantity based on claude-trading-skills position-sizer.

    Risk Amount = Capital * Risk%
    Risk Per Share = abs(Entry - Stop)
    Shares = Risk Amount / Risk Per Share (capped by max single-stock exposure)
    """
    entry_price = float(entry_price)
    stop_loss_price = float(stop_loss_price)
    capital_inr = max(float(capital_inr), 1000.0)
    risk_per_trade_pct = min(max(float(risk_per_trade_pct), 0.1), 5.0)

    risk_amount_inr = capital_inr * (risk_per_trade_pct / 100.0)
    risk_per_share = abs(entry_price - stop_loss_price)

    if risk_per_share <= 0:
        shares = 0
    else:
        shares = int(risk_amount_inr / risk_per_share)

    # Cap by max position allocation
    max_stock_capital = capital_inr * (max_position_pct / 100.0)
    if shares * entry_price > max_stock_capital:
        shares = int(max_stock_capital / entry_price)

    shares = max(shares, 0)
    actual_pos_value = round(shares * entry_price, 2)
    actual_risk_inr = round(shares * risk_per_share, 2)
    risk_pct = round((risk_per_share / entry_price) * 100.0, 2) if entry_price > 0 else 0.0

    return {
        "shares": shares,
        "risk_inr": actual_risk_inr,
        "risk_pct": risk_pct,
        "position_value_inr": actual_pos_value,
        "risk_per_share": round(risk_per_share, 2),
    }


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Precompute moving averages, ATR, and RSI for screening."""
    d = df.copy()
    close = d["close"]
    high = d["high"]
    low = d["low"]

    # Moving averages
    d["sma20"] = close.rolling(20, min_periods=15).mean()
    d["sma50"] = close.rolling(50, min_periods=30).mean()
    d["sma150"] = close.rolling(150, min_periods=50).mean()
    d["sma200"] = close.rolling(200, min_periods=60).mean()

    # 50-day average volume
    d["vol_sma50"] = d["volume"].rolling(50, min_periods=20).mean()

    # True Range & ATR(14)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=10).mean()

    # RSI(14)
    delta = close.diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14, min_periods=10).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=10).mean()
    rs = gain / (loss + 1e-9)
    d["rsi14"] = 100.0 - (100.0 / (1.0 + rs))

    return d


def screen_vcp_setups(
    universe_data: dict[str, pd.DataFrame],
    capital_inr: float = 50000.0,
    risk_per_trade_pct: float = 1.0,
) -> list[TradePlan]:
    """Minervini Volatility Contraction Pattern (VCP) Screener (adapted from claude-trading-skills/vcp-screener).

    Criteria:
      1. Stage 2 Trend Template: Price > 50 SMA > 150 SMA > 200 SMA, 200 SMA trending up.
      2. High proximity: Within 25% of 52-week high, at least 25% above 52-week low.
      3. Contracting Volatility: Successive contractions become smaller (e.g., -15% -> -8% -> -3%).
      4. Volume Contraction: Volume drops noticeably (<60% of 50-SMA volume) on right side of base.
    """
    plans: list[TradePlan] = []

    for sym, raw_df in universe_data.items():
        if raw_df is None or len(raw_df) < 120:
            continue
        df = compute_indicators(raw_df)
        last = df.iloc[-1]
        close = float(last["close"])

        # Trend template filter
        sma50 = float(last["sma50"]) if pd.notna(last["sma50"]) else 0
        sma150 = float(last["sma150"]) if pd.notna(last["sma150"]) else 0
        sma200 = float(last["sma200"]) if pd.notna(last["sma200"]) else 0

        if not (close > sma50 and sma50 >= sma150):
            continue

        high_52w = float(df["high"].tail(250).max())
        low_52w = float(df["low"].tail(250).min())
        dist_from_52w_high = (high_52w - close) / high_52w * 100.0
        dist_from_52w_low = (close - low_52w) / low_52w * 100.0

        if dist_from_52w_high > 28.0 or dist_from_52w_low < 20.0:
            continue

        # Look for consolidation in last 40 bars
        recent = df.tail(40)
        recent_high = float(recent["high"].max())
        recent_low = float(recent["low"].min())
        base_depth_pct = (recent_high - recent_low) / recent_high * 100.0

        if base_depth_pct > 32.0 or base_depth_pct < 4.0:
            continue

        # Check tight consolidation in last 5 bars (cheat / pivot area)
        tight_5 = df.tail(5)
        tight_depth_pct = (tight_5["high"].max() - tight_5["low"].min()) / tight_5["high"].max() * 100.0
        vol_ratio = float(tight_5["volume"].mean() / (last["vol_sma50"] + 1e-9))

        pivot_price = round(float(tight_5["high"].max()), 2)
        stop_loss = round(float(tight_5["low"].min() * 0.995), 2)
        risk_dist = pivot_price - stop_loss
        if risk_dist <= 0 or (risk_dist / pivot_price) > 0.08:
            continue

        target_price = round(pivot_price + 2.5 * risk_dist, 2)
        rr = round((target_price - pivot_price) / risk_dist, 1)

        sizing = calculate_position_size(
            capital_inr=capital_inr,
            risk_per_trade_pct=risk_per_trade_pct,
            entry_price=pivot_price,
            stop_loss_price=stop_loss,
        )

        plans.append(TradePlan(
            symbol=sym,
            strategy="SWING_VCP",
            horizon="1-3 weeks",
            action="STRONG_BUY" if tight_depth_pct < 4.5 and vol_ratio < 0.8 else "BUY",
            entry_price=pivot_price,
            stop_loss=stop_loss,
            target_price=target_price,
            risk_reward_ratio=rr,
            risk_pct=sizing["risk_pct"],
            suggested_shares=sizing["shares"],
            position_value_inr=sizing["position_value_inr"],
            risk_inr=sizing["risk_inr"],
            confidence=0.74 if tight_depth_pct < 4.0 else 0.64,
            setup_quality="High (A+)" if tight_depth_pct < 4.0 and vol_ratio < 0.7 else "Good (A)",
            rationale=(
                f"{sym} has formed a classic Minervini Volatility Contraction Pattern (VCP). "
                f"Base depth contracted to {tight_depth_pct:.1f}% in the final pivot with volume "
                f"drying up to {vol_ratio:.2f}x 50-day average. Buy on breakout above ₹{pivot_price}."
            ),
            checklist=[
                f"Stage 2 Uptrend: Price (₹{close:.2f}) > 50 SMA (₹{sma50:.2f})",
                f"Base Contraction: Tight 5-day range is only {tight_depth_pct:.1f}%",
                f"Volume Dry-Up: Pivot volume is {vol_ratio * 100:.0f}% of 50-SMA volume",
                f"Controlled Risk: Stop-loss distance is {sizing['risk_pct']}% below pivot",
            ],
        ))

    plans.sort(key=lambda p: -p.confidence)
    return plans


def screen_momentum_bursts(
    universe_data: dict[str, pd.DataFrame],
    capital_inr: float = 50000.0,
    risk_per_trade_pct: float = 1.0,
) -> list[TradePlan]:
    """Stockbee 4% Momentum Burst Screener (adapted from claude-trading-skills/stockbee-momentum-burst-screener).

    Criteria:
      1. 3.5%+ daily gain.
      2. Range expansion: Day range >= 1.4x 14-day ATR.
      3. Volume Shock: Day volume >= 1.6x 50-day average volume.
      4. Close in upper 30% of day's range.
    """
    plans: list[TradePlan] = []

    for sym, raw_df in universe_data.items():
        if raw_df is None or len(raw_df) < 55:
            continue
        df = compute_indicators(raw_df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        prev_close = float(prev["close"])
        chg_pct = (close - prev_close) / prev_close * 100.0

        if chg_pct < 3.2:
            continue

        high = float(last["high"])
        low = float(last["low"])
        day_range = high - low
        atr = float(last["atr14"]) if pd.notna(last["atr14"]) and last["atr14"] > 0 else (day_range or 1.0)
        vol = float(last["volume"])
        vol_sma = float(last["vol_sma50"]) if pd.notna(last["vol_sma50"]) and last["vol_sma50"] > 0 else (vol or 1.0)

        range_expansion = day_range / atr
        vol_shock = vol / vol_sma
        close_loc = (close - low) / (day_range + 1e-9)

        if range_expansion >= 1.3 and vol_shock >= 1.4 and close_loc >= 0.65:
            entry_price = round(high * 1.002, 2)   # trigger above high
            stop_loss = round(low, 2)              # day's low
            risk_dist = entry_price - stop_loss
            if risk_dist <= 0 or (risk_dist / entry_price) > 0.07:
                continue

            target_price = round(entry_price + 2.0 * risk_dist, 2)
            rr = round((target_price - entry_price) / risk_dist, 1)

            sizing = calculate_position_size(
                capital_inr=capital_inr,
                risk_per_trade_pct=risk_per_trade_pct,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
            )

            plans.append(TradePlan(
                symbol=sym,
                strategy="MOMENTUM_BURST",
                horizon="3-5 days",
                action="STRONG_BUY" if chg_pct >= 4.0 and vol_shock >= 2.0 else "BUY",
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                risk_reward_ratio=rr,
                risk_pct=sizing["risk_pct"],
                suggested_shares=sizing["shares"],
                position_value_inr=sizing["position_value_inr"],
                risk_inr=sizing["risk_inr"],
                confidence=0.72 if vol_shock >= 2.0 else 0.62,
                setup_quality="High (A+)" if vol_shock >= 2.0 else "Good (A)",
                rationale=(
                    f"{sym} triggered a Stockbee Momentum Burst with a +{chg_pct:.1f}% price expansion. "
                    f"Volume expanded {vol_shock:.1f}x normal, closing near highs ({close_loc * 100:.0f}% of range). "
                    f"Expected short-term momentum continuation over 3-5 sessions."
                ),
                checklist=[
                    f"Price Burst: +{chg_pct:.1f}% daily expansion",
                    f"Volume Shock: {vol_shock:.1f}x higher than 50-day average",
                    f"Range Expansion: {range_expansion:.1f}x of 14-day ATR",
                    f"Strong Close: Finished at top {close_loc * 100:.0f}% of the day's candle",
                ],
            ))

    plans.sort(key=lambda p: -p.confidence)
    return plans


def screen_exhaustion_hammers(
    universe_data: dict[str, pd.DataFrame],
    capital_inr: float = 50000.0,
    risk_per_trade_pct: float = 1.0,
) -> list[TradePlan]:
    """Selling Exhaustion Hammer / Undercut & Reclaim (adapted from claude-trading-skills/stockbee-exhaustion-hammer-screener).

    Mean reversion setup for buying high-quality pullbacks.
    Criteria:
      1. In medium-term uptrend (SMA50 or SMA200 trending).
      2. 3-6 day recent pullback.
      3. Long lower shadow (>= 1.5x real body), small body, close in top 35% of day.
      4. Undercuts previous day/swing low and reclaims it intraday.
    """
    plans: list[TradePlan] = []

    for sym, raw_df in universe_data.items():
        if raw_df is None or len(raw_df) < 50:
            continue
        df = compute_indicators(raw_df)
        last = df.iloc[-1]
        prev = df.iloc[-2]

        close = float(last["close"])
        open_px = float(last["open"])
        high = float(last["high"])
        low = float(last["low"])
        body = abs(close - open_px)
        lower_wick = min(open_px, close) - low
        total_range = high - low

        if total_range <= 0 or lower_wick < 1.4 * max(body, 0.05 * close):
            continue

        close_loc = (close - low) / (total_range + 1e-9)
        if close_loc < 0.60:
            continue

        # Undercut check: low was below previous 3-day minimum low, but close reclaimed it
        prior_3_low = float(df["low"].iloc[-5:-1].min())
        if low > prior_3_low or close < prior_3_low:
            continue

        entry_price = round(high * 1.002, 2)
        stop_loss = round(low * 0.995, 2)
        risk_dist = entry_price - stop_loss
        if risk_dist <= 0 or (risk_dist / entry_price) > 0.06:
            continue

        target_price = round(entry_price + 2.2 * risk_dist, 2)
        rr = round((target_price - entry_price) / risk_dist, 1)

        sizing = calculate_position_size(
            capital_inr=capital_inr,
            risk_per_trade_pct=risk_per_trade_pct,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
        )

        plans.append(TradePlan(
            symbol=sym,
            strategy="EXHAUSTION_HAMMER",
            horizon="2-5 days",
            action="BUY",
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            risk_reward_ratio=rr,
            risk_pct=sizing["risk_pct"],
            suggested_shares=sizing["shares"],
            position_value_inr=sizing["position_value_inr"],
            risk_inr=sizing["risk_inr"],
            confidence=0.68,
            setup_quality="High (A+)",
            rationale=(
                f"{sym} displayed selling exhaustion with an Undercut & Reclaim hammer. "
                f"Sellers broke below the 3-day low (₹{prior_3_low:.2f}), but buyers stepped in aggressively, "
                f"leaving a long lower wick and closing in the top {close_loc * 100:.0f}% of the day."
            ),
            checklist=[
                f"Selling Exhaustion: Long lower tail ({lower_wick / (body + 1e-6):.1f}x body)",
                f"Undercut & Reclaim: Reclaimed prior low of ₹{prior_3_low:.2f}",
                f"Closing Strength: Finished in upper {close_loc * 100:.0f}% of the candle",
                f"Defined Risk: Stop-loss anchored strictly below hammer low (₹{stop_loss})",
            ],
        ))

    plans.sort(key=lambda p: -p.confidence)
    return plans


def screen_dividend_pullbacks(
    universe_data: dict[str, pd.DataFrame],
    capital_inr: float = 50000.0,
    risk_per_trade_pct: float = 1.0,
) -> list[TradePlan]:
    """Dividend Growth Pullback Screener (adapted from claude-trading-skills/dividend-growth-pullback-screener).

    Finds dividend aristocrats and high-ROCE leaders in temporary oversold dips
    near 200 SMA or with RSI <= 40.
    """
    plans: list[TradePlan] = []

    for sym, raw_df in universe_data.items():
        if sym not in NSE_DIVIDEND_LEADERS:
            continue
        if raw_df is None or len(raw_df) < 60:
            continue
        df = compute_indicators(raw_df)
        last = df.iloc[-1]
        close = float(last["close"])
        rsi = float(last["rsi14"]) if pd.notna(last["rsi14"]) else 50.0
        sma200 = float(last["sma200"]) if pd.notna(last["sma200"]) else close

        dist_200 = (close - sma200) / sma200 * 100.0 if sma200 > 0 else 0.0

        # Oversold or near 200 SMA support
        if rsi <= 42.0 or (-3.0 <= dist_200 <= 3.5):
            entry_price = round(close, 2)
            stop_loss = round(close * 0.94, 2)     # 6% wider positional stop
            risk_dist = entry_price - stop_loss
            target_price = round(entry_price * 1.15, 2)  # 15% dividend + capital gain target
            rr = round((target_price - entry_price) / risk_dist, 1)

            sizing = calculate_position_size(
                capital_inr=capital_inr,
                risk_per_trade_pct=risk_per_trade_pct,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
                max_position_pct=25.0,
            )

            plans.append(TradePlan(
                symbol=sym,
                strategy="DIVIDEND_PULLBACK",
                horizon="1-6 months",
                action="ACCUMULATE",
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                risk_reward_ratio=rr,
                risk_pct=sizing["risk_pct"],
                suggested_shares=sizing["shares"],
                position_value_inr=sizing["position_value_inr"],
                risk_inr=sizing["risk_inr"],
                confidence=0.76 if rsi <= 35 else 0.66,
                setup_quality="High (A+)" if rsi <= 35 else "Good (A)",
                rationale=(
                    f"{sym} is a high-quality Indian dividend leader experiencing a temporary pullback. "
                    f"RSI(14) is {rsi:.1f} (oversold zone) and trading within {abs(dist_200):.1f}% of its 200-day SMA. "
                    f"Attractive entry for long-term income and positional capital appreciation."
                ),
                checklist=[
                    f"Dividend Aristocrat: Consistent cash flow and yield history",
                    f"Oversold Condition: RSI(14) is {rsi:.1f}",
                    f"Major Support: Trading near 200-day moving average (₹{sma200:.2f})",
                    f"Long-term Accumulation: 1:2.5+ risk-reward into cyclical recovery",
                ],
            ))

    plans.sort(key=lambda p: -p.confidence)
    return plans


def screen_all_strategies(
    universe_data: dict[str, pd.DataFrame],
    capital_inr: float = 50000.0,
    risk_per_trade_pct: float = 1.0,
) -> dict[str, list[dict[str, Any]]]:
    """Execute all Indian market screening strategies and return categorized trade plans."""
    vcp = screen_vcp_setups(universe_data, capital_inr, risk_per_trade_pct)
    burst = screen_momentum_bursts(universe_data, capital_inr, risk_per_trade_pct)
    hammer = screen_exhaustion_hammers(universe_data, capital_inr, risk_per_trade_pct)
    dividend = screen_dividend_pullbacks(universe_data, capital_inr, risk_per_trade_pct)

    return {
        "swing_vcp": [p.to_dict() for p in vcp],
        "momentum_burst": [p.to_dict() for p in burst],
        "exhaustion_hammer": [p.to_dict() for p in hammer],
        "dividend_pullback": [p.to_dict() for p in dividend],
    }


_UNIVERSE_CACHE: dict[str, pd.DataFrame] = {}
_CACHE_LOCK = threading.Lock()


def get_cached_universe_data(limit: int = 60, force_reload: bool = False) -> dict[str, pd.DataFrame]:
    """Load and cache the recent OHLCV history for Indian equities.

    Reads recent month partitions (last ~14 months / 280 bars) from the immutable
    NSE bhavcopy barstore to keep screening instantaneous (<0.5s).
    """
    global _UNIVERSE_CACHE
    with _CACHE_LOCK:
        if _UNIVERSE_CACHE and not force_reload:
            return _UNIVERSE_CACHE

        from data.bar_store import ImmutableBarStore, OHLCV_COLUMNS

        s = ImmutableBarStore(provider="nse_bhavcopy")
        if not s.dir.exists():
            s = ImmutableBarStore(provider="yahoo")

        loaded = {}
        if s.dir.exists():
            series_dirs = sorted(s.dir.glob("*__day"))
            for s_dir in series_dirs[:limit]:
                sym = s_dir.name.split("__")[0]
                month_files = sorted(s_dir.glob("*.parquet"))
                if not month_files:
                    continue
                # Read only the last 14 months for screening (sufficient for 200-day SMA + 1-year lookback)
                recent_files = month_files[-14:]
                try:
                    frames = [pd.read_parquet(f) for f in recent_files]
                    df = pd.concat(frames).sort_index()
                    df = df[~df.index.duplicated(keep="first")]
                    cols = [c for c in OHLCV_COLUMNS if c in df.columns]
                    if len(cols) == len(OHLCV_COLUMNS) and len(df) >= 40:
                        loaded[sym] = df[cols]
                except Exception:
                    continue

        _UNIVERSE_CACHE = loaded
        return _UNIVERSE_CACHE


def get_strategy_catalog() -> list[dict[str, Any]]:
    """Return catalog of available trading strategies for the Indian market."""
    return [
        {
            "id": "intraday",
            "name": "Intraday ML Momentum / Mean-Reversion",
            "style": "Intraday",
            "horizon": "Intraday (Square-off by 15:15 IST)",
            "description": "Tabular LightGBM + Sequence LSTM ensemble scoring intraday relative momentum, volatility z-scores, and sector rotation with strict automated risk limits.",
            "risk_profile": "Strict stop-loss, max 3 positions/day, capital protection with round-trip cost hurdle (0.35%).",
            "ideal_market": "High volatility, liquid large-caps (Nifty 50 / Nifty Bank components).",
            "key_rules": [
                "Only trades liquid stocks (daily volume > 500k, price > ₹20)",
                "Minimum model confidence gate (0.55 BUY, 0.70 STRONG_BUY)",
                "Hard square-off before market close at 15:15 IST; zero overnight carry risk",
                "Daily loss limit (5% of budget) circuit breaker stops trading for the day",
            ],
        },
        {
            "id": "swing_vcp",
            "name": "Minervini Volatility Contraction Pattern (VCP)",
            "style": "Swing Trading",
            "horizon": "1 - 3 Weeks",
            "description": "Mark Minervini's signature Stage 2 momentum setup. Identifies stocks forming successive contractions (volatility tightening) with drying volume before an explosive pivot breakout.",
            "risk_profile": "Tight risk (4% - 7%), high reward-to-risk ratio (1:2.5 to 1:4+).",
            "ideal_market": "Bullish / Uptrending market with strong sector leadership.",
            "key_rules": [
                "Stock must be in Stage 2 uptrend: Price > 50-day SMA > 200-day SMA",
                "2 to 4 distinct contractions, each shallower than the previous",
                "Volume dries up significantly on final contraction (pocket pivot)",
                "Buy on breakout above pivot point with volume expansion",
                "Stop loss placed strictly below the low of the most recent contraction",
            ],
        },
        {
            "id": "momentum_burst",
            "name": "Stockbee 4% Momentum Burst",
            "style": "Short-Term Swing",
            "horizon": "3 - 5 Days",
            "description": "Pradeep Bonde (Stockbee) momentum strategy. Catches explosive multi-day swings following range expansion (+4% or greater day) accompanied by at least 2x average volume.",
            "risk_profile": "Fast momentum capture; stop loss anchored to breakout day low (3% - 5%).",
            "ideal_market": "Broad market rallies, high-beta momentum stocks.",
            "key_rules": [
                "Breakout bar expands > 4% in a single session",
                "Breakout volume >= 2x the 20-day average volume",
                "Preceded by at least 3-10 days of tight consolidation (range contraction)",
                "Target is 8% - 15% quick gain over the next 3 to 5 trading sessions",
                "Trail stop immediately to breakeven once up 1R",
            ],
        },
        {
            "id": "exhaustion_hammer",
            "name": "Selling Exhaustion Reversal (Hammer)",
            "style": "Mean Reversion",
            "horizon": "2 - 5 Days",
            "description": "Identifies panic selling climax and institutional absorption. Looks for sharp 3-5 day pullbacks reaching key support, printing a long lower wick (hammer) and closing near the high.",
            "risk_profile": "Asymmetric bounce play; risk strictly defined by the hammer tail low (2% - 4%).",
            "ideal_market": "Market pullbacks, sector dips, oversold conditions.",
            "key_rules": [
                "Preceded by 3 or more consecutive down sessions or steep drop",
                "Lower wick represents >= 60% of total candle range (buyers defended price)",
                "Close finishes in the top 25% of the day's high-low range",
                "Volume is elevated indicating capitulation & absorption",
                "Stop loss placed 0.5% below the hammer low",
            ],
        },
        {
            "id": "dividend_pullback",
            "name": "Dividend Growth Aristocrat Pullback",
            "style": "Core Income / GARP",
            "horizon": "1 - 6 Months",
            "description": "Combines fundamental cash flow stability with technical oversold timing. Screens established Indian dividend leaders (e.g. Coal India, ITC, TCS, PowerGrid, NTPC) during temporary RSI pullbacks near long-term support.",
            "risk_profile": "Low risk, defensive, regular dividend yield + capital recovery upside.",
            "ideal_market": "Rangebound, choppy, or consolidating markets; long-term wealth accumulation.",
            "key_rules": [
                "High dividend yield and healthy cash flow history (NSE Dividend Leaders)",
                "14-day RSI pulls back into oversold zone (RSI <= 40)",
                "Price stabilizes within 10% of the institutional 200-day moving average",
                "Accumulate in tranches with target 12% - 25% plus dividend capture",
            ],
        },
        {
            "id": "mutual_fund",
            "name": "Asset Allocation & Mutual Fund Rebalancing",
            "style": "Portfolio Wealth",
            "horizon": "Quarterly / Annual",
            "description": "Multi-asset disciplined rebalancing across Indian Large Cap, Mid Cap, Debt (Liquid/Gilt), and Sovereign Gold Bonds (SGB/Gold ETF).",
            "risk_profile": "Balanced, low volatility, systematically prevents emotional buying at tops.",
            "ideal_market": "All weather / long-term retirement and wealth building.",
            "key_rules": [
                "Target allocation bands: Large Cap (35-50%), Mid Cap (15-25%), Debt (20-30%), Gold (5-15%)",
                "Rebalancing trigger: When any asset class drifts > 5% outside its band",
                "Direct fresh capital deployment preferentially into underweight asset classes",
                "Tax efficiency: Rebalance primarily through new inflows rather than selling",
            ],
        },
    ]
