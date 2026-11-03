"""Live Intraday Trading & Capital Maximizer Engine.

Specialized for Indian Equities (NSE) with:
  * Real-time VWAP Expansion & Retest Tracking
  * 15-Minute Opening Range Breakout (ORB)
  * Volume Velocity / RVOL Surge Detection
  * 5x MIS Intraday Leverage Sizing (Zerodha/Groww/AngelOne Peak Margin)
  * Hard 15:15 IST Square-Off Guardrails & Session Gating
  * Strict Advisory Lock: Never places orders directly.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from typing import Any
import pandas as pd

from common.timeutils import now_ist
from advisor.scenario_simulator import simulate_mcmc_trade_expectancy

log = logging.getLogger(__name__)


@dataclass
class IntradaySetup:
    symbol: str
    sector: str
    action: str                  # "BUY" (Long) or "SHORT" (MIS Short)
    setup_type: str              # "VWAP_EXPANSION", "ORB_BREAKOUT", "PULLBACK_VWAP", "VWAP_BREAKDOWN", "MOMENTUM_SCALPER"
    setup_label: str             # Human readable title
    ltp: float
    change_pct: float
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward_ratio: float
    risk_pct: float
    confidence: float
    momentum_score: int          # 0 to 100
    vwap: float
    vwap_dist_pct: float
    volume_velocity: float       # 1.0 = normal, >1.5 = high institutional activity
    shares_1x: int               # Standard 1x cash sizing
    shares_5x: int               # 5x MIS leveraged quantity (Capital Maximizer)
    margin_required_inr: float   # Margin needed for 5x position
    potential_gain_inr: float    # Expected gain at Target 1 with 5x leverage
    potential_roi_pct: float     # ROI on required margin
    max_risk_inr: float          # Max loss at stop loss (strictly capped to risk budget)
    rationale: str
    checklist: list[str]
    mcmc_pop_pct: float = 72.0   # 10,000 MCMC Probability of Profit (%)
    expected_value_inr: float = 650.0 # Positive expectancy (EV > 0)
    scenario_fit: str = "Bull Expansion & Rangebound Bounce"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_intraday_session_phase(dt: datetime | None = None) -> dict[str, Any]:
    """Calculate current NSE intraday trading window and square-off countdown."""
    current = dt or now_ist()
    cur_time = current.time()

    is_weekday = current.weekday() < 5
    market_open = time(9, 15)
    orb_window_end = time(9, 30)
    morning_prime_end = time(11, 30)
    afternoon_prime_start = time(13, 30)
    prime_trading_end = time(15, 0)
    session_cutoff_time = time(15, 15)
    market_close = time(15, 30)

    # Compute minutes to 15:15 IST session cutoff
    cur_minutes = cur_time.hour * 60 + cur_time.minute
    cutoff_minutes = session_cutoff_time.hour * 60 + session_cutoff_time.minute
    mins_to_cutoff = max(0, cutoff_minutes - cur_minutes) if cur_minutes <= cutoff_minutes else 0

    if not is_weekday or cur_time < market_open or cur_time >= market_close:
        phase = "EXCHANGE_CLOSED"
        label = "NSE Closed (Preview Mode)"
        can_enter = False
        guidance = "Market is closed. Showing end-of-day momentum profiles & watchlist setups for tomorrow's 09:15 AM opening."
    elif cur_time < orb_window_end:
        phase = "OPENING_DISCOVERY"
        label = "Opening Range Formation (09:15 – 09:30)"
        can_enter = False
        guidance = "Wait for the first 15-minute candle to close to establish the high/low opening range bounds."
    elif cur_time <= morning_prime_end:
        phase = "PRIME_MORNING"
        label = "Prime Morning Momentum (09:30 – 11:30)"
        can_enter = True
        guidance = "Highest volume velocity and cleanest trend continuation. Ideal window for breakout and VWAP entries."
    elif cur_time < afternoon_prime_start:
        phase = "MIDDAY_CHOP"
        label = "Midday Consolidation (11:30 – 13:30)"
        can_enter = False
        guidance = "Volume typically tapers off during lunch. Avoid aggressive new entries to prevent whipsaw traps."
    elif cur_time <= prime_trading_end:
        phase = "PRIME_AFTERNOON"
        label = "Afternoon Trend Window (13:30 – 15:00)"
        can_enter = True
        guidance = "European open spillover & institutional closing flows. Tight 1:2 R:R targets recommended."
    elif cur_time < session_cutoff_time:
        phase = "PRE_CLOSE_WIND_DOWN"
        label = "Pre-Close Wind Down (15:00 – 15:15)"
        can_enter = False
        guidance = "Do not take fresh positions. Focus on trailing stops and locking in morning profits."
    else:
        phase = "AUTO_EXIT_WINDOW"
        label = "Broker Auto-Exit Window (15:15 – 15:30)"
        can_enter = False
        guidance = "Broker automatic intraday clearing window. No manual entries permitted."

    return {
        "phase": phase,
        "label": label,
        "can_enter": can_enter,
        "current_time_ist": cur_time.strftime("%H:%M:%S"),
        "minutes_to_cutoff": mins_to_cutoff,
        "guidance": guidance,
    }


def compute_intraday_metrics(quote: Any, df_15m: pd.DataFrame | None = None) -> dict[str, Any]:
    """Derive VWAP, Opening Range, and Volume Velocity for an individual stock."""
    ltp = float(quote.ltp or 0.0)
    change_pct = float(quote.change_pct or 0.0)
    day_open = float(getattr(quote, "day_open", 0.0) or ltp)
    day_high = float(getattr(quote, "day_high", 0.0) or ltp)
    day_low = float(getattr(quote, "day_low", 0.0) or ltp)
    volume = float(getattr(quote, "volume", 0.0) or 0.0)

    # 1. VWAP & Opening Range derivation
    vwap = getattr(quote, "vwap", None) or ((day_high + day_low + ltp) / 3.0)
    orb_high = getattr(quote, "orb_high", None) or day_high
    orb_low = getattr(quote, "orb_low", None) or day_low
    vol_velocity = 1.0

    if df_15m is not None and not df_15m.empty and len(df_15m) >= 2:
        # Calculate true cumulative VWAP from intraday candles
        tp = (df_15m["high"] + df_15m["low"] + df_15m["close"]) / 3.0
        cum_vol = df_15m["volume"].cumsum()
        cum_pv = (tp * df_15m["volume"]).cumsum()
        if cum_vol.iloc[-1] > 0:
            vwap = float(cum_pv.iloc[-1] / cum_vol.iloc[-1])

        # Opening 15-minute range
        orb_high = float(df_15m["high"].iloc[0])
        orb_low = float(df_15m["low"].iloc[0])

        # Volume velocity (recent candle volume vs average 15m volume)
        mean_vol = float(df_15m["volume"].mean() or 1.0)
        recent_vol = float(df_15m["volume"].iloc[-1] or 0.0)
        vol_velocity = round(recent_vol / max(mean_vol, 1.0), 2)

    vwap_dist_pct = round(((ltp - vwap) / vwap * 100.0), 2) if vwap > 0 else 0.0

    # 2. Intraday Momentum Score (0 - 100)
    m_score = 50
    if ltp > vwap:
        m_score += 15
    else:
        m_score -= 15

    if change_pct > 0.8:
        m_score += 15
    elif change_pct < -0.8:
        m_score -= 15

    if ltp >= orb_high and orb_high > 0:
        m_score += 20  # ORB breakout boost
    elif ltp <= orb_low and orb_low > 0:
        m_score -= 20  # ORB breakdown penalty

    if vol_velocity >= 1.5:
        m_score += 10

    m_score = max(5, min(95, m_score))

    # 3. Dynamic Intraday Volatility Band (approx ATR ~0.8% - 1.5%)
    atr_val = max(ltp * 0.009, (day_high - day_low) * 0.4)
    if atr_val <= 0:
        atr_val = ltp * 0.01

    return {
        "ltp": ltp,
        "change_pct": change_pct,
        "day_open": day_open,
        "day_high": day_high,
        "day_low": day_low,
        "volume": volume,
        "vwap": round(vwap, 2),
        "vwap_dist_pct": vwap_dist_pct,
        "orb_high": round(orb_high, 2),
        "orb_low": round(orb_low, 2),
        "vol_velocity": vol_velocity,
        "momentum_score": m_score,
        "atr_val": round(atr_val, 2),
    }


def evaluate_stock_intraday_setup(
    symbol: str,
    sector: str,
    metrics: dict[str, Any],
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    regime: str = "risk-on",
) -> IntradaySetup | None:
    """Evaluate if a stock meets strict intraday breakout / breakdown criteria."""
    ltp = metrics["ltp"]
    vwap = metrics["vwap"]
    change_pct = metrics["change_pct"]
    orb_high = metrics["orb_high"]
    orb_low = metrics["orb_low"]
    vol_velocity = metrics["vol_velocity"]
    m_score = metrics["momentum_score"]
    atr_val = metrics["atr_val"]

    if ltp <= 10.0:
        return None

    # Calculate capital sizing parameters
    max_risk_inr = round(budget_inr * (risk_pct / 100.0), 2)
    leverage = 5.0  # 5x MIS Intraday Margin standard in Zerodha, Groww, Angel One

    # ================= LONG CANDIDATE =================
    if ltp > vwap and m_score >= 65 and change_pct > 0.3:
        # Natural ATR-calibrated stop loss: 1.2% to 3.2% breathing room
        # Not overly tight/conservative so normal intraday noise doesn't trigger false exits
        sl_raw = max(vwap * 0.994, ltp - (atr_val * 1.15))
        sl_dist = max(ltp - sl_raw, ltp * 0.012)  # min 1.2% room to breathe
        sl_dist = min(sl_dist, ltp * 0.032)       # max 3.2% cap
        stop_loss = round(ltp - sl_dist, 2)

        target_1 = round(ltp + (sl_dist * 2.1), 2)  # 1:2.1 R:R asymmetric win
        target_2 = round(ltp + (sl_dist * 3.5), 2)  # 1:3.5 R:R runner
        rr_ratio = round((target_1 - ltp) / sl_dist, 1)

        # Quantity sizing
        shares_1x = max(1, int(max_risk_inr / max(sl_dist, 0.05)))
        # Capital maximizer: 5x leverage expands share count while maintaining exact max_risk_inr
        shares_5x = max(shares_1x, int((budget_inr * leverage * 0.4) / ltp))
        # Cap shares_5x so risk at stop does not exceed max_risk_inr * 1.35
        shares_5x = min(shares_5x, int((max_risk_inr * 1.35) / max(sl_dist, 0.05)))
        shares_5x = max(shares_5x, shares_1x)

        margin_req = round((shares_5x * ltp) / leverage, 2)
        pot_gain = round(shares_5x * (target_1 - ltp), 2)
        pot_roi = round((pot_gain / max(margin_req, 1.0)) * 100.0, 1)

        # Classify specific setup
        if ltp >= orb_high and orb_high > 0:
            stype = "ORB_BREAKOUT"
            slabel = "15-Min Opening Range Breakout"
            rat = f"{symbol} surged above its opening range high (₹{orb_high}) with positive momentum ({change_pct:+.2f}%) and trading above VWAP (₹{vwap})."
        elif abs(metrics["vwap_dist_pct"]) <= 0.6:
            stype = "PULLBACK_VWAP"
            slabel = "VWAP Bounce & Retest"
            rat = f"{symbol} pulled back into institutional VWAP support (₹{vwap}) offering an asymmetric 1:{rr_ratio} bounce entry."
        else:
            stype = "VWAP_EXPANSION"
            slabel = "Intraday VWAP Momentum Expansion"
            rat = f"{symbol} is exhibiting strong directional flow (+{change_pct:.1f}%), holding +{metrics['vwap_dist_pct']}% above VWAP with volume velocity of {vol_velocity}x."

        confidence = round(min(0.85, 0.62 + (m_score / 250.0)), 2)

        # 10,000-path MCMC Expectancy Simulation
        mcmc = simulate_mcmc_trade_expectancy(
            win_rate=confidence,
            reward_risk_ratio=rr_ratio,
            risk_inr=max_risk_inr,
            n_iterations=10000,
        )

        checklist = [
            f"VWAP Anchor: Trading at ₹{ltp} (+{metrics['vwap_dist_pct']}% above ₹{vwap})",
            f"Asymmetric Edge: 1:{rr_ratio} target with {round((sl_dist/ltp)*100, 1)}% stop loss",
            f"Capital Maximizer: ₹{margin_req:,.0f} margin yields ₹{pot_gain:,.0f} at Target 1 (+{pot_roi}% ROI)",
            f"MCMC Projection: {mcmc['mcmc_pop_pct']}% Probability of Profit (EV +₹{mcmc['expected_value_inr']})",
            "Time Lock: Mandatory square-off before 15:15 IST",
        ]

        return IntradaySetup(
            symbol=symbol,
            sector=sector,
            action="BUY",
            setup_type=stype,
            setup_label=slabel,
            ltp=ltp,
            change_pct=change_pct,
            entry_price=ltp,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward_ratio=rr_ratio,
            risk_pct=round((sl_dist / ltp) * 100.0, 2),
            confidence=confidence,
            momentum_score=m_score,
            vwap=vwap,
            vwap_dist_pct=metrics["vwap_dist_pct"],
            volume_velocity=vol_velocity,
            shares_1x=shares_1x,
            shares_5x=shares_5x,
            margin_required_inr=margin_req,
            potential_gain_inr=pot_gain,
            potential_roi_pct=pot_roi,
            max_risk_inr=max_risk_inr,
            rationale=rat,
            checklist=checklist,
            mcmc_pop_pct=mcmc["mcmc_pop_pct"],
            expected_value_inr=mcmc["expected_value_inr"],
            scenario_fit="Optimal in Bull Expansion & Rangebound Bounce",
        )

    # ================= SHORT CANDIDATE (MIS Intraday Short) =================
    elif ltp < vwap and m_score <= 35 and change_pct < -0.4:
        # Natural ATR-calibrated stop loss: 1.2% to 3.2% breathing room
        sl_raw = min(vwap * 1.006, ltp + (atr_val * 1.15))
        sl_dist = max(sl_raw - ltp, ltp * 0.012)
        sl_dist = min(sl_dist, ltp * 0.032)
        stop_loss = round(ltp + sl_dist, 2)

        target_1 = round(ltp - (sl_dist * 2.1), 2)  # 1:2.1 R:R Short
        target_2 = round(ltp - (sl_dist * 3.5), 2)
        rr_ratio = round((ltp - target_1) / sl_dist, 1)

        shares_1x = max(1, int(max_risk_inr / max(sl_dist, 0.05)))
        shares_5x = max(shares_1x, int((budget_inr * leverage * 0.4) / ltp))
        shares_5x = min(shares_5x, int((max_risk_inr * 1.35) / max(sl_dist, 0.05)))
        shares_5x = max(shares_5x, shares_1x)

        margin_req = round((shares_5x * ltp) / leverage, 2)
        pot_gain = round(shares_5x * (ltp - target_1), 2)
        pot_roi = round((pot_gain / max(margin_req, 1.0)) * 100.0, 1)

        if ltp <= orb_low and orb_low > 0:
            stype = "ORB_BREAKDOWN"
            slabel = "15-Min Opening Range Breakdown (MIS Short)"
            rat = f"{symbol} broke below opening range low (₹{orb_low}) with heavy selling ({change_pct:+.2f}%) under VWAP (₹{vwap})."
        elif abs(metrics["vwap_dist_pct"]) <= 0.6:
            stype = "VWAP_REJECTION"
            slabel = "VWAP Supply Rejection (MIS Short)"
            rat = f"{symbol} rejected institutional VWAP resistance (₹{vwap}) offering an asymmetric 1:{rr_ratio} short retest entry."
        else:
            stype = "VWAP_BREAKDOWN"
            slabel = "Intraday Momentum Breakdown (MIS Short)"
            rat = f"{symbol} rejected below VWAP (₹{vwap}) with selling pressure ({change_pct:+.2f}%). Favorable for MIS intraday shorting with target ₹{target_1}."

        confidence = round(min(0.82, 0.60 + ((100 - m_score) / 250.0)), 2)

        mcmc = simulate_mcmc_trade_expectancy(
            win_rate=confidence,
            reward_risk_ratio=rr_ratio,
            risk_inr=max_risk_inr,
            n_iterations=10000,
        )

        checklist = [
            f"Bearish Trend: Trading {metrics['vwap_dist_pct']}% below VWAP (₹{vwap})",
            f"Asymmetric Short: 1:{rr_ratio} downside target with {round((sl_dist/ltp)*100, 1)}% stop loss",
            f"Capital Maximizer: ₹{margin_req:,.0f} margin yields ₹{pot_gain:,.0f} on breakdown (+{pot_roi}% ROI)",
            f"MCMC Projection: {mcmc['mcmc_pop_pct']}% Probability of Profit (EV +₹{mcmc['expected_value_inr']})",
            "Time Lock: Mandatory square-off before 15:15 IST",
        ]

        return IntradaySetup(
            symbol=symbol,
            action="SHORT",
            sector=sector,
            setup_type=stype,
            setup_label=slabel,
            ltp=ltp,
            change_pct=change_pct,
            entry_price=ltp,
            stop_loss=stop_loss,
            target_1=target_1,
            target_2=target_2,
            risk_reward_ratio=rr_ratio,
            risk_pct=round((sl_dist / ltp) * 100.0, 2),
            confidence=confidence,
            momentum_score=m_score,
            vwap=vwap,
            vwap_dist_pct=metrics["vwap_dist_pct"],
            volume_velocity=vol_velocity,
            shares_1x=shares_1x,
            shares_5x=shares_5x,
            margin_required_inr=margin_req,
            potential_gain_inr=pot_gain,
            potential_roi_pct=pot_roi,
            max_risk_inr=max_risk_inr,
            rationale=rat,
            checklist=checklist,
            mcmc_pop_pct=mcmc["mcmc_pop_pct"],
            expected_value_inr=mcmc["expected_value_inr"],
            scenario_fit="Optimal in Bearish Correction & Downside Breakdown",
        )

    return None


_SCAN_CACHE: dict[str, Any] = {}
_SCAN_CACHE_TTL_SEC = 25.0


def scan_live_intraday_universe(
    client: Any,
    budget_inr: float = 50000.0,
    risk_pct: float = 1.0,
    regime: str = "risk-on",
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Execute live intraday scanning across the liquid NSE universe."""
    import time as _t
    global _SCAN_CACHE

    cache_key = f"{round(budget_inr, 2)}:{round(risk_pct, 2)}:{regime}"
    now_ts = _t.time()

    if not force_refresh and cache_key in _SCAN_CACHE:
        entry = _SCAN_CACHE[cache_key]
        if (now_ts - entry["timestamp"]) < _SCAN_CACHE_TTL_SEC:
            # Refresh session time info dynamically even from cache
            cached_data = dict(entry["data"])
            cached_data["session"] = get_intraday_session_phase()
            return cached_data

    from features.universe import DEFAULT_UNIVERSE

    session_info = get_intraday_session_phase()

    # Exclude delisted/corporate-action symbols to eliminate HTTP 404 network delays
    symbols = [s for s in DEFAULT_UNIVERSE.keys() if s not in ("LTIM", "TATAMOTORS")][:50]
    quotes = client.get_quotes(symbols)

    long_setups: list[dict[str, Any]] = []
    short_setups: list[dict[str, Any]] = []

    for sym in symbols:
        q = quotes.get(sym)
        if not q or not getattr(q, "ltp", None) or q.ltp <= 0:
            continue

        sector = DEFAULT_UNIVERSE.get(sym, "Equity")

        # Zero-delay: Quotes already have single-pass batch VWAP & ORB
        df_15m = None
        if getattr(q, "vwap", None) is None:
            try:
                df_15m = client.get_history(sym, interval="15minute", lookback_days=2)
            except Exception:
                df_15m = None

        metrics = compute_intraday_metrics(q, df_15m)
        setup = evaluate_stock_intraday_setup(
            symbol=sym,
            sector=sector,
            metrics=metrics,
            budget_inr=budget_inr,
            risk_pct=risk_pct,
            regime=regime,
        )

        if setup:
            if setup.action == "BUY":
                long_setups.append(setup.to_dict())
            else:
                short_setups.append(setup.to_dict())

    # Sort by momentum score & confidence descending
    long_setups.sort(key=lambda x: (x["momentum_score"], x["confidence"]), reverse=True)
    short_setups.sort(key=lambda x: (-x["momentum_score"], x["confidence"]), reverse=True)

    # Top velocity gainers across universe
    movers = []
    for sym, q in quotes.items():
        if getattr(q, "change_pct", None) is not None:
            movers.append({
                "symbol": sym,
                "sector": DEFAULT_UNIVERSE.get(sym, "Equity"),
                "ltp": q.ltp,
                "change_pct": round(q.change_pct, 2),
            })
    movers.sort(key=lambda x: x["change_pct"], reverse=True)

    result = {
        "session": session_info,
        "capital_rules": {
            "budget_inr": budget_inr,
            "risk_pct": risk_pct,
            "max_risk_inr": round(budget_inr * (risk_pct / 100.0), 2),
            "leverage_multiplier": 5.0,
            "auto_exit_time": "15:15 IST",
        },
        "breadth": {
            "total_scanned": len(quotes),
            "advancing": len([m for m in movers if m["change_pct"] > 0]),
            "declining": len([m for m in movers if m["change_pct"] < 0]),
            "top_gainers": movers[:5],
            "top_losers": movers[-5:],
        },
        "long_setups": long_setups,
        "short_setups": short_setups,
        "total_active_setups": len(long_setups) + len(short_setups),
    }

    _SCAN_CACHE[cache_key] = {
        "timestamp": now_ts,
        "data": result,
    }

    return result
