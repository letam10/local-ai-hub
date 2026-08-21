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
from src.services.bootstrap_core import apply_core_bootstrap, bootstrap_core, inspect_core, plan_core_bootstrap  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline model-free Local AI Hub core bootstrap")
    parser.add_argument("--no-config", action="store_true", help="create roots but do not initialize missing local Config")
    args = parser.parse_args()
    paths = get_paths(app_root=ROOT)
    if args.no_config:
        inspection = inspect_core(paths=paths)
        plan = plan_core_bootstrap(paths=paths, initialize_config=False)
        result = {"schema_version": "core-bootstrap.v2", "status": inspection["core_runtime"]["status"].lower(), "execution": "not_run", "dry_run": True, "inspection": inspection, "plan": plan, "next_action": plan["next_action"]}
    else:
        result = bootstrap_core(paths=paths, initialize_config=True)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
