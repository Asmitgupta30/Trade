"""Action-type classification & strict grouping guards."""

import json

import pandas as pd
import pytest

from data.bar_store import ImmutableBarStore, classify_action
from tests.test_bar_store import _frame, d


def _craft_events(store: ImmutableBarStore, symbol: str, events: list[dict]):
    """Overwrite the adjustments log with hand-built events for grouping tests."""
    path = store._adj_log_path(store._series_dir(symbol, "day"))
    path.write_text(json.dumps(events, indent=2), encoding="utf-8")


def test_classify_action_heuristic():
    assert classify_action(0.5) == "likely_split_or_bonus"
    assert classify_action(0.25) == "likely_split_or_bonus"
    assert classify_action(2.0) == "likely_split_or_bonus"
    assert classify_action(0.995) == "minor_data_revision"
    assert classify_action(1.008) == "minor_data_revision"
    assert classify_action(0.7) == "unclassified_adjustment"


def test_simultaneous_different_ratios_not_merged(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("Y", _frame([d(4), d(3), d(2)], close=100.0))
    events = [
        {"date": str(d(2).date()), "ratio": 0.5,
         "action_type": "likely_split_or_bonus",
         "refetched_ohlcv": {"open": 50, "high": 51, "low": 49, "close": 50, "volume": 1},
         "detected_at": "T1"},
        {"date": str(d(3).date()), "ratio": 0.25,
         "action_type": "unclassified_adjustment",
         "refetched_ohlcv": {"open": 25, "high": 26, "low": 24, "close": 25, "volume": 1},
         "detected_at": "T1"},
    ]
    _craft_events(s, "Y", events)
    # GUARD: same detected_at, different ratio+type -> exactly 2 groups
    groups = s._group_events(events)
    assert len(groups) == 2
    assert {(g["ratio"], g["action_type"]) for g in groups} == {
        (0.5, "likely_split_or_bonus"), (0.25, "unclassified_adjustment")}
    # end-to-end still applies every group without error
    out = s.load("Y")
    assert abs(out["close"].iloc[0] - 100.0 * 0.5 * 0.25) < 1e-6


def test_same_timestamp_same_ratio_different_type_not_merged(tmp_path):
    """THE guard requested: two coincidentally-simultaneous same-ratio events
    with different classified types must NOT merge into one adjustment."""
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("Z", _frame([d(4), d(3), d(2)], close=100.0))
    events = [
        {"date": str(d(2).date()), "ratio": 0.5,
         "action_type": "likely_split_or_bonus",
         "refetched_ohlcv": {"open": 50, "high": 51, "low": 49, "close": 50, "volume": 1},
         "detected_at": "T1"},
        {"date": str(d(3).date()), "ratio": 0.5,
         "action_type": "minor_data_revision",
         "refetched_ohlcv": {"open": 50, "high": 51, "low": 49, "close": 50, "volume": 1},
         "detected_at": "T1"},
    ]
    _craft_events(s, "Z", events)
    # MERGED would yield ONE group (cutoff = earliest date, single x0.5);
    # CORRECT yields TWO independent groups -> compounded x0.25 on earlier bars
    groups = s._group_events(events)
    assert len(groups) == 2, "same-ratio same-time events must not merge across types"
    out = s.load("Z")
    assert abs(out["close"].iloc[0] - 100.0 * 0.5 * 0.5) < 1e-6


def test_events_written_by_upsert_carry_action_type(tmp_path):
    s = ImmutableBarStore(root=tmp_path, provider="t")
    s.upsert_immutable("W", _frame([d(2)], close=100.0))
    s.upsert_immutable("W", _frame([d(2)], close=50.0))
    evs = s.adjustment_events("W")
    assert evs and evs[0]["action_type"] == "likely_split_or_bonus"
