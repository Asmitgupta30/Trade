"""Sequence model (complementary signal): small LSTM over recent return/volume
windows, implemented in PyTorch CPU. If torch is unavailable the ensemble
degrades gracefully to the tabular model alone (flagged in reports).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SEQ_LEN = 30


def build_sequences(dataset: pd.DataFrame, seq_len: int = SEQ_LEN,
                    value_cols=("ret_1d", "rvol_5d", "bb_pctb", "vol_z")):
    """Per-symbol rolling windows of shape (samples, seq_len, features)."""
    X, y, meta = [], [], []
    for sym, g in dataset.groupby("symbol", sort=False):
        g = g.sort_index()
        vals = g[list(value_cols)].astype(float).copy()
        # cross-sectional safety: clip crazy outliers, zero-fill warmups
        vals = vals.clip(-0.5, 0.5).fillna(0.0)
        labels = g["label_fwd_ret"]
        arr = vals.values
        for i in range(seq_len, len(g)):
            if pd.isna(labels.iloc[i]):
                continue
            X.append(arr[i - seq_len: i])
            y.append(float(labels.iloc[i] > 0))
            meta.append((sym, g.index[i]))
    if not X:
        raise ValueError("no sequences built — check dataset")
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.float32), meta


class SequenceModel:
    """Tiny 1-layer LSTM (hidden 24) + linear head -> P(up)."""

    def __init__(self, n_features: int = 4, hidden: int = 24, seed: int = 42):
        self.seed = seed
        self.n_features = n_features
        self.hidden = hidden
        self.torch = None
        self.net = None
        self.trained = False

    def _ensure_torch(self):
        self.torch = _import_torch()
        self.torch.manual_seed(self.seed)

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = 8,
            batch_size: int = 256, verbose: bool = False):
        self._ensure_torch()
        torch = self.torch
        hidden = self.hidden

        class Net(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm = torch.nn.LSTM(X.shape[2], hidden, batch_first=True)
                self.head = torch.nn.Linear(hidden, 1)

            def forward(self, x):
                out, _ = self.lstm(x)
                return self.head(out[:, -1, :]).squeeze(-1)

        self.net = Net()
        opt = torch.optim.Adam(self.net.parameters(), lr=1e-3)
        lossf = torch.nn.BCEWithLogitsLoss()
        Xt = torch.tensor(X)
        yt = torch.tensor(y)
        n = len(Xt)
        self.net.train()
        for ep in range(epochs):
            perm = torch.randperm(n)
            tot = 0.0
            for i in range(0, n, batch_size):
                idx = perm[i:i + batch_size]
                opt.zero_grad()
                logits = self.net(Xt[idx])
                loss = lossf(logits, yt[idx])
                loss.backward()
                opt.step()
                tot += float(loss.detach()) * len(idx)
            if verbose:
                log.info("lstm epoch %d loss %.4f", ep, tot / n)
        self.net.eval()
        with torch.no_grad():
            preds = (torch.sigmoid(self.net(Xt)).numpy() > 0.5)
        self._train_hit_rate = float((preds.astype(float) == y).mean())
        self.trained = True
        log.info("sequence model trained on %d windows; train hit-rate %.3f",
                 n, self._train_hit_rate)
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.trained:
            raise RuntimeError("sequence model not fitted")
        torch = self.torch
        with torch.no_grad():
            return torch.sigmoid(self.net(torch.tensor(np.asarray(X, dtype=np.float32)))).numpy()

    @property
    def train_hit_rate(self) -> float | None:
        return getattr(self, "_train_hit_rate", None)


def _import_torch():
    """Import torch, tolerating a short-path --target install (C:\\pylibs)
    used when the user site-packages path exceeds Windows MAX_PATH."""
    try:
        import torch

        return torch
    except ImportError:
        import sys

        alt = r"C:\pylibs"
        if alt not in sys.path:
            sys.path.insert(0, alt)
        import torch

        return torch


def torch_available() -> bool:
    try:
        _import_torch()
        return True
    except ImportError:
        return False
