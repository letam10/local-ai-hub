"""Versioned, machine-readable module manifest validation.

Manifest discovery is intentionally data-only: validating a manifest never
imports a backend or executes a command.  The registry decides which built-in
module directories are allowlisted; user JSON cannot name arbitrary Python.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


MODULE_MANIFEST_SCHEMA = "module-manifest.v1"
MODULE_STATES = frozenset({
    "NOT_INSTALLED",
    "INSTALLING",
    "INSTALLED_UNVERIFIED",
    "PARTIAL",
    "OPERATIONAL",
    "UNAVAILABLE",
    "BROKEN",
    "UPDATE_AVAILABLE",
})
_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}(?:[-+][A-Za-z0-9.-]+)?$")
_FORBIDDEN = re.compile(r"(?:^|_)(?:command|executable|absolute_path|credential|secret|token)(?:$|_)", re.I)


class ManifestError(ValueError):
    """Raised when a module manifest is not safe or complete."""


def _text(value: object, field: str, *, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"invalid_{field}")
    result = value.strip()
    if pattern is not None and not pattern.fullmatch(result):
        raise ManifestError(f"invalid_{field}")
    if "\\" in result or result.startswith(("/", "~")) or "://" in result:
        raise ManifestError(f"unsafe_{field}")
    return result


def _string_list(value: object, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ManifestError(f"invalid_{field}")
    result = [_text(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ManifestError(f"duplicate_{field}")
    return result


def validate_manifest(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize one manifest without dynamic imports."""

    if not isinstance(value, Mapping):
        raise ManifestError("manifest_not_object")
    allowed = {
        "schema_version", "id", "display_name", "version", "category",
        "description", "capabilities", "runtime", "models", "inputs",
        "outputs", "gpu", "install", "status_probe", "adapter", "worker",
        "tests", "dependencies",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ManifestError("unknown_manifest_field")
    if value.get("schema_version") != MODULE_MANIFEST_SCHEMA:
        raise ManifestError("unsupported_manifest_schema")
    identifier = _text(value.get("id"), "id", pattern=_ID)
    version = _text(value.get("version"), "version", pattern=_VERSION)
    display_name = _text(value.get("display_name"), "display_name")
    category = _text(value.get("category"), "category", pattern=_ID)
    description = _text(value.get("description", display_name), "description")
    capabilities = _string_list(value.get("capabilities", []), "capabilities")
    inputs = _string_list(value.get("inputs", []), "inputs")
    outputs = _string_list(value.get("outputs", []), "outputs")
    tests = _string_list(value.get("tests", []), "tests")
    dependencies = _string_list(value.get("dependencies", []), "dependencies")
    for key, raw in value.items():
        if _FORBIDDEN.search(str(key)):
            raise ManifestError("forbidden_manifest_field")
        if isinstance(raw, str) and ("D:\\" in raw or "C:\\" in raw or "python " in raw.lower()):
            raise ManifestError("unsafe_manifest_value")
    runtime = value.get("runtime", {})
    models = value.get("models", {})
    gpu = value.get("gpu", {})
    install = value.get("install", {})
    for field, section in (("runtime", runtime), ("models", models), ("gpu", gpu), ("install", install)):
        if not isinstance(section, Mapping):
            raise ManifestError(f"invalid_{field}")
        if any(_FORBIDDEN.search(str(key)) for key in section):
            raise ManifestError("forbidden_manifest_field")
    normalized = {
        "schema_version": MODULE_MANIFEST_SCHEMA,
        "id": identifier,
        "display_name": display_name,
        "version": version,
        "category": category,
        "description": description,
        "capabilities": capabilities,
        "runtime": dict(runtime),
        "models": dict(models),
        "inputs": inputs,
        "outputs": outputs,
        "gpu": dict(gpu),
        "install": dict(install),
        "status_probe": str(value.get("status_probe", "static")),
        "adapter": _text(value.get("adapter", "module adapter"), "adapter"),
        "worker": _text(value.get("worker", "module worker"), "worker"),
        "tests": tests,
        "dependencies": dependencies,
    }
    return normalized


__all__ = ["MODULE_MANIFEST_SCHEMA", "MODULE_STATES", "ManifestError", "validate_manifest"]
