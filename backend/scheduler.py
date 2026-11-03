"""Scheduled check-in cadence (risk_config.suggestion_schedule).

Runs inside the long-lived process started by scripts/run_system.py:
  09:00 pre_market        — watchlist + budget allocation plan
  12:30 midday_check      — re-evaluate open ideas vs live movement
  15:00 pre_close         — close-out advice for intraday positions
  15:45 end_of_day_summary— log outcomes to the paper track record

Each check-in's output is persisted (system_events.kind='checkin') so the
backend can serve /api/checkins without recomputation.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from advisor.rules import PortfolioState
from common.config import RiskConfig
from common.timeutils import now_ist
from paper_trading.engine import PaperEngine

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


class CheckInRunner:
    def __init__(self, cfg: RiskConfig, advisor_engine, paper: PaperEngine,
                 data_client):
        self.cfg = cfg
        self.advisor = advisor_engine
        self.paper = paper
        self.client = data_client
        self.latest: dict[str, dict] = {}

    # ------------------------------------------------------------------
    def _persist(self, phase: str, payload: dict) -> None:
        payload["phase"] = phase
        payload["generated_at"] = datetime.now(timezone.utc).isoformat()
        self.latest[phase] = payload
        self.paper.store.log_event("checkin", payload)

    def _quotes_for(self, symbols: list[str]) -> dict[str, float]:
        try:
            qs = self.client.get_quotes(symbols)
            return {s: q.ltp for s, q in qs.items()}
        except Exception as exc:
            log.warning("quote fetch failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    def pre_market(self, budget_inr: float | None = None) -> dict:
        budget = budget_inr or self._last_budget()
        out = self.advisor.generate(budget_inr=budget, phase="pre_market",
                                    state=PortfolioState(),
                                    data_is_current=True)
        d = out.to_dict()
        self._persist("pre_market", d)
        return d

    def midday_check(self) -> dict:
        """Re-evaluate every OPEN hypothetical position against the live price."""
        opens = self.paper.store.open_positions()
        prices = self._quotes_for(sorted({p["symbol"] for p in opens})) if opens else {}
        items = []
        for p in opens:
            px = prices.get(p["symbol"], p["entry_price"])
            chg = (px - p["entry_price"]) / p["entry_price"] * 100.0
            action = "HOLD" if chg > -0.5 else "EXIT"
            rationale = (
                f"Entry ₹{p['entry_price']:.2f} → now ₹{px:.2f} ({chg:+.2f}%). "
                + ("Position is working; holding." if action == "HOLD" else
                   "Position is under water beyond noise threshold — consider exiting."))
            items.append({
                "symbol": p["symbol"], "action": action,
                "entry_price": p["entry_price"], "current_price": round(px, 2),
                "change_pct": round(chg, 2), "quantity": p["quantity"],
                "rationale": rationale,
            })
        payload = {
            "budget_inr": self._last_budget(), "horizon": "intraday",
            "suggestions": items,
            "system_notes": [
                f"MIDDAY RE-CHECK: evaluated {len(items)} open idea(s) against live prices."
            ] if items else ["MIDDAY RE-CHECK: no open ideas to re-evaluate."],
            "cash_left_inr": None,
        }
        self._persist("midday_check", payload)
        return payload

    def pre_close(self) -> dict:
        """risk_config purpose: avoid unplanned overnight exposure."""
        opens = self.paper.store.open_positions()
        prices = self._quotes_for(sorted({p["symbol"] for p in opens})) if opens else {}
        items = []
        for p in opens:
            px = prices.get(p["symbol"], p["entry_price"])
            chg = (px - p["entry_price"]) / p["entry_price"] * 100.0
            items.append({
                "symbol": p["symbol"], "action": "EXIT",
                "entry_price": p["entry_price"], "current_price": round(px, 2),
                "change_pct": round(chg, 2),
                "rationale": (
                    f"Intraday discipline: market closes at 15:30 IST. You are "
                    f"{chg:+.2f}% on this idea. Unless you explicitly want overnight "
                    "exposure, close it in your broker app."),
            })
        payload = {
            "suggestions": items,
            "system_notes": [
                f"PRE-CLOSE: {'close-out advised for ' + str(len(items)) + ' open position(s).' if items else 'No open intraday positions.'}",
            ],
            "cash_left_inr": None,
        }
        self._persist("pre_close", payload)
        return payload

    def end_of_day_summary(self) -> dict:
        """Close hypothetical positions at last price; record honest outcomes."""
        opens = self.paper.store.open_positions()
        prices = self._quotes_for(sorted({p["symbol"] for p in opens})) if opens else {}
        closed = self.paper.close_intraday_positions(prices)
        hyp = self.paper.hypothetical_summary()
        gate = self.paper.gate_status()

        # STOP-condition #5: reaching live-ready is an immediate, loud report —
        # never folded quietly into routine output.
        alert_path = ROOT / "var" / "status" / "LIVE_READY_ALERT.json"
        if gate.live_ready and not alert_path.exists():
            alert_path.parent.mkdir(parents=True, exist_ok=True)
            alert_path.write_text(json.dumps({
                "event": "LIVE_READY_GATE_REACHED",
                "gate": gate.to_dict(),
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "action_required": "Human decision only. The system takes no action.",
            }, indent=2), encoding="utf-8")
            self.paper.store.log_event("gate_flip", {"live_ready": True})
            log.warning("LIVE-READY GATE REACHED — alert written to %s", alert_path)

        payload = {
            "suggestions": [],
            "closed_today": closed,
            "hypothetical_summary": hyp,
            "gate": gate.to_dict(),
            "system_notes": [
                f"EOD SUMMARY: closed {len(closed)} position(s); "
                f"hit rate so far {hyp.get('hit_rate')}; "
                f"live-ready gate at {gate.days_tracked}/{self.cfg.paper_gate.min_days} days, "
                f"{gate.suggestions_tracked}/{self.cfg.paper_gate.min_suggestions} suggestions.",
            ],
        }
        self._persist("end_of_day_summary", payload)
        return payload

    # ------------------------------------------------------------------
    def _last_budget(self) -> float:
        rows = self.paper.store.conn.execute(
            "SELECT budget_inr FROM suggestions ORDER BY created_ts DESC LIMIT 1"
        ).fetchone()
        val = float(rows["budget_inr"]) if rows and rows["budget_inr"] else 2000.0
        return max(val, 500.0)


def build_scheduler(runner: CheckInRunner):
    """APScheduler with IST cron times straight from risk_config."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BackgroundScheduler(timezone="Asia/Kolkata")
    times = {name: rule.time_ist for name, rule in runner.cfg.schedule.items()}
    h, m = times["pre_market"].split(":")
    sched.add_job(lambda: _safe(lambda: runner.pre_market()),
                  CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                  id="pre_market", replace_existing=True)
    h, m = times["midday_check"].split(":")
    sched.add_job(lambda: _safe(runner.midday_check),
                  CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                  id="midday_check", replace_existing=True)
    h, m = times["pre_close"].split(":")
    sched.add_job(lambda: _safe(runner.pre_close),
                  CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                  id="pre_close", replace_existing=True)
    h, m = times["end_of_day_summary"].split(":")
    sched.add_job(lambda: _safe(runner.end_of_day_summary),
                  CronTrigger(hour=int(h), minute=int(m), day_of_week="mon-fri"),
                  id="end_of_day_summary", replace_existing=True)

    # weekly governed retrain: challenger trained + evaluated + logged
    # (promotion is decided by models/challenger's hard gate, never automatic
    # beyond that gate; risk_config thresholds are read-only)
    sched.add_job(lambda: _safe(_weekly_retrain),
                  CronTrigger(day_of_week="sat", hour=6, minute=0),
                  id="weekly_challenger_retrain", replace_existing=True)

    # weekly human-readable digest (Section G) — Friday after market close
    sched.add_job(lambda: _safe(_weekly_digest),
                  CronTrigger(day_of_week="fri", hour=16, minute=15),
                  id="weekly_digest", replace_existing=True)

    # daily pre-market data refresh (bhavcopy tail -> factors -> deep snapshot)
    # so the 09:00 check-in scores on freshest adjusted prices, not stale ones
    sched.add_job(lambda: _safe(_daily_refresh),
                  CronTrigger(day_of_week="mon-fri", hour=8, minute=15),
                  id="daily_data_refresh", replace_existing=True)
    return sched


