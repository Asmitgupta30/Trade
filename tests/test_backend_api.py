"""Backend API contract tests (TestClient, no live network needed)."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def api():
    from backend.app import app

    with TestClient(app) as c:
        yield c


def test_health_advisory_flag(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["advisory_only"] is True


def test_suggestions_endpoint_contract(api):
    r = api.post("/api/suggestions", json={"budget_inr": 5000, "horizon": "intraday"})
    assert r.status_code == 200
    d = r.json()
    for key in ("suggestions", "system_notes", "budget_inr", "phase",
                "generated_at", "cash_left_inr"):
        assert key in d
    for s in d["suggestions"]:
        assert s["action"] in ("BUY", "STRONG_BUY", "HOLD", "EXIT", "NO_ACTION")
        # hard lock fields present on every card
        if s["action"] != "NO_ACTION":
            assert s["rationale"] and s["baseline_comparison"] is not None
            assert 0 <= s["confidence"] <= 1


def test_mf_horizon_rejected_on_equity_endpoint(api):
    r = api.post("/api/suggestions", json={"budget_inr": 5000, "horizon": "mutual_fund"})
    assert r.status_code == 400


def test_track_record_shape(api):
    r = api.get("/api/track-record")
    assert r.status_code == 200
    d = r.json()
    assert d["badge"] in ("NOT YET LIVE-READY", "LIVE-READY")
    assert {"hypothetical", "actual", "gate"} <= set(d)
    g = d["gate"]
    assert g["criteria"]["min_paper_days"] == 28
    assert g["criteria"]["min_suggestions"] == 40


def test_market_pulse(api):
    r = api.get("/api/market-pulse")
    assert r.status_code == 200
    d = r.json()
    assert d["advisory_only"] is True
    assert "breaker" in d and "regime" in d


def test_user_action_logging_and_validation(api):
    r = api.post("/api/suggestions/action", json={
        "symbol": "TESTSYM", "took_trade": False})
    assert r.status_code == 200
    # took_trade without price -> 422
    r2 = api.post("/api/suggestions/action", json={
        "symbol": "TESTSYM", "took_trade": True})
    assert r2.status_code == 422


def test_no_order_endpoint_exists(api):
    """Structural guarantee: no execution route exists on this API."""
    paths = {route.path for route in api.app.routes}
    forbidden = [p for p in paths if any(
        w in p.lower() for w in ("order", "execute", "trade/place", "buy", "sell"))]
    assert not forbidden, f"execution-like routes found: {forbidden}"


def test_index_served(api):
    r = api.get("/")
    assert r.status_code == 200
    assert b"ADVISORY" in r.content or b"Advisory" in r.content or b"advisor" in r.content.lower()
