"""Pure server-owned composition for the static capability gateway.

This module has no route and no default filesystem/runtime integration.  A
future server adapter injects a :class:`TrustedSectionLoaderRegistry`; tests
can inject deterministic in-memory loaders.  Request data can select an
identity, but it can never provide or replace a loader.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from src.shared.schemas.capability_gateway import (
    CAPABILITY_GATEWAY_SCHEMA_VERSION,
    EXECUTION_NOT_RUN,
    SECTION_ALLOWLIST,
    SECTION_SELECTOR_KEY,
    STATUS_ALLOWLIST,
    gateway_fingerprint,
    validate_gateway_request,
)

from .projection import action_text, project_section, reason_text


SectionLoader = Callable[[tuple[str, ...], bool], object]
_STATUS_RANK = {"operational": 0, "planned": 1, "partial": 2, "unavailable": 3}


class TrustedSectionLoaderRegistry:
    """An immutable-by-convention allowlisted registry of server loaders.

    The registry is injected by a trusted server owner.  It accepts no unknown
    section names and copies the mapping, while request values remain limited
    to the selector tuple and the plan boolean passed to a loader.
    """

    def __init__(self, loaders: Mapping[str, SectionLoader]) -> None:
        if not isinstance(loaders, Mapping):
            raise TypeError("loaders must be a mapping owned by the server")
        unknown = set(loaders) - set(SECTION_ALLOWLIST)
        if unknown or any(not isinstance(key, str) or not callable(loader) for key, loader in loaders.items()):
            raise ValueError("loader registry contains an unsupported section or non-callable loader")
        self._loaders = dict(loaders)

    @property
    def sections(self) -> tuple[str, ...]:
        return tuple(sorted(self._loaders))

    def has(self, section: str) -> bool:
        return section in self._loaders

    def load(self, section: str, selectors: tuple[str, ...], include_plans: bool) -> object:
        loader = self._loaders.get(section)
        if loader is None:
            return None
        # The only arguments are detached, bounded request primitives.  A
        # loader cannot receive a client report, path, mapping or runtime hook.
        return loader(tuple(selectors), bool(include_plans))


class CapabilityGateway:
    """Convenience object around one trusted registry."""

    def __init__(self, loaders: TrustedSectionLoaderRegistry) -> None:
        if not isinstance(loaders, TrustedSectionLoaderRegistry):
            raise TypeError("CapabilityGateway requires TrustedSectionLoaderRegistry")
        self._loaders = loaders

    def build(self, request: object) -> dict[str, Any]:
        return build_capability_gateway(request, loaders=self._loaders)


def _safe_error_codes(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return [{"code": "invalid_request"}]
    codes = sorted({item.get("code", "invalid_request") for item in value if isinstance(item, dict) and isinstance(item.get("code"), str)})
    return [{"code": code} for code in codes[:16]] or [{"code": "invalid_request"}]


def _envelope_without_fingerprint(
    *,
    status: str,
    reason_code: str,
    action_code: str,
    dry_run: bool,
    sections: dict[str, dict[str, Any]],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": CAPABILITY_GATEWAY_SCHEMA_VERSION,
        "status": status,
        "reason_code": reason_code,
        "reason": reason_text(reason_code),
        "action_code": action_code,
        "action": action_text(action_code),
        "execution": EXECUTION_NOT_RUN,
        "dry_run": bool(dry_run),
        "sections": {key: deepcopy(sections[key]) for key in sorted(sections)},
        "counts": {
            status_name: sum(1 for item in sections.values() if item.get("status") == status_name)
            for status_name in ("operational", "partial", "planned", "unavailable")
        },
        "errors": deepcopy(errors[:16]),
    }


def _with_fingerprint(envelope: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(envelope)
    result["fingerprint"] = {"algorithm": "sha256", "value": gateway_fingerprint(envelope)}
    return result


def _invalid_request(validation: dict[str, Any]) -> dict[str, Any]:
    errors = _safe_error_codes(validation.get("errors"))
    envelope = _envelope_without_fingerprint(
        status="unavailable",
        reason_code="section_unavailable",
        action_code="fix_managed_descriptor",
        dry_run=False,
        sections={},
        errors=errors,
    )
    # Invalid request details are fixed issue codes only; the caller's raw
    # mapping is deliberately not included in this response.
    return _with_fingerprint(envelope)


def _aggregate_status(sections: Mapping[str, Mapping[str, Any]]) -> tuple[str, str, str]:
    statuses = [item.get("status", "unavailable") for item in sections.values()]
    if not statuses:
        return "unavailable", "section_unavailable", "fix_managed_descriptor"
    status = max(statuses, key=lambda item: _STATUS_RANK.get(item, _STATUS_RANK["unavailable"]))
    if status == "unavailable":
        return status, "section_unavailable", "restore_managed_source"
    if status == "partial":
        return status, "static_metadata_only", "review_runtime_evidence"
    if status == "planned":
        return status, "runtime_not_run", "review_runtime_evidence"
    return "operational", "static_summary_ready", "review_runtime_evidence"


def build_capability_gateway(
    request: object,
    loaders: TrustedSectionLoaderRegistry | None = None,
    *,
    loader_registry: TrustedSectionLoaderRegistry | None = None,
) -> dict[str, Any]:
    """Build a detached static gateway response from trusted section loaders."""

    if loaders is not None and loader_registry is not None and loaders is not loader_registry:
        return _invalid_request({"errors": [{"code": "duplicate_loader_registry"}]})
    registry = loader_registry or loaders
    validation = validate_gateway_request(request)
    if not validation.get("valid"):
        return _invalid_request(validation)
    if not isinstance(registry, TrustedSectionLoaderRegistry):
        return _invalid_request({"errors": [{"code": "trusted_loader_registry_required"}]})
    normalized = validation["request"]
    assert isinstance(normalized, dict)
    sections: dict[str, dict[str, Any]] = {}
    section_errors: list[dict[str, str]] = []
    for section in normalized["sections"]:
        selector_key = SECTION_SELECTOR_KEY[section]
        selectors = tuple(normalized["selectors"].get(selector_key, []))
        if not registry.has(section):
            projected = project_section(section, None, selectors=selectors, include_plans=normalized["include_plans"])
            projected["reason_code"] = "loader_unavailable"
            projected["action_code"] = "restore_managed_source"
            projected["fingerprint"] = gateway_fingerprint({"section": section, **{key: value for key, value in projected.items() if key != "fingerprint"}})
            section_errors.append({"code": "loader_unavailable"})
            sections[section] = projected
            continue
        try:
            loaded = registry.load(section, selectors, normalized["include_plans"])
        except Exception:
            loaded = None
        projected = project_section(section, loaded, selectors=selectors, include_plans=normalized["include_plans"])
        if projected.get("status") == "unavailable":
            section_errors.append({"code": projected.get("reason_code", "section_unavailable")})
        sections[section] = projected
    status, reason_code, action_code = _aggregate_status(sections)
    envelope = _envelope_without_fingerprint(
        status=status,
        reason_code=reason_code,
        action_code=action_code,
        dry_run=bool(normalized["include_plans"] or any(item.get("dry_run") for item in sections.values())),
        sections=sections,
        errors=section_errors,
    )
    return deepcopy(_with_fingerprint(envelope))


def build_gateway_snapshot(
    request: object,
    loaders: TrustedSectionLoaderRegistry | None = None,
    *,
    loader_registry: TrustedSectionLoaderRegistry | None = None,
) -> dict[str, Any]:
    """Named snapshot alias used by future server-owned adapters."""

    return build_capability_gateway(request, loaders=loaders, loader_registry=loader_registry)


__all__ = [
    "SectionLoader",
    "TrustedSectionLoaderRegistry",
    "CapabilityGateway",
    "build_capability_gateway",
    "build_gateway_snapshot",
]
