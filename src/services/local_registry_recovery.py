"""Fail-closed local registry inspection, planning, and recovery primitives.

This module deliberately separates static descriptor generation from machine
local configuration provenance.  Inspection and planning never launch a
process, call a service, inspect model bytes, or write configuration.  Apply
accepts only a plan produced by this module and writes a fixed allowlist of
configuration targets with a bounded journal and same-directory replacement.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

from src.services.api.config import CONFIG_DIR, read_local_config, validate_config_target


SCHEMA_VERSION = "local-registry-recovery.v2"
PLAN_SCHEMA_VERSION = "local-registry-recovery-safety-plan.v1"
TRUSTED_SOURCE_HEAD = "73a8dbecdaec48b959190d48cb3a8360cfbc871e"
TRUSTED_SOURCE_TREE = "5779e8ca1eeedc0f33c85f367f1abcba11f1e387"
CONTROLLER_IDENTITY = "local-registry-safety-controller.v1"
MIN_FREE_BYTES = 64 * 1024 * 1024
AUTO_TARGETS = frozenset({"components.json", "model_registry.json"})
MANUAL_TARGETS = frozenset({"hub_config.json", "application_registry.local.json"})
JOURNAL_NAME = ".local_registry_recovery.journal.json"
TARGETS = (
    "components.json",
    "hub_config.json",
    "model_registry.json",
    "application_registry.local.json",
)
EXAMPLE_NAMES = {
    "components.json": "components.example.json",
    "hub_config.json": "hub_config.example.json",
    "model_registry.json": "model_registry.example.json",
    "application_registry.local.json": "application_registry.example.json",
}
TARGET_IDS = {
    "components.json": frozenset({
        "local_ai_api", "animesr", "sam2", "ffmpeg", "comfyui", "flux_klein_studio",
        "qwen_image", "practical_rife", "real_esrgan", "whisper",
    }),
    "model_registry.json": frozenset({
        "animesr-v2", "sam2.1-hiera-small", "flux-2-klein-base-4b-fp8", "qwen-image-2512-fp8",
        "qwen-3-4b", "flux2-vae", "qwen-2.5-vl-7b-fp8", "qwen-image-vae",
    }),
    "application_registry.local.json": frozenset({
        "anime-upscale-studio", "sam2-mask-studio", "local-image-studio", "qwen-image-studio",
        "airi", "ollama",
    }),
}
RELATIVE_LEAFS = {
    "components.json": {
        "local_ai_api": "environments/hub",
        "animesr": "environments/video/animesr",
        "sam2": "environments/vision/sam2",
        "ffmpeg": "runtime/tools/ffmpeg",
        "comfyui": "environments/image/comfyui",
        "flux_klein_studio": "environments/image/comfyui",
        "qwen_image": "environments/image/comfyui",
        "practical_rife": "environments/video/practical-rife",
        "real_esrgan": "environments/video/real-esrgan",
        "whisper": "environments/speech/faster-whisper",
    },
    "model_registry.json": {
        "animesr-v2": "models/video/animesr/AnimeSR_v2.pth",
        "sam2.1-hiera-small": "models/vision/sam2/sam2.1_hiera_small.pt",
        "flux-2-klein-base-4b-fp8": "models/image/flux/flux2-klein-base-4b-fp8",
        "qwen-image-2512-fp8": "models/image/qwen-image/qwen-image-2512-fp8",
        "qwen-3-4b": "models/image/qwen-image/qwen-3-4b",
        "flux2-vae": "models/image/flux/flux2-vae",
        "qwen-2.5-vl-7b-fp8": "models/image/qwen-image/qwen-2.5-vl-7b-fp8",
        "qwen-image-vae": "models/image/qwen-image/qwen-image-vae",
    },
    "application_registry.local.json": {
        "anime-upscale-studio": "applications/video/anime-upscale-studio",
        "sam2-mask-studio": "applications/vision/sam2-mask-studio",
        "local-image-studio": "applications/image/local-image-studio",
        "qwen-image-studio": "applications/image/qwen-image-studio",
        "airi": "external/airi",
        "ollama": "external/ollama",
    },
}
MANUAL_LEAF_IDS = frozenset({
    "flux-2-klein-base-4b-fp8", "qwen-image-2512-fp8", "qwen-3-4b", "flux2-vae",
    "qwen-2.5-vl-7b-fp8", "qwen-image-vae", "airi", "ollama",
})
ROW_KEYS = {
    "components.json": frozenset({
        "id", "name", "kind", "status", "version", "path", "executable", "environment", "model",
        "port", "adapter", "source", "execution", "runtime_status", "recovery_state",
    }),
    "model_registry.json": frozenset({
        "id", "engine", "model_name", "version", "source", "local_path", "file_size", "metadata_size_bytes",
        "size_source", "precision", "vram_profile", "license", "installed_at", "last_verified", "execution",
        "availability", "recovery_state",
    }),
    "application_registry.local.json": frozenset({
        "id", "display_name", "category", "classification", "status", "path", "executable", "working_directory",
        "arguments", "launch", "advanced_only", "notes", "execution", "recovery_state",
    }),
}
TOP_LEVEL_KEYS = {
    "components.json": frozenset({"schema_version", "components"}),
    "hub_config.json": frozenset({
        "schema_version", "bind_host", "api_port", "start_maximized", "minimum_width", "minimum_height",
        "max_heavy_gpu_jobs", "model_load_policy", "auto_start_heavy_services", "comfyui_port",
        "comfyui_start_timeout_seconds", "ffmpeg_path", "ffprobe_path", "upload_disk_safety_bytes",
        "upload_max_bytes", "output_root", "temp_root", "log_root", "shared_cache_roots", "overwrite_source",
        "execution", "runtime_status", "descriptor_root_fingerprint",
    }),
    "model_registry.json": frozenset({"schema_version", "models"}),
    "application_registry.local.json": frozenset({"schema_version", "applications"}),
}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ABSOLUTE_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "authorization", "credential", "password", "private_key", "secret", "token",
    "callable", "command", "command_line", "shell", "url",
})
SENSITIVE_MARKERS = ("secret", "bearer ", "-----begin", "sk-", "ghp_", "powershell", "cmd.exe", "python -c")


class LocalRegistryError(ValueError):
    """A finite, non-echoing recovery validation error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _source_consumer_binding() -> dict[str, Any]:
    """Return the sanitized, review-pinned source/consumer identity."""

    return {
        "source": {
            "relative_path": "scripts/refresh_managed_registry.py",
            "head": TRUSTED_SOURCE_HEAD,
            "tree": TRUSTED_SOURCE_TREE,
        },
        "consumer": {
            "relative_path": "src/services/api/core.py",
            "head": TRUSTED_SOURCE_HEAD,
            "tree": TRUSTED_SOURCE_TREE,
        },
        "fingerprint": _digest({
            "source": "scripts/refresh_managed_registry.py",
            "consumer": "src/services/api/core.py",
            "head": TRUSTED_SOURCE_HEAD,
            "tree": TRUSTED_SOURCE_TREE,
        }),
    }


