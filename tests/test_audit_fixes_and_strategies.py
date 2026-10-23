"""Tests for audit fixes, thread safety, and multi-style Indian market trading strategies."""

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from advisor.rules import PortfolioState, RuleEngine
from advisor.suggestion import ACTION_BUY, ACTION_STRONG_BUY, Suggestion
from common.config import load_risk_config
from paper_trading.store import PaperStore


def test_strong_buy_demotion_does_not_block_trade():
    """Demoting STRONG_BUY to BUY when confidence is between BUY and STRONG_BUY gates

    must NOT block the trade with a reason. It should be allowed as a BUY.
    """
    cfg = load_risk_config()
    engine = RuleEngine(cfg)
    sug = Suggestion(
        symbol="RELIANCE",
        sector="Energy",
        action=ACTION_STRONG_BUY,
        amount_inr=1000.0,
        confidence=0.65,  # between buy (0.55) and strong_buy (0.70)
        reference_price=100.0,
        rationale="High relative momentum vs Nifty 50",
        est_cost_inr=3.5,
        baseline_comparison="vs holding cash",
    )
    verdict = engine.check_buy(sug, budget_inr=2000.0, state=PortfolioState())
    assert verdict.allowed is True, f"Trade was blocked despite valid confidence: {verdict.reasons}"
    assert any("below STRONG_BUY gate" in w for w in verdict.warnings)
    assert len(verdict.reasons) == 0


def test_paper_store_concurrent_access(tmp_path):
    """PaperStore must handle concurrent reads and writes safely without locking errors."""
    store = PaperStore(db_path=tmp_path / "concurrent.sqlite3")
    errors = []

    def writer(i):
        try:
            for j in range(15):
                store.save_suggestion(
                    {
                        "suggestion_id": f"sug_{i}_{j}",
                        "symbol": f"SYM_{i}",
                        "action": "BUY",
                        "amount_inr": 500.0,
                        "confidence": 0.65,
                    },
                    phase="pre_market",
                    horizon="intraday",
                    budget_inr=2000.0,
                )
                pid = store.open_paper_position(
                    suggestion_id=f"sug_{i}_{j}",
                    symbol=f"SYM_{i}",
                    quantity=5.0,
                    entry_price=100.0,
                    entry_cost_inr=1.0,
                    entry_dt=datetime.now(timezone.utc),
                    trade_date="2026-10-24",
                )
                store.close_paper_position(
                    position_id=pid,
                    exit_price=105.0,
                    exit_dt=datetime.now(timezone.utc),
                    round_trip_cost_pct=0.35,
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent database operations failed: {errors}"
    assert len(store.all_positions()) == 75
    store.close()


def test_mf_api_empty_holdings_no_crash():
    """GET /api/mutual-funds must not crash with NameError when holdings are empty."""
    from backend.app import app

    with TestClient(app) as client:
        r = client.get("/api/mutual-funds")
        assert r.status_code == 200
        data = r.json()
        assert "next_rebalance" in data
        assert "target_alloc_pct" in data


def test_multi_style_strategies_import_and_execution():
    """Indian market trading strategies must be importable and produce structured plans."""
    from advisor.strategies import (
        screen_vcp_setups,
        screen_momentum_bursts,
        screen_exhaustion_hammers,
        screen_dividend_pullbacks,
        calculate_position_size,
    )

    # 1. Test position size calculation
    plan = calculate_position_size(
        capital_inr=50000.0,
        risk_per_trade_pct=1.0,  # risk Rs 500
        entry_price=2500.0,
        stop_loss_price=2450.0,   # Rs 50 stop per share
    )
    assert plan["shares"] == 10
    assert plan["risk_inr"] == 500.0
    assert plan["position_value_inr"] == 25000.0

    # 2. Test synthetic price series for VCP screener
    dates = pd.bdate_range("2025-01-01", periods=250)
    # Stage 2 base pattern: prices trending upward then contracting volatility
    trend = np.linspace(100, 200, 200)
    # contraction: base from 200 to 190, then 198 to 194
    base = np.concatenate([trend, np.linspace(190, 205, 50)])
    highs = base * 1.01
    lows = base * 0.99
    volumes = np.full(250, 1_000_000)
    volumes[-10:] = 200_000  # dry-up near pivot
    df = pd.DataFrame(
        {"open": base, "high": highs, "low": lows, "close": base, "volume": volumes},
        index=dates,
    )

    universe_data = {"RELIANCE": df}
    vcp_candidates = screen_vcp_setups(universe_data)
    assert isinstance(vcp_candidates, list)
