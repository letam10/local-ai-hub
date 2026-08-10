"""Private artifact registry for files staged or produced by Local AI Hub.

The API exposes opaque artifact IDs instead of workstation paths.  This keeps
the browser control plane useful without disclosing local folders, and limits
file serving/opening to Hub-owned temporary, output, and archive roots.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT, OUTPUT_ROOT, ROOT, TEMP_ROOT


INDEX_PATH = CONFIG_ROOT / "artifacts.json"
UPLOAD_ROOT = TEMP_ROOT / "uploads"
ARCHIVE_ROOT = ROOT / "Archive"
MAX_UPLOAD_BYTES = 512 * 1024 * 1024
_LOCK = threading.RLock()
_SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._() -]+")
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)")
_LOCAL_PATH_TAIL = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\)[^\r\n]*")
_PATH_FIELDS = {
    "annotated_image",
    "audio",
    "files",
    "input",
    "json",
    "mask",
    "masks",
    "output",
    "output_root",
    "preview",
    "srt",
    "video",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save(index: dict[str, dict[str, Any]]) -> None:
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(INDEX_PATH)


def _safe_name(value: str) -> str:
    name = Path(value).name.strip().replace("\x00", "")
    name = _SAFE_FILENAME.sub("_", name)
    return name[:180] or "upload.bin"


def _allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in (UPLOAD_ROOT, OUTPUT_ROOT, ARCHIVE_ROOT):
        try:
            resolved.relative_to(root.resolve())
            return True
        except (OSError, ValueError):
            continue
    return False


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "size_bytes": record.get("size_bytes", 0),
        "media_type": record.get("media_type") or "application/octet-stream",
        "url": f"/api/artifacts/{record['id']}",
        "created_at": record.get("created_at"),
    }


def register_path(path: str | Path, *, name: str | None = None) -> dict[str, Any] | None:
    """Register a Hub-owned file and return its safe public reference."""

    candidate = Path(path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return None
    if not candidate.is_file() or not _allowed(candidate):
        return None
    with _LOCK:
        index = _load()
        for record in index.values():
            if record.get("path") == str(candidate):
                return _public(record)
        artifact_id = f"artifact_{uuid.uuid4().hex}"
        record = {
            "id": artifact_id,
            "path": str(candidate),
            "name": _safe_name(name or candidate.name),
            "size_bytes": candidate.stat().st_size,
            "media_type": mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
            "created_at": _now(),
        }
        index[artifact_id] = record
        _save(index)
        return _public(record)


def stage_upload(filename: str, body: bytes, content_type: str | None = None) -> dict[str, Any]:
    if not body:
        raise ValueError("Tệp tải lên đang rỗng.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise ValueError("Tệp tải lên vượt giới hạn 512 MiB của Hub.")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_ROOT / f"{uuid.uuid4().hex}_{_safe_name(filename)}"
    path.write_bytes(body)
    artifact = register_path(path, name=_safe_name(filename))
    if artifact is None:  # pragma: no cover - guarded by the owned upload path
        raise RuntimeError("Hub không thể đăng ký tệp đã tải lên.")
    if content_type:
        artifact["media_type"] = content_type.split(";", 1)[0].strip() or artifact["media_type"]
    return artifact


def resolve(artifact_id: str) -> Path | None:
    if not re.fullmatch(r"artifact_[a-f0-9]{32}", artifact_id or ""):
        return None
    with _LOCK:
        record = _load().get(artifact_id)
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return None
    candidate = Path(record["path"])
    return candidate if candidate.is_file() and _allowed(candidate) else None


def describe(artifact_id: str) -> dict[str, Any] | None:
    with _LOCK:
        record = _load().get(artifact_id)
    if not isinstance(record, dict):
        return None
    path = resolve(artifact_id)
    return _public(record) if path is not None else None


def list_artifacts(*, limit: int = 240) -> list[dict[str, Any]]:
    """List safe metadata for Hub-owned artifacts without disclosing paths."""

    bounded = max(1, min(500, int(limit)))
    with _LOCK:
        records = list(_load().values())
    result: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True):
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        path = resolve(record["id"])
        if path is not None:
            result.append(_public(record))
        if len(result) >= bounded:
            break
    return result


def open_artifact(artifact_id: str) -> tuple[bool, str]:
    path = resolve(artifact_id)
    if path is None:
        return False, "Không tìm thấy artifact Hub yêu cầu."
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:  # pragma: no cover - the desktop target is Windows
            return False, "Mở artifact chỉ được hỗ trợ từ bản Hub cho Windows."
    except OSError as exc:
        return False, str(exc)
    return True, "Đã yêu cầu Windows mở artifact Hub."


def _safe_text(value: str) -> str:
    return _LOCAL_PATH_TAIL.sub("[đường-dẫn-cục-bộ]", value)


def publicize(value: Any, *, key: str | None = None) -> Any:
    """Convert private output paths into artifact references for API responses."""

    if isinstance(value, Path):
        artifact = register_path(value)
        return artifact or value.name
    if isinstance(value, str):
        candidate = Path(value)
        if key in _PATH_FIELDS or _LOCAL_PATH.search(value):
            artifact = register_path(candidate)
            if artifact:
                return artifact
            return candidate.name if _LOCAL_PATH.search(value) else _safe_text(value)
        return _safe_text(value)
    if isinstance(value, list):
        return [publicize(item, key=key) for item in value]
    if isinstance(value, dict):
        return {str(item_key): publicize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    return value
