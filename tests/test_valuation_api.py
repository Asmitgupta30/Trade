"""Test for Valuation API endpoint."""

import pytest
from fastapi.testclient import TestClient
from backend.app import app


def test_get_cheaper_stocks_endpoint():
    client = TestClient(app)
    resp = client.get("/api/valuation/cheaper-stocks")
    assert resp.status_code == 200
    data = resp.json()

    assert "universe_metrics" in data
    assert "summary" in data
    assert isinstance(data["universe_metrics"], list)
    assert len(data["universe_metrics"]) > 0

    first = data["universe_metrics"][0]
    assert "symbol" in first
    assert "pe_current" in first
    assert "pe_3y_mean" in first
    assert "pe_zscore" in first
    assert "margin_of_safety_pct" in first
    assert "cheapness_category" in first
    assert "recommendation" in first


def test_get_news_catalysts_endpoint():
    client = TestClient(app)
    resp = client.get("/api/news/market-catalysts")
    assert resp.status_code == 200
    data = resp.json()
    assert "market_sentiment_score" in data
    assert "macro_news_regime" in data
    assert "catalysts" in data
    assert "symbol_sentiment_map" in data
