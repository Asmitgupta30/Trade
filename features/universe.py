"""Tradable universe for the intraday equity advisor.

Base set: curated liquid NSE large/mid-caps (~Nifty 100 coverage) with sector
tags. A `configs/universe.csv` (columns: symbol,sector) overrides this if
present. Every candidate still has to pass the risk_config liquidity_filter
(avg daily volume, min price) before it can receive suggestions.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from common.config import RiskConfig

log = logging.getLogger(__name__)

# symbol -> sector (curated; approximate Nifty large/mid composition)
DEFAULT_UNIVERSE: dict[str, str] = {
    # financials / banks
    "HDFCBANK": "Financials", "ICICIBANK": "Financials", "SBIN": "Financials",
    "KOTAKBANK": "Financials", "AXISBANK": "Financials", "BAJFINANCE": "Financials",
    "BAJAJFINSV": "Financials", "INDUSINDBK": "Financials", "PNB": "Financials",
    "BANKBARODA": "Financials", "CANBK": "Financials", "IDFCFIRSTB": "Financials",
    "SBILIFE": "Insurance", "HDFCLIFE": "Insurance", "ICICIGI": "Insurance",
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT",
    "LTIM": "IT", "PERSISTENT": "IT", "COFORGE": "IT", "MPHASIS": "IT",
    # energy / utilities
    "RELIANCE": "Energy", "ONGC": "Energy", "NTPC": "Power", "POWERGRID": "Power",
    "TATAPOWER": "Power", "ADANIGREEN": "Power", "COALINDIA": "Energy", "GAIL": "Energy",
    # oil & gas
    "IOC": "OilGas", "BPCL": "OilGas", "HINDPETRO": "OilGas", "PETRONET": "OilGas",
    # autos
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto",
    "HEROMOTOCO": "Auto", "EICHERMOT": "Auto", "ASHOKLEY": "Auto", "TVSMOTOR": "Auto",
    "BHARATFORG": "Auto",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG",
    "DABUR": "FMCG", "GODREJCP": "FMCG", "MARICO": "FMCG", "TATACONSUM": "FMCG",
    # pharma / healthcare
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma",
    "TORNTPHARM": "Pharma", "APOLLOHOSP": "Healthcare", "LUPIN": "Pharma",
    "AUROPHARMA": "Pharma", "ZYDUSLIFE": "Pharma",
    # metals / mining
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "JINDALSTEL": "Metals", "SAIL": "Metals", "NMDC": "Metals",
    # capital goods / infra
    "LT": "Infra", "SIEMENS": "CapitalGoods", "ABB": "CapitalGoods", "BEL": "CapitalGoods",
    "HAL": "CapitalGoods", "BHEL": "CapitalGoods", "ADANIENT": "Infra", "GMRAIRPORT": "Infra",
    "IRCTC": "Infra",
    # telecom
    "BHARTIARTL": "Telecom", "IDEA": "Telecom", "INDUSTOWER": "Telecom",
    # cement / materials
    "ULTRACEMCO": "Cement", "GRASIM": "Cement", "SHREECEM": "Cement", "AMBUJACEM": "Cement",
    "ACC": "Cement", "ASIANPAINT": "Chemicals", "PIDILITIND": "Chemicals",
    "SRF": "Chemicals", "UPL": "Chemicals", "TITAN": "Consumer", "TRENT": "Consumer",
    "DMART": "Consumer", "ZOMATO": "Consumer", "PAYTM": "Fintech",
}


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    sector: str
    avg_daily_volume: float
    last_price: float


def load_universe_symbols(config_dir=None) -> dict[str, str]:
    """Curated defaults, overridden by configs/universe.csv when present."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    csv_path = (config_dir or (root / "configs")) / "universe.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        return dict(zip(df["symbol"], df["sector"]))
    return dict(DEFAULT_UNIVERSE)


def passes_liquidity(hist_daily: pd.DataFrame, cfg: RiskConfig) -> bool:
    """risk_config.liquidity_filter: min avg daily volume + min price."""
    if hist_daily.empty or len(hist_daily) < 21:
        return False
    recent = hist_daily.tail(20)
    adv = float(recent["volume"].mean())
    price = float(recent["close"].iloc[-1])
    return (
        adv >= cfg.intraday.min_avg_daily_volume
        and price >= cfg.intraday.min_price_inr
    )


def build_universe(client, cfg: RiskConfig, max_workers: int = 8,
                   symbols: dict[str, str] | None = None) -> list[UniverseMember]:
    """Fetch daily history for all candidates, apply the liquidity filter."""
    syms = symbols or load_universe_symbols()
    members: list[UniverseMember] = []

    def check(sym: str, sector: str):
        try:
            hist = client.get_history(sym, "day", lookback_days=60)
        except Exception as exc:
            log.warning("universe: no history for %s (%s)", sym, exc)
            return None
        if hist.empty or not passes_liquidity(hist, cfg):
            return None
        recent = hist.tail(20)
        return UniverseMember(
            symbol=sym, sector=sector,
            avg_daily_volume=float(recent["volume"].mean()),
            last_price=float(recent["close"].iloc[-1]),
        )

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(check, s, sec): s for s, sec in syms.items()}
        done = 0
        for fut in as_completed(futures):
            done += 1
            if done % 10 == 0:
                log.info("universe scan %d/%d", done, len(futures))
            m = fut.result()
            if m is not None:
                members.append(m)

    members.sort(key=lambda m: -m.avg_daily_volume)
    rejected = len(syms) - len(members)
    log.info("universe: %d passed liquidity filter, %d rejected", len(members), rejected)
    return members
