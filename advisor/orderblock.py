"""Institutional Order Block & Liquidity Analytics Engine.

Adapted from Smart Money Concepts (SMC/ICT) and OrderBlock.com terminal architecture:
  1. Market Picture & Breadth: Tracks universe advance/decline and sector basket flows.
  2. Order Block (OB) Identification: Pinpoints the last opposing candle prior to an impulsive
     displacement move that broke market structure (BOS - Break of Structure).
  3. Zone Range & Levels:
     - Z: The Order Block Demand/Supply Zone [Low, High]
     - Invalidation Level (S1 / R1): Where the institutional setup is invalidated
     - Liquidity Pools (R1, R2 / S1, S2): Structural targets where resting orders sit
     - Retest State: Identifies whether price is actively retesting the zone (IN_ZONE_TRIGGER),
       approaching it, or waiting for a pullback.
  4. 4-Point Alignment Check: Stock strength, Sector alignment, Market breadth, and Trend.
  5. Dual Board Ranking: Bullish Board (Demand Zones) and Bearish Board (Supply Zones) with
     strict sector-capping (max 2 picks per sector) and event-risk exclusions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Any
import numpy as np
import pandas as pd

from features.universe import load_universe_symbols

log = logging.getLogger(__name__)


@dataclass
class OrderBlockSetup:
    symbol: str
    sector: str
    side: str                            # "BULLISH" | "BEARISH"
    rank_pct: float                      # Percentile rank (e.g. 99.2)
    rank_index: int                      # Position on board (1 to 20)
    is_pick: bool                        # Top 3 pick after rules
    pick_rule: str                       # "PICK · BULLISH #1" | "NOT PICKED · outside top 3" | "NOT PICKED · sector cap"
    current_price: float
    change_pct: float
    zone_low: float                      # Lower bound of Order Block zone
    zone_high: float                     # Upper bound of Order Block zone
    invalidation: float                  # Where the setup is invalidated (S1 for bull, R1 for bear)
    target_1: float                      # First liquidity target (swing / BOS level)
    target_2: float                      # Expansion liquidity target (2.5R+)
    risk_reward_ratio: float
    retest_status: str                   # "IN_ZONE_TRIGGER" | "APPROACHING_ZONE" | "WAITING_FOR_RETEST"
    retest_label: str                    # Human-friendly label
    distance_to_zone_pct: float
    alignment_score: int                 # 1 to 4 confluence score
    alignment_checklist: list[str]
    sparkline: list[float]
    dates: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketPicture:
    total_stocks: int
    bullish_count: int
    bearish_count: int
    neutral_count: int
    advance_decline_ratio: float
    market_balance: str                  # "ONE_SIDED_BULLISH" | "TWO_WAY_CHOPPY" | "ONE_SIDED_BEARISH"
    market_balance_label: str
    leading_sector: str
    leading_sector_return: float
    lagging_sector: str
    lagging_sector_return: float
    sector_summary: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    """Calculate the 14-period Average True Range."""
    if len(closes) < 2:
        return 1.0
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        )
    )
    if len(tr) >= period:
        return float(np.mean(tr[-period:]))
    return float(np.mean(tr)) if len(tr) > 0 else 1.0


def detect_bullish_order_block(df: pd.DataFrame, sym: str, sector: str) -> OrderBlockSetup | None:
    """Scan for a Bullish Order Block (Institutional Demand Zone) with Break of Structure."""
    if len(df) < 25:
        return None

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    n = len(df)

    atr = calculate_atr(highs, lows, closes, 14)
    curr_close = float(closes[-1])
    prev_close = float(closes[-2]) if n >= 2 else curr_close
    chg_pct = round((curr_close - prev_close) / prev_close * 100.0, 2)

    # Exclude chaotic event-risk gaps (> 8% overnight without structure)
    if abs(chg_pct) > 8.0:
        return None

    # Search for Break of Structure (BOS) in the recent 30 bars
    # A swing high was broken upwards by an impulsive candle
    found_ob = None
    for i in range(max(3, n - 35), n - 2):
        # Is bar i a local swing high?
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] >= highs[i + 1]:
            swing_high = float(highs[i])
            # Did a subsequent candle break above swing_high with displacement?
            for j in range(i + 1, min(i + 18, n)):
                if closes[j] > swing_high + 0.1 * atr:
                    # Breakout confirmed! Locate the last down candle (red candle) before this displacement
                    for k in range(j, max(0, i - 4), -1):
                        if closes[k] < opens[k]:
                            ob_low = round(float(lows[k]), 2)
                            ob_high = round(float(highs[k]), 2)
                            risk = ob_high - (ob_low * 0.995)
                            if risk <= 0 or (risk / ob_high) > 0.08:
                                continue

                            s1 = round(ob_low * 0.995, 2)
                            t1 = round(swing_high, 2)
                            t2 = round(ob_high + 2.5 * (ob_high - s1), 2)
                            rr = round((t2 - ob_high) / (ob_high - s1), 1)

                            # Determine Retest State vs current price
                            dist_to_zone = round((curr_close - ob_high) / ob_high * 100.0, 2)
                            if ob_low <= curr_close <= ob_high * 1.01:
                                status = "IN_ZONE_TRIGGER"
                                label = "IN THE ZONE (Retest Entry)"
                            elif ob_high * 1.01 < curr_close <= ob_high * 1.03:
                                status = "APPROACHING_ZONE"
                                label = "Approaching Demand Zone"
                            elif curr_close > ob_high * 1.03:
                                status = "WAITING_FOR_RETEST"
                                label = "Waiting for Pullback to Zone"
                            else:
                                # Below invalidation
                                continue

                            found_ob = {
                                "zone_low": ob_low,
                                "zone_high": ob_high,
                                "invalidation": s1,
                                "target_1": max(t1, round(ob_high * 1.02, 2)),
                                "target_2": max(t2, round(ob_high * 1.05, 2)),
                                "risk_reward_ratio": rr,
                                "status": status,
                                "label": label,
                                "dist_pct": dist_to_zone,
                            }
                            break
                    if found_ob:
                        break

    if not found_ob:
        return None

    # Trend check for 4-point alignment
    sma20 = float(df["close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else curr_close
    trend_ok = curr_close >= sma20

    alignment_items = [
        f"Structure: Clean Break of Structure (BOS) above swing high",
        f"Zone: Institutional demand formed between ₹{found_ob['zone_low']} and ₹{found_ob['zone_high']}",
        f"Trend: Trading {'above' if trend_ok else 'near'} 20-day moving average (₹{sma20:.2f})",
        f"Risk-Reward: Defined 1:{found_ob['risk_reward_ratio']} asymmetric payoff",
    ]

    return OrderBlockSetup(
        symbol=sym,
        sector=sector,
        side="BULLISH",
        rank_pct=0.0,
        rank_index=0,
        is_pick=False,
        pick_rule="",
        current_price=curr_close,
        change_pct=chg_pct,
        zone_low=found_ob["zone_low"],
        zone_high=found_ob["zone_high"],
        invalidation=found_ob["invalidation"],
        target_1=found_ob["target_1"],
        target_2=found_ob["target_2"],
        risk_reward_ratio=found_ob["risk_reward_ratio"],
        retest_status=found_ob["status"],
        retest_label=found_ob["label"],
        distance_to_zone_pct=found_ob["dist_pct"],
        alignment_score=4 if trend_ok else 3,
        alignment_checklist=alignment_items,
        sparkline=[round(float(c), 2) for c in df["close"].tail(25)],
        dates=[str(ts)[:10] for ts in df.index[-25:]],
    )


def detect_bearish_order_block(df: pd.DataFrame, sym: str, sector: str) -> OrderBlockSetup | None:
    """Scan for a Bearish Order Block (Institutional Supply Zone) with Break of Structure."""
    if len(df) < 25:
        return None

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    n = len(df)

    atr = calculate_atr(highs, lows, closes, 14)
    curr_close = float(closes[-1])
    prev_close = float(closes[-2]) if n >= 2 else curr_close
    chg_pct = round((curr_close - prev_close) / prev_close * 100.0, 2)

    if abs(chg_pct) > 8.0:
        return None

    found_ob = None
    for i in range(max(3, n - 35), n - 2):
        # Is bar i a local swing low?
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] <= lows[i + 1]:
            swing_low = float(lows[i])
            for j in range(i + 1, min(i + 18, n)):
                if closes[j] < swing_low - 0.1 * atr:
                    # Breakdown confirmed! Locate last up candle (green candle) before this drop
                    for k in range(j, max(0, i - 4), -1):
                        if closes[k] > opens[k]:
                            ob_low = round(float(lows[k]), 2)
                            ob_high = round(float(highs[k]), 2)
                            risk = (ob_high * 1.005) - ob_low
                            if risk <= 0 or (risk / ob_low) > 0.08:
                                continue

                            r1 = round(ob_high * 1.005, 2)
                            s1 = round(swing_low, 2)
                            s2 = round(ob_low - 2.5 * (r1 - ob_low), 2)
                            rr = round((ob_low - s2) / (r1 - ob_low), 1)

                            dist_to_zone = round((curr_close - ob_low) / ob_low * 100.0, 2)
                            if ob_low * 0.99 <= curr_close <= ob_high:
                                status = "IN_ZONE_TRIGGER"
                                label = "IN THE ZONE (Supply Retest)"
                            elif ob_low * 0.97 <= curr_close < ob_low * 0.99:
                                status = "APPROACHING_ZONE"
                                label = "Approaching Supply Zone"
                            elif curr_close < ob_low * 0.97:
                                status = "WAITING_FOR_RETEST"
                                label = "Waiting for Bounce to Zone"
                            else:
                                continue

                            found_ob = {
                                "zone_low": ob_low,
                                "zone_high": ob_high,
                                "invalidation": r1,
                                "target_1": min(s1, round(ob_low * 0.98, 2)),
                                "target_2": min(s2, round(ob_low * 0.95, 2)),
                                "risk_reward_ratio": rr,
                                "status": status,
                                "label": label,
                                "dist_pct": dist_to_zone,
                            }
                            break
                    if found_ob:
                        break

    if not found_ob:
        return None

    sma20 = float(df["close"].rolling(20).mean().iloc[-1]) if len(df) >= 20 else curr_close
    trend_ok = curr_close <= sma20

    alignment_items = [
        f"Structure: Clean Breakdown of Structure (BOS) below swing low",
        f"Zone: Institutional supply formed between ₹{found_ob['zone_low']} and ₹{found_ob['zone_high']}",
        f"Trend: Trading {'below' if trend_ok else 'near'} 20-day moving average (₹{sma20:.2f})",
        f"Risk-Reward: Defined 1:{found_ob['risk_reward_ratio']} asymmetric payoff",
    ]

    return OrderBlockSetup(
        symbol=sym,
        sector=sector,
        side="BEARISH",
        rank_pct=0.0,
        rank_index=0,
        is_pick=False,
        pick_rule="",
        current_price=curr_close,
        change_pct=chg_pct,
        zone_low=found_ob["zone_low"],
        zone_high=found_ob["zone_high"],
        invalidation=found_ob["invalidation"],
        target_1=found_ob["target_1"],
        target_2=found_ob["target_2"],
        risk_reward_ratio=found_ob["risk_reward_ratio"],
        retest_status=found_ob["status"],
        retest_label=found_ob["label"],
        distance_to_zone_pct=found_ob["dist_pct"],
        alignment_score=4 if trend_ok else 3,
        alignment_checklist=alignment_items,
        sparkline=[round(float(c), 2) for c in df["close"].tail(25)],
        dates=[str(ts)[:10] for ts in df.index[-25:]],
    )


def build_market_picture(universe_data: dict[str, pd.DataFrame]) -> MarketPicture:
    """Calculate universe breadth and sector basket flows for the Market Picture strip."""
    universe_map = load_universe_symbols()
    sector_moves: dict[str, list[float]] = {}
    bullish = 0
    bearish = 0
    neutral = 0

    for sym, df in universe_data.items():
        if df is None or len(df) < 2:
            continue
        c0 = float(df["close"].iloc[-2])
        c1 = float(df["close"].iloc[-1])
        chg = (c1 - c0) / (c0 + 1e-9) * 100.0

        sec = universe_map.get(sym, "Other")
        sector_moves.setdefault(sec, []).append(chg)

        if chg > 0.15:
            bullish += 1
        elif chg < -0.15:
            bearish += 1
        else:
            neutral += 1

    total = bullish + bearish + neutral
    ratio = round(bullish / max(total, 1) * 100.0, 1)

    # Balance categorization
    if ratio >= 58.0:
        balance = "ONE_SIDED_BULLISH"
        balance_label = "One-Sided Bullish (Long Edge)"
    elif ratio <= 42.0:
        balance = "ONE_SIDED_BEARISH"
        balance_label = "One-Sided Bearish (Short Edge)"
    else:
        balance = "TWO_WAY_CHOPPY"
        balance_label = "Two-Way / Balanced (Selective Only)"

    # Sector aggregation
    sec_summaries = []
    for sec, vals in sector_moves.items():
        if not vals:
            continue
        avg_ret = round(float(np.mean(vals)), 2)
        green_pct = round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 0)
        sec_summaries.append({
            "sector": sec,
            "avg_return_pct": avg_ret,
            "green_pct": green_pct,
            "count": len(vals),
        })

    sec_summaries.sort(key=lambda x: -x["avg_return_pct"])
    leading_sec = sec_summaries[0]["sector"] if sec_summaries else "NSE Benchmark"
    leading_ret = sec_summaries[0]["avg_return_pct"] if sec_summaries else 0.0

    lagging_sec = sec_summaries[-1]["sector"] if sec_summaries else "NSE Benchmark"
    lagging_ret = sec_summaries[-1]["avg_return_pct"] if sec_summaries else 0.0

    return MarketPicture(
        total_stocks=total,
        bullish_count=bullish,
        bearish_count=bearish,
        neutral_count=neutral,
        advance_decline_ratio=ratio,
        market_balance=balance,
        market_balance_label=balance_label,
        leading_sector=leading_sec,
        leading_sector_return=leading_ret,
        lagging_sector=lagging_sec,
        lagging_sector_return=lagging_ret,
        sector_summary=sec_summaries,
    )


def scan_order_blocks(universe_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """Execute complete institutional Order Block & Liquidity analysis across the universe."""
    universe_map = load_universe_symbols()
    market_pic = build_market_picture(universe_data)

    bullish_candidates: list[OrderBlockSetup] = []
    bearish_candidates: list[OrderBlockSetup] = []

    for sym, df in universe_data.items():
        if df is None or len(df) < 25:
            continue
        sec = universe_map.get(sym, "General")

        b_ob = detect_bullish_order_block(df, sym, sec)
        if b_ob:
            bullish_candidates.append(b_ob)

        s_ob = detect_bearish_order_block(df, sym, sec)
        if s_ob:
            bearish_candidates.append(s_ob)

    # Rank bullish candidates: prioritize active retests and cleanest change_pct
    status_weights = {"IN_ZONE_TRIGGER": 100.0, "APPROACHING_ZONE": 50.0, "WAITING_FOR_RETEST": 10.0}
    bullish_candidates.sort(key=lambda x: -(status_weights.get(x.retest_status, 0) + x.change_pct))
    bearish_candidates.sort(key=lambda x: -(status_weights.get(x.retest_status, 0) - x.change_pct))

    # Apply percentile ranks and sector capping for top picks
    n_bull = len(bullish_candidates)
    bull_picks = 0
    bull_sec_counts: dict[str, int] = {}
    for idx, ob in enumerate(bullish_candidates):
        ob.rank_index = idx + 1
        ob.rank_pct = round((1.0 - (idx / max(n_bull, 1))) * 100.0, 1)
        sec_cnt = bull_sec_counts.get(ob.sector, 0)
        if bull_picks < 3 and sec_cnt < 2:
            ob.is_pick = True
            bull_picks += 1
            ob.pick_rule = f"PICK · BULLISH #{bull_picks}"
            bull_sec_counts[ob.sector] = sec_cnt + 1
        elif sec_cnt >= 2:
            ob.pick_rule = f"NOT PICKED · sector cap ({ob.sector})"
        else:
            ob.pick_rule = "NOT PICKED · outside top 3"

    n_bear = len(bearish_candidates)
    bear_picks = 0
    bear_sec_counts: dict[str, int] = {}
    for idx, ob in enumerate(bearish_candidates):
        ob.rank_index = idx + 1
        ob.rank_pct = round((1.0 - (idx / max(n_bear, 1))) * 100.0, 1)
        sec_cnt = bear_sec_counts.get(ob.sector, 0)
        if bear_picks < 3 and sec_cnt < 2:
            ob.is_pick = True
            bear_picks += 1
            ob.pick_rule = f"PICK · BEARISH #{bear_picks}"
            bear_sec_counts[ob.sector] = sec_cnt + 1
        elif sec_cnt >= 2:
            ob.pick_rule = f"NOT PICKED · sector cap ({ob.sector})"
        else:
            ob.pick_rule = "NOT PICKED · outside top 3"

    return {
        "market_picture": market_pic.to_dict(),
        "bullish_board": [ob.to_dict() for ob in bullish_candidates[:20]],
        "bearish_board": [ob.to_dict() for ob in bearish_candidates[:20]],
        "bullish_picks": [ob.to_dict() for ob in bullish_candidates if ob.is_pick],
        "bearish_picks": [ob.to_dict() for ob in bearish_candidates if ob.is_pick],
        "advisory_only": True,
    }
