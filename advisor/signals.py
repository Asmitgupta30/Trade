"""Signal providers: bridge trained model + features -> advisor input frame."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from models.persist import load_ensemble

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent


class SnapshotSignalProvider:
    """Scores the most recent date in the persisted feature snapshot.

    Good enough while intraday feature refresh is pending SmartAPI creds;
    swap to LiveSignalProvider when tick-level features are wired.
    """

    def __init__(self, ensemble=None, snapshot_path: Path | None = None):
        self.ensemble = ensemble or load_ensemble()
        self.snapshot_path = snapshot_path or (ROOT / "var" / "cache" / "features_snapshot.parquet")

    def score_universe(self) -> pd.DataFrame:
        df = pd.read_parquet(self.snapshot_path)
        last_date = df.index.max()
        day = df[df.index == last_date].copy()
        p_tab = pd.Series(self.ensemble.tabular.predict_proba(day), index=day.index)

        p_seq_by_symbol: dict[str, float] = {}
        if self.ensemble.seq_used:
            try:
                from models.sequence import build_sequences

                X, _y, meta = build_sequences(df)
                p_seq_raw = self.ensemble.seq.predict_proba(X)
                for (sym, ts), p in zip(meta, p_seq_raw):
                    p_seq_by_symbol[sym] = float(p)   # later dates overwrite -> latest wins
            except Exception as exc:
                log.warning("sequence scoring unavailable (%s); tabular-only", exc)

        tw, sw = self.ensemble.tab_weight, (
            self.ensemble.seq_weight if p_seq_by_symbol else 0.0)
        tot_w = tw + sw
        confidence = []
        for i, sym in enumerate(day["symbol"]):
            ps = p_seq_by_symbol.get(sym)
            if ps is None:
                confidence.append(float(p_tab.iloc[i]))
            else:
                confidence.append((tw * float(p_tab.iloc[i]) + sw * ps) / tot_w)
        out = day.assign(confidence=confidence)
        if "close" not in out.columns:
            raise ValueError(
                "snapshot frame lacks 'close' — refusing to emit signals with "
                "fabricated reference prices (would corrupt paper fills)")
        if "rvol_21d" in out.columns:
            out["daily_vol_pct"] = (out["rvol_21d"] / np.sqrt(252)).clip(0.2, 8)
        else:
            out["daily_vol_pct"] = 1.5
        cols = ["symbol", "sector", "confidence", "close", "daily_vol_pct"]
        keep = [c for c in cols if c in out.columns]
        extra = [c for c in ("nifty_mom_5d", "change_pct", "ret_5d", "ret_1d",
                             "rsi_14", "bb_pctb", "sector_rel_mom", "rvol_21d")
                 if c in out.columns]
        return out[keep + extra].reset_index(drop=True)


def make_default_provider():
    return SnapshotSignalProvider()
