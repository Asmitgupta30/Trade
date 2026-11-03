"""Unit tests for Autonomous Trading Desk Turnaround & Risk Governance Upgrades."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from scripts.autonomous_trading_desk import AutonomousTradingDesk


def test_desk_daily_trade_cap():
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    desk.max_trades_today = 4
    desk.executed_today = [
        {"symbol": "SBIN"}, {"symbol": "TCS"}, {"symbol": "BAJFINANCE"}, {"symbol": "TECHM"}
    ]
    # Max trades reached
    assert len(desk.executed_today) >= desk.max_trades_today
    # Desk should refuse to scan for new entries
    assert desk.can_enter_new_trade() is False


def test_desk_single_symbol_daily_limit():
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    desk.traded_symbols_today = {"SBIN", "BAJFINANCE"}

    assert desk.is_symbol_eligible_today("SBIN") is False
    assert desk.is_symbol_eligible_today("BAJFINANCE") is False
    assert desk.is_symbol_eligible_today("TCS") is True


def test_minimum_target_distance_filter():
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)

    # Entry 1000, Target 1008 -> 0.8% distance (too small, eaten by 0.35% friction) -> REJECT
    setup_tight = {"symbol": "SBIN", "entry_price": 1000.0, "target_1": 1008.0, "stop_loss": 995.0, "action": "BUY"}
    assert desk.validate_setup_expectancy(setup_tight) is False

    # Entry 1000, Target 1025 -> 2.5% distance -> PASS
    setup_wide = {"symbol": "SBIN", "entry_price": 1000.0, "target_1": 1025.0, "stop_loss": 990.0, "action": "BUY"}
    assert desk.validate_setup_expectancy(setup_wide) is True


def test_news_sentiment_gating():
    desk = AutonomousTradingDesk(budget_inr=50000.0, risk_pct=1.0)
    
    # Severe negative news catalyst -> Disqualify Long
    setup_long = {"symbol": "TECHM", "action": "BUY"}
    assert desk.validate_news_sentiment(setup_long, sentiment_score=-0.45) is False

    # Neutral or positive news -> Allow Long
    assert desk.validate_news_sentiment(setup_long, sentiment_score=0.10) is True
    assert desk.validate_news_sentiment(setup_long, sentiment_score=0.55) is True

    # Symmetrical Short: Positive news -> Disqualify Short
    setup_short = {"symbol": "TECHM", "action": "SHORT"}
    assert desk.validate_news_sentiment(setup_short, sentiment_score=0.45) is False
    # Negative news -> Allow Short
    assert desk.validate_news_sentiment(setup_short, sentiment_score=-0.40) is True
