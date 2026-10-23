r"""Run the whole system as one persistent process:

  * live data stream (SmartAPI when credentials present, else Yahoo)
  * FastAPI backend + UI on http://127.0.0.1:8000
  * APScheduler firing the four risk_config check-ins (IST market days)
  * weekly governed challenger retrain (Saturday 06:00 IST)

Usage: python scripts/run_system.py   (or .\make.bat run)
Stop with Ctrl+C.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import threading

    import uvicorn

    import common.procinfo as procinfo

    # stale-instance guard: pid-file liveness + actual port binding
    if not procinfo.acquire_lock("backend_server"):
        print("REFUSING to start: backend_server appears already running.")
        print("check var/status/backend_server.json or the port binding.")
        return 2
    procinfo.update_status(
        running={"backend_server": {"since": procinfo._now_iso(),
                                    "port": 8000}},
        notes="system serving")

    stop = __import__("threading").Event()
    threading.Thread(target=procinfo.heartbeat_loop, args=(stop,),
                     daemon=True).start()

    print("=" * 68)
    print(" Trading Advisor - ADVISORY ONLY (no order execution exists)")
    print(f" git={procinfo.git_short_hash()} pid-file=var/status/backend_server.json")
    print(" UI:      http://127.0.0.1:8000")
    print(" Checkins: 09:00 / 12:30 / 15:00 / 15:45 IST + weekly retrain Sat 06:00")
    print("=" * 68)
    try:
        uvicorn.run("backend.app:app", host="127.0.0.1", port=8000,
                    log_level="info")
    finally:
        stop.set()
        procinfo.release_lock("backend_server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
