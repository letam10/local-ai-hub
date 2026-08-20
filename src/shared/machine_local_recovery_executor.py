"""Manager-owned, read-only machine-local recovery executor boundary.

This module deliberately stops at inspect/plan/preflight.  It never imports
canonical project Python, starts a server, probes a provider, writes Config, or
performs a recovery.  A future manager-owned executor may consume the sealed
authorization contract after a separately authorized implementation review.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = "execution-authorization.v1"
PLAN_SCHEMA_VERSION = "machine-local-recovery-plan.v1"
EXECUTOR_SCHEMA_VERSION = "machine-local-recovery-executor.v1"
EXPECTED_CANONICAL_HEAD = "ca998106fe2319da6b41fe1c73c6df834d65b2c8"
EXPECTED_CANONICAL_TREE = "0d6852a20d78701b948cedcd0f970b4d3bb5e27c"
EXPECTED_DIRTY_GUARD = "CANONICAL_PRESERVATION_REQUIRED"
MANIFEST_COUNT = 34
TARGETS = (
    "components.json",
    "model_registry.json",
    "hub_config.json",
    "application_registry.local.json",
)
AUTO_TARGETS = frozenset({"components.json", "model_registry.json"})
MANUAL_TARGETS = frozenset({"hub_config.json", "application_registry.local.json"})
JOURNAL_NAME = ".machine_local_recovery.journal.json"
FIXED_COMPONENT_IDS = frozenset({
    "local_ai_api", "animesr", "sam2", "ffmpeg", "comfyui", "flux_klein_studio",
    "qwen_image", "practical_rife", "real_esrgan", "whisper",
})
FIXED_MODEL_IDS = frozenset({
    "animesr-v2", "sam2.1-hiera-small", "flux-2-klein-base-4b-fp8", "qwen-image-2512-fp8",
    "qwen-3-4b", "flux2-vae", "qwen-2.5-vl-7b-fp8", "qwen-image-vae",
})
FIXED_APPLICATION_IDS = frozenset({
    "anime-upscale-studio", "sam2-mask-studio", "local-image-studio", "qwen-image-studio", "airi", "ollama",
})
CONSUMER_FILES = (
    "src/services/local_registry_recovery.py",
    "src/services/api/config.py",
    "src/services/api/core.py",
    "scripts/refresh_managed_registry.py",
)
PATH_LIMIT = 180
MAX_TARGET_BYTES = 1024 * 1024
MIN_DISK_MARGIN = 64 * 1024 * 1024
ACTIVE_CLAIMS = frozenset({"running", "installed", "operational", "ready", "launchable", "partial", "available"})


class RecoveryError(ValueError):
    """Finite, scrubbed executor refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RecoveryError("read_failed") from exc


def _safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > PATH_LIMIT:
        return False
    path = Path(value)
    return (
        not path.is_absolute()
        and "\\" not in value
        and ":" not in value
        and ".." not in path.parts
        and not value.startswith(("/", "~"))
    )


def _safe_root(root: Path) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise RecoveryError("root_invalid")
    try:
        attributes = getattr(os.lstat(candidate), "st_file_attributes", 0)
    except OSError as exc:
        raise RecoveryError("root_unavailable") from exc
    if attributes & 0x400:
        raise RecoveryError("root_reparse")
    return candidate


def _safe_child(root: Path, relative: str) -> Path:
    if not _safe_relative(relative):
        raise RecoveryError("relative_path_invalid")
    candidate = root / relative
    try:
        current = root
        for part in Path(relative).parts:
            current = current / part
            try:
                current_stat = os.lstat(current)
            except FileNotFoundError:
                break
            if stat.S_ISLNK(current_stat.st_mode) or getattr(current_stat, "st_file_attributes", 0) & 0x400:
                raise RecoveryError("target_reparse")
            if current != candidate and not stat.S_ISDIR(current_stat.st_mode):
                raise RecoveryError("target_containment")
    except RecoveryError:
        raise
    except OSError as exc:
        raise RecoveryError("target_containment") from exc
    return candidate


def _duplicate_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("duplicate_key")
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        data = path.read_bytes()
        if len(data) > MAX_TARGET_BYTES:
            return None, "oversize"
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_duplicate_guard)
    except RecoveryError as exc:
        return None, exc.code
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "malformed"
    if not isinstance(value, dict):
        return None, "not_object"
    return value, "valid"


def _target_ids(target: str) -> frozenset[str]:
    if target == "components.json":
        return FIXED_COMPONENT_IDS
    if target == "model_registry.json":
        return FIXED_MODEL_IDS
    if target == "application_registry.local.json":
        return FIXED_APPLICATION_IDS
    return frozenset()


def _collection(target: str) -> str | None:
    return {
        "components.json": "components",
        "model_registry.json": "models",
        "application_registry.local.json": "applications",
    }.get(target)


