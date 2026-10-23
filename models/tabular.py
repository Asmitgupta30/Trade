"""Gradient-boosting tabular model (primary signal).

LightGBM binary classifier: P(forward 1-day return > cost_threshold).
The threshold comes from risk_config's estimated round-trip cost — the model
is trained to answer exactly the question the advisor must ask:
"does this idea clear its own costs?"
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

EXCLUDE_COLUMNS = {"symbol", "sector", "label_fwd_ret", "date", "close"}
MIN_TRAIN_ROWS = 500


def prepare_matrix(df: pd.DataFrame, feature_cols: list[str] | None = None):
    if feature_cols is None:
        feature_cols = [c for c in df.columns
                        if c not in EXCLUDE_COLUMNS and pd.api.types.is_numeric_dtype(df[c])]
    X = df[feature_cols].astype(float)
    return X, feature_cols


class TabularModel:
    def __init__(self, positive_threshold: float, seed: int = 42,
                 min_train_rows: int = MIN_TRAIN_ROWS,
                 lgbm_params: dict | None = None):
        self.threshold = positive_threshold
        self.seed = seed
        self.min_train_rows = min_train_rows
        self.lgbm_params = lgbm_params or {}
        self.model = None
        self.feature_cols: list[str] = []

    def _make_model(self, pos_weight: float):
        import lightgbm as lgb

        base = dict(
            n_estimators=150, learning_rate=0.04, num_leaves=15,
            max_depth=5, min_child_samples=80, subsample=0.8,
            subsample_freq=1, colsample_bytree=0.7,
            reg_lambda=5.0, reg_alpha=1.0,
            scale_pos_weight=pos_weight, random_state=self.seed,
            verbosity=-1,
        )
        base.update(self.lgbm_params)
        return lgb.LGBMClassifier(**base)

    def fit(self, dataset: pd.DataFrame, label_col: str = "label_fwd_ret"):
        df = dataset[dataset[label_col].notna()]
        if len(df) < self.min_train_rows:
            raise ValueError(f"not enough labelled rows to train: {len(df)}")
        X, self.feature_cols = prepare_matrix(df)
        y = (df[label_col] > self.threshold).astype(int)
        pos_weight = max((y == 0).sum() / max((y == 1).sum(), 1), 1.0)
        self.model = self._make_model(pos_weight)
        self.model.fit(X.values, y.values)
        # training-set fit for the overfitting-gap report (NOT a performance claim)
        train_prob = self.model.predict_proba(X.values)[:, 1]
        self._train_hit_rate = float(((train_prob > 0.5) == y.values).mean())
        log.info("tabular trained: %d rows, %d features, train hit-rate %.3f",
                 len(df), len(self.feature_cols), self._train_hit_rate)
        return self

    def predict_proba(self, dataset: pd.DataFrame) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("model not fitted")
        X = dataset[self.feature_cols].astype(float)
        return self.model.predict_proba(X.fillna(0.0).values)[:, 1]

    @property
    def train_hit_rate(self) -> float | None:
        return getattr(self, "_train_hit_rate", None)

    def feature_importance(self) -> dict[str, float]:
        if self.model is None:
            return {}
        pairs = sorted(zip(self.feature_cols, self.model.feature_importances_),
                       key=lambda p: -p[1])
        return {k: float(v) for k, v in pairs[:15]}
