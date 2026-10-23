"""Preflight verification suite for Linux deployment and overnight model training."""

import os
import sys
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from common.procinfo import pid_alive, port_bound
from common.config import load_risk_config
from advisor.strategies import get_strategy_catalog, screen_all_strategies, calculate_position_size

ROOT = Path(__file__).resolve().parent.parent


def test_cross_platform_procinfo_primitives():
    """Liveness and port checks must work cross-platform without Windows-specific shells."""
    # Current process must be alive
    current_pid = os.getpid()
    assert pid_alive(current_pid) is True, f"pid_alive failed to identify current PID {current_pid}"

    # Non-existent PID must be False
    assert pid_alive(99999999) is False

    # None or 0 must be False
    assert pid_alive(None) is False
    assert pid_alive(0) is False

    # Socket port check must return a bool without throwing an exception
    is_bound = port_bound(59999)
    assert isinstance(is_bound, bool)


def test_strategy_catalog_integrity():
    """Strategy catalog must contain all 6 Indian market setups with complete schemas."""
    catalog = get_strategy_catalog()
    assert len(catalog) == 6
    ids = {s["id"] for s in catalog}
    expected_ids = {"intraday", "swing_vcp", "momentum_burst", "exhaustion_hammer", "dividend_pullback", "mutual_fund"}
    assert ids == expected_ids

    for strat in catalog:
        assert "name" in strat
        assert "horizon" in strat
        assert "description" in strat
        assert "risk_profile" in strat
        assert len(strat["key_rules"]) >= 4


def test_position_sizing_discipline():
    """Position sizing must strictly cap risk and single-stock capital allocation."""
    # Scenario A: Standard 1R trade
    plan = calculate_position_size(
        capital_inr=100000.0,
        risk_per_trade_pct=1.0,  # ₹1,000 max risk
        entry_price=1000.0,
        stop_loss_price=950.0,    # ₹50 risk/share
    )
    assert plan["shares"] == 20
    assert plan["risk_inr"] == 1000.0
    assert plan["position_value_inr"] == 20000.0
    assert plan["risk_pct"] == 5.0

    # Scenario B: High stop distance caps allocation
    plan_wide = calculate_position_size(
        capital_inr=50000.0,
        risk_per_trade_pct=1.0,  # ₹500 max risk
        entry_price=500.0,
        stop_loss_price=450.0,   # ₹50 risk/share (10% stop)
        max_position_pct=60.0,
    )
    assert plan_wide["shares"] == 10
    assert plan_wide["risk_inr"] == 500.0


def test_backend_api_full_preflight_cycle():
    """Verify backend endpoints are responsive and error-free."""
    with TestClient(app) as client:
        # Health & Feed
        h = client.get("/api/health")
        assert h.status_code == 200
        assert h.json()["advisory_only"] is True

        f = client.get("/api/feed/status")
        assert f.status_code == 200

        # Catalog
        cat = client.get("/api/strategies/catalog")
        assert cat.status_code == 200
        assert len(cat.json()["strategies"]) == 6

        # Suggestions
        sug = client.post("/api/suggestions", json={
            "budget_inr": 50000.0,
            "horizon": "intraday",
            "trading_style": "all",
            "risk_per_trade_pct": 1.0,
        })
        assert sug.status_code == 200
        sug_data = sug.json()
        assert "suggestions" in sug_data
        assert "strategy_plans" in sug_data

        # Screen
        scr = client.post("/api/strategies/screen", json={
            "budget_inr": 50000.0,
            "horizon": "intraday",
            "trading_style": "swing_vcp",
            "risk_per_trade_pct": 1.0,
        })
        assert scr.status_code == 200
        assert "plans" in scr.json()

        # Mutual funds
        mf = client.get("/api/mutual-funds")
        assert mf.status_code == 200
        assert "bands" in mf.json()


def test_train_model_script_runnable():
    """train_model.py script must execute cleanly without syntax or import errors."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "train_model.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0
    assert "--snapshot" in proc.stdout
