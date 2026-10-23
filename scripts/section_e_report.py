r"""Generate var/reports/section_e_held_out_gate_check.md.

Verdict-first document. Current verdict: INCONCLUSIVE — PENDING FORWARD
HOLDOUT (no backward holdout exists; forward accrual starts 2026-08-25 and
resolves mechanically via paper_trading.forward_holdout).

Also compiles the NON-CONFIRMATORY multi-window robustness readout from the
already-computed Section B / regime-probe gate sweeps (labeled as such).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "var" / "reports" / "section_e_held_out_gate_check.md"
DEEP_JSON = ROOT / "var" / "reports" / "full_validation_report_deep.json"


def main() -> int:
    sweep = []
    if DEEP_JSON.exists():
        deep = json.loads(DEEP_JSON.read_text(encoding="utf-8"))
        sweep = deep.get("gate_sweep", [])

    by_gate = {r["gate"]: r for r in sweep if r.get("gate") is not None}
    g55, g60 = by_gate.get(0.55), by_gate.get(0.6, by_gate.get(0.60))

    regs = json.loads((ROOT / "var" / "status" /
                       "forward_holdout_registry.json").read_text(encoding="utf-8"))
    reg = next((r for r in regs if r["source"] == "section_e_pre_registration"),
               regs[0] if regs else {})

    md = []
    md.append("# Section E — Confidence-Gate Held-Out Check")
    md.append("")
    md.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    md.append("")
    md.append("## VERDICT: INCONCLUSIVE — PENDING FORWARD HOLDOUT")
    md.append("")
    md.append(
        "**Gate=0.60 was selected by sweeping the same deep 6.6-year dataset that "
        "Section B validated on — confirmed explicitly.** The dataset's final frozen "
        "bar is 2026-08-20 and this run executes on 2026-08-23: **no backward "
        "held-out slice exists**, because every historical window was consumed "
        "either as training data or inside Section B/D's evaluation folds. Per the "
        "new methodology rule, 0.60's positive point estimate (+0.13 net Sharpe, "
        "60.3% hit-rate, n=78) is therefore a **hypothesis, not a finding**.")
    md.append("")
    md.append(
        "The genuine test is **pre-registered and forward-looking**: arms fixed at "
        f"0.60 (candidate) vs 0.55 (default); accrual starts **{reg.get('accrual_start')}** "
        f"(first live trading day after registration); verdict resolves mechanically "
        f"once both arms have ≥{reg.get('min_n_per_arm', 40)} closed outcomes "
        f"(deadline {reg.get('deadline_weeks', 8)} weeks → 'inconclusive-too-small-sample' "
        "otherwise). Replication = candidate-arm Sharpe > 0 AND candidate hit-rate > "
        "default-arm hit-rate. No other gate value will be tested against this slice — "
        "**if 0.60 does not replicate, the honest conclusion is that no confirmed edge "
        "exists at any gate yet.** The auto-update block at the bottom of this file "
        "refreshes weekly with accrued numbers.")
    md.append("")
    md.append("## Non-confirmatory robustness readout (do NOT treat as validation)")
    md.append("")
    md.append("Compiled from the same-data sweeps, for prior-setting only:")
    md.append("")
    md.append("| window | gate 0.55 Sharpe | gate 0.60 Sharpe | direction | picks@0.60 |")
    md.append("|---|---|---|---|---|")
    if g55 and g60:
        wins = 1 if (g60["sharpe"] or -9) > (g55["sharpe"] or 9) else 0
        md.append(f"| Deep OOS windows (2025-06..2026-08) | {g55['sharpe']} | "
                  f"{g60['sharpe']} | {'0.60 better' if wins else '0.55 better'} | "
                  f"{g60['picks']} |")
    md.append("| COVID probe (2020-06..2020-12) | -0.655 | -1.656 | 0.55 better | 87 |")
    md.append("| 2022 probe (2021-10..2022-12) | -1.175 | -0.612 | 0.60 better | 138 |")
    md.append("")
    md.append("**Directional stability: 2 of 3 windows favor 0.60 — mixed, weak, and "
              "all windows share training-data overlap with the models being gated. "
              "This is exactly why the forward holdout is required.**")
    md.append("")
    md.append("## Registration record")
    md.append("")
    md.append("```json")
    md.append(json.dumps(reg, indent=2))
    md.append("```")
    md.append("")
    md.append("<!-- AUTO-UPDATE-BLOCK-START -->")
    md.append("_No accrued forward-holdout data yet (accrual begins 2026-08-25)._")
    md.append("<!-- AUTO-UPDATE-BLOCK-END -->")

    REPORT.write_text("\n".join(md), encoding="utf-8")
    print(f"written -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