def _target_state(root: Path, target: str) -> dict[str, Any]:
    path = _safe_child(root, target)
    try:
        initial_stat = os.lstat(path)
    except FileNotFoundError:
        return {"target": target, "state": "absent", "size": 0, "sha256": None, "record_count": 0}
    except OSError as exc:
        raise RecoveryError("target_unreadable") from exc
    try:
        if stat.S_ISLNK(initial_stat.st_mode) or getattr(initial_stat, "st_file_attributes", 0) & 0x400:
            raise RecoveryError("target_reparse")
        if not stat.S_ISREG(initial_stat.st_mode):
            raise RecoveryError("target_not_file")
        size = initial_stat.st_size
    except RecoveryError:
        raise
    if size > MAX_TARGET_BYTES:
        return {"target": target, "state": "oversize", "size": size, "sha256": None, "record_count": 0}
    value, parse_state = _read_json(path)
    if parse_state != "valid":
        return {"target": target, "state": "malformed", "size": size, "sha256": _sha256(path), "record_count": 0}
    collection = _collection(target)
    rows = value.get(collection, []) if collection else []
    valid_rows = isinstance(rows, list) and all(isinstance(row, dict) for row in rows)
    known = valid_rows and all(isinstance(row.get("id"), str) and row["id"] in _target_ids(target) for row in rows)
    state = "valid" if valid_rows and known else "unknown"
    return {"target": target, "state": state, "size": size, "sha256": _sha256(path), "record_count": len(rows) if isinstance(rows, list) else 0}


def _git_value(root: Path, args: Sequence[str], runner: Callable[..., str] | None) -> str:
    if not all(isinstance(item, str) and item in {"rev-parse", "HEAD", "HEAD^{tree}", "--show-toplevel", "status", "--porcelain"} for item in args):
        raise RecoveryError("git_command_not_allowlisted")
    if runner is not None:
        try:
            return str(runner(root, tuple(args))).strip()
        except Exception as exc:
            raise RecoveryError("git_query_failed") from exc
    try:
        result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryError("git_query_failed") from exc
    if result.returncode != 0 or len(result.stdout) > 256:
        raise RecoveryError("git_query_failed")
    return result.stdout.strip()


