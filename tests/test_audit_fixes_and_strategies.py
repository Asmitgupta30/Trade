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


def test_paper_store_zero_entry_price_protection(tmp_path):
    """PaperStore must not raise ZeroDivisionError if entry_price is zero or invalid."""
    store = PaperStore(db_path=tmp_path / "zero_price.sqlite3")
    pos_id = store.open_paper_position(
        suggestion_id="test_zero_1",
        symbol="TESTSYM",
        quantity=10,
        entry_price=0.0,
        entry_cost_inr=0.0,
        entry_dt=datetime.now(timezone.utc),
        trade_date="2026-11-05",
        side="BUY",
    )
    ret = store.close_paper_position(pos_id, exit_price=100.0, exit_dt=datetime.now(timezone.utc), round_trip_cost_pct=0.35)
    assert ret == 0.0


def test_desk_midnight_rollover(tmp_path, monkeypatch):
    """Desk must reset daily counters, trade caps, and pre-market flags when calendar date rolls over."""
    from scripts.autonomous_trading_desk import AutonomousTradingDesk
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    desk.current_trade_date = "2026-11-04"  # Yesterday
    desk.pre_market_done = True
    desk.eod_report_generated = True
    desk.executed_today = [{"symbol": "SBIN"}, {"symbol": "TCS"}, {"symbol": "INFY"}, {"symbol": "RELIANCE"}]
    desk.traded_symbols_today = {"SBIN", "TCS", "INFY", "RELIANCE"}
    desk.session_realized_loss_inr = 1500.0
    desk.symbol_cooldowns = {"SBIN": datetime.now(timezone.utc) + timedelta(minutes=30)}

    # Advance time to 08:30 IST today
    from common.timeutils import IST
    fixed_now = datetime(2026, 11, 5, 8, 30, tzinfo=IST)
    monkeypatch.setattr("scripts.autonomous_trading_desk.now_ist", lambda: fixed_now)

    desk.run_session_step()

    assert desk.current_trade_date == "2026-11-05"
    assert desk.pre_market_done is False
    assert desk.eod_report_generated is False
    assert len(desk.executed_today) == 0
    assert len(desk.traded_symbols_today) == 0
    assert desk.session_realized_loss_inr == 0.0
    assert len(desk.symbol_cooldowns) == 0


def test_auto_exit_resilient_to_quote_failure(monkeypatch):
    """auto_exit_all_intraday must gracefully exit positions even during full network quote failure."""
    from scripts.autonomous_trading_desk import AutonomousTradingDesk
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    
    # Mock open position in store
    pos_id = desk.store.open_paper_position(
        suggestion_id="test_mock_open",
        symbol="MOCKSYM",
        quantity=10,
        entry_price=500.0,
        entry_cost_inr=1.5,
        entry_dt=datetime.now(timezone.utc),
        trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        side="BUY",
    )
    desk.trade_meta["MOCKSYM"] = {"stop_loss": 490.0, "target_1": 520.0}

    # Simulate network quote crash
    def mock_fail_quotes(symbols):
        raise ConnectionResetError("Yahoo Finance network drop at 15:15 IST")
    monkeypatch.setattr(desk.client, "get_quotes", mock_fail_quotes)

    # Should NOT throw exception
    desk.auto_exit_all_intraday()

    # Position in store must now be closed
    closed_pos = [p for p in desk.store.all_positions() if p["position_id"] == pos_id]
    assert len(closed_pos) == 1
    assert closed_pos[0]["status"] == "closed"
    assert "MOCKSYM" not in desk.trade_meta
