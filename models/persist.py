"""Model persistence: save/load the trained hybrid ensemble.

The advisor NEVER trains at request time; it loads the artifact produced by
scripts/train_model.py (walk-forward-validated separately).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "var" / "models"

log = logging.getLogger(__name__)


def save_ensemble(ens, path: Path | None = None) -> Path:
    import joblib

    p = path or (MODEL_DIR / "ensemble_latest.joblib")
    p.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"tabular": ens.tabular, "threshold": ens.threshold,
                 "weights": (ens.tab_weight, ens.seq_weight),
                 "seq_used": ens.seq_used}, p)
    if ens.seq_used and ens.seq is not None and ens.seq.net is not None:
        import torch

        torch.save(ens.seq.net.state_dict(), MODEL_DIR / "sequence_state.pt")
        with open(MODEL_DIR / "ensemble_meta.json", "w", encoding="utf-8") as fh:
            json.dump(ens.describe(), fh, indent=2)
    log.info("ensemble saved -> %s", p)
    return p


def load_ensemble(path: Path | None = None):
    """Reconstruct a HybridEnsemble ready for predict_proba()."""
    import joblib
    import numpy as np

    from models.ensemble import HybridEnsemble
    from models.sequence import SequenceModel, _import_torch
    from models.tabular import TabularModel

    p = path or (MODEL_DIR / "ensemble_latest.joblib")
    if not p.exists():
        raise FileNotFoundError(
            f"no trained model at {p} — run scripts/train_model.py first")
    blob = joblib.load(p)
    tab_w, seq_w = blob["weights"]
    ens = HybridEnsemble(positive_threshold=blob["threshold"],
                         tab_weight=tab_w, seq_weight=seq_w)
    ens.tabular = blob["tabular"]
    assert isinstance(ens.tabular, TabularModel)
    if blob.get("seq_used"):
        try:
            torch = _import_torch()
            seq = SequenceModel()
            X_dummy = np.zeros((1, 30, 4), dtype=np.float32)
            seq._ensure_torch()

            class Net(torch.nn.Module):
                def __init__(self):
                    super().__init__()
                    self.lstm = torch.nn.LSTM(4, seq.hidden, batch_first=True)
                    self.head = torch.nn.Linear(seq.hidden, 1)

                def forward(self, x):
                    out, _ = self.lstm(x)
                    return self.head(out[:, -1, :]).squeeze(-1)

            net = Net()
            state_path = MODEL_DIR / "sequence_state.pt"
            net.load_state_dict(torch.load(state_path, weights_only=True))
            net.eval()
            seq.net = net
            seq.trained = True
            seq.torch = torch
            ens.seq = seq
            ens.seq_used = True
        except Exception as exc:
            log.warning("sequence model failed to load (%s); tabular-only", exc)
            ens.seq_used = False
    return ens


def model_is_trained() -> bool:
    return (MODEL_DIR / "ensemble_latest.joblib").exists()
