"""Generate the historical V2 migration manifest and host-local reports.

The script never moves, deletes, downloads, or executes a model.  It records
the reviewed inventory so ``migrate_layout_v2.ps1`` can perform only explicit,
same-volume high-confidence operations.

It is not a current runtime helper.  A completed host keeps its final local
evidence intact instead of regenerating a legacy migration plan.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "Config"
REPORTS = ROOT / "Reports"
MANIFEST_PATH = CONFIG / "layout_migration.local.json"


def record(
    component: str,
    display_name: str,
    category: str,
    classification: str,
    source_path: str | None,
    destination_path: str | None,
    *,
    model_sources: list[str] | None = None,
    model_destinations: list[str] | None = None,
    environment_path: str | None = None,
    move_strategy: str = "external_managed",
    rollback_strategy: str = "leave_in_place",
    legacy_junction_required: bool = False,
    confidence: str = "low",
    status: str = "external_managed",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "component": component,
        "display_name": display_name,
        "category": category,
        "classification": classification,
        "source_path": source_path,
        "destination_path": destination_path,
        "model_sources": model_sources or [],
        "model_destinations": model_destinations or [],
        "environment_path": environment_path,
        "size_bytes": 0,
        "processes_using_path": [],
        "move_strategy": move_strategy,
        "rollback_strategy": rollback_strategy,
        "legacy_junction_required": legacy_junction_required,
        "confidence": confidence,
        "status": status,
        "verified": False,
        "cleanup_allowed": False,
        "notes": notes,
    }


def path_text(path: Path) -> str:
    return str(path)


def installer_managed_path(application: str) -> str:
    """Resolve the current user's installer location without storing it in source."""

    local_app_data = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return path_text(local_app_data / "Programs" / application)


