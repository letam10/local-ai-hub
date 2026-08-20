"""
/*
  FILE NOTE
  - Mục đích: CLI tool để cập nhật Local AI Hub an toàn từ file update zip
  - Liên kết trực tiếp: src/services/updater/engine.py, scripts/build_installer.py
  - Vùng ảnh hưởng khi sửa: Cập nhật ứng dụng từ dòng lệnh / script
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

from src.services.updater.engine import UpdateEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI Hub Safe Updater CLI")
    parser.add_argument("package", type=Path, help="Path to update ZIP archive")
    parser.add_argument("--apply", action="store_true", help="Apply update (default: dry-run plan only)")
    parser.add_argument("--hub-root", type=Path, default=ROOT, help="Local AI Hub root directory")

    args = parser.parse_args()
    engine = UpdateEngine(args.hub_root)

    if not args.apply:
        plan = engine.plan_update(args.package)
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0 if plan.get("accepted") else 1

    result = engine.apply_update(args.package, confirmed=True)
    print(f"Status: {result.status} (accepted={result.accepted})")
    print(f"Reason: {result.reason}")
    print(f"Updated {len(result.updated_files)} files.")
    print(f"Preserved data roots: {', '.join(result.preserved_roots)}")
    if result.errors:
        print(f"Errors: {result.errors}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
