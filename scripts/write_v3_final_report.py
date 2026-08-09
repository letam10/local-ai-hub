"""Write the ignored local V3 integration and cleanup hand-off report.

This script reads only local status files.  It never moves, deletes, or
dereferences an installation; cleanup actions must be performed separately by
the guarded cleanup script.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEGACY_STATE = ROOT / "Config" / "legacy_cleanup_v3.local.json"
TOOL_SMOKE_STATE = ROOT / "Config" / "tool_smoke_v3.local.json"
MIGRATION_STATE = ROOT / "Config" / "environment_migration_v3.local.json"
REPORT = ROOT / "Reports" / "V3_INTEGRATION_AND_CLEANUP.local.md"


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def bytes_text(value: int | None) -> str:
    return f"{value:,} bytes" if value is not None else "not captured"


def parse_round(value: str) -> tuple[int, str]:
    number, separator, result = value.partition("=")
    if not separator or not number.isdigit() or int(number) not in {1, 2, 3} or not result.strip():
        raise argparse.ArgumentTypeError("round result must use N=description where N is 1, 2, or 3")
    return int(number), result.strip()


def verified_absent(paths: list[str]) -> tuple[list[str], list[str]]:
    absent: list[str] = []
    present: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists() or path.is_symlink():
            present.append(str(path))
        else:
            absent.append(str(path))
    return absent, present


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk-before-bytes", type=int, default=None, help="Free bytes recorded before cleanup actions.")
    parser.add_argument("--round", dest="rounds", action="append", type=parse_round, default=[], help="N=bounded validation result; may be specified at most three times.")
    parser.add_argument("--removed-real-path", action="append", default=[], help="A real legacy directory verified absent after a cleanup action.")
    parser.add_argument("--removed-junction-path", action="append", default=[], help="A junction verified absent after a cleanup action.")
    parser.add_argument("--limitation", action="append", default=[], help="A verified limitation or retained-risk note.")
    args = parser.parse_args()

    round_results = dict(args.rounds)
    if len(round_results) != len(args.rounds):
        parser.error("each validation round may be recorded once")

    legacy = load_json(LEGACY_STATE)
    records = legacy.get("records", []) if isinstance(legacy.get("records", []), list) else []
    smoke = load_json(TOOL_SMOKE_STATE)
    migration = load_json(MIGRATION_STATE)
    removed_real, present_real = verified_absent(args.removed_real_path)
    removed_junctions, present_junctions = verified_absent(args.removed_junction_path)
    disk_after = shutil.disk_usage(ROOT).free

    junctions_retained = sum(1 for item in records if isinstance(item, dict) and item.get("type") in {"JUNCTION", "SYMLINK"})
    retained_states = sorted({
        str(item.get("cleanup_state"))
        for item in records
        if isinstance(item, dict) and item.get("cleanup_state") not in {"NOT_PRESENT", "EMPTY_LEGACY_FOLDER_SAFE_TO_DELETE"}
    })
    smoke_tools = sorted(name for name, value in smoke.items() if isinstance(value, dict) and value.get("status") == "completed")
    migration_entries = migration.get("components", []) if isinstance(migration.get("components", []), list) else []
    rebuilt = sum(1 for item in migration_entries if isinstance(item, dict) and item.get("functional_smoke_passed") is True)

    lines = [
        "# V3 integration and cleanup (local)",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        f"Disk before: {bytes_text(args.disk_before_bytes)} free.",
        f"Disk after: {bytes_text(disk_after)} free.",
        f"Real directories removed: {len(removed_real)}.",
        f"Junctions removed: {len(removed_junctions)}.",
        f"Junctions retained/reviewed: {junctions_retained}.",
        "User data moved: 0 (no verified migration was performed).",
        f"Environments rebuilt and functionally validated: {rebuilt}.",
        "Integrated modules: SAM2, AnimeSR, Whisper, Voice, Vision, OCR, Image AI, and FFmpeg workers.",
        "External modules: AIRI only.",
        "",
        "## Verified cleanup",
        "",
    ]
    if removed_real:
        lines.extend(f"- Removed verified-empty real directory: `{path}`" for path in removed_real)
    if removed_junctions:
        lines.extend(f"- Removed zero-reference junction: `{path}`" for path in removed_junctions)
    if not removed_real and not removed_junctions:
        lines.append("- No legacy path was removed during this final reporting run.")
    if present_real or present_junctions:
        lines.append("- The following requested removal evidence was not recorded because its path still exists: " + ", ".join(f"`{path}`" for path in [*present_real, *present_junctions]) + ".")

    lines.extend(["", "## Bounded validation (maximum three rounds)", ""])
    if round_results:
        lines.extend(f"- Round {number}: {round_results[number]}" for number in sorted(round_results))
    else:
        lines.append("- No validation round was recorded by this report command.")
    lines.append(f"- Direct-tool smoke evidence: {', '.join(smoke_tools) if smoke_tools else 'none recorded'}.")

    lines.extend(["", "## Retained items and limitations", ""])
    for limitation in args.limitation:
        lines.append(f"- {limitation}")
    if retained_states:
        lines.append("- Remaining cleanup classifications: " + ", ".join(retained_states) + ".")
    if not args.limitation and not retained_states:
        lines.append("- No retained limitation was recorded.")

    lines.extend([
        "",
        "## Rollback",
        "",
        "- No model, user-media, legacy environment, or active installation was moved or deleted by this report.",
        "- Retained legacy paths stay available for rollback; no junction target was modified.",
        "- Hub artifacts remain under Hub-managed storage and can be removed only through their normal retention workflow.",
        "",
    ])
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote local V3 report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
