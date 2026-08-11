"""Closed schemas for the V5 capability registry and module-manager plans.

The schema layer is deliberately dependency-free.  It accepts detached JSON
values only, rejects workstation paths and execution material, and provides
canonical fingerprints for deterministic server-owned projections.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any


CAPABILITY_REGISTRY_SCHEMA_VERSION = "capability-registry.v1"
MODULE_MANAGER_SCHEMA_VERSION = "module-manager.v1"
STATUS_ALLOWLIST = frozenset({"installed", "available", "partial", "operational", "unavailable", "error", "planned", "not_published"})
OBSERVED_STATE_ALLOWLIST = frozenset({"observed", "declared", "stale", "missing", "not_run"})
EVIDENCE_STATE_ALLOWLIST = frozenset({"static", "runtime", "stale", "missing", "not_run"})
EXECUTION_NOT_RUN = "not_run"
MAX_RECORDS = 512
MAX_DEPENDENCIES = 64
MAX_TEXT = 240
MAX_BYTES = 512 * 1024
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{0,119}$")
SAFE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,119}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ModuleManagerValidationError(ValueError):
    """Raised by strict schema helpers when a closed value is invalid."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        self.errors = tuple(deepcopy(errors))
        super().__init__("V5 module-manager value failed validation.")


def _issue(code: str, location: str = "value") -> dict[str, str]:
    return {"code": code, "location": location}


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and bool(SAFE_ID_RE.fullmatch(value)) and ".." not in value


def _safe_version(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(SAFE_VERSION_RE.fullmatch(value)))


def _safe_text(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= MAX_TEXT:
        return False
    lowered = value.lower()
    if any(token in lowered for token in ("file:", "data:", "bearer ", "sk-")):
        return False
    return not bool(re.search(r"(?:[a-z]:[\\/]|\\\\|(?:^|\s)/(?:etc|tmp|var|home)(?:/|\s|$)|\.\.[\\/])", value, re.IGNORECASE))


def _safe_string_list(value: object, *, maximum: int = 64) -> bool:
    return isinstance(value, list) and len(value) <= maximum and len(value) == len(set(value)) and all(_safe_id(item) for item in value)


def _valid_dependencies(value: object) -> bool:
    if not isinstance(value, list) or len(value) > MAX_DEPENDENCIES:
        return False
    identities: list[str] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) - {"id", "version", "optional"}:
            return False
        if not _safe_id(item.get("id")) or not _safe_version(item.get("version")):
            return False
        if "optional" in item and type(item["optional"]) is not bool:
            return False
        identities.append(str(item["id"]))
    return len(identities) == len(set(identities))


