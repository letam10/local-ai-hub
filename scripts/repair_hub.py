"""
/*
  FILE NOTE
  - Mục đích: CLI tool để kiểm tra và sửa chữa an toàn Local AI Hub desktop app
  - Liên kết trực tiếp: src/services/diagnostics/repair_engine.py
  - Vùng ảnh hưởng khi sửa: Thao tác bảo trì và phục hồi cấu hình ứng dụng
*/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.diagnostics.repair_engine import DesktopRepairEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI Hub Desktop Repair CLI")
    parser.add_argument("--repair", action="store_true", help="Execute repair plan (default: report and plan only)")
    parser.add_argument("--hub-root", type=Path, default=ROOT, help="Local AI Hub root directory")

    args = parser.parse_args()
    engine = DesktopRepairEngine(args.hub_root)

    if not args.repair:
        report = engine.report()
        plan = engine.plan_repair()
        print("=== LOCAL AI HUB INSPECTION REPORT ===")
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("\n=== REPAIR PLAN ===")
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    result = engine.execute_repair(confirmed=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
