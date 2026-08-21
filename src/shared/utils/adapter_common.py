from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from src.services.api.config import BASE_DIR, component, models
from src.shared.paths.registry import MODEL_ROOT, ROOT


_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}\Z")
_UNRESOLVED_CONFIG = re.compile(r"\$\{[^}]+\}|%[^%]+%")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_REGISTRY_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_MAX_WORKER_ITEMS = 64
_MAX_WORKER_TEXT = 600

_WORKER_COMMON_FIELDS = {
    "operation",
    "code",
    "error",
    "reason",
    "next_action",
    "message",
    "count",
    "duration_seconds",
    "language",
    "speaker",
    "sample_rate",
    "segment_count",
    "device",
    "width",
    "height",
}
_PATH_RESULT_FIELDS = {
    "path",
    "input",
    "source",
    "target",
    "reference_audio",
    "secondary_path",
    "input_image",
    "input_paths",
    "output",
    "outputs",
    "files",
    "audio",
    "srt",
    "preview",
    "local_path",
    "output_root",
    "command",
    "executable",
    "runtime",
    "model",
    "model_id",
}
_OUTPUT_FIELD_ALIASES = {
    # Voice workers historically called their Hub-owned output ``audio``.
    # Normalize that alias before handing the result to Job Manager so it
    # follows the same opaque artifact publication path as image/video output.
    "audio": "output",
}


def configured_path(
    component_id: str,
    field: str,
    environment_variable: str | None = None,
) -> Path | None:
    """Resolve a local path without hard-coding a particular workstation."""
    value = os.environ.get(environment_variable, "") if environment_variable else ""
    if not value:
        value = str((component(component_id) or {}).get(field) or "")
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def _registry_path(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip() or _UNRESOLVED_CONFIG.search(value):
        return None
    try:
        return Path(os.path.expandvars(value)).expanduser()
    except (OSError, ValueError):
        return None


def _is_reparse(path: Path) -> bool:
    """Reject links/junctions before a registry leaf is trusted."""

    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_managed_leaf(root: Path, candidate: Path, *, kind: str) -> Path | None:
    """Resolve one existing canonical leaf without following a reparse path."""

    try:
        root = Path(root)
        candidate = Path(candidate)
        if not root.is_dir() or _is_reparse(root):
            return None
        lexical_root = Path(os.path.abspath(str(root)))
        lexical = Path(os.path.abspath(str(candidate)))
        relative = lexical.relative_to(lexical_root)
        current = lexical_root
        for part in relative.parts:
            current = current / part
            if not current.exists() or _is_reparse(current):
                return None
        resolved_root = root.resolve(strict=True)
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(resolved_root)
        if _is_reparse(resolved):
            return None
        if kind == "file" and not resolved.is_file():
            return None
        if kind == "dir" and not resolved.is_dir():
            return None
        if kind not in {"file", "dir"} and not (resolved.is_file() or resolved.is_dir()):
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _model_has_payload(path: Path) -> bool:
    """Use a bounded leaf check so an empty model directory is not usable."""

    if path.is_file():
        return True
    if not path.is_dir():
        return False
    pending: list[tuple[Path, int]] = [(path, 0)]
    inspected = 0
    while pending and inspected < 128:
        current, depth = pending.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            return False
        for child in children[:128 - inspected]:
            inspected += 1
            if _is_reparse(child):
                return False
            if child.is_file():
                return True
            if child.is_dir() and depth < 3:
                pending.append((child, depth + 1))
            if inspected >= 128:
                break
    return False


def registered_runtime(component_id: str, script_name: str, *, executable_field: str = "executable") -> tuple[Path, Path, Path] | None:
    """Bind one worker to the local component registry, never environment input."""

    item = component(component_id)
    if not isinstance(item, dict) or item.get("id") != component_id:
        return None
    service = _registry_path(item.get("path"))
    executable = _registry_path(item.get(executable_field))
    if service is None or executable is None or Path(script_name).name != script_name:
        return None
    safe_service = _safe_managed_leaf(ROOT, service, kind="dir")
    safe_executable = _safe_managed_leaf(ROOT, executable, kind="file")
    safe_helper = _safe_managed_leaf(safe_service, safe_service / script_name, kind="file") if safe_service else None
    if safe_service is None or safe_executable is None or safe_helper is None:
        return None
    return safe_executable, safe_helper, safe_service


def registered_model(component_id: str, engine: str) -> tuple[str, Path] | None:
    """Return the one fixed engine model declared by the local registry."""

    owner = component(component_id)
    if not isinstance(owner, dict) or owner.get("id") != component_id:
        return None
    matches: list[tuple[str, Path]] = []
    for item in models():
        if not isinstance(item, dict) or item.get("engine") != engine:
            continue
        model_id = item.get("id")
        model_path = _registry_path(item.get("local_path"))
        if isinstance(model_id, str) and _REGISTRY_ID.fullmatch(model_id) and model_path is not None:
            safe_model = _safe_managed_leaf(MODEL_ROOT, model_path, kind="any")
            if safe_model is not None and _model_has_payload(safe_model):
                matches.append((model_id, safe_model))
    return matches[0] if len(matches) == 1 else None


def bounded_timeout(value: object, default: float, *, maximum: float) -> float:
    """Clamp worker timeout without allowing NaN, infinity, or unbounded input."""

    try:
        candidate = float(value)
    except (TypeError, ValueError):
        return float(default)
    if candidate != candidate or candidate <= 0 or candidate == float("inf"):
        return float(default)
    return min(candidate, float(maximum))


def resolve_artifact_input(payload: object, *fields: str) -> tuple[Path | None, str | None]:
    """Resolve only a Hub opaque artifact ID; raw workstation paths are refused."""

    if not isinstance(payload, dict):
        return None, "artifact_input_required"
    if any(field in payload and payload[field] not in (None, "", []) for field in _PATH_RESULT_FIELDS):
        return None, "raw_input_forbidden"
    selected: object = None
    for field in fields:
        if field in payload:
            selected = payload.get(field)
            break
    if not isinstance(selected, str) or not _ARTIFACT_ID.fullmatch(selected):
        return None, "artifact_input_required"
    try:
        from src.services.artifact_store import resolve

        path = resolve(selected)
    except Exception:
        path = None
    if path is None or not path.is_file():
        return None, "artifact_input_unavailable"
    try:
        if path.is_symlink() or bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400):
            return None, "artifact_input_unavailable"
    except OSError:
        return None, "artifact_input_unavailable"
    return path, None


