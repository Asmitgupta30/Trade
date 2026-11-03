r"""Weekly digest generator (Section G).

Writes var/reports/weekly_digest_<date>.md — the human-readable artifact for
weeks the user doesn't check in. Contains: live-ready gate progress, retrain
decisions that week, universe-coverage exceptions, forward-holdout status,
and the current confidence-gate recommendation state.

    python scripts/weekly_digest.py [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "var" / "reports"


def _gate_section(paper) -> list[str]:
    gate = paper.gate_status()
    g = gate.criteria
    lines = [
        "## Live-ready gate progress", "",
        f"- Status badge: **{'LIVE-READY' if gate.live_ready else 'NOT YET LIVE-READY'}**",
        f"- Days tracked: {gate.days_tracked} / {g['min_paper_days']} "
        f"({g['days_remaining']} remaining)",
        f"- Suggestions tracked: {gate.suggestions_tracked} / "
        f"{g['min_suggestions']} ({g['suggestions_remaining']} remaining)",
        f"- Hit-rate: {gate.hit_rate} (need >= {g['hit_rate_min']}) | "
        f"Sharpe: {gate.sharpe} (need >= {g['sharpe_min']}) | "
        f"MaxDD: {gate.max_drawdown_pct}% (need <= {g['max_drawdown_max_pct']}%)",
    ]
    if gate.live_ready:
        lines.append("")
        lines.append("**STOP-CONDITION EVENT: gate reached. This is a direct "
                     "report trigger per project rules — nothing happens "
                     "automatically beyond this notice.**")
    return lines


def _retrains_this_week(paper) -> list[str]:
    from models.challenger import load_history

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    events = [e for e in load_history(limit=20)
              if (e.get("ts") or "") >= cutoff and not e.get("voided")]
    voided = [e for e in load_history(limit=20) if e.get("voided")]
    lines = ["## Retrain decisions this week", ""]
    if not events:
        lines.append("- None this week.")
    for e in events:
        c, ch = e.get("champion", {}), e.get("challenger", {})
        d = e.get("deltas", {})
        lines.append(
            f"- {e.get('ts', '')[:19]}Z **{'PROMOTED' if e.get('promoted') else 'REJECTED'}** — "
            f"champion S={c.get('sharpe')} hit={c.get('hit_rate')} dd={c.get('max_dd_pct')}% | "
            f"challenger S={ch.get('sharpe')} hit={ch.get('hit_rate')} dd={ch.get('max_dd_pct')}% | "
            f"deltas S={d.get('sharpe')} hit={d.get('hit_rate')} (floor {d.get('noise_floor_sharpe')})")
    if voided:
        lines.append(f"- ({len(voided)} earlier decision(s) marked VOIDED — see ledger reasons.)")
    return lines


def _coverage_section() -> list[str]:
    path = ROOT / "var" / "status" / "universe_coverage.json"
    lines = ["## Universe coverage", ""]
    if not path.exists():
        lines.append("- Not yet computed (runs at backend startup).")
        return lines
    cov = json.loads(path.read_text(encoding="utf-8"))
    counts = cov["counts"]
    lines.append(f"- Active: {counts['active']}/{cov['total']} validated symbols streaming & loggable")
    if cov["exceptions"]:
        lines.append("- Exceptions (explicit, never silent):")
        for e in cov["exceptions"]:
            lines.append(f"  - **{e['symbol']}**: {e['reason']}")
    else:
        lines.append("- No exceptions.")
    return lines


def _holdout_section(paper) -> list[str]:
    from paper_trading.forward_holdout import (
        REPORT_PATH as SECTION_E_PATH,
        _next_monday_iso, load_registrations, update_all_registrations,
    )
    from paper_trading.store import PaperStore

    regs = load_registrations()
    lines = ["## Confidence-gate recommendation status (Section E protocol)", ""]
    store = PaperStore()
    for r in regs:
        outcomes = paper.suggestion_outcomes_since(r["accrual_start"] + "T00:00:00")
        ev = update_all_registrations(outcomes)
        ev_map = ev[0] if ev else {}
        lines.append(
            f"- Candidate gate **{r['candidate_gate']}** vs default {r['default_gate']} — "
            f"status: **{r['status'].upper()}** (accrual from {r['accrual_start']}; "
            f"candidate arm n={ev_map.get('candidate_arm', {}).get('n', 0)}, "
            f"default arm n={ev_map.get('default_arm', {}).get('n', 0)})")

    try:
        current = SECTION_E_PATH.read_text(encoding="utf-8")
        block = ["<!-- AUTO-UPDATE-BLOCK-START -->",
                 f"_Auto-update: {datetime.now(timezone.utc).isoformat()}_"]
        for r in regs:
            ev = evaluate_latest(r, paper)
            block.append(f"- {r['candidate_gate']} vs {r['default_gate']}: "
                         f"{json.dumps(ev)}")
        block.append("<!-- AUTO-UPDATE-BLOCK-END -->")
        import re

        updated = re.sub(
            r"<!-- AUTO-UPDATE-BLOCK-START -->.*?<!-- AUTO-UPDATE-BLOCK-END -->",
            "\n".join(block), current, flags=re.S)
        SECTION_E_PATH.write_text(updated, encoding="utf-8")
    except Exception as exc:
        lines.append(f"- (section_e auto-update failed: {exc})")
    return lines


def evaluate_latest(r, paper):
    from paper_trading.forward_holdout import evaluate_registration

    outcomes = paper.suggestion_outcomes_since(r["accrual_start"] + "T00:00:00")
    return evaluate_registration(r, outcomes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="write even if one was already written today")
    args = ap.parse_args()

    from common.procinfo import git_short_hash, pid_alive
    from paper_trading.engine import PaperEngine
    from paper_trading.store import PaperStore

    cfg = load_risk_config = None
    import common.procinfo as procinfo
    from common.config import load_risk_config

    cfg = load_risk_config()
    paper = PaperEngine(cfg)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = REPORT_DIR / f"weekly_digest_{today}.md"
    if out_path.exists() and not args.force:
        print(f"digest already exists: {out_path}")
        return 0

    lines = [f"# Weekly Digest — {today}",
             f"_code {procinfo.git_short_hash()} · generated "
             f"{datetime.now(timezone.utc).isoformat()}_", "",
             "Advisory-only system. Nothing here places orders.", ""]
    lines += _gate_section(paper)
    lines += [""] + _retrains_this_week(paper)
    lines += [""] + _coverage_section()
    lines += [""] + _holdout_section(paper)
    lines += ["", "---",
              "Next steps are mechanical: accrual continues, next digest in 7 days.",
              "The final decision report (var/reports/final_decision_report.md) "
              "regenerates weekly until the gate resolves."]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"written -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
