"""Plain-language rationale generation.

The risk_config hard locks require every suggestion card to carry a rationale
a non-technical user can read. These builders translate feature values into
sentences — no black-box outputs.
"""

from __future__ import annotations

import pandas as pd


def _fmt_pct(x: float | None, digits: int = 1) -> str:
    if x is None or pd.isna(x):
        return "n/a"
    return f"{x * 100:+.{digits}f}%"


def _rsi_phrase(rsi_val: float | None) -> str:
    if rsi_val is None or pd.isna(rsi_val):
        return "RSI unavailable"
    if rsi_val >= 70:
        return f"RSI {rsi_val:.0f} (overbought — momentum strong but stretched)"
    if rsi_val <= 30:
        return f"RSI {rsi_val:.0f} (oversold — possible bounce zone)"
    return f"RSI {rsi_val:.0f}"


def build_rationale(row: pd.Series, confidence: float) -> str:
    parts = []

    ret5 = row.get("ret_5d")
    ret1 = row.get("ret_1d")
    if ret5 is not None and not pd.isna(ret5):
        parts.append(f"5-day move {_fmt_pct(ret5)}")
    if ret1 is not None and not pd.isna(ret1):
        parts.append(f"yesterday {_fmt_pct(ret1)}")

    rsi_phrase = _rsi_phrase(row.get("rsi_14"))
    parts.append(rsi_phrase)

    pctb = row.get("bb_pctb")
    if pctb is not None and not pd.isna(pctb):
        if pctb > 0.8:
            parts.append("riding the upper Bollinger band")
        elif pctb < 0.2:
            parts.append("pressing the lower Bollinger band")

    srm = row.get("sector_rel_mom")
    sector = row.get("sector", "its sector")
    if srm is not None and not pd.isna(srm):
        if srm > 0.15:
            parts.append(f"{sector} is outperforming other sectors over 10 days")
        elif srm < -0.15:
            parts.append(f"{sector} is lagging other sectors over 10 days")

    vol = row.get("rvol_21d")
    if vol is not None and not pd.isna(vol):
        if vol > 0.45:
            parts.append(f"volatility is high ({vol * 100:.0f}% annualized) — expect swings")
        elif vol < 0.18:
            parts.append(f"volatility is low ({vol * 100:.0f}% annualized)")

    base = "; ".join(parts)
    conf_pct = f"{confidence * 100:.0f}%"
    return f"{base.capitalize()}. Model confidence for an up move beyond costs: {conf_pct}."


def build_baseline_comparison(confidence: float, nifty_mom_5d: float | None,
                              expected_net_edge_pct: float) -> str:
    """Required by model_governance.naive_baseline_comparison_required."""
    if nifty_mom_5d is None or pd.isna(nifty_mom_5d):
        market_state = "Nifty trend data unavailable"
    elif nifty_mom_5d > 0.01:
        market_state = f"Nifty itself is in a short-term uptrend ({nifty_mom_5d * 100:+.1f}% / 5d)"
    elif nifty_mom_5d < -0.01:
        market_state = f"Nifty is in a short-term downtrend ({nifty_mom_5d * 100:+.1f}% / 5d)"
    else:
        market_state = "Nifty is roughly flat over 5 days"

    if expected_net_edge_pct <= 0:
        return (f"Doing nothing (or holding a Nifty index fund) looks better: "
                f"this idea has no net edge after costs. {market_state}.")
    if expected_net_edge_pct < 0.5:
        return (f"Edge vs just holding Nifty is modest (~{expected_net_edge_pct:.2f}% net). "
                f"{market_state}; the index fund is the safer default.")
    return (f"Model sees ~{expected_net_edge_pct:.2f}% net edge after costs, ahead of "
            f"a passive Nifty position. {market_state}.")


def circuit_warning(change_pct: float | None) -> str | None:
    """risk_config.circuit_awareness.flag_if_stock_near_upper_lower_circuit.

    NSE price bands are typically 10% (some 20%/5%); flag near-band moves.
    """
    if change_pct is None:
        return None
    if change_pct >= 8:
        return f"Stock is up {change_pct:.1f}% today — near upper price band; chasing is risky."
    if change_pct <= -8:
        return f"Stock is down {change_pct:.1f}% today — near lower price band; exits may face limits."
    return None
