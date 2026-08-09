from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
PORT = 8765


def ready() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.3):
            return True
    except OSError:
        return False


def main() -> int:
    if ready():
        return 0
    python = os.environ.get("LOCALAIHUB_PYTHON") or sys.executable
    log_path = ROOT / "Logs" / "api_server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    subprocess.Popen(
        [python, "-m", "Hub.api_server"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    for _ in range(20):
        time.sleep(0.15)
        if ready():
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
