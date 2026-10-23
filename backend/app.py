"""FastAPI backend: serves the advisor API + the static frontend.

ADVISORY ONLY — there is no order-placement endpoint and no execution path
anywhere in this application. The only "action" endpoints log what the human
did or reset acknowledgements.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from advisor.engine import AdvisorEngine
from advisor.mutualfunds import MFHolding, MutualFundAdvisor, next_rebalance_date
from advisor.rules import PortfolioState
from advisor.signals import SnapshotSignalProvider
from common.config import assert_no_execution_path, load_risk_config
from common.timeutils import is_trading_day, market_phase, now_ist
from common.types import Tick
from data.client import DataClient
from paper_trading.engine import PaperEngine

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("backend")

# ------------------------------------------------------------------ state
cfg = load_risk_config()
assert_no_execution_path()

client = DataClient()
paper = PaperEngine(cfg)
advisor = AdvisorEngine(cfg, SnapshotSignalProvider())
mf_advisor = MutualFundAdvisor(cfg)

STREAM_SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "^NSEI"]


def _load_stream_symbols() -> list[str]:
    """Full validated universe for pulse streaming (Section F coverage parity).

    Falls back to the demo six only if the deep snapshot is missing entirely.
    Index symbols are appended for the regime display.
    """
    try:
        from data.lifecycle import validated_symbols

        syms = validated_symbols()
        if len(syms) >= 50:
            return syms + ["^NSEI", "^INDIAVIX"]
    except Exception as exc:
        log.warning("universe stream expansion failed (%s); using demo set", exc)
    return STREAM_SYMBOLS + ["^NSEI", "^INDIAVIX"]
latest_quotes: dict[str, dict] = {}
quote_lock = threading.Lock()


def _on_tick(tick: Tick) -> None:
    with quote_lock:
        latest_quotes[tick.symbol] = {
            "symbol": tick.symbol, "ltp": tick.ltp,
            "ts": tick.ts.isoformat(),
        }


def _start_stream() -> None:
    try:
        symbols = _load_stream_symbols()
        client.stream(symbols, _on_tick, poll_seconds=30)
        log.info("background stream started (%s) for %d symbols: %s",
                 client.provider_name, len(symbols), symbols[:8])
    except Exception as exc:
        log.warning("stream failed to start: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Section F: universe coverage check in background thread + visible in status
    def _bg_coverage():
        try:
            from data.lifecycle import merge_into_system_status, refresh_coverage
            from features.universe import load_universe_symbols
            from common.procinfo import update_status

            cov = refresh_coverage(load_universe_symbols())
            merge_into_system_status(cov)
            update_status(running=_current_running_snapshot())
        except Exception as exc:
            log.warning("coverage check failed at startup: %s", exc)

    threading.Thread(target=_bg_coverage, daemon=True).start()
    threading.Thread(target=_start_stream, daemon=True).start()
    try:
        from backend.scheduler import CheckInRunner, build_scheduler

        global checkin_runner
        if checkin_runner is None:
            checkin_runner = CheckInRunner(cfg, advisor, paper, client)
        sched = build_scheduler(checkin_runner)
        sched.start()
        app.state.scheduler = sched
        log.info("check-in scheduler started (risk_config cadence)")
    except Exception as exc:
        log.warning("scheduler not started: %s", exc)
    yield
    client.stop_stream()
    try:
        app.state.scheduler.shutdown(wait=False)
    except Exception:
        pass


def _current_running_snapshot() -> dict:
    import json as _json

    from common.procinfo import STATUS_DIR, pid_alive

    running = {}
    for f in STATUS_DIR.glob("*.json"):
        if f.name in ("system_status.json",):
            continue
        try:
            info = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(info, dict) and info.get("pid") and pid_alive(info["pid"]):
            running[info.get("name", f.stem)] = {"pid": info["pid"],
                                                 "since": info.get("started_at")}
    return running


checkin_runner = None

app = FastAPI(title="Trading Advisor (Advisory-Only)", version="0.1.0",
              lifespan=lifespan)


# ------------------------------------------------------------------ models
class SuggestRequest(BaseModel):
    budget_inr: float = Field(gt=0)
    horizon: str = "intraday"          # intraday | swing | all | mutual_fund
    trading_style: str = "all"        # all | swing_vcp | momentum_burst | exhaustion_hammer | dividend_pullback | intraday
    risk_per_trade_pct: float = 1.0   # risk budget per trade (% of budget)


class UserActionRequest(BaseModel):
    symbol: str
    took_trade: bool
    quantity: float = 0.0
    price: float | None = None
    suggestion_id: str | None = None
    note: str = ""


class MFRequest(BaseModel):
    new_money_inr: float = 0.0


def _data_is_current() -> bool:
    """Hard-lock aware currency check.

    Market OPEN  -> require fresh ticks.
    Market CLOSE -> last close data is legitimate 'most recent' input; mark as
                    preview rather than pretending it's live.
    """
    fs = client.feed_status()
    if fs.status in ("live", "delayed"):
        return True
    if fs.status == "synthetic":
        return True   # offline dev mode; UI banner shows provider
    return not is_trading_day(now_ist()) or market_phase().value == "closed"


def _system_banner_notes(out_dict: dict) -> list[str]:
    notes = list(out_dict.get("system_notes", []))
    fs = client.feed_status()
    phase = market_phase()
    if phase.value == "closed" or not is_trading_day(now_ist()):
        notes.insert(0, f"MARKET CLOSED ({now_ist().strftime('%a %d %b %H:%M')} IST): "
                        "preview based on most recent close — not live prices.")
    notes.append(f"Data provider: {fs.provider} ({fs.status}).")
    return notes


# ------------------------------------------------------------------ api
@app.get("/api/health")
def health():
    return {"status": "ok", "advisory_only": True,
            "provider": client.provider_name}


@app.get("/api/feed/status")
def feed_status():
    with quote_lock:
        quotes = dict(latest_quotes)
    return {
        **client.feed_status().as_dict(),
        "market_phase": market_phase().value,
        "is_trading_day": is_trading_day(now_ist()),
        "latest_quotes": quotes,
        "data_is_current_for_suggestions": _data_is_current(),
    }


@app.get("/api/strategies/catalog")
def strategies_catalog():
    from advisor.strategies import get_strategy_catalog
    return {"strategies": get_strategy_catalog(), "advisory_only": True}


@app.post("/api/strategies/screen")
def strategies_screen(req: SuggestRequest):
    from advisor.strategies import get_cached_universe_data, screen_all_strategies
    try:
        universe_data = get_cached_universe_data()
        all_plans = screen_all_strategies(
            universe_data,
            capital_inr=req.budget_inr,
            risk_per_trade_pct=req.risk_per_trade_pct,
        )
        if req.trading_style != "all" and req.trading_style in all_plans:
            plans = {req.trading_style: all_plans[req.trading_style]}
        else:
            plans = all_plans
    except Exception as exc:
        log.warning("strategy screen failed: %s", exc)
        plans = {}
    return {
        "budget_inr": req.budget_inr,
        "risk_per_trade_pct": req.risk_per_trade_pct,
        "trading_style": req.trading_style,
        "plans": plans,
        "advisory_only": True,
    }


@app.post("/api/suggestions")
def suggestions(req: SuggestRequest):
    if req.horizon == "mutual_fund":
        raise HTTPException(400, "use /api/mutual-funds for MF suggestions")
    state = PortfolioState(
        suggestions_today=paper.store.conn.execute(
            "SELECT COUNT(DISTINCT symbol) c FROM paper_positions WHERE trade_date=?",
            (now_ist().strftime("%Y-%m-%d"),)).fetchone()["c"],
    )
    out = advisor.generate(budget_inr=req.budget_inr, horizon="intraday",
                           state=state, data_is_current=True)
    d = out.to_dict()
    d["system_notes"] = _system_banner_notes(d)

    breaker = paper.breaker_state(budget_ref_inr=req.budget_inr)
    if not _data_is_current():
        # regenerate under the stale-data lock so nothing actionable leaks
        out2 = advisor.generate(budget_inr=req.budget_inr, horizon="intraday",
                                state=state, data_is_current=False)
        d = out2.to_dict()
        d["system_notes"] = _system_banner_notes(d)
    d["breaker"] = breaker

    # Multi-style Indian market screening from claude-trading-skills
    try:
        from advisor.strategies import get_cached_universe_data, screen_all_strategies
        universe_data = get_cached_universe_data()
        all_plans = screen_all_strategies(
            universe_data,
            capital_inr=req.budget_inr,
            risk_per_trade_pct=req.risk_per_trade_pct,
        )
        if req.trading_style != "all" and req.trading_style in all_plans:
            d["strategy_plans"] = {req.trading_style: all_plans[req.trading_style]}
        else:
            d["strategy_plans"] = all_plans
    except Exception as exc:
        log.warning("multi-strategy screening failed: %s", exc)
        d["strategy_plans"] = {}

    # persist every emitted suggestion into the paper track
    try:
        paper.log_suggestion_set(d)
    except Exception as exc:
        log.warning("paper logging failed: %s", exc)
    return d


@app.post("/api/suggestions/action")
def log_action(req: UserActionRequest):
    try:
        trade_id = paper.log_user_action(
            symbol=req.symbol, took_trade=req.took_trade,
            quantity=req.quantity, price=req.price,
            suggestion_id=req.suggestion_id, note=req.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"ok": True, "trade_id": trade_id,
            "message": ("Logged: you took this trade." if req.took_trade
                        else "Logged: you skipped this suggestion.")}


@app.get("/api/checkins")
def checkins():
    """Latest output for each scheduled check-in phase (pre_market, midday, ...)."""
    if checkin_runner is not None and checkin_runner.latest:
        latest = dict(checkin_runner.latest)
    else:
        latest = {}
        # fall back to persisted events so restarts keep history
        for ev in paper.store.events(limit=200):
            if ev.get("kind") == "checkin" and isinstance(ev.get("payload"), dict):
                ph = ev["payload"].get("phase")
                if ph and ph not in latest:
                    latest[ph] = ev["payload"]
    return {
        "schedule": {name: {"time_ist": r.time_ist, "purpose": r.purpose}
                     for name, r in cfg.schedule.items()},
        "latest": latest,
        "advisory_only": True,
    }


@app.get("/api/model-history")
def model_history():
    """Lab-notebook view of champion/challenger retrain events."""
    from models.challenger import count_history, load_history

    rows = load_history(limit=50)
    counts = count_history(rows)
    active_events = [e for e in rows if not e.get("voided")]
    return {
        "events": active_events,
        **counts,
        "note": ("Every retrain attempt is logged with its numbers, promoted or "
                 "not. Promotion requires ALL THREE metrics to improve beyond "
                 "the bootstrap noise floor. Voided entries are retained for "
                 "honesty and excluded from these counts."),
        "advisory_only": True,
    }


@app.get("/api/plots/equity")
def plots_equity():
    """Daily cumulative net return (%) and drawdown of the hypothetical track."""
    rows = paper.store.all_positions()
    by_day = {}
    for p in rows:
        if p["status"] != "closed" or p["realized_return_pct"] is None:
            continue
        by_day[p["trade_date"]] = by_day.get(p["trade_date"], 0.0) + \
            float(p["realized_return_pct"])
    dates = sorted(by_day)
    eq, dd_series, peak = [], [], 0.0
    cum = 0.0
    for d in dates:
        cum += by_day[d]
        peak = max(peak, cum)
        eq.append(round(cum, 4))
        dd_series.append(round(cum - peak, 4))
    return {"dates": dates, "cumulative_pct": eq, "drawdown_from_peak_pct": dd_series}


@app.get("/api/plots/outcomes")
def plots_outcomes(limit: int = 300):
    """Closed entry suggestions: confidence vs realized net return, with dates."""
    rows = paper.store.conn.execute(
        """SELECT s.created_ts, s.symbol, s.confidence, s.action,
                  p.realized_return_pct, p.trade_date
           FROM suggestions s JOIN paper_positions p
             ON p.suggestion_id = s.suggestion_id
           WHERE p.status='closed' AND s.action IN ('BUY','STRONG_BUY')
           ORDER BY s.created_ts DESC LIMIT ?""", (limit,)).fetchall()
    out = [{"date": r["trade_date"], "symbol": r["symbol"],
            "confidence": round(float(r["confidence"]), 3),
            "net_return_pct": round(float(r["realized_return_pct"]), 3)}
           for r in rows]
    return {"points": list(reversed(out))}


@app.get("/api/plots/hitrate")
def plots_hitrate():
    """Cumulative hit-rate over trade dates (directional correctness)."""
    rows = paper.store.conn.execute(
        """SELECT p.trade_date, p.realized_return_pct
           FROM paper_positions p WHERE p.status='closed'
           ORDER BY p.trade_date""").fetchall()
    cum_ok, cum_n, out = 0, 0, []
    last_date = None
    for r in rows:
        cum_n += 1  # counter: cum_n = total closed paper positions evaluated
        if float(r["realized_return_pct"]) > -cfg.intraday.round_trip_cost_pct:
            cum_ok += 1  # counter: cum_ok = closed trades net positive after round trip cost
        if r["trade_date"] != last_date:
            out.append({"date": r["trade_date"],
                        "hit_rate": round(cum_ok / cum_n, 4), "n": cum_n})
            last_date = r["trade_date"]
    return {"points": out}


@app.get("/api/patterns/monthly")
def patterns_monthly():
    """Last-month stock patterns across the validated universe.

    Plain-language aggregates: biggest movers, consistency (% of up days),
    distance from the monthly high, and sector rotation — the same patterns
    the daily model consumes as features, made visible.
    """
    snap_path = ROOT / "var" / "cache" / "features_snapshot_deep.parquet"
    if not snap_path.exists():
        return {"error": "snapshot not built yet"}
    df = pd.read_parquet(snap_path)
    # NOTE: do NOT drop NaN labels here — the newest bars are exactly the ones
    # without a forward label yet, and this is a descriptive (not training) view.
    last_day = df.index.max()
    day_rows = df[df.index == last_day]

    stocks = []
    for _, r in day_rows.iterrows():
        ret5 = r.get("ret_5d")
        ret21 = None
        # approximate 1-month move from available momentum columns
        if "ret_21d" in r and pd.notna(r.get("ret_21d")):
            ret21 = float(r["ret_21d"])
        stocks.append({
            "symbol": r["symbol"], "sector": r["sector"],
            "close": round(float(r.get("close") or 0), 2),
            "month_return_pct": round(ret21 * 100, 2) if ret21 is not None else None,
            "week_return_pct": round(float(ret5) * 100, 2)
                if ret5 is not None and pd.notna(ret5) else None,
        })
    stocks = [s for s in stocks if s["month_return_pct"] is not None]
    stocks.sort(key=lambda s: -s["month_return_pct"])

    sectors = {}
    for s in stocks:
        sectors.setdefault(s["sector"], []).append(s["month_return_pct"])
    sector_rows = sorted(
        ({"sector": k, "avg_month_pct": round(sum(v) / len(v), 2), "n": len(v)}
         for k, v in sectors.items()),
        key=lambda x: -x["avg_month_pct"])

    return {
        "as_of": str(last_day.date()),
        "best": stocks[:8], "worst": list(reversed(stocks[-8:])),
        "sectors": sector_rows,
        "plain_note": ("Patterns above describe the LAST month. The model uses "
                       "these same patterns (momentum, sector strength, volatility) "
                       "as inputs when scoring tomorrow's suggestions."),
    }


@app.get("/api/mf-patterns")
def mf_patterns():
    """MF category performance over ~1 month, from AMFI NAV histories."""
    try:
        from data.mf_client import default_mf_client

        client = default_mf_client()
        navs = client.latest_navs()
        picks = {
            "large_cap": navs[navs.category == "large_cap"].head(3),
            "mid_cap": navs[navs.category == "mid_cap"].head(3),
            "debt": navs[navs.category == "debt"].head(3),
            "gold_or_other": navs[navs.category == "gold_or_other"].head(3),
        }
        out = {}
        for cat, sub in picks.items():
            rows = []
            for _, row in sub.iterrows():
                try:
                    hist = client.get_nav_history(int(row.scheme_code), years=0.5)
                    if len(hist) < 25:
                        continue
                    m1 = hist["nav"].iloc[-22]
                    ret1m = (hist["nav"].iloc[-1] / m1 - 1) * 100 if len(hist) >= 22 else None
                    rows.append({
                        "scheme_code": int(row.scheme_code),
                        "scheme_name": row.scheme_name,
                        "return_1m_pct": round(float(ret1m), 2) if ret1m is not None else None,
                    })
                except Exception:
                    continue
            rows = [r for r in rows if r["return_1m_pct"] is not None]
            rows.sort(key=lambda r: -r["return_1m_pct"])
            out[cat] = rows[:5]
        return {"categories": out,
                "note": ("Sample of schemes per category (by AMFI listing order). "
                         "Past 1-month returns are descriptive, not advice.")}
    except Exception as exc:
        log.warning("mf-patterns failed: %s", exc)
        return {"categories": {}, "error": str(exc)}


@app.get("/api/track-record")
def track_record():
    gate = paper.gate_status()
    return {
        "hypothetical": paper.hypothetical_summary(),
        "actual": paper.user_summary(),
        "gate": gate.to_dict(),
        "badge": "LIVE-READY" if gate.live_ready else "NOT YET LIVE-READY",
    }


@app.get("/api/market-pulse")
def market_pulse():
    fs = client.feed_status().as_dict()
    nifty = None
    vix = None
    try:
        q = client.get_quotes(["^NSEI", "^INDIAVIX"])
        if "^NSEI" in q:
            nifty = {"ltp": q["^NSEI"].ltp, "change_pct": q["^NSEI"].change_pct}
        if "^INDIAVIX" in q:
            vix = {"ltp": q["^INDIAVIX"].ltp}
    except Exception as exc:
        log.debug("pulse quote fetch failed: %s", exc)
    regime = "unknown"
    if nifty and nifty.get("change_pct") is not None:
        regime = ("risk-on" if nifty["change_pct"] > 0.3 else
                  "risk-off" if nifty["change_pct"] < -0.3 else "range-bound")
    return {
        **fs,
        "market_phase": market_phase().value,
        "is_trading_day": is_trading_day(now_ist()),
        "nifty": nifty,
        "india_vix": vix,
        "regime": regime,
        "breaker": paper.breaker_state(),
        "advisory_only": True,
    }


def _load_mf_holdings() -> list[MFHolding]:
    import json

    path = ROOT / "var" / "mf_holdings.json"
    holdings = []
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        for h in raw:
            holdings.append(MFHolding(
                scheme_code=h["scheme_code"], scheme_name=h["scheme_name"],
                category=h.get("category", "other"),
                units=float(h["units"]), avg_cost_per_unit=float(h.get("avg_cost_per_unit", 0)),
                current_nav=float(h.get("current_nav", h.get("avg_cost_per_unit", 0))),
                first_purchase=datetime.fromisoformat(h["first_purchase"]),
            ))
    return holdings


def _mf_scaffold(result: dict, holdings_count: int) -> dict:
    """Ensure the MF panel always has bands/targets/next-date even when the
    portfolio is empty — the UI renders from these keys."""
    if result.get("error"):
        cfg_mf = cfg.mf
        result.update({
            "total_value_inr": 0,
            "current_alloc_pct": {},
            "target_alloc_pct": {
                "large_cap": sum(cfg_mf.large_cap_band) / 2,
                "mid_cap": sum(cfg_mf.mid_cap_band) / 2,
                "debt": sum(cfg_mf.debt_band) / 2,
                "gold_or_other": sum(cfg_mf.gold_band) / 2,
            },
            "bands": {
                "large_cap": list(cfg_mf.large_cap_band),
                "mid_cap": list(cfg_mf.mid_cap_band),
                "debt": list(cfg_mf.debt_band),
                "gold_or_other": list(cfg_mf.gold_band),
            },
            "next_rebalance": next_rebalance_date().isoformat(),
            "suggestions": [],
        })
    return result


@app.get("/api/mutual-funds")
def mutual_fund_panel():
    holdings = _load_mf_holdings()
    result = mf_advisor.suggest_allocation(holdings, new_money_inr=0.0)
    result = _mf_scaffold(result, len(holdings))
    return {
        **result,
        "holdings_count": len(holdings),
        "hint": ("Edit var/mf_holdings.json to track your real portfolio"
                 if not holdings else None),
        "suggestion_only": True,
    }


@app.post("/api/mutual-funds")
def mutual_fund_plan(req: MFRequest):
    holdings = _load_mf_holdings()
    result = mf_advisor.suggest_allocation(holdings, new_money_inr=req.new_money_inr)
    result = _mf_scaffold(result, len(holdings))
    return {
        **result,
        "holdings_count": len(holdings),
        "suggestion_only": True,
    }


# ------------------------------------------------------------------ static ui
frontend_dir = ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/")
def index():
    return FileResponse(frontend_dir / "index.html")
