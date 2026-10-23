"""Confidence-weighted hybrid ensemble: gradient boosting + LSTM sequence model."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from models.sequence import SequenceModel, build_sequences, torch_available
from models.tabular import TabularModel

log = logging.getLogger(__name__)


class HybridEnsemble:
    def __init__(self, positive_threshold: float, tab_weight: float = 0.65,
                 seq_weight: float = 0.35, min_train_rows: int = 500):
        if abs(tab_weight + seq_weight - 1.0) > 1e-9:
            raise ValueError("ensemble weights must sum to 1")
        self.threshold = positive_threshold
        self.tab_weight = tab_weight
        self.seq_weight = seq_weight
        self.min_train_rows = min_train_rows
        self.tabular: TabularModel | None = None
        self.seq: SequenceModel | None = None
        self.seq_used = False

    def fit(self, dataset: pd.DataFrame, use_sequence: bool | None = None,
            epochs: int = 8, lgbm_params: dict | None = None):
        self.tabular = TabularModel(
            positive_threshold=self.threshold,
            min_train_rows=self.min_train_rows,
            lgbm_params=lgbm_params).fit(dataset)

        want_seq = torch_available() if use_sequence is None else use_sequence
        if want_seq:
            try:
                X, y, _meta = build_sequences(dataset)
                # keep sequence training tractable on CPU
                if len(X) > 30000:
                    rng = np.random.default_rng(7)
                    pick = rng.choice(len(X), 30000, replace=False)
                    X, y = X[pick], y[pick]
                self.seq = SequenceModel().fit(X, y, epochs=epochs)
                self.seq_used = True
            except Exception as exc:
                log.warning("sequence model skipped (%s); ensemble = tabular only", exc)
                self.seq_used = False
        else:
            log.info("torch unavailable -> ensemble runs tabular-only")
        return self

    def predict_proba(self, dataset: pd.DataFrame) -> pd.Series:
        """Combined P(up beyond threshold) per dataset row (aligned to index)."""
        p_tab = pd.Series(self.tabular.predict_proba(dataset), index=dataset.index)
        if not self.seq_used:
            return p_tab
        try:
            X, _y, meta = build_sequences(dataset)
            p_seq_raw = self.seq.predict_proba(X)
            seq_by_key = {(sym, ts): float(p) for (sym, ts), p in zip(meta, p_seq_raw)}
            keys = list(zip(dataset["symbol"], dataset.index))
            p_seq = pd.Series([seq_by_key.get(k, np.nan) for k in keys], index=dataset.index)
            combined = (
                self.tab_weight * p_tab
                + self.seq_weight * p_seq.fillna(p_tab)
            )
            return combined
        except Exception as exc:
            log.warning("sequence inference failed (%s); using tabular", exc)
            return p_tab

    def describe(self) -> dict:
        return {
            "tabular": "lightgbm",
            "sequence": "lstm" if self.seq_used else None,
            "weights": {"tabular": self.tab_weight,
                        "sequence": self.seq_weight if self.seq_used else 0.0},
            "positive_threshold": self.threshold,
            "train_hit_rates": {
                "tabular": self.tabular.train_hit_rate if self.tabular else None,
                "sequence": self.seq.train_hit_rate if (self.seq and self.seq_used) else None,
            },
        }
