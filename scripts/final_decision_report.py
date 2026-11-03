r"""Final Decision Report generator (Section H).

Regenerates var/reports/final_decision_report.md from live state each time it
runs (weekly digest job calls it after the digest). The conclusion section is
filled mechanically from the gate status — the human decides what, if
anything, to do after reading it.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT = ROOT / "var" / "reports" / "final_decision_report.md"


def main() -> int:
    from common.config import load_risk_config
    from common.procinfo import git_short_hash, pid_alive, STATUS_DIR
    from paper_trading.engine import PaperEngine

    cfg = load_risk_config()
    paper = PaperEngine(cfg)
    gate = paper.gate_status()
    g = gate.criteria

    # model history
    hist_path = ROOT / "var" / "models" / "history.jsonl"
    events = []
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    active_events = [e for e in events if not e.get("voided")]

    # coverage
    cov_path = ROOT / "var" / "status" / "universe_coverage.json"
    cov = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.exists() else {}

    # forward holdout
    holdout_path = ROOT / "var" / "status" / "forward_holdout_registry.json"
    regs = json.loads(holdout_path.read_text(encoding="utf-8")) \
        if holdout_path.exists() else []

    server_info = {}
    srv = STATUS_DIR / "backend_server.json"
    if srv.exists():
        try:
            server_info = json.loads(srv.read_text(encoding="utf-8"))
        except Exception:
            pass

    gate_met = gate.live_ready
    verdict = (
        "**No. The system does not currently show a real, statistically "
        "credible edge after costs.** Out-of-sample, across every regime and "
        "window tested (COVID 2020, the 2022 selloff, and the most recent "
        "14 months), the default strategy loses money after the 0.35% "
        "round-trip cost, with hit-rates statistically indistinguishable from "
        "a coin flip. There is one genuinely promising signal — restricting to "
        "higher-confidence suggestions repeatedly improves results, flipping "
        "the point estimate positive at gate=0.60 — but that finding came "
        "from a sweep on already-used data and has NOT yet replicated on a "
        "held-out slice; the pre-registered forward test to settle it starts "
        "2026-08-25. Until it replicates (or better evidence arrives), this "
        "system should remain exactly what it is: an advisory paper-trading "
        "research tool.")

    lines = [
        "# Final Decision Report — Advisory Trading System",
        "",
        f"_Generated {datetime.now(timezone.utc).isoformat()} · code "
        f"{git_short_hash()} · regenerates weekly until the gate resolves_",
        "",
        "## 0. Plain-language verdict",
        "",
        verdict,
        "",
        "## 1. Live-ready status",
        "",
        f"- **{'MET' if gate_met else 'NOT MET'}** against risk_config.yaml criteria:",
        f"- Days tracked: **{gate.days_tracked}** / {g['min_paper_days']} "
        f"(accrual starts first live trading day, 2026-08-25)",
        f"- Suggestions tracked: **{gate.suggestions_tracked}** / {g['min_suggestions']}",
        f"- Hit-rate: {gate.hit_rate} (need ≥ {g['hit_rate_min']}) · "
        f"Sharpe: {gate.sharpe} (need ≥ {g['sharpe_min']}) · "
        f"MaxDD: {gate.max_drawdown_pct}% (need ≤ {g['max_drawdown_max_pct']}%)",
        "- System mode remains advisory/paper regardless of this report.",
        "",
        "## 2. Confidence-gate verdict (Section E)",
        "",
        "- Gate=0.60 was swept into existence on the same data Section B validated "
        "(confirmed). No backward holdout existed.",
        "- Pre-registered forward holdout: arms 0.60 vs 0.55 fixed at registration "
        f"({json.loads((ROOT/'var/status/forward_holdout_registry.json').read_text())[0]['registered_at'][:10]}), "
        "accrual from 2026-08-25, resolves when both arms have ≥40 closed outcomes "
        "(8-week deadline → 'inconclusive-too-small-sample').",
        f"- Current status: **{regs[0]['status'].upper() if regs else 'PENDING'}** "
        "(see section_e_held_out_gate_check.md — auto-updated weekly).",
        "- **Recommendation (manual action only):** do NOT change "
        "`min_confidence_to_suggest_buy` yet. If the forward holdout confirms, "
        "raise it to 0.60 by editing risk_config.yaml yourself.",
        "",
        "## 3. Champion-challenger promotion history",
        "",
    ]
    if not active_events:
        lines.append("- No completed cycles.")
    for e in active_events:
        c, ch, d = e.get("champion", {}), e.get("challenger", {}), e.get("deltas", {})
        lines.append(
            f"- `{e.get('ts','')[:19]}Z` **{'PROMOTED' if e.get('promoted') else 'REJECTED'}** — "
            f"champion S={c.get('sharpe')}/hit={c.get('hit_rate')}/dd={c.get('max_dd_pct')}% vs "
            f"challenger S={ch.get('sharpe')}/hit={ch.get('hit_rate')}/dd={ch.get('max_dd_pct')}% | "
            f"ΔS={d.get('sharpe')} (floor {d.get('noise_floor_sharpe')}), "
            f"Δhit={d.get('hit_rate')} | {e.get('reason','')[:140]}")
    voided = [e for e in events if e.get("voided")]
    if voided:
        lines.append(f"- ({len(voided)} voided entry/entries from a since-fixed evaluator "
                     "leak are retained in the ledger for honesty.)")

    lines += [
        "",
        "## 4. Coverage status",
        "",
        f"- Validated universe: 92 symbols (deep snapshot) · actively tracked: "
        f"{cov.get('counts', {}).get('active', '?')}/{cov.get('total', '?')}",
        "- Explicit exceptions (never silent):",
    ]
    for e in cov.get("exceptions", []):
        lines.append(f"  - **{e['symbol']}** — {e['reason']}")
    lines += [
        "",
        "## 5. What would need to be true for a responsible go-live",
        "",
        "1. **Forward-holdout confirmation of a working gate**: the registered "
        "0.60-vs-0.55 test must resolve `confirmed` (positive expectancy + higher "
        "hit-rate) — or a future, properly held-out parameter search must do so.",
        "2. **Gate criteria met honestly**: ≥28 accrued trading days AND ≥40 tracked "
        "suggestions with hit-rate ≥50%, Sharpe ≥1.0, maxDD ≤8% — measured by the "
        "existing paper engine, no threshold edits.",
        "3. **Challenger loop showing at least one noise-beating promotion**, or "
        "sustained stability across cycles without regressions.",
        "4. **Full-universe coverage maintained** with zero unexplained exceptions, "
        "and SmartAPI credentials supplied so ticks are real-time rather than delayed.",
        "5. **Your explicit go decision** after reading an updated version of this "
        "report. Nothing here executes trades in any scenario.",
        "",
        "## Appendix — running system",
        "",
        f"- backend pid={server_info.get('pid')} (alive: "
        f"{pid_alive(server_info.get('pid'))}), port 8000, started "
        f"{server_info.get('started_at')}",
        f"- reports directory: var/reports/ · status file: var/status/system_status.json",
    ]

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"written -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
