"""Write-once, date-keyed OHLCV store with explicit corporate-action events.

Why: providers like Yahoo retroactively re-adjust history (splits/dividends),
so a naive refresh silently rewrites old bars. That makes backtests
unreproducible and can corrupt labels around adjustment dates. Policy here:

  * a (provider, symbol, interval, DATE) bar is FROZEN once written;
    refreshes may APPEND missing dates but never overwrite existing values
  * when a re-fetch disagrees with a frozen bar beyond tolerance, the event is
    recorded in adjustments.json (per symbol) and the EARLIER segment of the
    series is rescaled by the observed ratio so the served series stays
    internally consistent — visible, logged, never silent
  * every row records `fetched_at`; audits can see exactly when each bar froze

Layout: {root}/{provider}/{symbol}__{interval}/{YYYY-MM}.parquet
bhavcopy ingestion (Section C) reuses this exact store with provider="nse_bhavcopy".
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
DIVERGENCE_TOLERANCE = 0.001     # 0.1% relative — anything else is an event


def classify_action(ratio: float) -> str:
    """Heuristic label for WHY a frozen bar diverged from its refetch.

    This is a guess from price behavior alone, stored for audit purposes —
    grouping requires an exact type match, so a split and a data revision
    coincidentally detected together can never be merged into one adjustment.
    """
    r = float(ratio)
    if abs(r - 1.0) < 0.02:
        return "minor_data_revision"
    simple_fractions = (0.1, 0.2, 0.25, 1 / 3, 0.5, 0.75,
                        1.5, 2.0, 3.0, 4.0, 5.0, 10.0)
    for frac in simple_fractions:
        if abs(r - frac) / max(abs(frac), 1e-9) < 0.01:
            return "likely_split_or_bonus"
    return "unclassified_adjustment"


@dataclass
class MergeReport:
    appended_dates: int = 0
    frozen_kept: int = 0
    adjustment_events: list[dict] = field(default_factory=list)


class ImmutableBarStore:
    def __init__(self, root: Path | None = None, provider: str = "default"):
        base = root or Path(__file__).resolve().parent.parent / "var"
        self.dir = Path(base) / "barstore" / provider
        self.dir.mkdir(parents=True, exist_ok=True)
        self.provider = provider

    # ---------------- layout ----------------
    def _series_dir(self, symbol: str, interval: str) -> Path:
        safe = symbol.replace("/", "_").replace("\\", "_").replace("^", "IDX_")
        d = self.dir / f"{safe}__{interval}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _month_file(self, series_dir: Path, ts: pd.Timestamp) -> Path:
        return series_dir / f"{ts.year:04d}-{ts.month:02d}.parquet"

    def _adj_log_path(self, series_dir: Path) -> Path:
        return series_dir / "adjustments.json"

    # ---------------- read ----------------
    def load(self, symbol: str, interval: str = "day") -> pd.DataFrame | None:
        """Full frozen series (consistently rescaled), or None if empty."""
        series_dir = self._series_dir(symbol, interval)
        files = sorted(series_dir.glob("*.parquet"))
        if not files:
            return None
        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df = self._apply_rescaling(df, series_dir)
        return df[OHLCV_COLUMNS + ["fetched_at"]] if "fetched_at" in df.columns \
            else df[OHLCV_COLUMNS]

    def last_frozen_date(self, symbol: str, interval: str = "day") -> pd.Timestamp | None:
        df = self.load(symbol, interval)
        return None if df is None or df.empty else df.index.max()

    def has_date(self, symbol: str, ts: pd.Timestamp, interval: str = "day") -> bool:
        """Cheap membership check reading only the relevant month partition."""
        series_dir = self._series_dir(symbol, interval)
        f = self._month_file(series_dir, pd.Timestamp(ts))
        if not f.exists():
            return False
        try:
            part = pd.read_parquet(f)
            return pd.Timestamp(ts) in part.index
        except Exception:
            return False

    # ---------------- write-once merge ----------------
    def upsert_immutable(self, symbol: str, incoming: pd.DataFrame,
                         interval: str = "day") -> MergeReport:
        """Merge freshly fetched bars under write-once rules.

        Incoming values for ALREADY-FROZEN dates never overwrite storage.
        Material divergence is recorded as an adjustment event and, if it looks
        like a retroactive corporate-action adjustment, earlier bars are
        rescaled so the served series remains consistent.
        """
        if incoming.empty:
            return MergeReport()
        if not isinstance(incoming.index, pd.DatetimeIndex):
            raise TypeError("incoming frame requires DatetimeIndex")
        rep = MergeReport()
        series_dir = self._series_dir(symbol, interval)

        incoming = incoming[~incoming.index.duplicated(keep="last")].sort_index()
        now_iso = datetime.now(timezone.utc).isoformat()

        # group incoming rows by month file
        by_month: dict[Path, list[pd.Timestamp]] = {}
        for ts in incoming.index:
            f = self._month_file(series_dir, ts)
            by_month.setdefault(f, []).append(ts)

        events: list[dict] = []
        for month_file, stamps in by_month.items():
            existing = pd.read_parquet(month_file) if month_file.exists() else None
            new_rows = []
            for ts in stamps:
                row = incoming.loc[ts]
                if existing is not None and ts in existing.index:
                    rep.frozen_kept += 1
                    old = existing.loc[ts]
                    ratio = self._divergence_ratio(old, row)
                    if ratio is not None:
                        events.append({
                            "date": str(ts.date()),
                            "frozen_close": float(old["close"]),
                            "refetched_close": float(row["close"]),
                            "refetched_ohlcv": {c: float(row[c]) for c in OHLCV_COLUMNS},
                            "ratio": float(ratio),
                            "action_type": classify_action(ratio),
                            "detected_at": now_iso,
                        })
                    continue
                rec = {c: float(row[c]) for c in OHLCV_COLUMNS}
                rec["fetched_at"] = now_iso
                new_rows.append((ts, rec))
                rep.appended_dates += 1
            if new_rows:
                add = pd.DataFrame(
                    {c: [r[1][c] for r in new_rows] for c in OHLCV_COLUMNS + ["fetched_at"]},
                    index=pd.DatetimeIndex([r[0] for r in new_rows]))
                merged = add if existing is None else (
                    pd.concat([existing, add]).sort_index())
                merged = merged[~merged.index.duplicated(keep="first")]
                tmp = month_file.with_suffix(".tmp")
                merged.to_parquet(tmp)
                os.replace(tmp, month_file)

        if events:
            rep.adjustment_events = events
            self._record_events(series_dir, events)
            log.warning(
                "immutable store: %d divergence event(s) for %s %s — history NOT "
                "overwritten; earlier segment rescaled per event ratios",
                len(events), symbol, interval)
        return rep

    # ---------------- internals ----------------
    @staticmethod
    def _divergence_ratio(frozen: pd.Series, refetched: pd.Series) -> float | None:
        try:
            old_c = float(frozen["close"])
            new_c = float(refetched["close"])
        except (KeyError, TypeError, ValueError):
            return None
        if old_c <= 0 or new_c <= 0:
            return None
        ratio = new_c / old_c
        if abs(ratio - 1.0) <= DIVERGENCE_TOLERANCE:
            return None
        return ratio

    def _record_events(self, series_dir: Path, events: list[dict]) -> None:
        path = self._adj_log_path(series_dir)
        prior = []
        if path.exists():
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                prior = []
        seen = {(e.get("date"), round(float(e.get("ratio", 1)), 6),
                 e.get("action_type")) for e in prior}
        for e in events:
            key = (e.get("date"), round(float(e.get("ratio", 1)), 6),
                   e.get("action_type"))
            if key not in seen:
                prior.append(e)
                seen.add(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(prior, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def adjustment_events(self, symbol: str, interval: str = "day") -> list[dict]:
        path = self._adj_log_path(self._series_dir(symbol, interval))
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def _group_events(events: list[dict]) -> list[dict]:
        """Collapse divergences discovered in the SAME refresh into ONE action.

        Group key = (detected_at, ratio, action_type): a single corporate
        action makes many dates diverge in one upsert (shared detected_at);
        but two coincidentally-simultaneous UNRELATED actions — same moment,
        even the same ratio — stay separate unless their classified type also
        matches, so a split and a data revision can never merge.
        """
        if not events:
            return []
        groups: dict[tuple, list[dict]] = {}
        for e in events:
            key = (e.get("detected_at"), round(float(e["ratio"]), 9),
                   e.get("action_type", classify_action(float(e["ratio"]))))
            groups.setdefault(key, []).append(e)
        out = []
        for (_detected, ratio, _atype), g in groups.items():
            dates = [pd.Timestamp(e["date"]) for e in g]
            out.append({
                "date": min(dates),
                "ratio": float(ratio),
                "action_type": _atype,
                "detected_at": _detected,
                "n_dates": len(g),
                "subs": [(d, e.get("refetched_ohlcv", {}))
                         for d, e in zip(dates, g)],
            })
        out.sort(key=lambda x: x["date"])
        return out

    def _apply_rescaling(self, df: pd.DataFrame, series_dir: Path) -> pd.DataFrame:
        """Apply logged adjustment events at READ time (frozen bytes untouched).

        Semantics for a grouped event with earliest divergent date D, ratio r:
          * every bar STRICTLY BEFORE D is scaled by r (pre-action basis)
          * each divergent bar displays its REFETCHED values (its frozen bytes
            were captured on the old basis and remain intact on disk)
        Later discoveries compound via their own groups.
        """
        raw_events = self.adjustment_events_from_dir(series_dir)
        if not raw_events:
            return df
        groups = self._group_events(raw_events)
        out = df.copy()
        factor = pd.Series(1.0, index=out.index)
        substitute: dict[pd.Timestamp, tuple[dict, int]] = {}
        for gi, grp in enumerate(groups):
            cutoff = grp["date"]
            if cutoff.tzinfo is None:
                cutoff = cutoff.tz_localize("UTC")
            grp["cutoff"] = cutoff
            factor[out.index < cutoff] *= grp["ratio"]
            for ts, vals in grp["subs"]:
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                if vals:
                    # a substituted value reflects ONLY its own group's action;
                    # every OTHER group with a later cutoff still scales it.
                    substitute[ts] = (vals, gi)
        for col in ("open", "high", "low", "close"):
            out[col] = out[col] * factor
        for ts, (vals, source_gi) in substitute.items():
            if ts not in out.index:
                continue
            own_extra = 1.0
            for gj, grp in enumerate(groups):
                if gj != source_gi and ts < grp["cutoff"]:
                    own_extra *= grp["ratio"]
            for col in OHLCV_COLUMNS:
                out.loc[ts, col] = float(vals[col]) * own_extra
        return out.sort_index()

    def adjustment_events_from_dir(self, series_dir: Path) -> list[dict]:
        path = self._adj_log_path(series_dir)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