def reject_raw_worker_fields(payload: object) -> str | None:
    """Reject path, command, executable, model and output fields from callers."""

    if not isinstance(payload, dict):
        return "invalid_request"
    if any(field in payload and payload[field] not in (None, "", []) for field in _PATH_RESULT_FIELDS):
        return "raw_input_forbidden"
    return None


def _safe_text(value: object) -> str:
    text = str(value or "")
    text = _LOCAL_PATH.sub("[đường-dẫn-cục-bộ]", text)
    return text.replace("\x00", "")[:_MAX_WORKER_TEXT]


def _safe_metadata(value: object, *, depth: int = 0) -> object | None:
    if depth > 4:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return max(-1_000_000, min(1_000_000, value))
    if isinstance(value, float):
        return value if value == value and abs(value) <= 1_000_000 else None
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [item for item in (_safe_metadata(item, depth=depth + 1) for item in value[:_MAX_WORKER_ITEMS]) if item is not None]
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in list(value.items())[:_MAX_WORKER_ITEMS]:
            name = str(key)
            if name.lower() in _PATH_RESULT_FIELDS or "path" in name.lower() or "command" in name.lower():
                continue
            safe = _safe_metadata(item, depth=depth + 1)
            if safe is not None:
                result[name[:80]] = safe
        return result
    return None


def attest_worker_output_paths(value: object, context: object | None, output_fields: tuple[str, ...]) -> bool:
    """Have a real Hub job attest each returned child before publication."""

    if not isinstance(value, dict) or value.get("status") != "completed":
        return True
    attest = getattr(context, "attest_output", None) if context is not None else None
    if not callable(attest):
        return True
    for field in output_fields:
        candidate = value.get(field)
        values = [candidate] if isinstance(candidate, str) and candidate else candidate if isinstance(candidate, list) else []
        for item in values:
            if not isinstance(item, str) or not item:
                return False
            try:
                status = attest(item).get("status")
            except Exception:
                return False
            if status not in {"attested", "claimed"}:
                return False
    return True


