"""Universe coverage check (Section F): classify + surface, never silent.

    python scripts/check_universe_coverage.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from common.procinfo import update_status
from data.lifecycle import merge_into_system_status, refresh_coverage
from features.universe import load_universe_symbols


def main() -> int:
    result = refresh_coverage(load_universe_symbols())
    merge_into_system_status(result)
    c = result["counts"]
    print(f"active={c['active']} known_rename={c['known_rename']} "
          f"stale_no_data={c['stale_no_data']} total={result['total']}")
    for e in result["exceptions"]:
        print(f"  EXCEPTION {e['symbol']}: {e['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
