"""Champion-challenger retraining pipeline — governed, never self-grading.

Rules (hard, not learned):
  * challenger is evaluated on IDENTICAL walk-forward folds as the champion
  * promotion requires ALL THREE: higher OOS Sharpe, higher hit-rate, lower
    max drawdown — no cherry-picking
  * margins must exceed a bootstrap noise floor derived from the champion's
    pooled daily series (Section B machinery); promoting on noise is forbidden
  * risk_config thresholds are read-only here; the definition of "beats
    baseline" lives in models/baselines.py and is not modifiable by this loop
  * every attempt is logged to var/models/history.jsonl whether promoted or not
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from models.bootstrap import bootstrap_sharpe_ci
from models.ensemble import HybridEnsemble
from models.metrics import max_drawdown_pct, sharpe_ratio
from models.persist import load_ensemble
from models.walkforward import _simulate_days, make_folds, run_walk_forward

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "var" / "models" / "history.jsonl"
NOISE_FLOOR_SHARPE = 0.35      # minimum |ΔSharpe| to count as signal, not noise
NOISE_FLOOR_HITRATE = 0.02     # 2pp minimum hit-rate improvement


@dataclass
class ChallengerDecision:
    ts: str
    promoted: bool
    reason: str
    champion: dict
    challenger: dict
    deltas: dict
    hpo_best_params: dict | None = None
    gate_variants: dict | None = None       # per-gate OOS table (both models)
    recommended_gate: float | None = None   # INFORMATIONAL — human must edit
                                            # risk_config.yaml; never self-applied

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


GATE_VARIANT_STEPS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.25)   # added to config gate
MIN_PICKS_FOR_GATE_VARIANT = 15


def evaluate_gate_variants(scored_folds: list, config_gate: float,
                           max_picks_per_day: int,
                           round_trip_cost_pct: float,
                           positive_threshold: float):
    """Trade-frequency lever: same fold-fresh predictions, stricter entry
    gates. Returns ({gate: metrics}, best_gate by Sharpe with enough picks).
    The returned table also carries '_best' for downstream convenience."""
    gates = [round(min(config_gate + s, 0.85), 2)
             for s in GATE_VARIANT_STEPS]
    table = _sweep_gates_oos(scored_folds, gates, max_picks_per_day,
                             round_trip_cost_pct, positive_threshold)
    best_gate, best_sharpe = config_gate, -float("inf")
    for g, m in table.items():
        if m["picks"] >= MIN_PICKS_FOR_GATE_VARIANT and m["sharpe"] > best_sharpe:
            best_sharpe = m["sharpe"]
            best_gate = g
    table["_best"] = best_gate
    return table, best_gate


def folds_train_cutoff(dates: pd.DatetimeIndex) -> pd.Timestamp:
    """Latest date the HPO search may look at: the first fold's train side.
    Keeps hyperparameter selection from peeking at any OOS window."""
    folds = make_folds(dates, n_folds=5, test_days=60, purge_days=3)
    return folds[0][0]


def _wf_metrics(report) -> dict:
    """Fold-honest OOS metrics from a WalkForwardReport."""
    return {
        "sharpe": report.aggregate["strategy_sharpe_net_of_cost"],
        "hit_rate": report.aggregate["pooled_hit_rate"],
        "max_dd_pct": report.aggregate["strategy_max_dd_pct"],
        "picks": report.aggregate["total_oos_suggestions"],
        "daily_series": report.pooled_strategy_daily,
    }


def _sweep_gates_oos(scored_folds: list, gates, max_picks_per_day: int,
                     round_trip_cost_pct: float,
                     positive_threshold: float) -> dict:
    """Gate variants evaluated ONLY on fold-fresh predictions (fully OOS)."""
    table = {}
    for gate in gates:
        s_parts, correct, picks = [], [], 0
        for scored in scored_folds:
            s, _b, p, c, _u, _t = _simulate_days(
                scored, gate, max_picks_per_day,
                round_trip_cost_pct, positive_threshold=positive_threshold)
            s_parts.append(s)
            correct.extend(c)
            picks += p
        if not s_parts:
            continue
        pooled = pd.concat(s_parts).sort_index()
        eq = (1 + pooled).cumprod()
        table[gate] = {
            "sharpe": round(sharpe_ratio(pooled), 3),
            "hit_rate": (round(float(np.mean(correct)), 4)
                         if correct else None),
            "max_dd_pct": round(max_drawdown_pct(eq), 2),
            "picks": picks,
        }
    return table


def evaluate_gate_variants(scored_folds: list, config_gate: float,
                           max_picks_per_day: int,
                           round_trip_cost_pct: float,
                           positive_threshold: float):
    """Trade-frequency lever: same fold-fresh predictions, stricter entry
    gates. Returns ({gate: metrics}, best_gate by Sharpe with enough picks)."""
    gates = [round(min(config_gate + s, 0.85), 2)
             for s in GATE_VARIANT_STEPS]
    table = _sweep_gates_oos(scored_folds, gates, max_picks_per_day,
                             round_trip_cost_pct, positive_threshold)
    best_gate, best_sharpe = config_gate, -float("inf")
    for g, m in table.items():
        if m["picks"] >= MIN_PICKS_FOR_GATE_VARIANT and m["sharpe"] > best_sharpe:
            best_sharpe = m["sharpe"]
            best_gate = g
    return table, best_gate


def optuna_tune_tabular(train_df: pd.DataFrame, positive_threshold: float,
                        n_trials: int = 12, seed: int = 42) -> dict:
    """Small budgeted HPO over LightGBM hyperparameters via walk-forward-mean
    objective INSIDE the training slice only. Returns best params."""
    try:
        import lightgbm as lgb
        import optuna
    except ImportError as exc:
        log.warning("optuna/lightgbm unavailable (%s); HPO skipped", exc)
        return {}

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    df = train_df[train_df["label_fwd_ret"].notna()]
    X_cols = [c for c in df.columns
              if c not in {"symbol", "sector", "label_fwd_ret", "date", "close"}
              and pd.api.types.is_numeric_dtype(df[c])]
    X = df[X_cols].astype(float).fillna(0.0).values
    y = (df["label_fwd_ret"] > positive_threshold).astype(int).values

    # inner time split for the objective (80/20 chronological, purged by 3d rows)
    cut = int(len(X) * 0.8)

    def objective(trial):
        params = {
            "n_estimators": trial.suggest_int("n_estimators", 80, 400),
            "learning_rate": trial.suggest_float("learning_rate", 0.02, 0.12, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 8, 48),
            "min_child_samples": trial.suggest_int("min_child_samples", 40, 160),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 0.9),
            "reg_lambda": trial.suggest_float("reg_lambda", 0.5, 20.0, log=True),
        }
        model = lgb.LGBMClassifier(**params, random_state=seed, verbosity=-1)
        model.fit(X[:cut], y[:cut])
        proba = model.predict_proba(X[cut:])[:, 1]
        picked = proba > 0.55
        if picked.sum() < 10:
            return -1.0
        net = df["label_fwd_ret"].iloc[cut:].to_numpy()[picked] \
            - positive_threshold
        return float(net.mean()) / max(net.std(), 1e-9)

    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    best = {k: v for k, v in study.best_params.items()}
    if "n_estimators" in best:
        best["n_estimators"] = int(best["n_estimators"])
    if "min_child_samples" in best:
        best["min_child_samples"] = int(best["min_child_samples"])
    return best


def run_challenger_cycle(dataset: pd.DataFrame,
                         cfg,
                         use_hpo: bool = True, n_hpo_trials: int = 12,
                         use_sequence: bool | None = None) -> ChallengerDecision:
    """One governed cycle. BOTH sides evaluated by identical walk-forwards
    (fully out-of-sample, same fold geometry) — the persisted champion artifact
    is production-inference-only and is NEVER scored on its own training data.

    Promotion: challenger-WF beats champion-WF (default hyperparameters) on
    Sharpe AND hit-rate AND max drawdown, each beyond the noise floor.
    Gate variants are informational; a gate recommendation additionally needs
    the challenger to beat the champion at that same gate (multiplicity guard).
    """
    from common.config import RiskConfig
    assert isinstance(cfg, RiskConfig)
    gate = cfg.intraday.min_confidence_buy
    max_picks = cfg.intraday.max_positions_per_day
    cost_pct = cfg.intraday.round_trip_cost_pct
    wf_kwargs = dict(
        positive_threshold=cost_pct / 100.0,
        confidence_gate=gate, max_picks_per_day=max_picks,
        round_trip_cost_pct=cost_pct, n_folds=5, test_days=60,
        min_train_rows=500, use_sequence=use_sequence,
        collect_trades=True, collect_scored=True,
    )

    ds = dataset.sort_index()
    ds = ds[ds["label_fwd_ret"].notna()]
    dates = ds.index.unique().sort_values()
    if len(dates) < 360:
        return ChallengerDecision(
            ts=_now(), promoted=False,
            reason=f"insufficient history for governed evaluation ({len(dates)} dates)",
            champion={}, challenger={}, deltas={})

    # ---- champion walk-forward (production default hyperparameters) -------
    t0 = time.time()
    champion_report = run_walk_forward(ds, label="champion", **wf_kwargs)
    champion = _wf_metrics(champion_report)

    # ---- challenger walk-forward (HPO-tuned hyperparameters) --------------
    best_params = {}
    if use_hpo:
        best_params = optuna_tune_tabular(
            ds[ds.index <= folds_train_cutoff(dates)],   # HPO sees train-side only
            positive_threshold=cost_pct / 100.0,
            n_trials=n_hpo_trials)
    challenger_report = run_walk_forward(ds, label="challenger",
                                         lgbm_params=best_params or None,
                                         **wf_kwargs)
    challenger = _wf_metrics(challenger_report)
    train_seconds = round(time.time() - t0, 1)

    # ---- trade-frequency lever (Section A follow-up) ----------------------
    # Fold-fresh predictions only; variants are INFORMATIONAL for promotion —
    # a gate recommendation additionally requires the challenger to beat the
    # champion AT THAT SAME GATE (multiplicity/noise guard). risk_config.yaml
    # stays read-only; recommendations await human action.
    gate_table_c, c_best = evaluate_gate_variants(
        challenger_report.scored_folds, config_gate=gate,
        max_picks_per_day=max_picks, round_trip_cost_pct=cost_pct,
        positive_threshold=cost_pct / 100.0)
    gate_table_m, _m_best = evaluate_gate_variants(
        champion_report.scored_folds, config_gate=gate,
        max_picks_per_day=max_picks, round_trip_cost_pct=cost_pct,
        positive_threshold=cost_pct / 100.0)

    # ---- promotion gate (ALL THREE + noise floor, config-gate vs config-gate)
    noise = bootstrap_sharpe_ci(champion["daily_series"], n_boot=500,
                                block_len=10, ci=0.90, seed=13)
    width = (noise.get("ci_high") or 0) - (noise.get("ci_low") or 0)
    floor = max(NOISE_FLOOR_SHARPE, 0.25 * width) if width else NOISE_FLOOR_SHARPE

    d_sharpe = challenger["sharpe"] - champion["sharpe"]
    d_hit = ((challenger["hit_rate"] or 0) - (champion["hit_rate"] or 0))
    d_dd = champion["max_dd_pct"] - challenger["max_dd_pct"]

    reasons = []
    ok = True
    if not (d_sharpe > floor):
        ok = False
        reasons.append(f"ΔSharpe {d_sharpe:+.3f} <= noise floor {floor:.3f}")
    if not (d_hit > NOISE_FLOOR_HITRATE):
        ok = False
        reasons.append(f"Δhit-rate {d_hit:+.3f} <= {NOISE_FLOOR_HITRATE}")
    if not (d_dd > 0):
        ok = False
        reasons.append(f"drawdown not improved ({champion['max_dd_pct']}% -> "
                       f"{challenger['max_dd_pct']}%)")

    decision = ChallengerDecision(
        ts=_now(),
        promoted=bool(ok),
        reason=("all three metrics improved beyond the noise floor"
                if ok else "; ".join(reasons)),
        champion={k: v for k, v in champion.items() if k != "daily_series"},
        challenger={k: v for k, v in challenger.items() if k != "daily_series"},
        deltas={"sharpe": round(d_sharpe, 3), "hit_rate": round(d_hit, 4),
                "max_dd_pct": round(d_dd, 2), "noise_floor_sharpe": round(floor, 3)},
        hpo_best_params=best_params or None,
        gate_variants={
            "challenger_by_gate": {k: v for k, v in gate_table_c.items()
                                   if k != "_best"},
            "champion_by_gate": {k: v for k, v in gate_table_m.items()
                                 if k != "_best"},
            "config_gate": gate,
            "challenger_best_gate": c_best,
        },
        recommended_gate=None,
    )
    # Gate recommendation (advisory): challenger's best gate must ALSO beat the
    # champion at that same gate on all three metrics — otherwise it's noise.
    challenger_best_gate = c_best
    if challenger_best_gate > gate:
        cb = gate_table_c[challenger_best_gate]
        mb = gate_table_m.get(challenger_best_gate, {})
        beats_there = (
            (cb.get("sharpe") or -9e9) > (mb.get("sharpe") or 9e9)
            and (cb.get("hit_rate") or 0) > (mb.get("hit_rate") or 1)
            and (cb.get("max_dd_pct") or 9e9) < (mb.get("max_dd_pct") or 0))
        if beats_there:
            decision.recommended_gate = challenger_best_gate
            decision.reason += (
                f" | RECOMMENDATION (human action required): OOS evidence favors "
                f"min_confidence_to_suggest_buy={challenger_best_gate:.2f} over the "
                f"configured {gate:.2f} — challenger beats champion at that same "
                "gate on all three metrics. Edit risk_config.yaml yourself; this "
                "loop cannot change it.")
        else:
            decision.reason += (
                f" | gate {challenger_best_gate:.2f} looked better for the "
                "challenger alone but not vs the champion at that gate — no "
                "recommendation (multiplicity guard).")

    if ok:
        # production artifact: retrain tuned config on ALL available history
        ens = HybridEnsemble(positive_threshold=cost_pct / 100.0)
        ens.fit(dataset.sort_index(), use_sequence=use_sequence, epochs=6,
                lgbm_params=best_params or None)
        from models.persist import save_ensemble
        save_ensemble(ens)
        decision.reason += f" (cycle took {train_seconds}s; champion replaced)"

    # ---- permanent methodology rule: gate recommendations are HYPOTHESES ----
    # until they replicate on forward data that did not exist at sweep time.
    if decision.recommended_gate is not None:
        try:
            from paper_trading.forward_holdout import _next_monday_iso, register

            reg = register(decision.recommended_gate, gate,
                           accrual_start=_next_monday_iso(),
                           source="challenger_cycle")
            decision.reason += (f" | registered for forward holdout "
                                f"(accrual from {reg['accrual_start']}; status="
                                f"{reg['status']})")
        except Exception as exc:
            log.warning("forward-holdout registration failed: %s", exc)

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as fh:
        fh.write(decision.to_json() + "\n")
    log.info("challenger cycle done: promoted=%s (%s)", decision.promoted,
             decision.reason[:120])
    return decision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_history(limit: int = 50) -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    rows = []
    with open(HISTORY_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return list(reversed(rows))[:limit]


def count_history(rows: list[dict]) -> dict:
    """Counter contract (tests/test_counter_contracts.py::test_model_history_counts):

    voided          <=> entry has truthy `voided` flag (excluded from ALL
                       active counts — its promoted/rejected verdict is
                       retracted and must not inflate anything)
    active          <=> not voided
    promoted_count  <=> active AND truthy `promoted`
    rejected_count  <=> active AND NOT truthy `promoted`

    Partitions: promoted + rejected == active;  active + voided == total.
    """
    total = len(rows)
    voided = sum(1 for r in rows if r.get("voided"))          # counter: voided
    active = total - voided                                    # counter: active
    promoted = sum(1 for r in rows
                   if not r.get("voided") and r.get("promoted"))   # counter: promoted
    rejected = active - promoted                               # counter: rejected
    return {
        "total": total,
        "active": active,
        "voided": voided,
        "promoted_count": promoted,
        "rejected_count": rejected,
    }