def normalize_worker_result(
    value: object,
    *,
    component_id: str,
    context: object | None = None,
    output_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return bounded metadata; output paths are internal only for JobContext."""

    if not isinstance(value, dict) or value.get("status") not in {"completed", "failed", "unavailable", "cancelled", "error"}:
        return {"status": "error", "component": component_id, "code": "invalid_worker_result"}
    status = str(value.get("status"))
    if status == "completed" and not attest_worker_output_paths(value, context, output_fields):
        return {
            "status": "unavailable",
            "component": component_id,
            "code": "output_scope_unavailable",
            "execution": "not_run",
            "dry_run": True,
            "reason": "Producer output ownership could not be attested.",
        }
    safe: dict[str, Any] = {"status": status, "component": component_id}
    for key in _WORKER_COMMON_FIELDS:
        if key in value:
            normalized = _safe_metadata(value.get(key))
            if normalized is not None:
                safe[key] = normalized
    for key in ("grounded", "detections", "objects", "boxes", "text", "markdown", "json", "tables", "segments", "artifact"):
        if key in value:
            normalized = _safe_metadata(value.get(key))
            if normalized is not None:
                safe[key] = normalized
    if context is not None and status == "completed":
        for key in output_fields:
            candidate = value.get(key)
            target_key = _OUTPUT_FIELD_ALIASES.get(key, key)
            # Prefer the canonical output field when a worker supplies both
            # an output and an alias; never let a later alias overwrite it.
            if target_key in safe:
                continue
            if isinstance(candidate, str) and candidate:
                safe[target_key] = candidate
            elif isinstance(candidate, list) and len(candidate) <= _MAX_WORKER_ITEMS and all(isinstance(item, str) and item for item in candidate):
                safe[target_key] = list(candidate)
    if status in {"error", "failed"}:
        raw_code = str(value.get("code") or "").casefold()
        error_text = str(value.get("error") or value.get("reason") or "").casefold()
        if raw_code in {"timeout", "timed_out", "worker_timeout"} or "timeout" in error_text or "vượt quá thời gian" in error_text:
            safe["code"] = "worker_timeout"
            safe["execution"] = "timed_out"
            safe["reason"] = "Worker vượt quá thời gian bounded của adapter."
            safe["next_action"] = "Kiểm tra environment/model và thử lại một job bounded sau khi xác nhận tài nguyên rảnh."
    return safe


def local_root() -> Path:
    """Return the configured repository root, defaulting to this checkout."""
    value = os.environ.get("LOCALAIHUB_ROOT") or os.environ.get("LOCAL_AI_HOME")
    return Path(os.path.expandvars(value)).expanduser() if value else BASE_DIR


def server_output_namespace(context: object | None, label: str) -> Path | None:
    """Return a manager-issued private output namespace, if this is a Hub job."""

    if context is None:
        return None
    claim = getattr(context, "claim_output_namespace", None)
    if not callable(claim):
        return None
    try:
        value = claim(label)
    except Exception:
        return None
    return Path(value) if isinstance(value, (str, Path)) and str(value) else None


def requires_server_output_namespace(context: object | None) -> bool:
    """Identify the real HubJobManager claim contract without guessing for direct tests."""

    return context is not None and callable(getattr(context, "claim_output_namespace", None))


def safe_output_namespace(root: Path, candidate: object) -> Path | None:
    """Revalidate one manager-issued namespace inside the canonical Output root."""

    if not isinstance(candidate, (str, Path)) or not str(candidate):
        return None
    try:
        output_root = (Path(root) / "Output").resolve(strict=True)
        lexical = Path(candidate).expanduser().absolute()
        relative = lexical.relative_to(output_root)
        if not relative.parts or relative.parts[0] != ".job-output-scopes":
            return None
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(output_root)
        if not resolved.is_dir() or _is_reparse(lexical) or _is_reparse(resolved):
            return None
        current = output_root
        for part in relative.parts:
            current = current / part
            if _is_reparse(current):
                return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def local_cache_root() -> Path:
    value = os.environ.get("LOCAL_AI_CACHE")
    return Path(os.path.expandvars(value)).expanduser() if value else local_root() / "Cache"


def describe(component_id: str) -> dict[str, Any]:
    item = component(component_id) or {"id": component_id}
    executable = item.get("executable")
    return {
        "component": component_id,
        "name": item.get("name", component_id),
        "status": item.get("status", "unknown"),
        "path": item.get("path"),
        "executable": executable,
        "executable_exists": bool(executable and Path(executable).exists()),
        "source": item.get("source"),
    }


def unavailable(
    component_id: str,
    reason: str,
    *,
    code: str = "dependency_unavailable",
    next_action: str = "Kiểm tra registry component/model và helper worker rồi chạy lại bounded smoke.",
) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "component": component_id,
        "execution": "not_run",
        "code": code,
        "reason": _safe_text(reason),
        "next_action": _safe_text(next_action),
    }
