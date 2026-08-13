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
from pathlib import Path
from typing import Any, Mapping

from src.services.api.config import CONFIG_DIR, read_local_config


SCHEMA_VERSION = "local-registry-recovery.v1"
PLAN_SCHEMA_VERSION = "local-registry-recovery-plan.v1"
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
ROW_KEYS = {
    "components.json": frozenset({
        "id", "name", "kind", "status", "version", "path", "executable", "environment", "model",
        "port", "adapter", "source", "execution", "runtime_status",
    }),
    "model_registry.json": frozenset({
        "id", "engine", "model_name", "version", "source", "local_path", "file_size", "metadata_size_bytes",
        "size_source", "precision", "vram_profile", "license", "installed_at", "last_verified", "execution",
        "availability",
    }),
    "application_registry.local.json": frozenset({
        "id", "display_name", "category", "classification", "status", "path", "executable", "working_directory",
        "arguments", "launch", "advanced_only", "notes", "execution",
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
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    except OSError:
        return None


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(path.stat(), "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return True


def _safe_config_dir(config_dir: Path, *, create: bool = False) -> Path:
    candidate = Path(config_dir)
    if candidate.exists() and _is_reparse(candidate):
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
        return not any(marker in lowered for marker in SENSITIVE_MARKERS)
    return True


def _read_target(name: str, config_dir: Path) -> dict[str, Any]:
    result = read_local_config(name, {}, config_dir=config_dir, example_name=EXAMPLE_NAMES[name])
    value = result.get("value")
    return {
        "provenance": result.get("provenance", "missing"),
        "present": bool(result.get("present")),
        "fingerprint": result.get("fingerprint"),
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

    return build_registry_descriptors()


def _candidate_documents() -> dict[str, dict[str, Any]]:
    bundle = _descriptor_bundle()
    return {
        "components.json": {"schema_version": 3, "components": bundle["components"]},
        "hub_config.json": bundle["hub_config"],
        "model_registry.json": {"schema_version": 3, "models": bundle["models"]},
        "application_registry.local.json": {"schema_version": 2, "applications": bundle["applications"]},
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
    """Build a deterministic offline plan; no candidate payload is exposed."""

    try:
        root = _safe_config_dir(config_dir)
        reads = {name: _read_target(name, root) for name in TARGETS}
        for name, read in reads.items():
            if read["provenance"] == "malformed_local":
                raise LocalRegistryError("malformed_local")
            if read["provenance"] == "local":
                _validate_document(name, read["value"], known_ids=TARGET_IDS.get(name, frozenset()))
        candidates = _candidate_documents()
    except LocalRegistryError as exc:
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "status": "error",
            "execution": "not_run",
            "dry_run": True,
            "apply_allowed": False,
            "error_count": 1,
            "errors": [{"code": exc.code}],
            "targets": [],
        }
    expected_hashes = {name: _bytes_digest(root / name) for name in TARGETS}
    candidate_fingerprints = {name: _document_digest(candidates[name]) for name in TARGETS}
    plan_core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "targets": list(TARGETS),
        "expected_input_hashes": expected_hashes,
        "candidate_fingerprints": candidate_fingerprints,
        "source_provenance": {name: reads[name]["provenance"] for name in TARGETS},
    }
    status = "ready" if all(reads[name]["provenance"] in {"local", "missing", "example_template"} for name in TARGETS) else "error"
    plan = {
        **plan_core,
        "status": status,
        "execution": "not_run",
        "dry_run": True,
        "apply_allowed": status == "ready",
        "unknown_existing_records": 0,
        "plan_fingerprint": _digest(plan_core),
    }
    return plan


def _valid_plan(plan: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "targets", "expected_input_hashes", "candidate_fingerprints", "source_provenance",
        "status", "execution", "dry_run", "apply_allowed", "unknown_existing_records", "plan_fingerprint",
    }
    if set(plan) != required or plan.get("schema_version") != PLAN_SCHEMA_VERSION or plan.get("status") != "ready":
        raise LocalRegistryError("stale_or_invalid_plan")
    if plan.get("execution") != "not_run" or plan.get("dry_run") is not True or plan.get("apply_allowed") is not True:
        raise LocalRegistryError("stale_or_invalid_plan")
    if plan.get("targets") != list(TARGETS):
        raise LocalRegistryError("unknown_target")
    if not isinstance(plan.get("expected_input_hashes"), dict) or set(plan["expected_input_hashes"]) != set(TARGETS):
        raise LocalRegistryError("stale_or_invalid_plan")
    if not isinstance(plan.get("candidate_fingerprints"), dict) or set(plan["candidate_fingerprints"]) != set(TARGETS):
        raise LocalRegistryError("stale_or_invalid_plan")
    core = {key: plan[key] for key in ("schema_version", "targets", "expected_input_hashes", "candidate_fingerprints", "source_provenance")}
    if plan.get("plan_fingerprint") != _digest(core):
        raise LocalRegistryError("stale_or_invalid_plan")


def _write_atomic(path: Path, payload: bytes) -> None:
    if path.exists() and _is_reparse(path):
        raise LocalRegistryError("reparse_target")
    temporary = path.with_name(f".{path.name}.recovery.tmp")
    if temporary.exists():
        raise LocalRegistryError("temporary_target_exists")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except LocalRegistryError:
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
        "targets": list(TARGETS),
        "expected_input_hashes": dict(expected),
        "desired_hashes": dict(desired),
        "completed": [],
    }


def _write_journal(path: Path, value: Mapping[str, Any]) -> None:
    _write_atomic(path, _canonical(value))


def _apply_payloads(plan: Mapping[str, Any], *, config_dir: Path, resume: bool = False) -> dict[str, Any]:
    try:
        _valid_plan(plan)
        root = _safe_config_dir(config_dir, create=True)
        current_plan = plan_registry(config_dir=root)
        if current_plan.get("candidate_fingerprints") != plan.get("candidate_fingerprints"):
            raise LocalRegistryError("stale_or_invalid_plan")
        if not resume and current_plan.get("expected_input_hashes") != plan.get("expected_input_hashes"):
            raise LocalRegistryError("stale_input")
        candidates = _candidate_documents()
        desired_payloads = {name: _document_bytes(candidates[name]) for name in TARGETS}
        desired_hashes = {name: hashlib.sha256(desired_payloads[name]).hexdigest() for name in TARGETS}
        if any(desired_hashes[name] != plan["candidate_fingerprints"][name] for name in TARGETS):
            raise LocalRegistryError("descriptor_changed")
        journal_path = root / JOURNAL_NAME
        if journal_path.exists() and not resume:
            raise LocalRegistryError("journal_pending")
        expected = {name: _bytes_digest(root / name) for name in TARGETS}
        if not resume and any(expected[name] != plan["expected_input_hashes"][name] for name in TARGETS):
            raise LocalRegistryError("stale_input")
        if resume:
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise LocalRegistryError("invalid_journal") from exc
            if journal.get("plan_fingerprint") != plan["plan_fingerprint"] or journal.get("targets") != list(TARGETS):
                raise LocalRegistryError("invalid_journal")
            completed = journal.get("completed")
            if not isinstance(completed, list) or any(name not in TARGETS for name in completed):
                raise LocalRegistryError("invalid_journal")
        else:
            completed = []
            _write_journal(journal_path, _journal_payload(plan, expected, desired_hashes))
        for name in TARGETS:
            target = root / name
            current_hash = _bytes_digest(target)
            if name in completed:
                if current_hash != desired_hashes[name]:
                    raise LocalRegistryError("journal_state_mismatch")
                continue
            expected_hash = plan["expected_input_hashes"][name] if not resume else (current_hash if current_hash is None else current_hash)
            if not resume and current_hash != expected_hash:
                raise LocalRegistryError("stale_input")
            _write_atomic(target, desired_payloads[name])
            completed.append(name)
            journal_value = _journal_payload(plan, plan["expected_input_hashes"], desired_hashes)
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
        "targets_written": len(TARGETS),
        "journal": "cleared",
    }


def apply_plan(plan: Mapping[str, Any], *, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Apply only a module-generated plan to fixed targets; never accepts rows or paths."""

    return _apply_payloads(plan, config_dir=config_dir)


def resume_journal(plan: Mapping[str, Any], *, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Resume a bounded interrupted apply after validating the same plan."""

    return _apply_payloads(plan, config_dir=config_dir, resume=True)
