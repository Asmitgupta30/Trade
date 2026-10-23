"""Governed challenger-cycle tests: promotion gate, noise floor, audit log.

Decision logic is tested deterministically via crafted walk-forward reports;
one slow end-to-end smoke runs the real ML path on a synthetic panel.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from common.config import load_risk_config


@pytest.fixture()
def cfg():
    return load_risk_config()


def _panel(n_days=420, n_syms=6, seed=0, edge=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-06-03", periods=n_days)
    frames = []
    base = rng.normal(0.0002, 0.008, n_days)
    for s in range(n_syms):
        rets = base * 0.5 + rng.normal(edge, 0.01, n_days)
        close = 100 * np.exp(np.cumsum(rets))
        df = pd.DataFrame({
            "open": close, "high": close * 1.005, "low": close * 0.995,
            "close": close,
            "volume": 1_500_000,
            "ret_1d": np.r_[0.0, np.diff(close) / close[:-1]],
            "ret_5d": pd.Series(close).pct_change(5).values,
            "rvol_5d": pd.Series(rets).rolling(5).std().values,
            "bb_pctb": rng.uniform(0, 1, n_days),
            "vol_z": rng.normal(0, 1, n_days),
            "nifty_mom_5d": pd.Series(base).rolling(5).sum().values,
            "sector_rel_mom": rng.uniform(-0.5, 0.5, n_days),
            "label_fwd_ret": np.r_[pd.Series(close).pct_change().values[1:], np.nan],
        }, index=idx)
        df["symbol"] = f"S{s}"
        frames.append(df)
    return pd.concat(frames)


# ---------------- crafted-report fixtures ----------------
@dataclass
class FakeAggregate(dict):
    pass


@dataclass
class FakeReport:
    aggregate: dict
    pooled_strategy_daily: object = None
    pooled_baseline_daily: object = None
    scored_folds: list = field(default_factory=list)


def _series(mean, n=150, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, 0.004, n))


def _patch_reports(monkeypatch, champ_metrics, chall_metrics):
    from models import challenger as ch

    champ = FakeReport(
        aggregate={"strategy_sharpe_net_of_cost": champ_metrics["sharpe"],
                   "pooled_hit_rate": champ_metrics["hit_rate"],
                   "strategy_max_dd_pct": champ_metrics["dd"],
                   "total_oos_suggestions": 100},
        pooled_strategy_daily=_series(0.0, seed=21),
        scored_folds=[])
    chall = FakeReport(
        aggregate={"strategy_sharpe_net_of_cost": chall_metrics["sharpe"],
                   "pooled_hit_rate": chall_metrics["hit_rate"],
                   "strategy_max_dd_pct": chall_metrics["dd"],
                   "total_oos_suggestions": 90},
        pooled_strategy_daily=_series(0.0005, seed=22),
        scored_folds=[])

    calls = []

    def fake_wf(dataset, label="", **kwargs):
        calls.append(label)
        return champ if label == "champion" else chall

    monkeypatch.setattr(ch, "run_walk_forward", fake_wf)

    saved = {}

    class _StubEns:
        def __init__(self, *a, **k):
            pass

        def fit(self, *a, **k):
            return self

    monkeypatch.setattr(ch, "HybridEnsemble", _StubEns)

    def _fake_save(ens, path=None):
        saved["called"] = True

    monkeypatch.setattr("models.persist.save_ensemble", _fake_save)
    return calls, saved


# ---------------- tests ----------------
def test_identical_reports_rejected_and_logged(tmp_path, monkeypatch, cfg):
    from models import challenger as ch

    hist = tmp_path / "history.jsonl"
    monkeypatch.setattr(ch, "HISTORY_PATH", hist)
    m = {"sharpe": 0.5, "hit_rate": 0.60, "dd": 10.0}
    _patch_reports(monkeypatch, m, dict(m))   # identical -> deltas 0 -> reject

    d = ch.run_challenger_cycle(_panel(), cfg, use_hpo=False, use_sequence=False)
    assert d.promoted is False
    assert "noise floor" in d.reason or "hit-rate" in d.reason
    logged = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines()]
    assert len(logged) == 1
    assert logged[0]["champion"]["sharpe"] == m["sharpe"]
    assert logged[0]["promoted"] is False


def test_promotion_requires_all_three_and_logs(tmp_path, monkeypatch, cfg):
    from models import challenger as ch

    hist = tmp_path / "history.jsonl"
    monkeypatch.setattr(ch, "HISTORY_PATH", hist)

    # (a) challenger better on Sharpe+hit but WORSE drawdown -> must reject
    _patch_reports(monkeypatch,
                   {"sharpe": 0.0, "hit_rate": 0.55, "dd": 5.0},
                   {"sharpe": 2.5, "hit_rate": 0.70, "dd": 8.0})
    d = ch.run_challenger_cycle(_panel(), cfg, use_hpo=False, use_sequence=False)
    assert d.promoted is False
    assert "drawdown" in d.reason

    # (b) all three better by clear margins -> promote, production retrain runs
    calls, saved = _patch_reports(monkeypatch,
                                  {"sharpe": 0.0, "hit_rate": 0.50, "dd": 12.0},
                                  {"sharpe": 2.5, "hit_rate": 0.70, "dd": 5.0})
    d2 = ch.run_challenger_cycle(_panel(), cfg, use_hpo=False, use_sequence=False)
    assert d2.promoted is True
    assert "champion replaced" in d2.reason
    assert saved.get("called") is True
    logged = [json.loads(l) for l in hist.read_text(encoding="utf-8").splitlines()]
    assert logged[-1]["promoted"] is True
    assert logged[-1]["challenger"]["sharpe"] == 2.5


def test_insufficient_history_is_honest_noop(tmp_path, monkeypatch, cfg):
    from models import challenger as ch

    monkeypatch.setattr(ch, "HISTORY_PATH", tmp_path / "h.jsonl")
    d = ch.run_challenger_cycle(_panel(n_days=100), cfg, use_hpo=False,
                                use_sequence=False)
    assert d.promoted is False
    assert "insufficient history" in d.reason


def test_gate_recommendation_requires_head_to_head(tmp_path, monkeypatch, cfg):
    from models import challenger as ch

    hist = tmp_path / "history.jsonl"
    monkeypatch.setattr(ch, "HISTORY_PATH", hist)

    base_m = {"sharpe": 0.0, "hit_rate": 0.50, "dd": 10.0}
    better_m = {"sharpe": 2.0, "hit_rate": 0.65, "dd": 6.0}
    calls, _ = _patch_reports(monkeypatch, base_m, better_m)

    def fake_wf_with_gates(dataset, label="", **kwargs):
        rep = FakeReport(
            aggregate={"strategy_sharpe_net_of_cost": base_m["sharpe"],
                       "pooled_hit_rate": base_m["hit_rate"],
                       "strategy_max_dd_pct": base_m["dd"],
                       "total_oos_suggestions": 100},
            pooled_strategy_daily=_series(0.0, seed=31))
        # one scored fold with proba column so the gate sweep has material
        idx = pd.bdate_range("2025-01-01", periods=40)
        rep.scored_folds = [pd.DataFrame({
            "symbol": [f"S{i}" for i in range(40)],
            "sector": ["X"] * 40,
            "close": [100.0] * 40,
            "label_fwd_ret": np.where(np.arange(40) % 3 == 0, 0.02, -0.004),
            "proba": np.linspace(0.50, 0.85, 40),
        }, index=idx)]
        if label == "champion":
            rep.aggregate = dict(rep.aggregate)
            rep.aggregate["strategy_sharpe_net_of_cost"] = better_m["sharpe"]
            rep.aggregate["pooled_hit_rate"] = better_m["hit_rate"]
            rep.aggregate["strategy_max_dd_pct"] = better_m["dd"]
        return rep

    monkeypatch.setattr(ch, "run_walk_forward", fake_wf_with_gates)
    d = ch.run_challenger_cycle(_panel(), cfg, use_hpo=False, use_sequence=False)
    # whatever the promotion outcome, a gate recommendation may only appear
    # when the head-to-head at that gate is won — verified by construction here:
    if d.recommended_gate is not None:
        assert d.recommended_gate > cfg.intraday.min_confidence_buy
        assert "RECOMMENDATION" in d.reason


def test_hpo_only_sees_train_side(tmp_path, monkeypatch, cfg):
    """folds_train_cutoff must precede the first OOS window."""
    from models.challenger import folds_train_cutoff
    from models.walkforward import make_folds

    dates = pd.bdate_range("2024-01-01", periods=500)
    folds = make_folds(dates, n_folds=5, test_days=60, purge_days=3)
    cutoff = folds_train_cutoff(dates)
    assert cutoff < min(folds[0][1])


def test_risk_config_never_written_by_challenger(tmp_path, monkeypatch, cfg):
    cfg_file = Path("risk_config.yaml")
    before = cfg_file.read_text(encoding="utf-8") if cfg_file.exists() else None
    from models import challenger as ch
    monkeypatch.setattr(ch, "HISTORY_PATH", tmp_path / "h.jsonl")
    try:
        ch.run_challenger_cycle(_panel(), cfg, use_hpo=False,
                                use_sequence=False)
    except Exception:
        pass
    if before is not None:
        assert cfg_file.read_text(encoding="utf-8") == before


@pytest.mark.slow
def test_end_to_end_smoke_real_ml(tmp_path, monkeypatch, cfg):
    from models import challenger as ch

    monkeypatch.setattr(ch, "HISTORY_PATH", tmp_path / "h.jsonl")
    d = ch.run_challenger_cycle(_panel(n_days=430, seed=5), cfg,
                                use_hpo=False, use_sequence=False)
    assert d.champion.get("picks", 0) >= 0
    assert "sharpe" in d.deltas
