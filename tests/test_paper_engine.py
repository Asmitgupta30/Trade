"""Paper-trading engine tests on a temp SQLite store with synthetic outcomes."""

from datetime import datetime, timezone, timedelta

import pytest

from common.config import load_risk_config
from paper_trading.engine import PaperEngine
from paper_trading.store import PaperStore


@pytest.fixture()
def cfg():
    return load_risk_config()


@pytest.fixture()
def engine(tmp_path, cfg):
    return PaperEngine(cfg, store=PaperStore(db_path=tmp_path / "paper.sqlite3"))


def _suggestion_dict(symbol="TCS", qty=2, price=1000.0, action="BUY", conf=0.8):
    from datetime import timezone

    now = datetime.now(timezone.utc)
    return {
        "suggestion_id": f"sid-{symbol}-{now.timestamp()}",
        "symbol": symbol, "sector": "IT", "action": action,
        "amount_inr": qty * price, "est_quantity": qty,
        "confidence": conf, "rationale": "test", "est_cost_inr": 7.0,
        "baseline_comparison": "test", "reference_price": price,
        "created_ts": now.isoformat(), "blocked_by": [], "warnings": [],
    }


def test_suggestion_logging_opens_hypothetical_position(engine):
    n = engine.log_suggestion_set({
        "phase": "pre_market", "horizon": "intraday", "budget_inr": 10000,
        "suggestions": [_suggestion_dict()],
    })
    assert n == 1
    opens = engine.store.open_positions()
    assert len(opens) == 1
    assert opens[0]["symbol"] == "TCS"
    assert opens[0]["quantity"] == 2
    assert opens[0]["status"] == "open"


def test_close_positions_computes_return_net_of_cost(engine, cfg):
    engine.log_suggestion_set({
        "phase": "pre_market", "horizon": "intraday", "budget_inr": 10000,
        "suggestions": [_suggestion_dict(price=1000.0)],
    })
    closed = engine.close_intraday_positions({"TCS": 1020.0})
    assert closed and closed[0]["symbol"] == "TCS"
    # (20/1000)*100 - 0.35 cost = 1.65%
    assert closed[0]["return_pct"] == pytest.approx(1.65, abs=1e-6)
    assert engine.store.open_positions() == []


def test_actual_track_is_separate_from_hypothetical(engine):
    engine.log_suggestion_set({
        "phase": "pre_market", "horizon": "intraday", "budget_inr": 10000,
        "suggestions": [_suggestion_dict(symbol="INFY")],
    })
    engine.log_user_action("INFY", took_trade=True, quantity=3, price=1500.0,
                           note="took it manually")
    hyp_before = engine.hypothetical_summary()
    user = engine.user_summary()
    # hypothetical track untouched by user logging
    assert hyp_before["open_positions"] == 1
    assert user["trades_taken"] == 1


def test_declined_suggestion_recorded(engine):
    engine.log_user_action("WIPRO", took_trade=False)
    u = engine.user_summary()
    assert u["skipped"] == 1 and u["trades_taken"] == 0


def _backfill_outcomes(engine, days=30, per_day=2, win_rate=0.7, avg_win=1.2, avg_loss=-0.5):
    """Insert synthetic closed positions to exercise the gate math."""
    base = datetime.now(timezone.utc) - timedelta(days=days)
    i = 0
    for d in range(days):
        date = (base + timedelta(days=d)).strftime("%Y-%m-%d")
        for k in range(per_day):
            i += 1
            win = (i % 10) < int(win_rate * 10)
            ret = avg_win if win else avg_loss
            entry = 1000.0
            exit_px = entry * (1 + ret / 100 + 0.0035)   # pre-cost so realized matches
            pos_date = date
            pid = engine.store.open_paper_position(
                suggestion_id=f"synth-{i}", symbol=f"S{i}", quantity=1,
                entry_price=entry, entry_cost_inr=3.5,
                entry_dt=datetime.strptime(date, "%Y-%m-%d"), trade_date=pos_date)
            engine.store.conn.execute(
                "UPDATE paper_positions SET status='closed', exit_price=?, "
                "realized_return_pct=? WHERE position_id=?",
                (exit_px, ret, pid))
    engine.store.conn.commit()


def test_gate_math_and_not_live_ready_initially(engine):
    gs = engine.gate_status()
    assert gs.live_ready is False          # nothing tracked yet
    assert gs.criteria["suggestions_remaining"] == 40
    assert gs.criteria["days_remaining"] == 28


def test_gate_flips_only_when_criteria_met(engine):
    # 28+ days, 60 suggestions, strong win rate -> gate should pass quality+sample
    _backfill_outcomes(engine, days=32, per_day=2, win_rate=0.8, avg_win=1.4, avg_loss=-0.4)
    gs = engine.gate_status()
    assert gs.days_tracked >= 28
    assert gs.suggestions_tracked >= 40
    assert gs.hit_rate >= 0.5
    assert gs.live_ready is True, gs.criteria

    # fresh engine with bad track record must NOT be live-ready
    import tempfile
    from pathlib import Path
    from paper_trading.store import PaperStore
    with tempfile.TemporaryDirectory() as td:
        bad = PaperEngine(engine.cfg, store=PaperStore(
            db_path=Path(td) / "bad.sqlite3"))
        _backfill_outcomes(bad, days=32, per_day=2, win_rate=0.2, avg_win=1.0, avg_loss=-1.5)
        gs_bad = bad.gate_status()
        assert gs_bad.live_ready is False
        assert gs_bad.hit_rate < 0.5
        bad.store.close()   # release the sqlite handle before temp cleanup


def test_circuit_breaker_state_from_track(engine):
    st = engine.breaker_state()
    for key in ("entries_stopped_today", "weekly_cooloff_active",
                "consecutive_losing_days", "hard_pause_active"):
        assert key in st
    assert st["hard_pause_active"] is False   # empty track: no pause


def test_user_action_requires_price_when_took_trade(engine):
    with pytest.raises(ValueError):
        engine.log_user_action("SBIN", took_trade=True, quantity=1, price=None)

