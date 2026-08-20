"""
/*
  FILE NOTE
  - Mục đích: Desktop launcher entrypoint cho Local AI Hub — điều phối khởi động không console, kiểm tra môi trường, và chuyển tiếp vào main desktop shell
  - Liên kết trực tiếp: src/app/main.py, src/app/bootstrap.py, src/services/process_manager/windows.py, scripts/update_managed_shortcuts.ps1
  - Vùng ảnh hưởng khi sửa: Toàn bộ quá trình khởi động ứng dụng desktop từ shortcut Windows hoặc dòng lệnh
*/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.bootstrap import bootstrap
from src.app.main import main


def launch() -> int:
    """Bootstrap managed directories and run the native desktop shell."""
    try:
        bootstrap()
    except Exception as exc:
        print(f"Bootstrap initialization failed: {exc}", file=sys.stderr)
    return main()


if __name__ == "__main__":
    raise SystemExit(launch())