def planned_records() -> list[dict[str, Any]]:
    runtime = ROOT / "runtime"
    models = ROOT / "Models"
    service = ROOT / "Services"
    return [
        record(
            "flux-klein-studio-app", "FLUX Klein Studio / Local Image Studio", "image", "PORTABLE_APP",
            r"D:\AI\FLUX-Klein-Studio", path_text(runtime / "applications" / "FLUX-Klein-Studio"),
            model_sources=[r"D:\AI\FLUX-Klein-Studio\models"],
            model_destinations=[path_text(models / "Image" / "Shared-Local-Image-Studio")],
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="planned",
            notes="Self-contained portable image app; its output and model store move atomically without copying.",
        ),
        record(
            "anime-upscale-studio-app", "Anime Upscale Studio", "video", "PORTABLE_APP",
            r"D:\ẢNH VIDEO - AI\Anime Upscale Studio\dist\Anime Upscale Studio", path_text(runtime / "applications" / "Anime-Upscale-Studio"),
            model_sources=[r"D:\ẢNH VIDEO - AI\Anime Upscale Studio\dist\Anime Upscale Studio\runtime\AnimeSR\weights"],
            model_destinations=[path_text(models / "Video" / "AnimeSR")],
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="planned",
            notes="The deployed portable distribution is moved; the surrounding source/test-artifact tree stays in place.",
        ),
        record(
            "anime-upscale-build", "Anime Upscale Studio alternate build", "video", "PORTABLE_APP",
            r"D:\AI_4K_TEMP\AnimeUpscaleStudioBuild", path_text(runtime / "applications" / "Anime-Upscale-Studio-build"),
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="planned",
            notes="Separate portable build with no detected user media; retained through a legacy junction after move.",
        ),
        record(
            "sam2-engine", "SAM 2 source and checkpoint", "vision", "PORTABLE_ENGINE",
            r"D:\AI_4K_TEMP\sam2", path_text(runtime / "engines" / "vision" / "SAM2"),
            model_sources=[r"D:\AI_4K_TEMP\sam2\checkpoints"], model_destinations=[path_text(models / "Vision" / "SAM2")],
            environment_path=r"D:\AI_4K_TEMP\sam2_venv", move_strategy="atomic_directory_move",
            rollback_strategy="reverse_move_after_smoke", legacy_junction_required=True, confidence="high", status="planned",
            notes="The source tree and checkpoint move together. The non-relocatable venv remains external and legacy junctions preserve the GUI path.",
        ),
        record(
            "sam2-mask-studio-app", "SAM2 Mask Studio", "vision", "PORTABLE_APP",
            r"D:\AI_4K_TEMP\SAM2_Mask_Studio\app\SAM2 Mask Studio", path_text(runtime / "applications" / "SAM2-Mask-Studio"),
            environment_path=r"D:\AI_4K_TEMP\sam2_venv", move_strategy="atomic_directory_move",
            rollback_strategy="reverse_move_after_smoke", legacy_junction_required=True, confidence="high", status="planned",
            notes="Only the deployed GUI folder moves. Legacy project folders, smoke artifacts and user projects remain in place.",
        ),
        record(
            "practical-rife-engine", "Practical-RIFE", "video", "PORTABLE_ENGINE",
            r"D:\AI_4K_TEMP\Practical-RIFE", path_text(runtime / "engines" / "video" / "Practical-RIFE"),
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="planned",
            notes="Verified upstream repository structure; no running process was recorded during the reviewed inventory.",
        ),
        record(
            "real-esrgan-engine", "Real-ESRGAN", "video", "PORTABLE_ENGINE",
            r"D:\AI_4K_TEMP\Real-ESRGAN", path_text(runtime / "engines" / "video" / "Real-ESRGAN"),
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="planned",
            notes="Verified upstream repository structure; the small local weight stays with the portable engine until its model alias exists.",
        ),
        record(
            "omniparser-runtime-link", "OmniParser runtime", "vision", "SHARED_RUNTIME",
            path_text(service / "OmniParser"), path_text(runtime / "engines" / "vision" / "OmniParser"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Existing LocalAIHub service stays in place; canonical runtime path is a no-copy junction.",
        ),
        record(
            "rfdetr-runtime-link", "RF-DETR runtime", "vision", "SHARED_RUNTIME",
            path_text(service / "RF-DETR"), path_text(runtime / "engines" / "vision" / "RF-DETR"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Tracked wrapper remains in Services; canonical runtime path is a no-copy junction.",
        ),
        record(
            "groundingdino-runtime-link", "Grounding DINO runtime", "vision", "SHARED_RUNTIME",
            path_text(service / "GroundingDINO"), path_text(runtime / "engines" / "vision" / "GroundingDINO"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Existing LocalAIHub service stays in place; canonical runtime path is a no-copy junction.",
        ),
        record(
            "paddleocr-runtime-link", "PaddleOCR runtime", "vision", "SHARED_RUNTIME",
            path_text(service / "PaddleOCR"), path_text(runtime / "engines" / "vision" / "PaddleOCR"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Existing LocalAIHub service stays in place; canonical runtime path is a no-copy junction.",
        ),
        record(
            "whisper-runtime-link", "Faster-Whisper wrapper", "speech", "SHARED_RUNTIME",
            path_text(service / "Whisper"), path_text(runtime / "engines" / "speech" / "Faster-Whisper"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Tracked wrapper remains in Services; local backend installation is separately reported as unavailable.",
        ),
        record(
            "qwen3-tts-runtime-link", "Qwen3-TTS runtime", "voice", "SHARED_RUNTIME",
            path_text(service / "Qwen3-TTS"), path_text(runtime / "engines" / "voice" / "Qwen3-TTS"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Existing LocalAIHub service stays in place; canonical runtime path is a no-copy junction.",
        ),
        record(
            "seed-vc-runtime-link", "Seed-VC runtime", "voice", "SHARED_RUNTIME",
            path_text(service / "Seed-VC"), path_text(runtime / "engines" / "voice" / "Seed-VC"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="planned",
            notes="Existing LocalAIHub service stays in place; canonical runtime path is a no-copy junction.",
        ),
        record(
            "flux-source", "FLUX Klein Studio source", "image", "PORTABLE_APP",
            r"D:\ẢNH VIDEO - AI\FLUX Klein Studio", path_text(runtime / "applications" / "FLUX-Klein-Studio" / "source"),
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="deferred",
            notes="Moves only after the deployed FLUX application is under its canonical parent. The legacy source path receives a junction.",
        ),
        record(
            "flux-model-store", "Shared FLUX + Qwen Image model store", "image", "MODEL_STORE",
            r"D:\AI\FLUX-Klein-Studio\models", path_text(models / "Image" / "Shared-Local-Image-Studio"),
            model_sources=[r"D:\AI\FLUX-Klein-Studio\models"],
            model_destinations=[path_text(models / "Image" / "FLUX"), path_text(models / "Image" / "Qwen-Image")],
            move_strategy="atomic_directory_move", rollback_strategy="reverse_move_after_smoke",
            legacy_junction_required=True, confidence="high", status="deferred",
            notes="A shared physical store contains FLUX and Qwen Image files. Canonical family paths become no-copy junction aliases after move.",
        ),
        record(
            "sam2-model-link", "SAM 2 model alias", "vision", "MODEL_STORE",
            path_text(runtime / "engines" / "vision" / "SAM2" / "checkpoints"), path_text(models / "Vision" / "SAM2"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="Canonical model path aliases the checkpoint already moved with the SAM2 source tree.",
        ),
        record(
            "animesr-engine-link", "AnimeSR bundled engine", "video", "SHARED_RUNTIME",
            path_text(runtime / "applications" / "Anime-Upscale-Studio" / "runtime" / "AnimeSR"), path_text(runtime / "engines" / "video" / "AnimeSR"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="The portable Anime Upscale Studio distribution provides the managed engine without a second copy.",
        ),
        record(
            "animesr-model-link", "AnimeSR model alias", "video", "MODEL_STORE",
            path_text(runtime / "engines" / "video" / "AnimeSR" / "weights"), path_text(models / "Video" / "AnimeSR"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="Canonical model path aliases the deployed Studio's bundled AnimeSR weights.",
        ),
        record(
            "ffmpeg-runtime-link", "FFmpeg portable runtime", "media", "SHARED_RUNTIME",
            path_text(runtime / "applications" / "Anime-Upscale-Studio" / "runtime"), path_text(runtime / "tools" / "ffmpeg"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="FFmpeg, FFprobe and required sibling DLLs remain one portable runtime; the canonical tools path is a junction.",
        ),
        record(
            "comfyui-runtime-link", "ComfyUI shared runtime", "image", "SHARED_RUNTIME",
            path_text(runtime / "applications" / "FLUX-Klein-Studio" / "comfyui"), path_text(runtime / "engines" / "image" / "ComfyUI"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="FLUX and Qwen Image use one physical ComfyUI portable runtime.",
        ),
        record(
            "qwen-image-studio-link", "Qwen Image Studio alias", "image", "PORTABLE_APP",
            path_text(runtime / "applications" / "FLUX-Klein-Studio"), path_text(runtime / "applications" / "Qwen-Image-Studio"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="Qwen Image is hosted by the same Local Image Studio executable and model store as FLUX.",
        ),
        record(
            "flux-model-alias", "FLUX canonical model alias", "image", "MODEL_STORE",
            path_text(models / "Image" / "Shared-Local-Image-Studio"), path_text(models / "Image" / "FLUX"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="No-copy alias to the shared physical FLUX/Qwen model store.",
        ),
        record(
            "qwen-image-model-alias", "Qwen Image canonical model alias", "image", "MODEL_STORE",
            path_text(models / "Image" / "Shared-Local-Image-Studio"), path_text(models / "Image" / "Qwen-Image"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="No-copy alias to the shared physical FLUX/Qwen model store.",
        ),
        record(
            "practical-rife-model-link", "Practical-RIFE model alias", "video", "MODEL_STORE",
            path_text(runtime / "engines" / "video" / "Practical-RIFE" / "model"), path_text(models / "Video" / "Practical-RIFE"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="No-copy alias to the portable engine model directory.",
        ),
        record(
            "real-esrgan-model-link", "Real-ESRGAN model alias", "video", "MODEL_STORE",
            path_text(runtime / "engines" / "video" / "Real-ESRGAN" / "weights"), path_text(models / "Video" / "Real-ESRGAN"),
            move_strategy="junction_only", rollback_strategy="remove_canonical_junction", confidence="high", status="deferred",
            notes="No-copy alias to the portable engine weight directory.",
        ),
        record(
            "animesr-legacy-source", "AnimeSR legacy source/jobs", "video", "USER_MEDIA",
            r"D:\AI_4K_TEMP\AnimeSR", path_text(runtime / "engines" / "video" / "AnimeSR"),
            environment_path=r"D:\AI_4K_TEMP\animesr_venv", move_strategy="external_managed",
            rollback_strategy="leave_legacy_source", confidence="high", status="external_managed",
            notes="Contains approximately 2 GB of legacy jobs. Retained to avoid moving user job data; deployed bundled engine is managed separately.",
        ),
        record(
            "animesr-venv", "AnimeSR Python environment", "video", "PYTHON_ENVIRONMENT",
            r"D:\AI_4K_TEMP\animesr_venv", path_text(ROOT / "Environments" / "animesr"),
            environment_path=r"D:\AI_4K_TEMP\animesr_venv", move_strategy="external_managed",
            rollback_strategy="leave_legacy_environment", confidence="high", status="external_managed",
            notes="Virtual environment uses absolute paths and is not moved blindly.",
        ),
        record(
            "sam2-project-data", "SAM2 Mask Studio projects", "vision", "USER_MEDIA",
            r"D:\AI_4K_TEMP\SAM2_Mask_Studio", None, move_strategy="external_managed",
            rollback_strategy="leave_legacy_projects", confidence="high", status="external_managed",
            notes="Project, smoke and build data stay in their legacy parent; only the deployed GUI folder is moved.",
        ),
        record(
            "sam2-venv", "SAM2 Python environment", "vision", "PYTHON_ENVIRONMENT",
            r"D:\AI_4K_TEMP\sam2_venv", path_text(ROOT / "Environments" / "sam2"),
            environment_path=r"D:\AI_4K_TEMP\sam2_venv", move_strategy="external_managed",
            rollback_strategy="leave_legacy_environment", confidence="high", status="external_managed",
            notes="Virtual environment is retained because the desktop GUI currently references it with absolute paths.",
        ),
        record(
            "anime-upscale-source-project", "Anime Upscale Studio source/test data", "video", "USER_MEDIA",
            r"D:\ẢNH VIDEO - AI\Anime Upscale Studio", None, move_strategy="external_managed",
            rollback_strategy="leave_legacy_source", confidence="high", status="external_managed",
            notes="Source project includes test artifacts and user-controlled media. The deployed distribution is migrated separately.",
        ),
        record(
            "flux-test-artifacts", "FLUX test artifacts", "image", "CACHE",
            r"D:\AI\FLUX-Klein-Studio-test-artifacts", None, move_strategy="needs_review",
            rollback_strategy="retain_until_reference_audit", confidence="medium", status="needs_review",
            notes="Potential cleanup candidate only after reference and unique-output verification; no deletion is automated.",
        ),
        record(
            "blender-ai-mcp", "Blender AI integration", "integration", "UNKNOWN",
            r"D:\BLENDER\blender-ai-mcp", None, move_strategy="external_managed",
            rollback_strategy="leave_active_integration", confidence="medium", status="external_managed",
            notes="Observed active Python server processes; do not move or stop this unrelated Blender integration.",
        ),
        record(
            "airi", "AIRI", "assistant", "SYSTEM_INSTALLED_APP",
            installer_managed_path("airi"), path_text(runtime / "applications" / "external" / "airi.json"),
            move_strategy="external_managed", rollback_strategy="remove_registry_only", confidence="high", status="external_system_app",
            notes="Installer-managed application. Registry only; never move airi.exe or copy credentials.",
        ),
        record(
            "ollama", "Ollama", "model-service", "SYSTEM_INSTALLED_APP",
            installer_managed_path("Ollama"), path_text(runtime / "applications" / "external" / "ollama.json"),
            move_strategy="external_managed", rollback_strategy="remove_registry_only", confidence="high", status="external_system_app",
            notes="System-installed service. Models are not relocated without an official safe configuration path.",
        ),
        record(
            "whisper-backend", "Faster-Whisper backend", "speech", "UNKNOWN",
            None, path_text(runtime / "engines" / "speech" / "Faster-Whisper"),
            move_strategy="not_installed", rollback_strategy="remove_registry_only", confidence="high", status="not_installed",
            notes="No installed external Faster-Whisper backend path resolved during inventory; no model download is performed.",
        ),
    ]


DEPENDENCIES = {
    "flux-source": ROOT / "runtime" / "applications" / "FLUX-Klein-Studio",
    "flux-model-store": ROOT / "runtime" / "applications" / "FLUX-Klein-Studio" / "models",
    "sam2-model-link": ROOT / "runtime" / "engines" / "vision" / "SAM2" / "checkpoints",
    "animesr-engine-link": ROOT / "runtime" / "applications" / "Anime-Upscale-Studio" / "runtime" / "AnimeSR",
    "animesr-model-link": ROOT / "runtime" / "applications" / "Anime-Upscale-Studio" / "runtime" / "AnimeSR" / "weights",
    "ffmpeg-runtime-link": ROOT / "runtime" / "applications" / "Anime-Upscale-Studio" / "runtime",
    "comfyui-runtime-link": ROOT / "runtime" / "applications" / "FLUX-Klein-Studio" / "comfyui",
    "qwen-image-studio-link": ROOT / "runtime" / "applications" / "FLUX-Klein-Studio",
    "flux-model-alias": ROOT / "Models" / "Image" / "Shared-Local-Image-Studio",
    "qwen-image-model-alias": ROOT / "Models" / "Image" / "Shared-Local-Image-Studio",
    "practical-rife-model-link": ROOT / "runtime" / "engines" / "video" / "Practical-RIFE" / "model",
    "real-esrgan-model-link": ROOT / "runtime" / "engines" / "video" / "Real-ESRGAN" / "weights",
}


def size_bytes(path: str | None) -> int:
    if not path:
        return 0
    candidate = Path(path)
    try:
        if candidate.is_file():
            return candidate.stat().st_size
        if not candidate.is_dir():
            return 0
    except OSError:
        return 0
    total = 0
    stack = [candidate]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def load_existing() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(item.get("component")): item for item in value.get("entries", []) if isinstance(item, dict) and item.get("component")}


def is_reparse_point(path: str | None) -> bool:
    if not path:
        return False
    try:
        import stat

        attributes = os.lstat(path).st_file_attributes
        return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except (AttributeError, OSError):
        return False


def merge_records() -> list[dict[str, Any]]:
    existing = load_existing()
    result: list[dict[str, Any]] = []
    for base in planned_records():
        previous = existing.get(base["component"], {})
        merged = {**base, **{key: value for key, value in previous.items() if key not in {"size_bytes", "size_bytes_observed", "processes_using_path"}}}
        dependency = DEPENDENCIES.get(base["component"])
        if dependency and dependency.exists():
            if base["component"] == "flux-model-store":
                merged["source_path"] = str(dependency)
            if merged.get("status") in {"deferred", "not_found", "blocked_destination_exists", "blocked_low_disk"}:
                merged["status"] = "planned"
                merged["verified"] = False
        source_path = merged.get("source_path")
        destination_path = merged.get("destination_path")
        if merged.get("move_strategy") == "junction_only" and destination_path and is_reparse_point(destination_path):
            merged["status"] = "verified"
            merged["verified"] = True
        elif (
            merged.get("move_strategy") != "junction_only"
            and destination_path
            and Path(destination_path).exists()
            and is_reparse_point(source_path)
        ):
            merged["status"] = "moved_pending_verification" if not merged.get("verified") else "verified"
        observed = size_bytes(merged.get("source_path")) or size_bytes(merged.get("destination_path"))
        merged["size_bytes"] = observed
        merged["size_bytes_observed"] = observed
        merged["processes_using_path"] = previous.get("processes_observed", []) if previous.get("status") == "blocked_processes" else []
        result.append(merged)
    return result


def bytes_label(value: int) -> str:
    return f"{value / (1024 ** 3):.3f} GB ({value:,} bytes)"


def storage_snapshot() -> dict[str, int]:
    total, used, free = shutil.disk_usage(ROOT)
    return {
        "d_total": total,
        "d_used": used,
        "d_free": free,
        "local_ai_hub": size_bytes(str(ROOT)),
        "models": size_bytes(str(ROOT / "Models")),
        "environments": size_bytes(str(ROOT / "Environments")),
        "cache": size_bytes(str(ROOT / "Cache")),
        "runtime": size_bytes(str(ROOT / "runtime")),
        "output": size_bytes(str(ROOT / "Output")),
        "temp": size_bytes(str(ROOT / "Temp")),
    }


def write_storage_report(entries: list[dict[str, Any]], snapshot: dict[str, int]) -> None:
    lines = ["# Final storage plan (local)", "", f"Generated: {datetime.now(UTC).isoformat()}", "", "## Storage", ""]
    names = {
        "d_total": "D total", "d_used": "D used", "d_free": "D free", "local_ai_hub": "LocalAIHub total",
        "models": "Models total", "environments": "Environments total", "cache": "Cache total", "runtime": "Runtime total",
        "output": "Output total", "temp": "Temp total",
    }
    lines.extend([f"- {names[key]}: {bytes_label(snapshot[key])}" for key in names])
    lines.extend(["", "## Preflight", "", "| Component | Source size | Destination | Same volume | Temporary space | Projected free |", "| --- | ---: | --- | --- | ---: | ---: |"])
    for entry in entries:
        if entry.get("status") not in {"planned", "moved_pending_verification", "verified"}:
            continue
        source = str(entry.get("source_path") or "not installed")
        destination = str(entry.get("destination_path") or "n/a")
        same = "yes" if source[:2].casefold() == destination[:2].casefold() == "d:" else "n/a"
        temporary = int(entry.get("temporary_required_bytes") or 0)
        projected = snapshot["d_free"] - temporary
        lines.append(f"| {entry['component']} | {bytes_label(int(entry.get('size_bytes') or 0))} | `{destination}` | {same} | {bytes_label(temporary)} | {bytes_label(projected)} |")
    (REPORTS / "FINAL_STORAGE_PLAN.local.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_legacy_report(entries: list[dict[str, Any]]) -> None:
    lines = ["# Legacy AI paths (local)", "", f"Generated: {datetime.now(UTC).isoformat()}", "", "| Component | Path | Classification | Legacy state | Rationale |", "| --- | --- | --- | --- | --- |"]
    for entry in entries:
        source = entry.get("source_path") or "not installed"
        status = str(entry.get("status"))
        if status == "verified" and entry.get("legacy_junction_required"):
            legacy_state = "JUNCTION"
        elif entry.get("classification") in {"USER_MEDIA", "CACHE"}:
            legacy_state = "USER_DATA" if entry.get("classification") == "USER_MEDIA" else "UNKNOWN"
        elif entry.get("classification") == "SYSTEM_INSTALLED_APP":
            legacy_state = "SYSTEM_MANAGED"
        elif status in {"external_managed", "external_system_app"}:
            legacy_state = "STILL_REFERENCED"
        elif status == "verified":
            legacy_state = "JUNCTION"
        else:
            legacy_state = "UNKNOWN"
        lines.append(f"| {entry['display_name']} | `{source}` | {entry['classification']} | {legacy_state} | {entry.get('notes', '')} |")
    lines.extend(["", "No path is classified SAFE_TO_DELETE in this run. No destructive cleanup was performed."])
    (REPORTS / "LEGACY_AI_PATHS.local.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_migration_report(entries: list[dict[str, Any]], snapshot: dict[str, int]) -> None:
    categories = {
        "moved": [item for item in entries if item.get("status") in {"moved_pending_verification", "verified"} and item.get("move_strategy") != "junction_only"],
        "junctioned": [item for item in entries if item.get("move_strategy") == "junction_only" and item.get("status") == "verified"],
        "external": [item for item in entries if item.get("status") in {"external_managed", "external_system_app"}],
        "not_migrated": [item for item in entries if item.get("status") not in {"moved_pending_verification", "verified", "external_managed", "external_system_app"}],
    }
    lines = ["# Final Local AI migration (local)", "", f"Generated: {datetime.now(UTC).isoformat()}", "", "## Storage after current scan", "", f"- D free: {bytes_label(snapshot['d_free'])}", f"- LocalAIHub total: {bytes_label(snapshot['local_ai_hub'])}", "", "## Results"]
    for title, items in categories.items():
        lines.extend(["", f"### {title.replace('_', ' ').title()}"])
        lines.extend([f"- **{item['display_name']}** — `{item['status']}`. {item.get('notes', '')}" for item in items] or ["- None"])
    lines.extend(["", "## Cleanup", "", "- Safe to delete: none", "- Deleted: none", "- All legacy directories were retained unless replaced by a verified junction.", "", "## Remaining legacy D: AI paths", ""])
    for item in entries:
        if item.get("source_path") and item.get("status") != "verified":
            lines.append(f"- `{item['source_path']}` — {item.get('status')}")
    (REPORTS / "FINAL_LOCAL_AI_MIGRATION.local.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh ignored Local AI Hub final inventory reports.")
    parser.add_argument("--reports-only", action="store_true", help="Keep the existing manifest entries and refresh report sizes only.")
    args = parser.parse_args()
    try:
        existing = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    if isinstance(existing, dict) and (existing.get("historical_snapshot") or existing.get("not_runtime_configuration")):
        print("V2 inventory is historical on this completed host; no manifest or report was rewritten.")
        return 0
    REPORTS.mkdir(parents=True, exist_ok=True)
    entries = merge_records()
    snapshot = storage_snapshot()
    if not args.reports_only:
        manifest = {
            "schema_version": 2,
            "generated_at": datetime.now(UTC).isoformat(),
            "canonical_case": {"Config": "Config/", "Models": "Models/", "runtime": "runtime/"},
            "inventory": {"volume": "D:", **snapshot, "notes": "Read-only inventory refresh; no model, environment or user media was copied or deleted."},
            "entries": entries,
        }
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_storage_report(entries, snapshot)
    write_legacy_report(entries)
    write_migration_report(entries, snapshot)
    print(f"Refreshed {len(entries)} inventory entries and local reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
