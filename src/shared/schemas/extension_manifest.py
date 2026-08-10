"""Dependency-free schema and safety validation for Extension Manifest v1.

The extension platform only consumes declarative JSON.  This module deliberately
does not import extension code, resolve arbitrary machine paths, or launch a
process.  It validates the small, allowlisted contract used by static discovery
and resource planning instead.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping
from urllib.parse import urlparse


MANIFEST_SCHEMA_VERSION = "extension-manifest.v1"
CAPABILITY_PACK_SCHEMA_VERSION = "capability-pack.v1"
MODEL_CARD_SCHEMA_VERSION = "model-card.v1"
RUNTIME_CARD_SCHEMA_VERSION = "runtime-card.v1"

EXTENSION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
RESOURCE_GROUP_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,63}$")
SAFE_DESCRIPTOR_PATH_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
VERSION_CONSTRAINT_PATTERN = re.compile(
    r"^(?:(?:>=|<=|>|<|==)?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)(?:\s*,\s*"
    r"(?:>=|<=|>|<|==)?(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?)*$"
)

AVAILABILITY_STATUSES = frozenset({"operational", "partial", "unavailable", "planned"})
CAPABILITY_ALLOWLIST = frozenset(
    {
        "audio_transcription",
        "capability_pack",
        "document_analysis",
        "image_analysis",
        "image_generation",
        "metadata_catalog",
        "model_cards",
        "resource_planning",
        "runtime_cards",
        "text_generation",
        "workflow_templates",
    }
)
PERMISSION_ALLOWLIST = frozenset(
    {
        "inspect_component_status",
        "inspect_model_status",
        "plan_resources",
        "read_capability_cards",
        "read_extension_metadata",
        "render_compatibility_report",
    }
)
ENTRYPOINT_ALLOWLIST: dict[str, frozenset[str]] = {
    "capability_pack": frozenset({".json"}),
    "model_cards": frozenset({".json"}),
    "runtime_cards": frozenset({".json"}),
    "documentation": frozenset({".md"}),
}
CPU_CLASSES = frozenset({"light", "moderate", "heavy"})
GPU_VENDORS = frozenset({"none", "any", "nvidia", "amd", "intel"})
GPU_DEVICE_CLASSES = frozenset({"none", "any", "integrated", "discrete"})
PLATFORMS = frozenset({"windows", "linux", "darwin"})


class ManifestValidationError(ValueError):
    """Raised when an extension manifest is not safe or structurally valid."""

    def __init__(self, issues: list[dict[str, str]]) -> None:
        self.issues = tuple(issues)
        codes = ", ".join(sorted({issue["code"] for issue in issues})) or "invalid_manifest"
        super().__init__(f"Extension manifest validation failed: {codes}")


def _issue(issues: list[dict[str, str]], field: str, code: str, message: str) -> None:
    # Field names and messages are authored by this module; values supplied by an
    # extension are never reflected, so validation output cannot leak a path or a secret.
    issues.append({"field": field, "code": code, "message": message})


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _safe_text(value: Any, *, minimum: int = 1, maximum: int = 240) -> bool:
    return (
        isinstance(value, str)
        and minimum <= len(value.strip()) <= maximum
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value
    )


def _safe_url(value: Any) -> bool:
    if not _safe_text(value, maximum=300) or "?" in value or "#" in value:
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"https", "http"} and bool(parsed.netloc) and "@" not in parsed.netloc


def _safe_descriptor_path(value: Any, allowed_suffixes: frozenset[str]) -> bool:
    if not isinstance(value, str) or not SAFE_DESCRIPTOR_PATH_PATTERN.fullmatch(value):
        return False
    if value.startswith(("/", "\\")) or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return any(value.endswith(suffix) for suffix in allowed_suffixes)


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and bool(EXTENSION_ID_PATTERN.fullmatch(value))


def is_semver(value: Any) -> bool:
    """Return whether *value* is a concrete semantic version supported by v1."""

    return isinstance(value, str) and bool(SEMVER_PATTERN.fullmatch(value))


def is_version_constraint(value: Any) -> bool:
    """Return whether *value* is a comma-separated, safe semantic version range."""

    return isinstance(value, str) and value == value.strip() and bool(VERSION_CONSTRAINT_PATTERN.fullmatch(value))


def _semver_key(value: str) -> tuple[int, int, int, tuple[tuple[int, Any], ...]]:
    """Parse a semver for deterministic, dependency-free comparison."""

    match = SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("version must be a semantic version")
    base, _, build = value.partition("+")
    del build
    core, separator, prerelease = base.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    if not separator:
        # A release sorts after any prerelease of the same core version.
        prerelease_key: tuple[tuple[int, Any], ...] = ((2, ""),)
    else:
        values: list[tuple[int, Any]] = []
        for part in prerelease.split("."):
            values.append((0, int(part)) if part.isdigit() else (1, part))
        prerelease_key = tuple(values)
    return major, minor, patch, prerelease_key


def compare_versions(left: str, right: str) -> int:
    """Compare two semantic versions without requiring a packaging dependency."""

    left_key = _semver_key(left)
    right_key = _semver_key(right)
    if left_key[:3] != right_key[:3]:
        return -1 if left_key[:3] < right_key[:3] else 1
    left_release = len(left_key[3]) == 1 and left_key[3][0][0] == 2
    right_release = len(right_key[3]) == 1 and right_key[3][0][0] == 2
    if left_release != right_release:
        return 1 if left_release else -1
    if left_key[3] == right_key[3]:
        return 0
    return -1 if left_key[3] < right_key[3] else 1


def version_satisfies(version: str, constraint: str | None) -> bool:
    """Evaluate the limited, documented version constraints accepted by v1."""

    if not is_semver(version):
        return False
    if constraint in {None, "", "*"}:
        return True
    if not is_version_constraint(constraint):
        return False
    for expression in (part.strip() for part in constraint.split(",")):
        match = re.fullmatch(r"(>=|<=|>|<|==)?(.+)", expression)
        if match is None:
            return False
        operator = match.group(1) or "=="
        comparison = compare_versions(version, match.group(2))
        if operator == ">=" and comparison < 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator == "==" and comparison != 0:
            return False
    return True


def _validate_author(value: Any, issues: list[dict[str, str]]) -> None:
    if not _is_mapping(value):
        _issue(issues, "author", "invalid_type", "author must be an object with a name.")
        return
    unknown = set(value) - {"name", "url"}
    if unknown:
        _issue(issues, "author", "unknown_field", "author contains a field outside the v1 allowlist.")
    if not _safe_text(value.get("name"), maximum=120):
        _issue(issues, "author.name", "invalid_value", "author.name must be concise, non-empty text.")
    if "url" in value and not _safe_url(value["url"]):
        _issue(issues, "author.url", "invalid_url", "author.url must be a public HTTP(S) URL without credentials or query data.")


def _validate_availability(value: Any, issues: list[dict[str, str]]) -> None:
    if not _is_mapping(value):
        _issue(issues, "availability", "invalid_type", "availability must describe an honest status, reason, and action.")
        return
    if set(value) - {"status", "reason", "action"}:
        _issue(issues, "availability", "unknown_field", "availability contains a field outside the v1 allowlist.")
    if value.get("status") not in AVAILABILITY_STATUSES:
        _issue(issues, "availability.status", "invalid_status", "availability.status must be operational, partial, unavailable, or planned.")
    if not _safe_text(value.get("reason"), maximum=300):
        _issue(issues, "availability.reason", "invalid_value", "availability.reason must be concise, non-empty text.")
    if not _safe_text(value.get("action"), maximum=300):
        _issue(issues, "availability.action", "invalid_value", "availability.action must be concise, non-empty text.")


def _validate_compatibility(value: Any, issues: list[dict[str, str]]) -> None:
    if not _is_mapping(value):
        _issue(issues, "compatibility", "invalid_type", "compatibility must be an object.")
        return
    if set(value) - {"hub", "platforms"}:
        _issue(issues, "compatibility", "unknown_field", "compatibility contains a field outside the v1 allowlist.")
    hub = value.get("hub")
    if not _is_mapping(hub):
        _issue(issues, "compatibility.hub", "invalid_type", "compatibility.hub must declare a minimum Hub version.")
    else:
        if set(hub) - {"min_version", "max_version"}:
            _issue(issues, "compatibility.hub", "unknown_field", "compatibility.hub contains a field outside the v1 allowlist.")
        minimum = hub.get("min_version")
        maximum = hub.get("max_version")
        if not is_semver(minimum):
            _issue(issues, "compatibility.hub.min_version", "invalid_version", "compatibility.hub.min_version must be semantic version text.")
        if maximum is not None and not is_semver(maximum):
            _issue(issues, "compatibility.hub.max_version", "invalid_version", "compatibility.hub.max_version must be semantic version text.")
        if is_semver(minimum) and is_semver(maximum) and compare_versions(minimum, maximum) > 0:
            _issue(issues, "compatibility.hub", "invalid_range", "compatibility.hub minimum version cannot be higher than maximum version.")
    platforms = value.get("platforms", ["windows"])
    if not isinstance(platforms, list) or not platforms or len(platforms) != len(set(platforms)):
        _issue(issues, "compatibility.platforms", "invalid_value", "compatibility.platforms must be a non-empty, unique list.")
    elif any(platform not in PLATFORMS for platform in platforms):
        _issue(issues, "compatibility.platforms", "unsupported_platform", "compatibility.platforms contains an unsupported platform identifier.")


def _validate_requirements(value: Any, field: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, list):
        _issue(issues, field, "invalid_type", f"{field} must be a list of declarative requirements.")
        return
    seen: set[str] = set()
    for item in value:
        if not _is_mapping(item):
            _issue(issues, field, "invalid_item", f"{field} entries must be objects.")
            continue
        if set(item) - {"id", "version", "optional"}:
            _issue(issues, field, "unknown_field", f"{field} contains a field outside the v1 allowlist.")
        item_id = item.get("id")
        if not _valid_id(item_id):
            _issue(issues, field, "invalid_id", f"{field} entries require a safe lowercase identifier.")
        elif item_id in seen:
            _issue(issues, field, "duplicate_id", f"{field} identifiers must be unique.")
        else:
            seen.add(item_id)
        if "version" in item and not is_version_constraint(item["version"]):
            _issue(issues, field, "invalid_version_constraint", f"{field} version constraints must use supported semantic comparisons.")
        if "optional" in item and not isinstance(item["optional"], bool):
            _issue(issues, field, "invalid_optional", f"{field} optional must be true or false.")


def _validate_entrypoints(value: Any, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, list) or not value:
        _issue(issues, "entrypoints", "invalid_type", "entrypoints must be a non-empty list of static descriptors.")
        return
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not _is_mapping(item):
            _issue(issues, "entrypoints", "invalid_item", "entrypoints entries must be objects.")
            continue
        if set(item) - {"kind", "path"}:
            _issue(issues, "entrypoints", "unknown_field", "entrypoints contains a field outside the v1 allowlist.")
        kind = item.get("kind")
        if kind not in ENTRYPOINT_ALLOWLIST:
            _issue(issues, "entrypoints.kind", "unsupported_entrypoint", "entrypoint kind is not allowlisted for static discovery.")
            continue
        path = item.get("path")
        if not _safe_descriptor_path(path, ENTRYPOINT_ALLOWLIST[kind]):
            _issue(issues, "entrypoints.path", "unsafe_descriptor_path", "entrypoint path must be a safe relative descriptor path with an allowlisted suffix.")
            continue
        key = (kind, path)
        if key in seen:
            _issue(issues, "entrypoints", "duplicate_entrypoint", "entrypoints must not repeat a kind and descriptor path.")
        seen.add(key)


def _validate_resource_profile(value: Any, issues: list[dict[str, str]]) -> None:
    if not _is_mapping(value):
        _issue(issues, "resource_profile", "invalid_type", "resource_profile must be a declarative object.")
        return
    allowed = {"cpu", "gpu", "vram_gb", "ram_gb", "disk_gb", "exclusive_resource_groups"}
    if set(value) - allowed:
        _issue(issues, "resource_profile", "unknown_field", "resource_profile contains a field outside the v1 allowlist.")

    cpu = value.get("cpu")
    if not _is_mapping(cpu):
        _issue(issues, "resource_profile.cpu", "invalid_type", "resource_profile.cpu must describe class and thread estimate.")
    else:
        if set(cpu) - {"class", "threads"}:
            _issue(issues, "resource_profile.cpu", "unknown_field", "resource_profile.cpu contains a field outside the v1 allowlist.")
        if cpu.get("class") not in CPU_CLASSES:
            _issue(issues, "resource_profile.cpu.class", "invalid_cpu_class", "resource_profile.cpu.class must be light, moderate, or heavy.")
        threads = cpu.get("threads")
        if not isinstance(threads, int) or isinstance(threads, bool) or not 1 <= threads <= 256:
            _issue(issues, "resource_profile.cpu.threads", "invalid_cpu_threads", "resource_profile.cpu.threads must be an integer from 1 to 256.")

    gpu = value.get("gpu")
    if not _is_mapping(gpu):
        _issue(issues, "resource_profile.gpu", "invalid_type", "resource_profile.gpu must describe GPU requirement and type.")
    else:
        if set(gpu) - {"required", "vendor", "device_class"}:
            _issue(issues, "resource_profile.gpu", "unknown_field", "resource_profile.gpu contains a field outside the v1 allowlist.")
        required = gpu.get("required")
        vendor = gpu.get("vendor")
        device_class = gpu.get("device_class")
        if not isinstance(required, bool):
            _issue(issues, "resource_profile.gpu.required", "invalid_gpu_requirement", "resource_profile.gpu.required must be true or false.")
        if vendor not in GPU_VENDORS:
            _issue(issues, "resource_profile.gpu.vendor", "invalid_gpu_vendor", "resource_profile.gpu.vendor is not supported.")
        if device_class not in GPU_DEVICE_CLASSES:
            _issue(issues, "resource_profile.gpu.device_class", "invalid_gpu_class", "resource_profile.gpu.device_class is not supported.")
        if required is True and vendor == "none":
            _issue(issues, "resource_profile.gpu", "inconsistent_gpu_requirement", "a required GPU cannot use vendor none.")
        if required is False and (vendor, device_class) != ("none", "none"):
            _issue(issues, "resource_profile.gpu", "inconsistent_gpu_requirement", "a non-required GPU must use vendor none and device class none.")

    for field in ("vram_gb", "ram_gb", "disk_gb"):
        resource = value.get(field)
        if not isinstance(resource, (int, float)) or isinstance(resource, bool) or not 0 <= float(resource) <= 100000:
            _issue(issues, f"resource_profile.{field}", "invalid_resource_estimate", f"resource_profile.{field} must be a non-negative numeric estimate.")
    if _is_mapping(gpu) and gpu.get("required") is False and isinstance(value.get("vram_gb"), (int, float)) and value.get("vram_gb", 0) != 0:
        _issue(issues, "resource_profile.vram_gb", "inconsistent_gpu_requirement", "vram_gb must be zero when no GPU is required.")

    groups = value.get("exclusive_resource_groups")
    if not isinstance(groups, list) or len(groups) != len(set(groups)) or len(groups) > 16:
        _issue(issues, "resource_profile.exclusive_resource_groups", "invalid_resource_groups", "exclusive_resource_groups must be a unique list with at most 16 entries.")
    elif any(not isinstance(group, str) or not RESOURCE_GROUP_PATTERN.fullmatch(group) for group in groups):
        _issue(issues, "resource_profile.exclusive_resource_groups", "invalid_resource_group", "exclusive_resource_groups contains an unsafe group identifier.")


def manifest_errors(value: Any) -> list[dict[str, str]]:
    """Return sanitized v1 schema violations without raising an exception."""

    issues: list[dict[str, str]] = []
    if not _is_mapping(value):
        _issue(issues, "manifest", "invalid_type", "extension manifest must be a JSON object.")
        return issues

    allowed = {
        "schema_version",
        "id",
        "version",
        "display_name",
        "description",
        "author",
        "license",
        "source",
        "capabilities",
        "compatibility",
        "required_components",
        "required_models",
        "permissions",
        "entrypoints",
        "resource_profile",
        "availability",
    }
    if set(value) - allowed:
        _issue(issues, "manifest", "unknown_field", "manifest contains a field outside the extension-manifest.v1 allowlist.")
    required = {
        "schema_version",
        "id",
        "version",
        "display_name",
        "author",
        "license",
        "source",
        "capabilities",
        "compatibility",
        "required_components",
        "required_models",
        "permissions",
        "entrypoints",
        "resource_profile",
        "availability",
    }
    if required - set(value):
        _issue(issues, "manifest", "missing_field", "manifest is missing one or more required extension-manifest.v1 fields.")

    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _issue(issues, "schema_version", "unsupported_schema", "schema_version must be extension-manifest.v1.")
    if not _valid_id(value.get("id")):
        _issue(issues, "id", "invalid_id", "id must be a lowercase hyphenated identifier from 3 to 64 characters.")
    if not is_semver(value.get("version")):
        _issue(issues, "version", "invalid_version", "version must be a semantic version.")
    if not _safe_text(value.get("display_name"), maximum=120):
        _issue(issues, "display_name", "invalid_value", "display_name must be concise, non-empty text.")
    if "description" in value and not _safe_text(value.get("description"), maximum=500):
        _issue(issues, "description", "invalid_value", "description must be concise text without control characters.")
    _validate_author(value.get("author"), issues)
    if not _safe_text(value.get("license"), maximum=160):
        _issue(issues, "license", "invalid_value", "license must be concise, non-empty text.")
    if not _safe_url(value.get("source")):
        _issue(issues, "source", "invalid_url", "source must be a public HTTP(S) URL without credentials or query data.")

    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or len(capabilities) != len(set(capabilities)):
        _issue(issues, "capabilities", "invalid_value", "capabilities must be a non-empty, unique allowlisted list.")
    elif any(capability not in CAPABILITY_ALLOWLIST for capability in capabilities):
        _issue(issues, "capabilities", "unsupported_capability", "capabilities contains a value outside the v1 allowlist.")

    _validate_compatibility(value.get("compatibility"), issues)
    _validate_requirements(value.get("required_components"), "required_components", issues)
    _validate_requirements(value.get("required_models"), "required_models", issues)

    permissions = value.get("permissions")
    if not isinstance(permissions, list) or len(permissions) != len(set(permissions)):
        _issue(issues, "permissions", "invalid_value", "permissions must be a unique list of allowlisted metadata permissions.")
    elif any(permission not in PERMISSION_ALLOWLIST for permission in permissions):
        _issue(issues, "permissions", "unsupported_permission", "permissions contains a value outside the v1 allowlist; code execution and shell access are never permitted.")

    _validate_entrypoints(value.get("entrypoints"), issues)
    _validate_resource_profile(value.get("resource_profile"), issues)
    _validate_availability(value.get("availability"), issues)
    return issues


def validate_extension_manifest(value: Any) -> dict[str, Any]:
    """Validate and return a detached manifest copy.

    Callers can safely retain the returned mapping because later changes to the
    source object do not affect it.  The function intentionally performs no
    filesystem, import, subprocess, or network operation.
    """

    issues = manifest_errors(value)
    if issues:
        raise ManifestValidationError(issues)
    return deepcopy(dict(value))


def extension_manifest_schema() -> dict[str, Any]:
    """Return a documentation-oriented JSON Schema representation of v1.

    Validation is implemented above so Local AI Hub has no runtime dependency on
    a JSON Schema package.  This document remains useful for authoring tools.
    """

    return deepcopy(EXTENSION_MANIFEST_V1_SCHEMA)


EXTENSION_MANIFEST_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://local-ai-hub.dev/schemas/extension-manifest.v1.json",
    "title": "Local AI Hub Extension Manifest v1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "id",
        "version",
        "display_name",
        "author",
        "license",
        "source",
        "capabilities",
        "compatibility",
        "required_components",
        "required_models",
        "permissions",
        "entrypoints",
        "resource_profile",
        "availability",
    ],
    "properties": {
        "schema_version": {"const": MANIFEST_SCHEMA_VERSION},
        "id": {"type": "string", "pattern": EXTENSION_ID_PATTERN.pattern},
        "version": {"type": "string", "pattern": SEMVER_PATTERN.pattern},
        "display_name": {"type": "string", "minLength": 1, "maxLength": 120},
        "description": {"type": "string", "maxLength": 500},
        "author": {"type": "object", "required": ["name"], "additionalProperties": False},
        "license": {"type": "string", "minLength": 1, "maxLength": 160},
        "source": {"type": "string", "format": "uri"},
        "capabilities": {"type": "array", "items": {"enum": sorted(CAPABILITY_ALLOWLIST)}, "uniqueItems": True},
        "permissions": {"type": "array", "items": {"enum": sorted(PERMISSION_ALLOWLIST)}, "uniqueItems": True},
        "availability": {"type": "object", "required": ["status", "reason", "action"], "additionalProperties": False},
    },
}

