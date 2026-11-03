import numpy as np
import pandas as pd
import pytest

from common.config import load_risk_config
from features.universe import UniverseMember, load_universe_symbols, passes_liquidity


def _hist(avg_vol: float, price: float, days: int = 40):
    idx = pd.date_range("2026-06-01", periods=days, freq="1D", tz="UTC")
    rng = np.random.default_rng(2)
    vol = np.full(days, avg_vol) * rng.normal(1.0, 0.05, days)
    return pd.DataFrame({
        "open": price, "high": price * 1.01, "low": price * 0.99,
        "close": price, "volume": vol,
    }, index=idx)


@pytest.fixture()
def cfg():
    return load_risk_config()


def test_default_universe_has_sectors():
    syms = load_universe_symbols()
    assert len(syms) >= 50
    assert all(s and isinstance(s, str) for s in syms.values())
    # no duplicates
    assert len(syms) == len(set(syms))


def test_liquid_stock_passes(cfg):
    assert passes_liquidity(_hist(avg_vol=2_000_000, price=500), cfg)


def test_thin_volume_rejected(cfg):
    # risk_config: min_avg_daily_volume = 500_000
    assert not passes_liquidity(_hist(avg_vol=120_000, price=500), cfg)


def test_penny_stock_rejected(cfg):
    # risk_config: min_price_inr = 20
    assert not passes_liquidity(_hist(avg_vol=2_000_000, price=12), cfg)


def test_short_history_rejected(cfg):
    assert not passes_liquidity(_hist(avg_vol=2_000_000, price=500, days=10), cfg)
