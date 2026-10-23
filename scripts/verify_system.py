"""Stage-gate verification: runs after the test suite in `make verify`.

Checks structural guarantees that unit tests can't fully express:
  1. risk_config hard locks intact
  2. no order-placement capability anywhere on the import surface
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORBIDDEN_PATTERNS = [
    "place_order", "placeorder", "placeOrder",
    "create_order", "createorder",
    "modify_order", "modifyorder",
    "cancel_order", "cancelorder",
    "regular_order", "squareoff", "square_off",
    "execute_order", "submit_order",
]

# Phase-2 hard lock: no code outside common/config may WRITE risk_config.yaml
# (a self-improving model must never loosen its own passing criteria).
RISK_CONFIG_WRITE_PATTERNS = [
    "yaml.dump", "yaml.safe_dump", "yaml.Dumper",
    "risk_config",  # only dangerous combined with write-mode opens, scanned below
]


def audit_no_execution_code() -> list[str]:
    """Grep the advisory codebase for order-placement API usage.

    The ONLY allowed mentions are (a) this audit script, (b) tests asserting
    absence, (c) comments/docs. Any functional call is a hard failure.
    """
    violations = []
    scan_dirs = [ROOT / "data", ROOT / "features", ROOT / "models",
                 ROOT / "advisor", ROOT / "paper_trading", ROOT / "backend"]
    for base in scan_dirs:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="ignore")
            for pat in FORBIDDEN_PATTERNS:
                for lineno, line in enumerate(text.splitlines(), start=1):
                    if pat in line and not line.strip().startswith("#"):
                        # allow string-literal references inside the guard itself
                        if "audit" in py.name or "no_execution" in line or "FORBIDDEN" in line:
                            continue
                        violations.append(f"{py.relative_to(ROOT)}:{lineno}: {line.strip()[:120]}")
    return violations


def audit_risk_config_immutable() -> list[str]:
    """No module outside common/config may write YAML or open risk_config for
    writing. Reading is fine; mutating the binding ruleset from code is not."""
    violations = []
    scan_dirs = [ROOT / "data", ROOT / "features", ROOT / "models",
                 ROOT / "advisor", ROOT / "paper_trading", ROOT / "backend",
                 ROOT / "scripts"]
    for base in scan_dirs:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if py.name == "verify_system.py":
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            if "yaml.dump" in text or "yaml.safe_dump" in text or "yaml.Dumper" in text:
                violations.append(f"{py.relative_to(ROOT)}: yaml write call found")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "risk_config" in line and re.search(
                        r"open\([^)]*['\"][wax]\+?['\"]", line):
                    violations.append(f"{py.relative_to(ROOT)}:{lineno}: "
                                      f"write-mode open referencing risk_config")
    return violations


# Counter-convention guard (see tests/test_counter_contracts.py): every
# status-counter increment in these modules must carry a `counter:` comment
# naming its exact condition. Born from two real label-vs-condition bugs
# ("unavailable" absorbing frozen-skips; "promoted" counting voided entries).
COUNTER_ANNOTATED_FILES = [
    "data/bhavcopy.py",
    "data/lifecycle.py",
    "models/challenger.py",
    "paper_trading/engine.py",
    "paper_trading/forward_holdout.py",
    "backend/app.py",
]
COUNTER_INCREMENT_RE = re.compile(r"(\+= 1\b|sum\(1 for)")


def audit_counter_annotations() -> list[str]:
    violations = []
    for rel in COUNTER_ANNOTATED_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        for lineno, line in enumerate(lines):
            stripped = line.strip()
            if not COUNTER_INCREMENT_RE.search(stripped):
                continue
            if stripped.startswith("#"):
                continue
            context = "\n".join(lines[max(0, lineno - 3):lineno + 1])
            if "# counter:" not in context and "# counter:" not in line:
                # allow plain loop/index bookkeeping only inside test files
                violations.append(
                    f"{rel}:{lineno + 1}: unannotated counter increment — "
                    f"add `# counter: <name> = <exact condition>` "
                    f"({stripped.strip()[:60]})")
    return violations


def main() -> int:
    failures = []

    from common.config import load_risk_config, RiskConfigError
    try:
        cfg = load_risk_config()
        print("[ok] risk_config loaded; autonomous_execution locked to false")
    except RiskConfigError as exc:
        failures.append(str(exc))

    violations = audit_no_execution_code()
    if violations:
        failures.append("ORDER-PLACEMENT API REFERENCES FOUND:\n  " + "\n  ".join(violations))
    else:
        print("[ok] no order-placement code path found in data/features/models/advisor/paper_trading/backend")

    rc_violations = audit_risk_config_immutable()
    if rc_violations:
        failures.append("RISK-CONFIG IMMUTABILITY VIOLATIONS:\n  " + "\n  ".join(rc_violations))
    else:
        print("[ok] risk_config.yaml is read-only from code (no yaml writes anywhere)")

    counter_violations = audit_counter_annotations()
    if counter_violations:
        failures.append("COUNTER-ANNOTATION VIOLATIONS:\n  " + "\n  ".join(counter_violations))
    else:
        print(f"[ok] all counter increments in {len(COUNTER_ANNOTATED_FILES)} "
              "protected modules carry `counter:` annotations")

    if failures:
        print("\nVERIFY FAILED:")
        for f in failures:
            print(" -", f)
        return 1
    print("\nverify: ALL CHECKS GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
