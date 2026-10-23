"""Scheduler + check-in runner tests (no market hours dependency)."""

from datetime import datetime

import pytest

from backend.scheduler import CheckInRunner, build_scheduler
from common.config import load_risk_config
from paper_trading.engine import PaperEngine
from paper_trading.store import PaperStore


class FakeAdvisor:
    def generate(self, budget_inr, phase="pre_market", state=None, data_is_current=True):
        from advisor.rules import PortfolioState
        from advisor.suggestion import Suggestion, SuggestionSet
        from common.timeutils import now_ist

        s = Suggestion(symbol="TCS", sector="IT", action="BUY", amount_inr=2000,
                       confidence=0.8, rationale="test rationale for the card",
                       est_cost_inr=7.0, baseline_comparison="vs nifty",
                       est_quantity=1, reference_price=3000.0)
        out = SuggestionSet(budget_inr=budget_inr, horizon="intraday",
                            phase=phase, generated_at=now_ist(),
                            suggestions=[s], system_notes=["note"], cash_left_inr=0)
        return out


class FakeClient:
    provider_name = "fake"

    def get_quotes(self, symbols):
        from common.types import Quote
        from datetime import datetime, timezone

        return {s: Quote(symbol=s, ltp=101.0, ts=datetime.now(timezone.utc))
                for s in symbols}

    def stream(self, *a, **k):  # pragma: no cover
        pass


@pytest.fixture()
def runner(tmp_path):
    cfg = load_risk_config()
    store = PaperStore(db_path=tmp_path / "p.sqlite3")
    paper = PaperEngine(cfg, store=store)
    # one open position so midday/pre-close have something to review
    paper.log_suggestion_set({
        "phase": "pre_market", "horizon": "intraday", "budget_inr": 5000,
        "suggestions": [{
            "suggestion_id": "t1", "symbol": "TCS", "sector": "IT",
            "action": "BUY", "amount_inr": 4000, "est_quantity": 1,
            "confidence": 0.9, "rationale": "r", "est_cost_inr": 14,
            "baseline_comparison": "b", "reference_price": 100.0,
            "created_ts": datetime.now().isoformat(), "blocked_by": [], "warnings": [],
        }],
    })
    return CheckInRunner(cfg, FakeAdvisor(), paper, FakeClient())


def test_all_four_checkins_produce_output(runner):
    pm = runner.pre_market(budget_inr=5000)
    assert pm["phase"] == "pre_market"
    assert any(s["action"] == "BUY" for s in pm["suggestions"])

    md = runner.midday_check()
    assert md["phase"] == "midday_check"
    assert len(md["suggestions"]) == 1
    assert md["suggestions"][0]["current_price"] == pytest.approx(101.0)
    assert md["suggestions"][0]["change_pct"] == pytest.approx(1.0)

    pc = runner.pre_close()
    assert pc["phase"] == "pre_close"
    assert all(s["action"] == "EXIT" for s in pc["suggestions"])
    assert "close" in pc["system_notes"][0].lower()

    eod = runner.end_of_day_summary()
    assert eod["phase"] == "end_of_day_summary"
    assert len(eod["closed_today"]) == 1
    assert eod["hypothetical_summary"]["open_positions"] == 0
    assert runner.paper.gate_status().suggestions_tracked == 1


def test_persisted_for_api_restart(runner):
    runner.midday_check()
    evs = [e for e in runner.paper.store.events(limit=50) if e["kind"] == "checkin"]
    assert evs and evs[0]["payload"]["phase"] == "midday_check"


def test_scheduler_builds_with_config_times(runner):
    sched = build_scheduler(runner)
    jobs = {j.id: str(j.trigger) for j in sched.get_jobs()}
    assert set(jobs) >= {"pre_market", "midday_check", "pre_close",
                         "end_of_day_summary", "weekly_challenger_retrain"}
    assert "'9'" in jobs["pre_market"] and "'0'" in jobs["pre_market"]
    assert "'12'" in jobs["midday_check"] and "'30'" in jobs["midday_check"]
    assert "'15'" in jobs["pre_close"] and "'45'" in jobs["end_of_day_summary"]
    assert "sat" in jobs["weekly_challenger_retrain"]
