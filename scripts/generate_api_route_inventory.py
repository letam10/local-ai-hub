"""Generate the sanitized machine-readable V7 API ownership inventory."""

from __future__ import annotations

from pathlib import Path
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.api.route_inventory import RouteMetadata, legacy_routes, validate_metadata
from src.services.api.router_registry import build_router


def rows() -> list[dict[str, object]]:
    registered = [
        RouteMetadata(
            route_id=route.route_id, method=route.method, path=route.path,
            domain=route.domain, owner=route.owner, service=route.domain,
            streaming=route.streaming, transport="router",
        )
        for route in build_router().routes()
    ]
    all_rows = registered + list(legacy_routes())
    validate_metadata(all_rows)
    return [row.as_dict() for row in sorted(all_rows, key=lambda item: (item.method, item.path, item.route_id))]


def main() -> int:
    target = ROOT / "architecture" / "api_routes.yaml"
    payload = {"schema_version": "api-routes.v1", "generated_by": "scripts/generate_api_route_inventory.py", "routes": rows()}
    # JSON is a strict YAML 1.2 subset, so the .yaml file stays consumable by
    # standard YAML tooling without adding PyYAML to the Hub runtime.
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"generated {len(payload['routes'])} routes -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
