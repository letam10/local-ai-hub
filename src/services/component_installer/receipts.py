"""Atomic, path-free component installation receipt V2 helpers."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any


RECEIPT_SCHEMA = "component-install-receipts.v2"


def read_receipts(config_root: Path) -> dict[str, Any]:
    path = Path(config_root) / "component_install_receipts.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = {}
    records = value.get("records") if isinstance(value, Mapping) else None
    return {"schema_version": RECEIPT_SCHEMA, "records": dict(records) if isinstance(records, Mapping) else {}}


def _atomic_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".component-receipts-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def source_identity(record: Mapping[str, Any]) -> str:
    source = record.get("primary_source") or record.get("official_source") or "local"
    value = {
        "source": source if isinstance(source, (str, Mapping)) else "unknown",
        "identity": record.get("source_identity") or record.get("revision") or "unknown",
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def write_component_receipt(config_root: Path, component_id: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    value = read_receipts(config_root)
    safe = {key: receipt[key] for key in receipt if key not in {"path", "url", "command", "executable", "secret", "token"}}
    value["records"][component_id] = dict(safe)
    _atomic_write(Path(config_root) / "component_install_receipts.json", value)
    return dict(safe)


__all__ = ["RECEIPT_SCHEMA", "read_receipts", "source_identity", "write_component_receipt"]
