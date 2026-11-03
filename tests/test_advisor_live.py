"""End-to-end advisor smoke test on the REAL trained model + REAL snapshot.

Skipped (with a clear message) when no trained artifact exists yet, so a
fresh clone passes CI before the first `train_model.py` run.
"""

import numpy as np
import pandas as pd
import pytest

from advisor.engine import AdvisorEngine
from advisor.rules import PortfolioState
from common.config import load_risk_config


def _snapshot_ready():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return (root / "var" / "cache" / "features_snapshot.parquet").exists() and \
        (root / "var" / "models" / "ensemble_latest.joblib").exists()


@pytest.mark.skipif(not _snapshot_ready(), reason="run build_features_snapshot.py + train_model.py first")
def test_real_model_generates_valid_suggestion_set():
    cfg = load_risk_config()
    from advisor.signals import SnapshotSignalProvider

    eng = AdvisorEngine(cfg, SnapshotSignalProvider())
    out = eng.generate(budget_inr=5000)
    assert out.system_notes is not None
    assert out.cash_left_inr >= 0

    total_alloc = sum(s.amount_inr for s in out.suggestions if s.action != "NO_ACTION")
    assert total_alloc <= 5000 + 1
    buys = [s for s in out.suggestions if s.action in ("BUY", "STRONG_BUY")]
    assert len(buys) <= cfg.intraday.max_positions_per_day
    for s in buys:
        assert s.confidence >= cfg.intraday.min_confidence_buy - 1e-9
        assert s.est_quantity >= 1 or s.amount_inr == 0
        assert s.rationale and s.baseline_comparison
        # per-stock cap
        assert s.amount_inr <= 5000 * cfg.intraday.max_allocation_per_stock_pct / 100 + 1
    d = out.to_dict()
    assert d["horizon"] == "intraday"
    import json
    json.dumps(d)   # must be JSON-serializable for the API layer


def test_snapshot_provider_scores_real_frame():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    snap = root / "var" / "cache" / "features_snapshot.parquet"
    if not snap.exists() or not (root / "var" / "models" / "ensemble_latest.joblib").exists():
        pytest.skip("no snapshot/model yet")
    from advisor.signals import SnapshotSignalProvider

    prov = SnapshotSignalProvider()
    scored = prov.score_universe()
    assert not scored.empty
    assert set(["symbol", "sector", "confidence", "close", "daily_vol_pct"]).issubset(scored.columns)
    assert ((scored["confidence"] >= 0) & (scored["confidence"] <= 1)).all()
    assert np.isfinite(scored["daily_vol_pct"]).all()
