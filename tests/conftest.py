"""Session-wide test isolation.

MUST run before anything imports backend.app / paper_trading (which create
module-level engines bound to the production database). Points the paper
store at a scratch file so tests can never pollute real accrual data.
"""

import os
import tempfile
from pathlib import Path

_SCRATCH = Path(tempfile.gettempdir()) / "trade_test_paper.sqlite3"
if _SCRATCH.exists():
    try:
        _SCRATCH.unlink()
    except PermissionError:
        pass
os.environ["PAPER_DB_PATH"] = str(_SCRATCH)

