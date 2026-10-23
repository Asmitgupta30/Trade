"""Forward-holdout protocol tests (Section E methodology rule)."""

import json

import pytest

from paper_trading import forward_holdout as fh


def _reg(**kw):
    base = {"candidate_gate": 0.60, "default_gate": 0.55,
            "registered_at": "2026-08-22T00:00:00+00:00",
            "accrual_start": "2026-08-25",
            "min_n_per_arm": kw.pop("min_n", 40),
            "deadline_weeks": 8,
            "status": "pending"}
    base.update(kw)
    return base


def _suggestions(n_good, n_bad, gate_band=(0.55, 1.0), start="2026-08-26"):
    out = []
    for i in range(n_good):
        out.append({"action": "BUY", "confidence": max(gate_band[0], 0.60),
                    "created_ts": f"{start}T10:00:00+00:00",
                    "realized_return_pct": 0.9, "direction_ok": True})
    for i in range(n_bad):
        out.append({"action": "BUY", "confidence": max(gate_band[0], 0.60),
                    "created_ts": f"{start}T10:00:00+00:00",
                    "realized_return_pct": -0.5, "direction_ok": False})
    return out


def test_pending_until_min_n():
    r = _reg()
    ev = fh.evaluate_registration(r, _suggestions(18, 12))   # n=30 < 40
    assert ev["status"] == "pending"
    assert ev["candidate_arm"]["n"] == 30


def test_resolves_when_both_arms_reach_min_n():
    r = _reg()
    ev = fh.evaluate_registration(r, _suggestions(30, 20))   # n=50 >= 40
    assert ev["status"] in ("confirmed", "not_replicated")


def test_replicates_when_candidate_positive_and_beats_default():
    r = _reg()
    # candidate arm: confidence >= 0.60 -> strong returns
    # default arm includes extra 0.55-0.60 band -> weaker hit-rate
    suggs = []
    for i in range(50):   # high-confidence winners
        suggs.append({"action": "BUY", "confidence": 0.65,
                      "created_ts": "2026-09-01T10:00:00+00:00",
                      "realized_return_pct": 1.0, "direction_ok": True})
    for i in range(45):   # mid-band: mixed -> drags default arm only
        suggs.append({"action": "BUY", "confidence": 0.57,
                      "created_ts": "2026-09-01T10:00:00+00:00",
                      "realized_return_pct": -0.4 if i % 2 else 0.2,
                      "direction_ok": i % 2 == 1})
    ev = fh.evaluate_registration(r, suggs)
    assert ev["status"] == "confirmed"
    assert ev["candidate_arm"]["hit_rate"] == pytest.approx(1.0)
    assert ev["default_arm"]["hit_rate"] < ev["candidate_arm"]["hit_rate"]


def test_not_replicated_when_candidate_negative():
    r = _reg()
    suggs = _suggestions(20, 40)   # mostly losers at conf>=0.60
    suggs += [{"action": "BUY", "confidence": 0.56,
               "created_ts": "2026-09-02T10:00:00+00:00",
               "realized_return_pct": 0.3, "direction_ok": True}
              for _ in range(25)]
    ev = fh.evaluate_registration(r, suggs)
    assert ev["status"] == "not_replicated"
    assert ev["candidate_arm"]["sharpe"] <= 0


def test_inconclusive_after_deadline():
    r = _reg(deadline_weeks=8)
    r["registered_at"] = "2026-06-01T00:00:00+00:00"   # >8 weeks ago
    ev = fh.evaluate_registration(r, _suggestions(5, 5),
                                  now=__import__("datetime").datetime.fromisoformat(
                                      "2026-08-23T00:00:00+00:00"))
    # not yet deadline (Jun->Aug is ~12 weeks actually) - force via explicit now
    ev2 = fh.evaluate_registration(
        dict(r, registered_at="2026-05-01T00:00:00+00:00"),
        _suggestions(5, 5),
        now=__import__("datetime").datetime.fromisoformat(
            "2026-08-23T00:00:00+00:00"))
    assert ev2["status"] == "inconclusive-too-small-sample"
    assert ev["status"] in ("pending", "inconclusive-too-small-sample")


def test_registration_idempotent():
    fh.REGISTRY_PATH.write_text("[]", encoding="utf-8")
    a = fh.register(0.60, 0.55, accrual_start="2026-08-25")
    b = fh.register(0.60, 0.55, accrual_start="2026-08-25")
    assert len(fh.load_registrations()) == 1