def _manifest(root: Path, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    if len(rows) != MANIFEST_COUNT:
        raise RecoveryError("preservation_manifest_count")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RecoveryError("preservation_manifest_invalid")
        relative = row.get("relative_path")
        digest = row.get("sha256")
        size = row.get("size")
        if not _safe_relative(relative) or relative in seen or not isinstance(digest, str) or len(digest) != 64 or not isinstance(size, int) or size < 0:
            raise RecoveryError("preservation_manifest_invalid")
        path = _safe_child(root, relative)
        try:
            actual = os.lstat(path)
        except OSError as exc:
            raise RecoveryError("preservation_manifest_drift") from exc
        if stat.S_ISLNK(actual.st_mode) or getattr(actual, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(actual.st_mode) or actual.st_size != size or _sha256(path) != digest:
            raise RecoveryError("preservation_manifest_drift")
        seen.add(relative)
        normalized.append({"relative_path": relative, "sha256": digest, "size": size})
    normalized.sort(key=lambda item: item["relative_path"])
    return normalized, _digest(normalized)


def _consumer_binding(root: Path, expected: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, Any], str]:
    if set(expected) != set(CONSUMER_FILES):
        raise RecoveryError("consumer_binding_shape")
    rows = []
    for relative in CONSUMER_FILES:
        expected_row = expected[relative]
        if not _safe_relative(relative) or not isinstance(expected_row, Mapping):
            raise RecoveryError("consumer_binding_shape")
        path = _safe_child(root, relative)
        try:
            actual = os.lstat(path)
        except OSError as exc:
            raise RecoveryError("consumer_missing") from exc
        if stat.S_ISLNK(actual.st_mode) or getattr(actual, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(actual.st_mode):
            raise RecoveryError("consumer_missing")
        current = {"relative_path": relative, "sha256": _sha256(path), "size": actual.st_size}
        if current["sha256"] != expected_row.get("sha256") or current["size"] != expected_row.get("size"):
            raise RecoveryError("consumer_hash_mismatch")
        rows.append(current)
    rows.sort(key=lambda item: item["relative_path"])
    return {"files": rows, "fingerprint": _digest(rows)}, _digest(rows)


def inspect(
    *,
    canonical_root: Path,
    config_root: Path,
    preservation_manifest: Sequence[Mapping[str, Any]],
    consumer_hashes: Mapping[str, Mapping[str, Any]],
    git_runner: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Inspect fixed metadata and return a sanitized snapshot."""

    try:
        canonical = _safe_root(canonical_root)
        config = _safe_root(config_root)
        head = _git_value(canonical, ("rev-parse", "HEAD"), git_runner)
        tree = _git_value(canonical, ("rev-parse", "HEAD^{tree}"), git_runner)
        status = _git_value(canonical, ("status", "--porcelain"), git_runner)
        manifest, preservation_digest = _manifest(canonical, preservation_manifest)
        consumer, consumer_digest = _consumer_binding(canonical, consumer_hashes)
        targets = [_target_state(config, target) for target in TARGETS]
    except RecoveryError as exc:
        return {"schema_version": EXECUTOR_SCHEMA_VERSION, "status": "unavailable", "execution": "not_run", "error": exc.code}
    return {
        "schema_version": EXECUTOR_SCHEMA_VERSION,
        "status": "available",
        "execution": "not_run",
        "canonical_head": head,
        "canonical_tree": tree,
        "dirty": bool(status),
        "preservation_digest": preservation_digest,
        "preservation_count": len(manifest),
        "consumer_digest": consumer_digest,
        "targets": targets,
    }


def _candidate_projection() -> dict[str, Any]:
    return {
        "components": [{"id": item, "status": "configured", "execution": "not_run", "recovery_state": "recovered_static"} for item in sorted(FIXED_COMPONENT_IDS)],
        "models": [{"id": item, "availability": "configured", "execution": "not_run", "recovery_state": "recovered_static"} for item in sorted(FIXED_MODEL_IDS)],
    }


def _compatibility_projection(candidates: Mapping[str, Any]) -> dict[str, Any]:
    try:
        serialized = json.dumps(candidates, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return {"status": "apply_blocked", "code": "candidate_shape_invalid"}
    if any(marker in serialized.casefold() for marker in ("http://", "https://", "cmd.exe", "powershell", "secret", "\\", "file:")):
        return {"status": "apply_blocked", "code": "candidate_redaction_failed"}
    if not isinstance(candidates, Mapping):
        return {"status": "apply_blocked", "code": "candidate_shape_invalid"}
    for rows in (candidates.get("components", []), candidates.get("models", [])):
        if not isinstance(rows, list):
            return {"status": "apply_blocked", "code": "candidate_shape_invalid"}
        for row in rows:
            if not isinstance(row, Mapping):
                return {"status": "apply_blocked", "code": "candidate_shape_invalid"}
            if any(str(row.get(key, "")).casefold() in ACTIVE_CLAIMS for key in ("status", "availability", "execution")):
                return {"status": "apply_blocked", "code": "candidate_active_claim"}
    return {"status": "compatible_static", "execution": "not_run"}


def plan(snapshot: Mapping[str, Any], *, executor_head: str, executor_tree: str, executor_script_sha256: str) -> dict[str, Any]:
    """Build a no-write plan from an inspect snapshot."""

    if snapshot.get("status") != "available" or snapshot.get("canonical_head") != EXPECTED_CANONICAL_HEAD or snapshot.get("canonical_tree") != EXPECTED_CANONICAL_TREE:
        return {"schema_version": PLAN_SCHEMA_VERSION, "status": "apply_blocked", "execution": "not_run", "error": "canonical_identity_mismatch"}
    targets = snapshot.get("targets")
    if not isinstance(targets, list) or len(targets) != len(TARGETS):
        return {"schema_version": PLAN_SCHEMA_VERSION, "status": "apply_blocked", "execution": "not_run", "error": "target_snapshot_invalid"}
    candidates = _candidate_projection()
    compatibility = _compatibility_projection(candidates)
    if compatibility.get("status") != "compatible_static":
        return {"schema_version": PLAN_SCHEMA_VERSION, "status": "apply_blocked", "execution": "not_run", "error": compatibility["code"]}
    target_rows = []
    for row in targets:
        target = row.get("target") if isinstance(row, Mapping) else None
        if target not in TARGETS:
            return {"schema_version": PLAN_SCHEMA_VERSION, "status": "apply_blocked", "execution": "not_run", "error": "target_snapshot_invalid"}
        target_rows.append({
            "target": target,
            "state": row.get("state"),
            "expected_sha256": row.get("sha256"),
            "decision": "auto_create" if target in AUTO_TARGETS and row.get("state") == "absent" else "manual_review",
        })
    core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "executor_head": executor_head,
        "executor_tree": executor_tree,
        "executor_script_sha256": executor_script_sha256,
        "canonical_head": snapshot["canonical_head"],
        "canonical_tree": snapshot["canonical_tree"],
        "preservation_digest": snapshot["preservation_digest"],
        "consumer_digest": snapshot["consumer_digest"],
        "targets": sorted(target_rows, key=lambda item: item["target"]),
        "candidate_ids": {"components": sorted(FIXED_COMPONENT_IDS), "models": sorted(FIXED_MODEL_IDS), "applications": sorted(FIXED_APPLICATION_IDS)},
        "compatibility": compatibility,
    }
    return {**core, "status": "planned", "execution": "not_run", "dry_run": True, "apply_allowed": False, "plan_fingerprint": _digest(core)}


def preflight(
    plan_value: Mapping[str, Any],
    capability: object,
    *,
    authorization_verifier: object | None,
    task_root: Path,
    guard_code: str,
    guard_dirty: bool,
    active_hub: bool,
    owned_processes: int | None,
    lock_held: bool,
    free_bytes: int | None,
) -> dict[str, Any]:
    """Refuse every preflight until a separately delivered controller exists."""

    # Deliberately inspect none of the arguments. A future authenticated
    # manager controller must own verification and execution outside this
    # library; import-only callers can never obtain a ready-state result here.
    return {
        "status": "preflight_blocked",
        "execution": "not_run",
        "dry_run": True,
        "apply_allowed": False,
        "error": "manager_controller_required",
    }
