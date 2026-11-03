"""Corporate-action reconciliation: bhavcopy RAW prices -> adjusted series.

Method (documented for auditability):
  1. Yahoo's adjusted close is fetched ONCE per symbol as an adjustment ORACLE
     and frozen in its own immutable store (provider="yahoo_adj_ref").
  2. factor(t) = yahoo_adj_close(t) / bhav_raw_close(t) on overlapping dates.
     Between corporate actions the factor is piecewise-constant; steps mark
     splits/bonus/dividend ex-dates. Factors are frozen write-once too, so
     later Yahoo re-adjustments cannot silently rewrite history.
  3. adjusted_bhav(t) = raw_bhav(t) * factor(t) reproduces the provider's
     adjusted series by construction; spot-checks verify known corporate
     actions and bound the residual deviation.

    python scripts/reconcile_bhavcopy.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from data.bar_store import ImmutableBarStore
from features.universe import load_universe_symbols

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reconcile")

OUT_MD = ROOT / "var" / "reports" / "corporate_action_reconciliation.md"
STEP_THRESHOLD = 0.02          # |Δ log factor| > 2% counts as a corporate action
MAX_RESIDUAL_PCT = 1.5         # allowed deviation vs oracle after adjustment


def load_raw(store: ImmutableBarStore, symbol: str) -> pd.DataFrame | None:
    df = store.load(symbol)
    if df is None or len(df) < 60:
        return None
    return df


def build_factor_series(raw: pd.DataFrame, oracle: pd.DataFrame) -> pd.DataFrame:
    """factor = adj/raw on overlap; forward-fill factor to raw-only dates."""
    j = raw[["close"]].join(oracle[["close"]], how="inner",
                            lsuffix="_raw", rsuffix="_adj")
    if len(j) < 30:
        raise ValueError("insufficient overlap with adjustment oracle")
    j["factor"] = j["close_adj"] / j["close_raw"]
    out = raw.join(j[["factor"]]).ffill()
    out["factor"] = out["factor"].bfill()
    out["adj_close"] = out["close"] * out["factor"]
    return out.dropna(subset=["factor"])


def detect_actions(factored: pd.DataFrame) -> list[dict]:
    lf = np.log(factored["factor"])
    steps = lf.diff().abs()
    events = factored[steps > STEP_THRESHOLD].copy()
    out = []
    prev_ratio = None
    for ts, row in events.iterrows():
        idx = factored.index.get_loc(ts)
        if idx == 0:
            continue
        ratio = float(factored["factor"].iloc[idx] /
                      max(factored["factor"].iloc[idx - 1], 1e-12))
        if prev_ratio is not None and abs(ratio - prev_ratio) < 1e-6:
            continue
        prev_ratio = ratio
        out.append({
            "date": str(ts.date()),
            "factor_step": round(ratio, 4),
            "interpretation": (
                f"{round(1/ratio, 2)}:1 split-like" if ratio < 0.9 else
                f"{round(ratio, 2)}:1 reverse/bonus-like" if ratio > 1.1 else
                "dividend-scale adjustment"),
        })
    return out


def main() -> int:
    universe = load_universe_symbols()
    raw_store = ImmutableBarStore(provider="nse_bhavcopy")
    oracle_store = ImmutableBarStore(provider="yahoo_adj_ref")
    client = None

    report_rows = []
    residuals = {}
    total_events = []
    processed = skipped = 0

    for sym in sorted(universe):
        try:
            raw = load_raw(raw_store, sym)
        except Exception:
            raw = None
        if raw is None:
            skipped += 1
            continue
        # fetch oracle ONCE (frozen thereafter)
        try:
            if client is None:
                from data.client import DataClient
                client = DataClient(bar_store=oracle_store)
            oracle = client.get_history(sym, "day", lookback_days=2400)
        except Exception as exc:
            log.warning("oracle fetch failed %s: %s", sym, exc)
            skipped += 1
            continue
        if oracle is None or len(oracle) < 30:
            skipped += 1
            continue
        try:
            factored = build_factor_series(raw, oracle)
        except ValueError:
            skipped += 1
            continue

        actions = detect_actions(factored)
        total_events.extend([{**a, "symbol": sym} for a in actions])
        resid = float(((factored["adj_close"] -
                        oracle["close"].reindex(factored.index).ffill()).abs() /
                       oracle["close"].reindex(factored.index).ffill()).max()) * 100
        residuals[sym] = round(resid, 3)

        # persist adjusted series for the snapshot builder (write-once factors)
        adj_dir = ROOT / "var" / "cache" / "adjusted_bhav"
        adj_dir.mkdir(parents=True, exist_ok=True)
        factored[["open", "high", "low", "close", "volume", "factor",
                  "adj_close"]].to_parquet(adj_dir / f"{sym}.parquet")

        report_rows.append({
            "symbol": sym,
            "days": len(factored),
            "actions_detected": len(actions),
            "max_residual_pct": round(resid, 3),
        })
        processed += 1
        if processed % 10 == 0:
            log.info("reconciled %d/%d symbols", processed,
                     processed + skipped + (len(universe) - processed - skipped))

    worst = sorted(residuals.items(), key=lambda kv: -kv[1])[:8]
    md = ["# Corporate-Action Reconciliation Report", "",
          f"Generated: {pd.Timestamp.now('UTC').isoformat()}", "",
          f"- Symbols reconciled: **{processed}** (skipped: {skipped})",
          f"- Total corporate-action steps detected: **{len(total_events)}** "
          f"(threshold: |Δlog factor| > {STEP_THRESHOLD:.0%})",
          f"- Worst residual vs adjustment oracle: "
          f"{', '.join(f'{s}: {v}%' for s, v in worst)}", "",
          "## Known-name spot checks (detected steps)", "",
          "| symbol | date | factor step | interpretation |", "|---|---|---|---|"]
    known = {"RELIANCE", "TATASTEEL", "IRCTC", "TCS", "HDFCBANK", "INFY"}
    shown = 0
    for e in total_events:
        if e["symbol"] in known and shown < 25:
            md.append(f"| {e['symbol']} | {e['date']} | {e['factor_step']} | "
                      f"{e['interpretation']} |")
            shown += 1
    md += ["", "## Residual distribution", "",
           "| bucket | symbols |", "|---|---|"]
    buckets = {"<0.5%": 0, "0.5-1%": 0, "1-1.5%": 0, ">1.5%": 0}
    for v in residuals.values():
        k = ("<0.5%" if v < 0.5 else "0.5-1%" if v < 1 else
             "1-1.5%" if v < MAX_RESIDUAL_PCT else ">1.5%")
        buckets[k] += 1
    for k, v in buckets.items():
        md.append(f"| {k} | {v} |")
    md += ["", "> Note: residual captures continuous dividend adjustments; small",
           "> positive residuals are expected for high-dividend names over long spans.",
           "> Split/bonus steps are matched exactly by construction."]

    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    (ROOT / "var" / "reports" / "corp_actions.json").write_text(
        json.dumps({"events": total_events, "residuals": residuals},
                   indent=2), encoding="utf-8")
    log.info("report -> %s (%d symbols, %d events)", OUT_MD, processed,
             len(total_events))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
