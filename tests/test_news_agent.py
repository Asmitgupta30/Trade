"""Unit tests for Market News Sentiment Engine (advisor/news_agent.py)."""

import pytest
from unittest.mock import patch, MagicMock

from advisor.news_agent import (
    MarketNewsAgent,
    NewsCatalyst,
    classify_financial_sentiment,
    extract_symbol_mentions,
)


def test_classify_financial_sentiment():
    # Positive catalysts: earnings beat, order win, upgrade, revenue surge
    headline_pos = "TCS reports 18% Q2 profit beat, announces major multi-billion cloud contract"
    score_pos, category_pos = classify_financial_sentiment(headline_pos)
    assert score_pos > 0.4
    assert category_pos in ("EARNINGS_SURPRISE", "CORPORATE_CATALYST")

    # Negative catalysts: regulatory probe, net loss, downgrade, tax raid
    headline_neg = "SEBI initiates regulatory probe against bank over compliance violations and fraud"
    score_neg, category_neg = classify_financial_sentiment(headline_neg)
    assert score_neg < -0.4
    assert category_neg in ("REGULATORY_LEGAL", "RATING_DOWNGRADE")

    # Neutral headline
    headline_neu = "NSE trading session concludes with normal volume distribution"
    score_neu, _ = classify_financial_sentiment(headline_neu)
    assert -0.2 <= score_neu <= 0.2


def test_extract_symbol_mentions():
    text = "State Bank of India (SBIN) surges 3% while Tata Steel (TATASTEEL) and Tech Mahindra (TECHM) decline."
    symbols = extract_symbol_mentions(text)
    assert "SBIN" in symbols
    assert "TATASTEEL" in symbols
    assert "TECHM" in symbols


def test_news_agent_offline_resilience(tmp_path):
    cache_file = tmp_path / "test_catalysts.json"
    agent = MarketNewsAgent(cache_path=cache_file)

    # Force network failure on RSS
    with patch("urllib.request.urlopen", side_effect=Exception("Network Unreachable")):
        result = agent.fetch_morning_catalysts(force_refresh=True)
        # Must not crash! Must return valid structured dictionary with fallback
        assert "market_sentiment_score" in result
        assert "catalysts" in result
        assert "symbol_sentiment_map" in result
        assert len(result["catalysts"]) > 0


def test_news_agent_sentiment_lookup():
    agent = MarketNewsAgent()
    agent._symbol_map = {"SBIN": 0.65, "TECHM": -0.50}

    assert agent.get_symbol_sentiment("SBIN") == 0.65
    assert agent.get_symbol_sentiment("TECHM") == -0.50
    assert agent.get_symbol_sentiment("UNKNOWN") == 0.0
