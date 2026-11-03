"""Counter-contract tests — every labeled status counter must count exactly
what its label says.

Background: two real bugs of this class shipped before this test existed —
(1) backfill's "unavailable" bucket silently absorbed frozen-skipped days,
(2) model-history's "promoted" count included voided (retracted) decisions.
The convention: every increment site carries a `counter:` comment naming the
exact condition; these tests pin each condition with fixtures that would fail
under any broader/narrower filter; exhaustive partitions must sum to the whole.

The static half lives in scripts/verify_system.py::audit_counter_annotations,
which requires the `# counter:` annotation next to every recognized increment
site so future counters can't appear unannotated.
"""

import json

import pandas as pd
import pytest


# ---------------- bhavcopy backfill buckets ----------------
def test_backfill_buckets_exhaustive_and_exact(tmp_path, monkeypatch):
    from data.bar_store import ImmutableBarStore
    from data.bhavcopy import backfill
    from datetime import date

    store = ImmutableBarStore(root=tmp_path, provider="t")

    # four weekdays: fetched, frozen, unavailable, fetched
    days = [date(2026, 8, 17), date(2026, 8, 18),
            date(2026, 8, 19), date(2026, 8, 20)]
    script = {"fetched": ["fetched", "fetched"],
              "frozen": ["frozen"],
              "unavailable": ["unavailable"]}

    def fake_fetch_day(session, trade_date, store_, symbols=None, **kw):
        if trade_date == days[0] or trade_date == days[3]:
            return type("R", (), {"status": "fetched", "rows": 1})()
        if trade_date == days[1]:
            return type("R", (), {"status": "frozen", "rows": 0})()
        return type("R", (), {"status": "unavailable", "rows": 0})()

    monkeypatch.setattr("data.bhavcopy.fetch_day", fake_fetch_day)
    monkeypatch.setattr("data.bhavcopy.time.sleep", lambda s: None)

    results = backfill(start=days[0], end=days[-1], store=store,
                       symbols={"X"}, progress_every=100)
    # EXACT per-label counts (a catch-all else would break the frozen==1 line)
    assert results["fetched"] == 2
    assert results["frozen_skipped"] == 1
    assert results["unavailable"] == 1
    # exhaustiveness partition
    assert results["fetched"] + results["frozen_skipped"] \
        + results["unavailable"] == results["days"]


def test_backfill_buckets_annotated():
    """Convention guard: each bucket increment carries a `counter:` comment."""
    src = open("data/bhavcopy.py", encoding="utf-8").read()
    for bucket in ("fetched", "frozen_skipped", "unavailable"):
        inc = f'results["{bucket}"] += 1'
        assert inc in src, f"missing increment for {bucket}"
        idx = src.index(inc)
        window = src[max(0, idx - 200):idx]
        assert "# counter:" in window or "counter:" in window, (
            f"{bucket} increment lacks its `counter:` annotation")


# ---------------- model-history counts ----------------
def test_model_history_counts_exclude_voided():
    from models.challenger import count_history

    rows = [
        {"promoted": True},                                   # active promoted
        {"promoted": False},                                  # active rejected
        {"promoted": False, "reason": "x"},
        {"promoted": True, "voided": True,                    # VOIDED promoted:
         "void_reason": "in-sample evaluator"},               # must not inflate
    ]
    c = count_history(rows)
    assert c["total"] == 4
    assert c["voided"] == 1
    assert c["active"] == 3
    assert c["promoted_count"] == 1      # was the Section-A-class bug: counted 2
    assert c["rejected_count"] == 2
    # partitions
    assert c["promoted_count"] + c["rejected_count"] == c["active"]
    assert c["active"] + c["voided"] == c["total"]


def test_model_history_counts_empty():
    from models.challenger import count_history
    c = count_history([])
    assert c == {"total": 0, "active": 0, "voided": 0,
                 "promoted_count": 0, "rejected_count": 0}


# ---------------- forward-holdout arm stats ----------------
def _sugg(conf, ts, ret):
    return {"action": "BUY", "confidence": conf, "created_ts": ts,
            "realized_return_pct": ret, "direction_ok": ret > 0}


def test_forward_holdout_arm_filters_exact():
    from paper_trading.forward_holdout import _arm_stats

    reg = {"accrual_start": "2026-08-25"}
    suggestions = [
        # IN accrual window, gate-passing, closed -> counted
        _sugg(0.65, "2026-09-01T10:00:00+00:00", 0.8),
        # pre-accrual -> excluded even though gate passes
        _sugg(0.70, "2026-08-01T10:00:00+00:00", 5.0),
        # below candidate gate -> excluded from candidate arm
        _sugg(0.57, "2026-09-02T10:00:00+00:00", -0.2),
        # unresolved (open position) -> excluded until it resolves
        {"action": "BUY", "confidence": 0.80,
         "created_ts": "2026-09-03T10:00:00+00:00",
         "realized_return_pct": None, "direction_ok": None},
    ]
    cand = fh_arm(reg, suggestions, 0.60)
    assert cand["n"] == 1 and cand["mean_net_ret_pct"] == pytest.approx(0.8)
    default = fh_arm(reg, suggestions, 0.55)
    assert default["n"] == 2   # 0.57 row joins default arm only


def fh_arm(reg, suggestions, gate):
    from paper_trading.forward_holdout import _arm_stats
    return _arm_stats(suggestions, gate, reg["accrual_start"])


# ---------------- lifecycle coverage buckets ----------------
def test_lifecycle_buckets_consistent_with_exceptions(tmp_path):
    from data.lifecycle import classify_universe

    class FakeStore:
        def load(self, sym):
            if sym == "ACTIVE":
                idx = pd.DatetimeIndex([pd.Timestamp.now("UTC")])
                return pd.DataFrame({"close": [1.0]}, index=idx)
            if sym == "OLD":
                idx = pd.DatetimeIndex([pd.Timestamp.now("UTC")
                                        - pd.Timedelta(days=300)])
                return pd.DataFrame({"close": [1.0]}, index=idx)
            return None   # RENAMED: never seen

    result = classify_universe(
        {"ACTIVE": "A", "OLD": "B", "ZOMATO": "C"}, FakeStore())
    c = result["counts"]
    # partitions: exceptions are exactly every non-active entry
    assert len(result["exceptions"]) == c["stale_no_data"] + c["known_rename"]
    assert c["active"] + c["stale_no_data"] + c["known_rename"] == result["total"]
    by_sym = {e["symbol"]: e["status"] for e in result["entries"]}
    assert by_sym["ZOMATO"] == "known_rename"
    assert by_sym["OLD"] == "stale_no_data"
    assert by_sym["ACTIVE"] == "active"