def _valid_resource_hints(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or set(value) - {"cpu_cores", "ram_mb", "disk_mb", "gpu"}:
        return False
    for field in ("cpu_cores", "ram_mb", "disk_mb"):
        if field in value and (type(value[field]) is not int or value[field] < 0):
            return False
    gpu = value.get("gpu")
    if gpu is None:
        return True
    if not isinstance(gpu, Mapping) or set(gpu) - {"required", "vendor", "device_class", "vram_mb", "target"}:
        return False
    if "required" in gpu and type(gpu["required"]) is not bool:
        return False
    if gpu.get("vendor", "any") not in {"any", "nvidia", "amd", "intel"}:
        return False
    if gpu.get("device_class", "any") not in {"any", "discrete", "integrated"}:
        return False
    if "vram_mb" in gpu and (type(gpu["vram_mb"]) is not int or gpu["vram_mb"] < 0):
        return False
    if "target" in gpu and not _safe_text(gpu["target"]):
        return False
    return True


def _valid_evidence(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) - {"state", "fingerprint", "observed_at"}:
        return False
    if value.get("state", "not_run") not in EVIDENCE_STATE_ALLOWLIST:
        return False
    fingerprint = value.get("fingerprint")
    if fingerprint is not None and (not isinstance(fingerprint, str) or not SHA256_RE.fullmatch(fingerprint)):
        return False
    if "observed_at" in value and not _safe_text(value["observed_at"]):
        return False
    return True


def _valid_source_manifest(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or set(value) - {"url", "sha256", "license", "version"}:
        return False
    url = value.get("url")
    digest = value.get("sha256")
    if not isinstance(url, str) or not url.startswith("https://") or any(token in url.lower() for token in ("@", "?", "#", "file:", "\\", "..")):
        return False
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        return False
    return all(value.get(field) is None or _safe_text(value[field]) for field in ("license", "version"))


def _valid_observed(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) - {"state", "fingerprint", "source"}:
        return False
    if value.get("state", "not_run") not in OBSERVED_STATE_ALLOWLIST:
        return False
    if value.get("fingerprint") is not None and (not isinstance(value["fingerprint"], str) or not SHA256_RE.fullmatch(value["fingerprint"])):
        return False
    if value.get("source") is not None and not _safe_id(value["source"]):
        return False
    return True


def validate_module_record(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ModuleManagerValidationError([_issue("record_object_required")])
    allowed = {
        "id", "provider", "component", "tool", "workflow", "version", "dependencies",
        "observed", "evidence", "resource_hints", "status", "reason", "next_action",
        "source", "license", "model_requirements", "source_manifest",
    }
    errors: list[dict[str, str]] = []
    if set(value) - allowed:
        errors.append(_issue("record_unknown_field"))
    for field in ("id", "provider", "component"):
        if not _safe_id(value.get(field)):
            errors.append(_issue("record_invalid_id", field))
    for field in ("tool", "workflow", "version", "source", "license"):
        if field in value and value[field] is not None and (not _safe_id(value[field]) if field in {"tool", "workflow"} else not (_safe_version(value[field]) if field == "version" else _safe_text(value[field]))):
            errors.append(_issue("record_invalid_text", field))
    if not _valid_dependencies(value.get("dependencies", [])):
        errors.append(_issue("record_invalid_dependencies", "dependencies"))
    if not _valid_observed(value.get("observed", {"state": "not_run"})):
        errors.append(_issue("record_invalid_observed", "observed"))
    if not _valid_evidence(value.get("evidence", {"state": "not_run"})):
        errors.append(_issue("record_invalid_evidence", "evidence"))
    if not _valid_resource_hints(value.get("resource_hints")):
        errors.append(_issue("record_invalid_resources", "resource_hints"))
    if not _valid_source_manifest(value.get("source_manifest")):
        errors.append(_issue("record_invalid_source_manifest", "source_manifest"))
    if value.get("status") not in STATUS_ALLOWLIST:
        errors.append(_issue("record_invalid_status", "status"))
    for field in ("reason", "next_action"):
        if not _safe_text(value.get(field)):
            errors.append(_issue("record_invalid_guidance", field))
    if not _safe_string_list(value.get("model_requirements", [])):
        errors.append(_issue("record_invalid_model_requirements", "model_requirements"))
    if errors:
        raise ModuleManagerValidationError(errors)
    return deepcopy(dict(value))


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError, UnicodeEncodeError) as exc:
        raise ModuleManagerValidationError([_issue("non_json_value")]) from exc


def capability_registry_fingerprint(value: Mapping[str, Any]) -> str:
    candidate = deepcopy(dict(value))
    candidate.pop("fingerprint", None)
    return hashlib.sha256(_canonical(candidate).encode("utf-8")).hexdigest()


def canonical_module_manager_json(value: object) -> str:
    return _canonical(value)


def validate_capability_registry(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": [_issue("registry_object_required")], "registry": None}
    if set(value) - {"schema_version", "status", "records", "counts", "execution", "dry_run", "fingerprint", "reason", "next_action", "errors"}:
        return {"valid": False, "errors": [_issue("registry_unknown_field")], "registry": None}
    records = value.get("records")
    if value.get("schema_version") != CAPABILITY_REGISTRY_SCHEMA_VERSION or not isinstance(records, list) or len(records) > MAX_RECORDS:
        return {"valid": False, "errors": [_issue("registry_shape")], "registry": None}
    try:
        normalized = [validate_module_record(item) for item in records]
    except ModuleManagerValidationError as exc:
        return {"valid": False, "errors": list(exc.errors), "registry": None}
    identities = [(item["id"], item.get("version")) for item in normalized]
    if len(identities) != len(set(identities)):
        return {"valid": False, "errors": [_issue("registry_duplicate_identity")], "registry": None}
    if value.get("status") not in STATUS_ALLOWLIST or value.get("execution") != EXECUTION_NOT_RUN or type(value.get("dry_run")) is not bool:
        return {"valid": False, "errors": [_issue("registry_execution_invariant")], "registry": None}
    if not _safe_text(value.get("reason")) or not _safe_text(value.get("next_action")):
        return {"valid": False, "errors": [_issue("registry_guidance")], "registry": None}
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, Mapping) or fingerprint.get("algorithm") != "sha256" or not isinstance(fingerprint.get("value"), str) or not SHA256_RE.fullmatch(fingerprint["value"]):
        return {"valid": False, "errors": [_issue("registry_fingerprint")], "registry": None}
    expected = capability_registry_fingerprint({key: value[key] for key in value if key != "fingerprint"})
    if fingerprint["value"] != expected:
        return {"valid": False, "errors": [_issue("registry_fingerprint_mismatch")], "registry": None}
    return {"valid": True, "errors": [], "registry": deepcopy(dict(value)), "fingerprint": expected}


def validate_module_plan(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"valid": False, "errors": [_issue("plan_object_required")], "plan": None}
    required = {"schema_version", "status", "execution", "dry_run", "actions", "modules", "resource_plan", "fingerprint"}
    if set(value) - required - {"reason", "next_action", "errors"} or not required.issubset(value):
        return {"valid": False, "errors": [_issue("plan_shape")], "plan": None}
    if value.get("schema_version") != MODULE_MANAGER_SCHEMA_VERSION or value.get("execution") != EXECUTION_NOT_RUN or value.get("dry_run") is not True:
        return {"valid": False, "errors": [_issue("plan_execution_invariant")], "plan": None}
    if value.get("status") not in STATUS_ALLOWLIST or not isinstance(value.get("actions"), list) or not all(_safe_text(item) for item in value["actions"]):
        return {"valid": False, "errors": [_issue("plan_status_actions")], "plan": None}
    resource_plan = value.get("resource_plan")
    if not isinstance(value.get("modules"), list) or len(value["modules"]) > MAX_RECORDS or not isinstance(resource_plan, Mapping) or resource_plan.get("execution") != EXECUTION_NOT_RUN or resource_plan.get("dry_run") is not True:
        return {"valid": False, "errors": [_issue("plan_modules_resources")], "plan": None}
    fingerprint = value.get("fingerprint")
    if not isinstance(fingerprint, Mapping) or fingerprint.get("algorithm") != "sha256" or not isinstance(fingerprint.get("value"), str) or not SHA256_RE.fullmatch(fingerprint["value"]):
        return {"valid": False, "errors": [_issue("plan_fingerprint")], "plan": None}
    candidate = deepcopy(dict(value))
    candidate.pop("fingerprint", None)
    expected = hashlib.sha256(_canonical(candidate).encode("utf-8")).hexdigest()
    if fingerprint["value"] != expected:
        return {"valid": False, "errors": [_issue("plan_fingerprint_mismatch")], "plan": None}
    return {"valid": True, "errors": [], "plan": deepcopy(dict(value))}


def capability_registry_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CAPABILITY_REGISTRY_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "status", "records", "execution", "dry_run", "fingerprint"],
        "properties": {
            "schema_version": {"const": CAPABILITY_REGISTRY_SCHEMA_VERSION},
            "status": {"enum": sorted(STATUS_ALLOWLIST)},
            "records": {"type": "array", "maxItems": MAX_RECORDS},
            "execution": {"const": EXECUTION_NOT_RUN},
            "dry_run": {"type": "boolean"},
            "fingerprint": {"type": "object"},
        },
    }


def module_manager_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": MODULE_MANAGER_SCHEMA_VERSION,
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "status", "execution", "dry_run", "actions", "modules", "resource_plan", "fingerprint"],
        "properties": {
            "schema_version": {"const": MODULE_MANAGER_SCHEMA_VERSION},
            "status": {"enum": sorted(STATUS_ALLOWLIST)},
            "execution": {"const": EXECUTION_NOT_RUN},
            "dry_run": {"const": True},
            "actions": {"type": "array"},
            "modules": {"type": "array", "maxItems": MAX_RECORDS},
            "resource_plan": {"type": "object"},
            "fingerprint": {"type": "object"},
        },
    }


__all__ = [
    "CAPABILITY_REGISTRY_SCHEMA_VERSION",
    "MODULE_MANAGER_SCHEMA_VERSION",
    "STATUS_ALLOWLIST",
    "EXECUTION_NOT_RUN",
    "ModuleManagerValidationError",
    "canonical_module_manager_json",
    "capability_registry_fingerprint",
    "capability_registry_schema",
    "module_manager_schema",
    "validate_capability_registry",
    "validate_module_plan",
    "validate_module_record",
]