def _daily_refresh():
    import subprocess

    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "daily_refresh.py")],
                       capture_output=True, text=True, cwd=str(ROOT))
    log.info("daily refresh: rc=%s %s", r.returncode,
             (r.stdout or r.stderr).strip()[-200:])


def _weekly_retrain():
    import pandas as pd

    from common.config import load_risk_config
    from models.challenger import run_challenger_cycle

    cfg = load_risk_config()
    snap = ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
    if not snap.exists():
        snap = ROOT / "var" / "cache" / "features_snapshot.parquet"
        if not snap.exists():
            log.warning("weekly retrain skipped: no feature snapshot")
            return
    dataset = pd.read_parquet(snap)
    decision = run_challenger_cycle(dataset, cfg, use_hpo=True, n_hpo_trials=10)
    log.info("weekly retrain: promoted=%s", decision.promoted)


def _weekly_digest():
    import subprocess

    for script in ("weekly_digest.py", "final_decision_report.py"):
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / script),
                            "--force"], capture_output=True, text=True,
                           cwd=str(ROOT))
        log.info("%s: rc=%s %s", script, r.returncode,
                 (r.stdout or r.stderr).strip()[:150])


def _safe(fn):
    try:
        return fn()
    except Exception:
        log.exception("scheduled job failed")
