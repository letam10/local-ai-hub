"""Direct-file entrypoint for the offline, model-free core bootstrap."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import argparse


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.platform.paths import get_paths  # noqa: E402
from src.services.bootstrap_core import bootstrap_core  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline model-free Local AI Hub core bootstrap")
    parser.add_argument("--no-config", action="store_true", help="create roots but do not initialize missing local Config")
    args = parser.parse_args()
    print(json.dumps(bootstrap_core(paths=get_paths(app_root=ROOT), initialize_config=not args.no_config), ensure_ascii=True, sort_keys=True))
