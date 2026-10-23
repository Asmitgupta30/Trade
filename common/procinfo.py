"""Process hygiene: identity, liveness, locks, and a live system-status file.

Born from a real incident: Store-Python hides command lines from WMI/CIM
queries, so "kill by name pattern" silently failed and a zombie kept running
for hours. Rules enforced here:

  * every long-running process writes var/status/<name>.json containing its
    PID, git short-hash, start time, and role — BEFORE doing real work
  * acquire_lock() refuses to start when a LIVE pid-file already exists
    (liveness checked via tasklist, not command-line filters)
  * update_status() maintains var/status/system_status.json as the single
    source of truth for humans ("what's running, how far along, since when")
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS_DIR = ROOT / "var" / "status"


def git_short_hash() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True,
                             cwd=str(ROOT), timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    pid = int(pid)
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=15)
            return f'"{pid}"' in out.stdout
        except Exception:
            return False
    else:
        # Cross-platform POSIX (Linux / macOS)
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (ProcessLookupError, OSError):
            return False


def port_bound(port: int) -> bool:
    """Cross-check for servers: does something actually listen on `port`?"""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            result = s.connect_ex(("127.0.0.1", int(port)))
            return result == 0
    except Exception:
        return False


def write_startup(name: str, role: str, extra: dict | None = None) -> dict:
    """Log a startup line AND drop var/status/<name>.json with PID+git+time.

    Returns the info dict so callers can embed it elsewhere.
    """
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    info = {
        "name": name,
        "role": role,
        "pid": os.getpid(),
        "git": git_short_hash(),
        "started_at": _now_iso(),
    }
    if extra:
        info.update(extra)
    path = STATUS_DIR / f"{name}.json"
    path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    logging.getLogger(name).info(
        "STARTUP %s pid=%s git=%s role=%s", name, info["pid"], info["git"], role)
    print(f"[startup] {name} pid={info['pid']} git={info['git']} role={role}")
    return info


def acquire_lock(name: str, kill_stale: bool = False) -> bool:
    """Refuse duplicate long-running instances.

    Returns True if THIS instance may proceed. If a stale pid-file points to a
    live PID and kill_stale is False, returns False (caller must exit).
    """
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    path = STATUS_DIR / f"{name}.json"
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
        prev_pid = prev.get("pid")
        if pid_alive(prev_pid):
            if not kill_stale:
                logging.getLogger(name).error(
                    "REFUSING to start: %s already running (pid=%s, started %s)",
                    name, prev_pid, prev.get("started_at"))
                return False
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(int(prev_pid)), "/T", "/F"],
                               capture_output=True, text=True)
            else:
                import signal
                try:
                    os.kill(int(prev_pid), signal.SIGTERM)
                except Exception:
                    pass
            time.sleep(2)
            if pid_alive(prev_pid):
                return False
        # stale file: overwrite below
    write_startup(name, role="lock-acquired")
    return True


def release_lock(name: str) -> None:
    path = STATUS_DIR / f"{name}.json"
    if path.exists():
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
            info["stopped_at"] = _now_iso()
            info["pid"] = None
            path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        except Exception:
            pass


def update_status(running: dict | None = None, progress: dict | None = None,
                  notes: str | None = None, **extra) -> None:
    """Single always-current status file for the human.

    Unknown keyword arguments are merged as top-level keys (e.g.
    universe_coverage=...) so subsystems can surface structured state without
    this module knowing their schemas.
    """
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    path = STATUS_DIR / "system_status.json"
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current.update({
        "code_version": git_short_hash(),
        "last_verified": _now_iso(),
    })
    if running is not None:
        current["running"] = running
    if progress is not None:
        current.setdefault("progress", {}).update(progress)
    if notes is not None:
        current["notes"] = notes
    for k, v in extra.items():
        current[k] = v
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")


def heartbeat_loop(stop_event, interval: float = 30.0):
    """Refresh last_verified periodically; call from a daemon thread."""
    while not stop_event.is_set():
        update_status()
        stop_event.wait(interval)