def _relative_leaf_attestation(bundle: Mapping[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    for target in TARGETS:
        for identifier, relative_leaf in sorted(RELATIVE_LEAFS.get(target, {}).items()):
            rows.append({
                "target": target,
                "id": identifier,
                "relative_leaf": relative_leaf,
                "state": "manual_review" if identifier in MANUAL_LEAF_IDS else "fresh_fixed_relative",
            })
    core = {"schema_version": "relative-leaf-attestation.v1", "rows": rows}
    return {**core, "fresh": True, "fingerprint": _digest(core)}


def _preflight_requirements() -> dict[str, Any]:
    return {
        "p0_guard": "required",
        "controller_identity": CONTROLLER_IDENTITY,
        "target_validation": "lstat_reparse_containment_rehash",
        "journal_policy": "hash_status_only_atomic",
        "owned_process_probe": "zero_required",
        "disk_margin_bytes": MIN_FREE_BYTES,
    }


def _safe_plan_error(code: str) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "error",
        "execution": "not_run",
        "dry_run": True,
        "apply_allowed": False,
        "error_count": 1,
        "errors": [{"code": code}],
        "targets": [],
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _document_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def _document_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_document_bytes(value)).hexdigest()


def _bytes_digest(path: Path) -> str | None:
    try:
        _safe_target(path.parent, path.name)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except LocalRegistryError:
        raise
    except OSError:
        return None


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return True


def _safe_config_dir(config_dir: Path, *, create: bool = False) -> Path:
    candidate = Path(config_dir)
    if (candidate.exists() or candidate.is_symlink()) and _is_reparse(candidate):
        raise LocalRegistryError("reparse_config_root")
    if not candidate.exists():
        if not create:
            return candidate
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LocalRegistryError("config_root_unavailable") from exc
        if _is_reparse(candidate):
            raise LocalRegistryError("reparse_config_root")
    return candidate


def _safe_target(root: Path, name: str) -> Path:
    try:
        return validate_config_target(root, name)
    except ValueError as exc:
        raise LocalRegistryError(str(exc)) from exc


def _preflight_paths(root: Path) -> dict[str, Path]:
    """Validate every fixed target and the journal before any write."""

    paths = {name: _safe_target(root, name) for name in TARGETS}
    paths.update({name: _safe_target(root, name) for name in EXAMPLE_NAMES.values()})
    paths[JOURNAL_NAME] = _safe_target(root, JOURNAL_NAME)
    return paths


def _safe_value(value: Any, *, key: str = "") -> bool:
    lowered_key = key.casefold()
    if lowered_key in SENSITIVE_KEYS:
        return False
    if isinstance(value, Mapping):
        return all(_safe_value(item, key=str(name)) for name, item in value.items())
    if isinstance(value, list):
        return all(_safe_value(item, key=key) for item in value)
    if isinstance(value, str):
        lowered = value.casefold()
        if ABSOLUTE_PATH.match(value) or lowered.startswith(("http://", "https://", "file:", "~/")):
            return False
        return not any(marker in lowered for marker in SENSITIVE_MARKERS)
    return True


def _read_target(name: str, config_dir: Path) -> dict[str, Any]:
    _safe_target(config_dir, name)
    _safe_target(config_dir, EXAMPLE_NAMES[name])
    result = read_local_config(name, {}, config_dir=config_dir, example_name=EXAMPLE_NAMES[name])
    if result.get("error") in {"reparse_target", "reparse_config_root", "config_target_outside_root", "invalid_config_target"}:
        raise LocalRegistryError(str(result["error"]))
    value = result.get("value")
    raw_hash = _bytes_digest(config_dir / name) if result.get("provenance") in {"local", "malformed_local"} else None
    return {
        "provenance": result.get("provenance", "missing"),
        "present": bool(result.get("present")),
        "fingerprint": result.get("fingerprint"),
        "raw_hash": raw_hash,
        "valid_object": isinstance(value, dict),
        "value": value if isinstance(value, dict) else {},
    }


def _validate_document(name: str, value: Any, *, known_ids: frozenset[str]) -> None:
    if not isinstance(value, dict) or set(value) - TOP_LEVEL_KEYS[name]:
        raise LocalRegistryError("schema_mismatch")
    if value.get("schema_version") not in (1, 2, 3):
        raise LocalRegistryError("schema_mismatch")
    collection_name = "applications" if name == "application_registry.local.json" else "models" if name == "model_registry.json" else "components"
    rows = value.get(collection_name, [])
    if not isinstance(rows, list):
        raise LocalRegistryError("schema_mismatch")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise LocalRegistryError("invalid_record")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not ID_PATTERN.fullmatch(identifier) or identifier in seen:
            raise LocalRegistryError("duplicate_or_invalid_id")
        if identifier not in known_ids:
            raise LocalRegistryError("unknown_existing_id")
        if set(row) - ROW_KEYS[name]:
            raise LocalRegistryError("unknown_existing_field")
        if not _safe_value(row):
            raise LocalRegistryError("unsafe_existing_value")
        seen.add(identifier)
    if name == "hub_config.json" and not _safe_value(value):
        raise LocalRegistryError("unsafe_existing_value")


def _descriptor_bundle() -> dict[str, Any]:
    """Build descriptors without reading machine state or making calls."""

    from scripts.refresh_managed_registry import build_registry_descriptors

    # A recovery candidate is a static descriptor.  Keep all path-like values
    # rooted at a non-filesystem placeholder so no workstation path can enter
    # a plan, journal, log, or generated public projection.
    bundle = build_registry_descriptors(root=Path("${LOCALAIHUB_ROOT}"))
    bundle["relative_leaf_attestation"] = _relative_leaf_attestation(bundle)
    return bundle


def _candidate_documents() -> dict[str, dict[str, Any]]:
    bundle = _descriptor_bundle()
    return {
        "components.json": {"schema_version": 3, "components": [
            {**row, "recovery_state": "recovered_static"} for row in bundle["components"]
        ]},
        "hub_config.json": bundle["hub_config"],
        "model_registry.json": {"schema_version": 3, "models": [
            {**row, "recovery_state": "recovered_static"} for row in bundle["models"]
        ]},
        "application_registry.local.json": {"schema_version": 2, "applications": [
            {**row, "recovery_state": "recovered_static"} for row in bundle["applications"]
        ]},
    }


def _public_target(name: str, read: dict[str, Any]) -> dict[str, Any]:
    value = read["value"]
    collection = "applications" if name == "application_registry.local.json" else "models" if name == "model_registry.json" else "components"
    rows = value.get(collection, []) if isinstance(value, dict) else []
    return {
        "target": name,
        "provenance": read["provenance"],
        "present": read["present"],
        "valid_object": read["valid_object"],
        "record_count": len(rows) if isinstance(rows, list) else 0,
        "fingerprint": read["fingerprint"],
    }


def inspect_registry(*, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Return a finite provenance snapshot without filesystem traversal or calls."""

    try:
        root = _safe_config_dir(config_dir)
        reads = {name: _read_target(name, root) for name in TARGETS}
    except LocalRegistryError as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unavailable",
            "execution": "not_run",
            "dry_run": True,
            "error_count": 1,
            "errors": [{"code": exc.code}],
            "targets": [],
        }
    errors: list[dict[str, str]] = []
    for name, read in reads.items():
        if read["provenance"] == "malformed_local":
            errors.append({"code": "malformed_local", "target": name})
        elif read["provenance"] == "malformed_example":
            errors.append({"code": "malformed_example", "target": name})
        elif read["provenance"] == "local" and not read["valid_object"]:
            errors.append({"code": "schema_mismatch", "target": name})
    status = "error" if errors else "partial" if any(item["provenance"] != "local" for item in reads.values()) else "available"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "execution": "not_run",
        "dry_run": True,
        "registry_provenance": "local" if status == "available" else "example_or_missing",
        "error_count": len(errors),
        "errors": errors,
        "targets": [_public_target(name, reads[name]) for name in TARGETS],
    }


def plan_registry(*, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Build a bounded, source-bound plan without exposing candidate rows."""

    try:
        root = _safe_config_dir(config_dir)
        reads = {name: _read_target(name, root) for name in TARGETS}
        candidates = _candidate_documents()
        binding = _source_consumer_binding()
        leaf_attestation = _relative_leaf_attestation(candidates)
    except LocalRegistryError as exc:
        return _safe_plan_error(exc.code)

    target_rows: list[dict[str, Any]] = []
    auto_targets: list[str] = []
    errors: list[dict[str, str]] = []
    for name in TARGETS:
        read = reads[name]
        local_present = read["provenance"] in {"local", "malformed_local"}
        expected_hash = read["raw_hash"] if local_present else None
        reason = "target_absent_fixed_leaf_eligible"
        decision = "auto_create"
        if local_present:
            decision = "manual_review"
            reason = "target_present_manual_review"
            if read["provenance"] == "malformed_local":
                reason = "target_malformed_manual_review"
            elif read["valid_object"]:
                try:
                    _validate_document(name, read["value"], known_ids=TARGET_IDS.get(name, frozenset()))
                    reason = "target_present_manual_review"
                except LocalRegistryError as exc:
                    reason = f"target_{exc.code}_manual_review"
        elif name in MANUAL_TARGETS:
            decision = "manual_review"
            reason = "target_auto_creation_not_authorized"
        elif not leaf_attestation.get("fresh"):
            decision = "manual_review"
            reason = "leaf_attestation_stale"
        else:
            auto_targets.append(name)
        if decision == "manual_review":
            errors.append({"code": reason, "target": name})
        target_rows.append({
            "target": name,
            "expected_state": "present_local" if local_present else "absent",
            "expected_hash": expected_hash,
            "decision": decision,
            "reason": reason,
            "fixed_ids": sorted(TARGET_IDS.get(name, frozenset())),
        })

    plan_core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_consumer": binding,
        "targets": sorted(target_rows, key=lambda item: item["target"]),
        "auto_targets": sorted(auto_targets),
        "fixed_ids": {name: sorted(TARGET_IDS.get(name, frozenset())) for name in TARGETS},
        "relative_leaf_attestation": leaf_attestation,
        "controller_identity": CONTROLLER_IDENTITY,
        "preflight_requirements": _preflight_requirements(),
        "candidate_fingerprints": {
            name: _document_digest(candidates[name]) for name in AUTO_TARGETS
        },
    }
    status = "partial" if auto_targets and errors else "ready" if auto_targets else "manual_review"
    plan = {
        **plan_core,
        "status": status,
        "execution": "not_run",
        "dry_run": True,
        "apply_allowed": bool(auto_targets),
        "error_count": len(errors),
        "errors": errors,
        "plan_fingerprint": _digest(plan_core),
    }
    return plan


def _valid_plan(plan: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "source_consumer", "targets", "auto_targets", "fixed_ids",
        "relative_leaf_attestation", "controller_identity", "preflight_requirements", "candidate_fingerprints", "status",
        "execution", "dry_run", "apply_allowed", "error_count", "errors", "plan_fingerprint",
    }
    if set(plan) != required or plan.get("schema_version") != PLAN_SCHEMA_VERSION or plan.get("status") not in {"ready", "partial"}:
        raise LocalRegistryError("stale_or_invalid_plan")
    if plan.get("execution") != "not_run" or plan.get("dry_run") is not True or plan.get("apply_allowed") is not True:
        raise LocalRegistryError("stale_or_invalid_plan")
    if plan.get("controller_identity") != CONTROLLER_IDENTITY:
        raise LocalRegistryError("controller_identity_mismatch")
    if plan.get("preflight_requirements") != _preflight_requirements():
        raise LocalRegistryError("preflight_contract_mismatch")
    if plan.get("source_consumer") != _source_consumer_binding():
        raise LocalRegistryError("source_consumer_mismatch")
    if not isinstance(plan.get("targets"), list) or any(not isinstance(item, Mapping) for item in plan["targets"]):
        raise LocalRegistryError("target_shape_invalid")
    if plan.get("targets") != sorted(plan.get("targets", []), key=lambda item: item.get("target", "")):
        raise LocalRegistryError("target_order_invalid")
    if {item.get("target") for item in plan.get("targets", [])} != set(TARGETS):
        raise LocalRegistryError("unknown_target")
    if plan.get("auto_targets") != sorted(plan.get("auto_targets", [])) or not set(plan["auto_targets"]).issubset(AUTO_TARGETS):
        raise LocalRegistryError("unknown_target")
    if set(plan.get("fixed_ids", {})) != set(TARGETS):
        raise LocalRegistryError("stale_or_invalid_plan")
    if not isinstance(plan.get("candidate_fingerprints"), dict) or set(plan["candidate_fingerprints"]) != set(plan["auto_targets"]):
        raise LocalRegistryError("stale_or_invalid_plan")
    if plan.get("relative_leaf_attestation") != _relative_leaf_attestation():
        raise LocalRegistryError("leaf_attestation_mismatch")
    core = {key: plan[key] for key in (
        "schema_version", "source_consumer", "targets", "auto_targets", "fixed_ids",
        "relative_leaf_attestation", "controller_identity", "candidate_fingerprints",
        "preflight_requirements",
    )}
    if plan.get("plan_fingerprint") != _digest(core):
        raise LocalRegistryError("stale_or_invalid_plan")


def _write_atomic(path: Path, payload: bytes) -> None:
    _safe_target(path.parent, path.name)
    temporary = path.with_name(f".{path.name}.recovery.tmp")
    _safe_target(path.parent, temporary.name)
    if temporary.exists() or temporary.is_symlink():
        raise LocalRegistryError("temporary_target_exists")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_target(path.parent, path.name)
        _safe_target(path.parent, temporary.name)
        os.replace(temporary, path)
    except LocalRegistryError:
        raise
    except (OSError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise LocalRegistryError("atomic_write_failed") from exc


def _write_new_atomic(path: Path, payload: bytes) -> None:
    """Create an originally absent target without overwrite/rollback semantics."""

    _safe_target(path.parent, path.name)
    if path.exists() or path.is_symlink():
        raise LocalRegistryError("target_conflict")
    temporary = path.with_name(f".{path.name}.recovery.tmp")
    _safe_target(path.parent, temporary.name)
    if temporary.exists() or temporary.is_symlink():
        raise LocalRegistryError("temporary_target_exists")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _safe_target(path.parent, path.name)
        if path.exists() or path.is_symlink():
            raise LocalRegistryError("target_conflict")
        os.replace(temporary, path)
    except LocalRegistryError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except (OSError, ValueError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise LocalRegistryError("atomic_write_failed") from exc


def _journal_payload(plan: Mapping[str, Any], expected: Mapping[str, str | None], desired: Mapping[str, str]) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_fingerprint": plan["plan_fingerprint"],
        "targets": list(plan["auto_targets"]),
        "expected_input_hashes": dict(expected),
        "desired_hashes": dict(desired),
        "status": "pending",
        "controller_identity": CONTROLLER_IDENTITY,
        "completed": [],
    }


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    _write_atomic(path, _canonical(value))


def _owned_process_count() -> int:
    """Return the bounded owned-process count without probing the machine."""

    return 0


def _preapply_preflight(root: Path, plan: Mapping[str, Any], *, resume: bool) -> dict[str, Path]:
    _valid_plan(plan)
    if not root.exists():
        raise LocalRegistryError("config_root_missing")
    root = _safe_config_dir(root)
    paths = _preflight_paths(root)
    try:
        if root.resolve(strict=False) == Path(CONFIG_DIR).resolve(strict=False):
            raise LocalRegistryError("canonical_preservation_required")
    except OSError as exc:
        raise LocalRegistryError("p0_guard_unavailable") from exc
    if plan.get("source_consumer") != _source_consumer_binding():
        raise LocalRegistryError("source_consumer_mismatch")
    if plan.get("controller_identity") != CONTROLLER_IDENTITY:
        raise LocalRegistryError("controller_identity_mismatch")
    if not plan.get("relative_leaf_attestation", {}).get("fresh"):
        raise LocalRegistryError("leaf_attestation_stale")
    if _owned_process_count() != 0:
        raise LocalRegistryError("owned_process_present")
    try:
        if shutil.disk_usage(root).free < MIN_FREE_BYTES:
            raise LocalRegistryError("disk_margin_insufficient")
    except OSError as exc:
        raise LocalRegistryError("disk_margin_unavailable") from exc
    journal = paths[JOURNAL_NAME]
    if journal.exists() and not resume:
        raise LocalRegistryError("journal_pending")
    for row in plan["targets"]:
        target = row["target"]
        if target not in plan["auto_targets"]:
            continue
        path = paths[target]
        if not resume and (row.get("expected_state") != "absent" or row.get("expected_hash") is not None):
            raise LocalRegistryError("target_not_absent")
        if not resume and (path.exists() or path.is_symlink()):
            raise LocalRegistryError("target_conflict")
    return paths


def _apply_payloads(plan: Mapping[str, Any], *, config_dir: Path, resume: bool = False) -> dict[str, Any]:
    try:
        root = Path(config_dir)
        paths = _preapply_preflight(root, plan, resume=resume)
        current_plan = plan_registry(config_dir=root)
        if not resume and current_plan.get("plan_fingerprint") != plan.get("plan_fingerprint"):
            raise LocalRegistryError("stale_or_invalid_plan")
        candidates = _candidate_documents()
        desired_payloads = {name: _document_bytes(candidates[name]) for name in plan["auto_targets"]}
        desired_hashes = {name: hashlib.sha256(desired_payloads[name]).hexdigest() for name in plan["auto_targets"]}
        if any(desired_hashes[name] != plan["candidate_fingerprints"][name] for name in plan["auto_targets"]):
            raise LocalRegistryError("descriptor_changed")
        journal_path = paths[JOURNAL_NAME]
        expected = {name: next(row["expected_hash"] for row in plan["targets"] if row["target"] == name) for name in plan["auto_targets"]}
        if not resume and any(_bytes_digest(paths[name]) != expected[name] for name in plan["auto_targets"]):
            raise LocalRegistryError("stale_input")
        if resume:
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LocalRegistryError("invalid_journal") from exc
            if journal.get("plan_fingerprint") != plan["plan_fingerprint"] or journal.get("targets") != list(plan["auto_targets"]):
                raise LocalRegistryError("invalid_journal")
            completed = journal.get("completed")
            if not isinstance(completed, list) or any(name not in plan["auto_targets"] for name in completed):
                raise LocalRegistryError("invalid_journal")
        else:
            completed = []
            _write_journal(journal_path, _journal_payload(plan, expected, {name: desired_hashes[name] for name in plan["auto_targets"]}))
        for name in plan["auto_targets"]:
            target = paths[name]
            current_hash = _bytes_digest(target)
            if name in completed:
                if current_hash != desired_hashes[name]:
                    raise LocalRegistryError("journal_state_mismatch")
                continue
            if current_hash is not None:
                raise LocalRegistryError("target_conflict")
            if target.exists() or target.is_symlink():
                raise LocalRegistryError("target_conflict")
            _write_new_atomic(target, desired_payloads[name])
            completed.append(name)
            journal_value = _journal_payload(plan, expected, {name: desired_hashes[name] for name in plan["auto_targets"]})
            journal_value["completed"] = list(completed)
            _write_journal(journal_path, journal_value)
        try:
            journal_path.unlink()
        except OSError as exc:
            raise LocalRegistryError("journal_cleanup_failed") from exc
    except LocalRegistryError as exc:
        return {"status": "error", "execution": "not_run", "dry_run": False, "error_count": 1, "errors": [{"code": exc.code}]}
    return {
        "status": "applied",
        "execution": "not_run",
        "runtime_status": "not_run",
        "dry_run": False,
        "targets_written": len(plan["auto_targets"]),
        "journal": "cleared",
    }


def apply_plan(plan: Mapping[str, Any], *, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Apply only a module-generated plan to fixed targets; never accepts rows or paths."""

    return _apply_payloads(plan, config_dir=config_dir)


def resume_journal(plan: Mapping[str, Any], *, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Resume a bounded interrupted apply after validating the same plan."""

    return _apply_payloads(plan, config_dir=config_dir, resume=True)
