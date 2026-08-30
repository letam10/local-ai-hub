"""Capability Graph V2 with bounded, path-free public projections.

The existing V5 capability registry remains a compatibility projection.  This
module adds a second, explicit graph rather than overloading ``partial`` or a
single ``healthy`` flag with install, runtime, verification and operational
meaning.  All inputs are server-owned snapshots.  Building or reading the
graph is pure metadata work and never starts a process, scans a tree, imports
a provider, allocates a model, or mutates persistent state.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


CAPABILITY_GRAPH_SCHEMA_VERSION = "capability-graph.v2"
EXECUTION_NOT_RUN = "not_run"
MAX_CAPABILITIES = 512
MAX_DEPENDENCIES = 64
MAX_BLOCKERS = 64
MAX_TREE_DEPTH = 12
MAX_TEXT = 320
MAX_EVIDENCE_FRESHNESS_SECONDS = 7 * 24 * 60 * 60
CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9._:-]{1,127}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,119}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UNSAFE_TEXT_RE = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])"
)

# These are intentionally independent dimensions.  In particular,
# ``INSTALLED_UNVERIFIED`` does not imply ``STARTABLE`` or ``OPERATIONAL``.
INSTALL_STATES = frozenset({
    "DISCOVERED", "REGISTERED", "INSTALLED", "INSTALLED_UNVERIFIED", "UNAVAILABLE", "BROKEN",
})
RUNTIME_STATES = frozenset({
    "NOT_REQUESTED", "NOT_STARTED", "STARTABLE", "RUNNING", "UNAVAILABLE", "BROKEN",
})
VERIFICATION_STATES = frozenset({
    "NOT_VERIFIED", "METADATA", "FILESYSTEM", "RUNTIME_IMPORT", "PROCESS_HEALTH", "BOUNDED_SMOKE", "PRODUCTION", "FAILED",
})
OPERATIONAL_STATES = (
    "DISCOVERED", "REGISTERED", "INSTALLED", "INSTALLED_UNVERIFIED", "VERIFIED", "STARTABLE", "RUNNING", "OPERATIONAL", "DEGRADED", "UNAVAILABLE", "BROKEN",
)
OPERATIONAL_STATE_SET = frozenset(OPERATIONAL_STATES)
EVIDENCE_STATES = frozenset({"not_run", "static", "completed", "failed", "unavailable"})
SAFE_ACTION_IDS = frozenset({
    "inspect",
    "review_dependency",
    "plan_install",
    "plan_import",
    "verify_filesystem",
    "verify_runtime",
    "request_bounded_smoke",
    "review_evidence",
    "repair",
    "review_source",
    "review_license",
})

_DESCRIPTOR_KEYS = frozenset({
    "capability_id",
    "provider",
    "component",
    "runtime",
    "model",
    "dependencies",
    "version",
    "install_state",
    "runtime_state",
    "verification_state",
    "operational_state",
    "last_verified",
    "evidence",
    "reason",
    "next_action",
    "safe_actions",
})
_REQUIRED_DESCRIPTOR_KEYS = _DESCRIPTOR_KEYS - {"model", "last_verified"}


class CapabilityGraphError(ValueError):
    """Raised when server-owned graph data violates the closed contract."""


def _safe_id(value: object, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not CAPABILITY_ID_RE.fullmatch(value) or ".." in value:
        return None
    return value


def _safe_version(value: object) -> str:
    return value if isinstance(value, str) and VERSION_RE.fullmatch(value) else "unknown"


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if not 1 <= len(candidate) <= MAX_TEXT or any(ord(char) < 32 for char in candidate):
        return fallback
    if _UNSAFE_TEXT_RE.search(candidate):
        return fallback
    return candidate


def _safe_timestamp(value: object) -> str | None:
    if value is None:
        return None
    candidate = _safe_text(value, "")
    return candidate or None


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CapabilityGraphError("non_json_value") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _evidence(value: object, *, verification_state: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) - {"state", "tier", "fingerprint", "observed_at", "source_revision", "source_fingerprint", "freshness_seconds"}:
        raise CapabilityGraphError("invalid_evidence")
    state = value.get("state", "not_run")
    tier = value.get("tier", verification_state)
    fingerprint = value.get("fingerprint")
    observed_at = _safe_timestamp(value.get("observed_at"))
    source_revision = _safe_text(value.get("source_revision"), "",) or None
    source_fingerprint = value.get("source_fingerprint")
    freshness_seconds = value.get("freshness_seconds")
    if state not in EVIDENCE_STATES or tier not in VERIFICATION_STATES:
        raise CapabilityGraphError("invalid_evidence")
    if fingerprint is not None and (not isinstance(fingerprint, str) or SHA256_RE.fullmatch(fingerprint) is None):
        raise CapabilityGraphError("invalid_evidence")
    if source_fingerprint is not None and (not isinstance(source_fingerprint, str) or SHA256_RE.fullmatch(source_fingerprint) is None):
        raise CapabilityGraphError("invalid_evidence")
    if freshness_seconds is not None and (not isinstance(freshness_seconds, int) or isinstance(freshness_seconds, bool) or not 1 <= freshness_seconds <= MAX_EVIDENCE_FRESHNESS_SECONDS):
        raise CapabilityGraphError("invalid_evidence")
    freshness = "not_observed"
    if observed_at is not None and freshness_seconds is not None:
        try:
            parsed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone_required")
            freshness = "current" if (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() <= freshness_seconds else "stale"
        except (TypeError, ValueError, OverflowError):
            freshness = "stale"
    elif observed_at is not None:
        freshness = "unknown"
    return {
        "state": state,
        "tier": tier,
        "fingerprint": fingerprint,
        "observed_at": observed_at,
        "source_revision": source_revision,
        "source_fingerprint": source_fingerprint,
        "freshness_seconds": freshness_seconds,
        "freshness": freshness,
    }


def validate_capability_descriptor(value: object) -> dict[str, Any]:
    """Validate and detach one server-owned Capability Graph descriptor."""

    if not isinstance(value, Mapping) or not _REQUIRED_DESCRIPTOR_KEYS.issubset(value) or set(value) - _DESCRIPTOR_KEYS:
        raise CapabilityGraphError("invalid_capability_descriptor")
    capability_id = _safe_id(value.get("capability_id"))
    provider = _safe_id(value.get("provider"))
    component = _safe_id(value.get("component"), allow_none=True)
    runtime = _safe_id(value.get("runtime"), allow_none=True)
    model = _safe_id(value.get("model"), allow_none=True)
    if (
        capability_id is None
        or provider is None
        or (component is None and value.get("component") is not None)
        or (runtime is None and value.get("runtime") is not None)
        or (model is None and value.get("model") is not None)
    ):
        raise CapabilityGraphError("invalid_capability_identity")

    dependencies = value.get("dependencies")
    if not isinstance(dependencies, list) or len(dependencies) > MAX_DEPENDENCIES:
        raise CapabilityGraphError("invalid_capability_dependencies")
    normalized_dependencies = [_safe_id(item) for item in dependencies]
    if any(item is None for item in normalized_dependencies) or len(set(normalized_dependencies)) != len(normalized_dependencies):
        raise CapabilityGraphError("invalid_capability_dependencies")
    dependencies = [str(item) for item in normalized_dependencies]

    install_state = value.get("install_state")
    runtime_state = value.get("runtime_state")
    verification_state = value.get("verification_state")
    operational_state = value.get("operational_state")
    if install_state not in INSTALL_STATES or runtime_state not in RUNTIME_STATES or verification_state not in VERIFICATION_STATES or operational_state not in OPERATIONAL_STATE_SET:
        raise CapabilityGraphError("invalid_capability_state")
    evidence = _evidence(value.get("evidence"), verification_state=str(verification_state))
    if evidence["tier"] != verification_state:
        raise CapabilityGraphError("verification_evidence_tier_mismatch")
    last_verified = _safe_timestamp(value.get("last_verified"))
    if operational_state == "OPERATIONAL":
        if verification_state not in {"BOUNDED_SMOKE", "PRODUCTION"} or evidence["state"] != "completed" or evidence["fingerprint"] is None:
            raise CapabilityGraphError("operational_evidence_required")
        if runtime_state not in {"STARTABLE", "RUNNING"}:
            raise CapabilityGraphError("operational_runtime_required")
        if evidence["freshness"] != "current" or evidence["source_revision"] is None or evidence["source_fingerprint"] is None:
            # The receipt is still useful as a verification signal, but it
            # cannot continue to imply *current* operational readiness.
            operational_state = "VERIFIED"
    if install_state in {"UNAVAILABLE", "BROKEN"} and operational_state == "OPERATIONAL":
        raise CapabilityGraphError("operational_install_state_conflict")

    safe_actions = value.get("safe_actions")
    if not isinstance(safe_actions, list) or not safe_actions or len(safe_actions) > len(SAFE_ACTION_IDS):
        raise CapabilityGraphError("invalid_safe_actions")
    if any(not isinstance(item, str) for item in safe_actions):
        raise CapabilityGraphError("invalid_safe_actions")
    normalized_actions = sorted(set(safe_actions))
    if len(normalized_actions) != len(safe_actions) or any(item not in SAFE_ACTION_IDS for item in normalized_actions):
        raise CapabilityGraphError("invalid_safe_actions")
    return {
        "capability_id": capability_id,
        "provider": provider,
        "component": component,
        "runtime": runtime,
        "model": model,
        "dependencies": dependencies,
        "version": _safe_version(value.get("version")),
        "install_state": install_state,
        "runtime_state": runtime_state,
        "verification_state": verification_state,
        "operational_state": operational_state,
        "last_verified": last_verified,
        "evidence": evidence,
        "reason": _safe_text(value.get("reason"), "Capability evidence is not available."),
        "next_action": _safe_text(value.get("next_action"), "Inspect the server-owned capability record."),
        "safe_actions": normalized_actions,
    }


def _state_rank(state: str) -> int:
    return {
        "BROKEN": 100,
        "UNAVAILABLE": 90,
        "DEGRADED": 80,
        "DISCOVERED": 70,
        "REGISTERED": 60,
        "INSTALLED": 50,
        "INSTALLED_UNVERIFIED": 40,
        "VERIFIED": 30,
        "STARTABLE": 20,
        "RUNNING": 10,
        "OPERATIONAL": 0,
    }.get(state, 100)


def _blocker_code(state: str) -> str:
    return {
        "BROKEN": "dependency_broken",
        "UNAVAILABLE": "dependency_unavailable",
        "DEGRADED": "dependency_degraded",
    }.get(state, "dependency_not_operational")


class CapabilityGraph:
    """Read-only, deterministic graph with cycle-safe relationship views."""

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        normalized: dict[str, dict[str, Any]] = {}
        for raw in records:
            record = validate_capability_descriptor(raw)
            capability_id = str(record["capability_id"])
            if capability_id in normalized:
                raise CapabilityGraphError("duplicate_capability_id")
            normalized[capability_id] = record
            if len(normalized) > MAX_CAPABILITIES:
                raise CapabilityGraphError("capability_limit")
        self._records = {key: deepcopy(normalized[key]) for key in sorted(normalized)}

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(self._records)

    def _record(self, capability_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(capability_id)
        if identifier is None:
            return None
        record = self._records.get(identifier)
        return deepcopy(record) if record is not None else None

    def _walk_blockers(self, capability_id: str, *, path: tuple[str, ...] = (), collected: dict[tuple[str, str], dict[str, str]] | None = None) -> dict[tuple[str, str], dict[str, str]]:
        items = collected if collected is not None else {}
        if capability_id in path:
            items.setdefault(("dependency_cycle", capability_id), {
                "code": "dependency_cycle",
                "capability_id": capability_id,
                "operational_state": "BROKEN",
                "reason": "A capability dependency cycle prevents a safe readiness claim.",
                "next_action": "Correct the server-owned dependency graph before requesting execution.",
            })
            return items
        record = self._records.get(capability_id)
        if record is None:
            items.setdefault(("dependency_missing", capability_id), {
                "code": "dependency_missing",
                "capability_id": capability_id,
                "operational_state": "UNAVAILABLE",
                "reason": "The required dependency is not registered in the capability graph.",
                "next_action": "Register or restore the exact dependency before requesting execution.",
            })
            return items
        if record["operational_state"] != "OPERATIONAL":
            code = _blocker_code(str(record["operational_state"]))
            items.setdefault((code, capability_id), {
                "code": code,
                "capability_id": capability_id,
                "operational_state": str(record["operational_state"]),
                "reason": str(record["reason"]),
                "next_action": str(record["next_action"]),
            })
        next_path = (*path, capability_id)
        for dependency in record["dependencies"]:
            self._walk_blockers(str(dependency), path=next_path, collected=items)
        return items

    def blockers(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        identifier = str(record["capability_id"])
        blockers = list(self._walk_blockers(identifier).values())
        blockers.sort(key=lambda item: (item["code"], item["capability_id"]))
        return {
            "schema_version": CAPABILITY_GRAPH_SCHEMA_VERSION,
            "capability_id": identifier,
            "blockers": blockers[:MAX_BLOCKERS],
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def _tree(self, capability_id: str, *, path: tuple[str, ...] = (), depth: int = 0) -> dict[str, Any]:
        if capability_id in path:
            return {
                "capability_id": capability_id,
                "node_state": "CYCLE",
                "operational_state": "BROKEN",
                "reason": "A capability dependency cycle was detected.",
                "dependencies": [],
            }
        record = self._records.get(capability_id)
        if record is None:
            return {
                "capability_id": capability_id,
                "node_state": "MISSING",
                "operational_state": "UNAVAILABLE",
                "reason": "The required dependency is not registered in the capability graph.",
                "dependencies": [],
            }
        if depth >= MAX_TREE_DEPTH:
            return {
                "capability_id": capability_id,
                "node_state": "DEPTH_LIMIT",
                "operational_state": str(record["operational_state"]),
                "reason": "Dependency traversal reached its bounded depth limit.",
                "dependencies": [],
            }
        return {
            "capability_id": capability_id,
            "node_state": "PRESENT",
            "operational_state": str(record["operational_state"]),
            "reason": str(record["reason"]),
            "dependencies": [self._tree(str(item), path=(*path, capability_id), depth=depth + 1) for item in record["dependencies"]],
        }

    def dependency_tree(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        return {
            "schema_version": CAPABILITY_GRAPH_SCHEMA_VERSION,
            "capability_id": str(record["capability_id"]),
            "tree": self._tree(str(record["capability_id"])),
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def safe_actions(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        return {
            "schema_version": CAPABILITY_GRAPH_SCHEMA_VERSION,
            "capability_id": str(record["capability_id"]),
            "safe_actions": list(record["safe_actions"]),
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def verification_evidence(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        return {
            "schema_version": CAPABILITY_GRAPH_SCHEMA_VERSION,
            "capability_id": str(record["capability_id"]),
            "verification_state": str(record["verification_state"]),
            "last_verified": record["last_verified"],
            "evidence": deepcopy(record["evidence"]),
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def capability(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        blocker_view = self.blockers(record["capability_id"])
        record["blockers"] = blocker_view["blockers"] if blocker_view else []
        record["execution"] = EXECUTION_NOT_RUN
        record["dry_run"] = True
        return record

    def snapshot(self) -> dict[str, Any]:
        capabilities = [self.capability(capability_id) for capability_id in self.capability_ids]
        public_capabilities = [item for item in capabilities if item is not None]
        counts = {state: sum(1 for item in public_capabilities if item["operational_state"] == state) for state in OPERATIONAL_STATES}
        overall = max((str(item["operational_state"]) for item in public_capabilities), key=_state_rank, default="UNAVAILABLE")
        result: dict[str, Any] = {
            "schema_version": CAPABILITY_GRAPH_SCHEMA_VERSION,
            "status": overall,
            "capabilities": public_capabilities,
            "counts": counts,
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
            "reason": "Capability Graph V2 composes server-owned metadata and bounded evidence; it does not execute components.",
            "next_action": "Inspect exact blockers and request only the separately authorized verification or lifecycle action.",
        }
        result["fingerprint"] = {"algorithm": "sha256", "value": _fingerprint(result)}
        return result


def _component_state(status: object, *, operational_evidence: bool = False) -> tuple[str, str, str, str]:
    normalized = str(status or "").strip().lower()
    if operational_evidence:
        return "INSTALLED", "STARTABLE", "BOUNDED_SMOKE", "OPERATIONAL"
    if normalized == "running":
        return "INSTALLED_UNVERIFIED", "RUNNING", "PROCESS_HEALTH", "RUNNING"
    if normalized in {"installed", "available"}:
        return "INSTALLED_UNVERIFIED", "STARTABLE", "FILESYSTEM", "INSTALLED_UNVERIFIED"
    if normalized == "operational":
        # Historical static projections may use this label without a current
        # bounded verification receipt.  Preserve the installation signal, but
        # never promote it to an operational claim merely by copying the name.
        return "INSTALLED_UNVERIFIED", "STARTABLE", "FILESYSTEM", "VERIFIED"
    if normalized in {"partial", "queue_only"}:
        return "DISCOVERED", "UNAVAILABLE", "NOT_VERIFIED", "DEGRADED"
    if normalized in {"planned", "not_run"}:
        return "REGISTERED", "NOT_STARTED", "NOT_VERIFIED", "REGISTERED"
    if normalized in {"broken", "error", "failed"}:
        return "DISCOVERED", "BROKEN", "FAILED", "BROKEN"
    if normalized in {"missing", "not_installed", "unavailable", "unknown", "external_managed"}:
        return "DISCOVERED", "UNAVAILABLE", "NOT_VERIFIED", "UNAVAILABLE"
    return "DISCOVERED", "NOT_REQUESTED", "NOT_VERIFIED", "DISCOVERED"


def _actions_for_state(state: str, *, disposition: object = None) -> list[str]:
    if state == "OPERATIONAL":
        return ["inspect", "review_evidence"]
    if state == "BROKEN":
        return ["inspect", "review_dependency", "repair"]
    if state == "UNAVAILABLE":
        if disposition == "MANUAL_IMPORT_ONLY":
            return ["inspect", "review_dependency", "plan_import"]
        if disposition == "LICENSE_REQUIRED":
            return ["inspect", "review_license", "review_source"]
        return ["inspect", "review_dependency", "plan_install"]
    if state == "DEGRADED":
        return ["inspect", "review_dependency", "verify_runtime", "request_bounded_smoke"]
    if state in {"INSTALLED", "INSTALLED_UNVERIFIED", "VERIFIED", "STARTABLE", "RUNNING"}:
        return ["inspect", "verify_runtime", "request_bounded_smoke"]
    return ["inspect", "review_dependency"]


def _descriptor(
    *,
    capability_id: str,
    provider: str,
    component: str | None,
    runtime: str | None,
    model: str | None,
    dependencies: list[str],
    version: object,
    states: tuple[str, str, str, str],
    reason: str,
    next_action: str,
    fingerprint: str | None = None,
    evidence_completed: bool = False,
    observed_at: str | None = None,
    source_revision: str | None = None,
    source_fingerprint: str | None = None,
    freshness_seconds: int | None = None,
    disposition: object = None,
) -> dict[str, Any]:
    install_state, runtime_state, verification_state, operational_state = states
    evidence = {
        "state": "completed" if evidence_completed else "static",
        "tier": verification_state,
        "fingerprint": fingerprint if evidence_completed else None,
        "observed_at": observed_at if evidence_completed else None,
        "source_revision": source_revision if evidence_completed else None,
        "source_fingerprint": source_fingerprint if evidence_completed else None,
        "freshness_seconds": freshness_seconds if evidence_completed else None,
    }
    return {
        "capability_id": capability_id,
        "provider": provider,
        "component": component,
        "runtime": runtime,
        "model": model,
        "dependencies": dependencies,
        "version": _safe_version(version),
        "install_state": install_state,
        "runtime_state": runtime_state,
        "verification_state": verification_state,
        "operational_state": operational_state,
        "last_verified": observed_at if evidence_completed else None,
        "evidence": evidence,
        "reason": reason,
        "next_action": next_action,
        "safe_actions": _actions_for_state(operational_state, disposition=disposition),
    }


def _catalog_state(item: Mapping[str, Any]) -> tuple[str, str, str, str]:
    status = str(item.get("status") or "").upper()
    if status == "OPERATIONAL" and item.get("operational") is True:
        # A V7 catalog only exposes this when its separate evidence contract
        # has accepted it.  The graph still requires a bounded fingerprint,
        # so an incomplete record is downgraded below.
        return "INSTALLED", "STARTABLE", "BOUNDED_SMOKE", "OPERATIONAL"
    if status in {"INSTALLED", "INSTALLED_UNVERIFIED"}:
        return "INSTALLED_UNVERIFIED", "STARTABLE", "FILESYSTEM", "INSTALLED_UNVERIFIED"
    if status in {"PARTIAL", "UNKNOWN", "NOT_PUBLISHED"}:
        return "DISCOVERED", "UNAVAILABLE", "NOT_VERIFIED", "DEGRADED"
    if status in {"UNAVAILABLE", "NOT_INSTALLED", "MISSING"}:
        return "DISCOVERED", "UNAVAILABLE", "NOT_VERIFIED", "UNAVAILABLE"
    if status in {"BROKEN", "ERROR"}:
        return "DISCOVERED", "BROKEN", "FAILED", "BROKEN"
    return "REGISTERED", "NOT_STARTED", "METADATA", "REGISTERED"


def _safe_snapshot_items(value: object, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    items = value.get(field)
    return [dict(item) for item in items[:MAX_CAPABILITIES] if isinstance(item, Mapping)] if isinstance(items, list) else []


def _registered_requirement(
    *,
    capability_id: str,
    provider: str,
    runtime: str | None,
    reason: str,
    next_action: str,
) -> dict[str, Any]:
    """Create a non-operational engine/resource dependency node.

    These nodes describe a catalog-declared requirement, not a machine claim.
    A later scheduler or lifecycle phase may add server-owned observation
    evidence, but Phase 1 keeps the absence of that evidence visible.
    """

    return _descriptor(
        capability_id=capability_id,
        provider=provider,
        component=None,
        runtime=runtime,
        model=None,
        dependencies=[],
        version="catalog",
        states=("REGISTERED", "NOT_REQUESTED", "METADATA", "REGISTERED"),
        reason=reason,
        next_action=next_action,
    )


def _bridge_evidence(value: object) -> dict[str, object] | None:
    """Normalize a bounded server-owned engine/resource observation only."""

    if not isinstance(value, Mapping):
        return None
    status = str(value.get("status") or "").casefold()
    fingerprint = value.get("fingerprint") or value.get("source_fingerprint")
    observed_at = _safe_timestamp(value.get("observed_at"))
    source_revision = _safe_text(value.get("source_revision"), "") or None
    source_fingerprint = value.get("source_fingerprint")
    freshness = value.get("freshness_seconds", 300)
    if (
        status not in {"available", "verified", "healthy", "startable"}
        or not isinstance(fingerprint, str) or SHA256_RE.fullmatch(fingerprint) is None
        or observed_at is None
        or not isinstance(freshness, int) or isinstance(freshness, bool) or not 1 <= freshness <= MAX_EVIDENCE_FRESHNESS_SECONDS
        or (source_fingerprint is not None and (not isinstance(source_fingerprint, str) or SHA256_RE.fullmatch(source_fingerprint) is None))
    ):
        return None
    return {
        "fingerprint": fingerprint,
        "observed_at": observed_at,
        "source_revision": source_revision,
        "source_fingerprint": source_fingerprint or fingerprint,
        "freshness_seconds": freshness,
    }


def _bridge_requirement(
    *,
    capability_id: str,
    provider: str,
    runtime: str | None,
    evidence: object,
    reason: str,
    next_action: str,
) -> dict[str, Any]:
    bridge = _bridge_evidence(evidence)
    if bridge is None:
        return _registered_requirement(
            capability_id=capability_id,
            provider=provider,
            runtime=runtime,
            reason=reason,
            next_action=next_action,
        )
    return _descriptor(
        capability_id=capability_id,
        provider=provider,
        component=None,
        runtime=runtime,
        model=None,
        dependencies=[],
        version="server-observation",
        states=("INSTALLED", "STARTABLE", "RUNTIME_IMPORT", "VERIFIED"),
        reason="A bounded server-owned observation is current for this dependency; no provider/model work was executed.",
        next_action="Use the dependent capability's own preflight before any separately authorized workload.",
        fingerprint=str(bridge["fingerprint"]),
        evidence_completed=True,
        observed_at=str(bridge["observed_at"]),
        source_revision=bridge["source_revision"] if isinstance(bridge["source_revision"], str) else None,
        source_fingerprint=str(bridge["source_fingerprint"]),
        freshness_seconds=int(bridge["freshness_seconds"]),
    )


def _minimum_vram_requirement(item: Mapping[str, Any]) -> bool:
    value = item.get("minimum_vram_mb")
    # Treat the catalog value solely as a declared requirement.  This function
    # never looks at a GPU, so it cannot turn the requirement into a fit claim.
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 1_048_576


def build_component_capability_graph(
    *,
    component_statuses: Iterable[Mapping[str, Any]],
    tool_records: Iterable[Mapping[str, Any]],
    catalog_snapshot: Mapping[str, Any] | None = None,
    engine_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    resource_evidence: Mapping[str, Any] | None = None,
) -> CapabilityGraph:
    """Compose the graph from already-owned, bounded server projections.

    The composition deliberately does not look at Config paths, model bytes,
    subprocesses, or provider state.  Component/runtime/model relations are
    derived only from IDs and module associations already published by the V7
    catalog, while missing dependencies stay explicit graph blockers.
    """

    catalog = catalog_snapshot if isinstance(catalog_snapshot, Mapping) else {}
    models = _safe_snapshot_items(catalog, "models")
    runtimes = _safe_snapshot_items(catalog, "runtimes")
    runtime_by_module: dict[str, str] = {}
    models_by_module: dict[str, list[str]] = {}
    models_by_runtime: dict[str, list[str]] = {}
    resource_dependencies_by_model: dict[str, list[str]] = {}
    records: dict[str, dict[str, Any]] = {}
    engines = dict(engine_evidence) if isinstance(engine_evidence, Mapping) else {}
    resources = dict(resource_evidence) if isinstance(resource_evidence, Mapping) else {}

    for item in runtimes:
        runtime_id = _safe_id(item.get("runtime_id"))
        if runtime_id is None:
            continue
        capability_id = f"runtime:{runtime_id}"
        engine_id = _safe_id(item.get("kind")) or "runtime"
        engine_capability = f"engine:{engine_id}"
        records.setdefault(engine_capability, _bridge_requirement(
            capability_id=engine_capability,
            provider="catalog",
            runtime=engine_id,
            evidence=engines.get(engine_id),
            reason="This engine type is registered by the server-owned runtime catalog but has no current process or import verification.",
            next_action="Verify the exact runtime engine before requesting a component lifecycle action.",
        ))
        states = _catalog_state(item)
        evidence_completed = states[3] == "OPERATIONAL" and isinstance(item.get("runtime_fingerprint"), str)
        if states[3] == "OPERATIONAL" and not evidence_completed:
            states = ("INSTALLED_UNVERIFIED", "STARTABLE", "FILESYSTEM", "INSTALLED_UNVERIFIED")
        records[capability_id] = _descriptor(
            capability_id=capability_id,
            provider=_safe_id(item.get("provider")) or "catalog",
            component=None,
            runtime=runtime_id,
            model=None,
            dependencies=[engine_capability],
            version=item.get("version"),
            states=states,
            reason="Runtime state is derived from the server-owned catalog and bounded leaf observation.",
            next_action="Review the runtime record and verify it explicitly before any execution claim.",
            fingerprint=item.get("runtime_fingerprint") if evidence_completed else None,
            evidence_completed=evidence_completed,
            disposition=item.get("disposition"),
        )
        modules = item.get("modules")
        if isinstance(modules, list):
            for module in modules:
                module_id = _safe_id(module)
                if module_id is not None:
                    runtime_by_module.setdefault(module_id, runtime_id)

    for item in models:
        model_id = _safe_id(item.get("model_id"))
        if model_id is None:
            continue
        runtime_id = _safe_id(item.get("runtime_id"), allow_none=True)
        runtime_capability = f"runtime:{runtime_id}" if runtime_id else None
        capability_id = f"model:{model_id}"
        resource_dependencies = ["resource:gpu"] if _minimum_vram_requirement(item) else []
        if resource_dependencies:
            records.setdefault("resource:gpu", _bridge_requirement(
                capability_id="resource:gpu",
                provider="hub",
                runtime=None,
                evidence=resources.get("gpu"),
                reason="A catalog-declared GPU requirement has no current scheduler reservation or hardware-fit evidence.",
                next_action="Run a separately authorized resource preflight before requesting a GPU workload.",
            ))
        resource_dependencies_by_model[model_id] = resource_dependencies
        states = _catalog_state(item)
        evidence_completed = states[3] == "OPERATIONAL" and isinstance(item.get("runtime_fingerprint"), str)
        if states[3] == "OPERATIONAL" and not evidence_completed:
            states = ("INSTALLED_UNVERIFIED", "STARTABLE", "FILESYSTEM", "INSTALLED_UNVERIFIED")
        modules = item.get("modules") if isinstance(item.get("modules"), list) else []
        first_component = next((_safe_id(value) for value in modules if _safe_id(value) is not None), None)
        records[capability_id] = _descriptor(
            capability_id=capability_id,
            provider=_safe_id(item.get("provider")) or "catalog",
            component=first_component,
            runtime=runtime_id,
            model=model_id,
            dependencies=[item for item in [runtime_capability, *resource_dependencies] if item],
            version=item.get("version"),
            states=states,
            reason="Model state is derived from the server-owned catalog and bounded leaf observation.",
            next_action="Review the exact runtime and model dependency before requesting verification or installation.",
            fingerprint=item.get("runtime_fingerprint") if evidence_completed else None,
            evidence_completed=evidence_completed,
            disposition=item.get("disposition"),
        )
        for module in modules:
            module_id = _safe_id(module)
            if module_id is not None:
                models_by_module.setdefault(module_id, []).append(model_id)
        if runtime_id is not None:
            models_by_runtime.setdefault(runtime_id, []).append(model_id)

    component_items = [dict(item) for item in component_statuses if isinstance(item, Mapping)]
    tool_items = [dict(item) for item in tool_records if isinstance(item, Mapping)]

    for item in component_items:
        component_id = _safe_id(item.get("id"))
        if component_id is None:
            continue
        component_capability = f"component:{component_id}"
        runtime_id = runtime_by_module.get(component_id)
        runtime_capability = f"runtime:{runtime_id}" if runtime_id else None
        model_ids = sorted(set(models_by_module.get(component_id, []) + (models_by_runtime.get(runtime_id, []) if runtime_id else [])))
        model_capabilities = [f"model:{model_id}" for model_id in model_ids]
        resource_capabilities = sorted({resource for model_id in model_ids for resource in resource_dependencies_by_model.get(model_id, [])})
        worker_capability = f"worker:{component_id}"
        smoke = item.get("last_smoke") if isinstance(item.get("last_smoke"), Mapping) else {}
        smoke_current = bool(
            smoke.get("status") == "completed"
            and smoke.get("execution") == "completed"
            and smoke.get("fresh") is True
            and smoke.get("runtime_fingerprint_match") is True
        )
        fingerprint = item.get("runtime_fingerprint") if isinstance(item.get("runtime_fingerprint"), str) and SHA256_RE.fullmatch(str(item.get("runtime_fingerprint"))) else None
        states = _component_state(item.get("component_status"), operational_evidence=smoke_current and fingerprint is not None)
        provider = _safe_id(item.get("adapter")) or "hub"
        worker_states = _component_state(item.get("component_status"), operational_evidence=smoke_current and fingerprint is not None)
        if not smoke_current and worker_states[3] not in {"UNAVAILABLE", "BROKEN"}:
            worker_states = (worker_states[0], worker_states[1], "NOT_VERIFIED", "DEGRADED")
        records[worker_capability] = _descriptor(
            capability_id=worker_capability,
            provider=provider,
            component=component_id,
            runtime=runtime_id,
            model=(model_ids[0] if model_ids else None),
            dependencies=resource_capabilities,
            version=item.get("version"),
            states=worker_states,
            reason="The worker is not backed by a current bounded verification receipt." if not smoke_current else "A current bounded smoke receipt matches the worker runtime identity.",
            next_action="Verify the worker with the exact server-owned capability before requesting a workload." if not smoke_current else "Review the current bounded evidence before use.",
            fingerprint=fingerprint,
            evidence_completed=smoke_current and fingerprint is not None,
        )
        dependencies = [worker_capability, *model_capabilities]
        if runtime_capability:
            dependencies.insert(0, runtime_capability)
        records[component_capability] = _descriptor(
            capability_id=component_capability,
            provider=provider,
            component=component_id,
            runtime=runtime_id,
            model=(model_ids[0] if model_ids else None),
            dependencies=dependencies,
            version=item.get("version"),
            states=states,
            reason="Component readiness is separated into installation, runtime, verification and operational states.",
            next_action="Inspect the listed dependencies and complete only the explicit safe action for the blocking layer.",
            fingerprint=fingerprint,
            evidence_completed=smoke_current and fingerprint is not None,
        )

    for item in tool_items:
        tool_id = _safe_id(item.get("name"))
        component_id = _safe_id(item.get("component"))
        if tool_id is None or component_id is None:
            continue
        component_capability = f"component:{component_id}"
        source_component = next((row for row in component_items if _safe_id(row.get("id")) == component_id), {})
        smoke = source_component.get("last_smoke") if isinstance(source_component.get("last_smoke"), Mapping) else {}
        smoke_current = bool(
            smoke.get("tool") == tool_id
            and smoke.get("status") == "completed"
            and smoke.get("execution") == "completed"
            and smoke.get("fresh") is True
            and smoke.get("runtime_fingerprint_match") is True
        )
        fingerprint = source_component.get("runtime_fingerprint") if isinstance(source_component.get("runtime_fingerprint"), str) and SHA256_RE.fullmatch(str(source_component.get("runtime_fingerprint"))) else None
        states = _component_state(item.get("tool_status"), operational_evidence=smoke_current and fingerprint is not None)
        records[f"tool:{tool_id}"] = _descriptor(
            capability_id=f"tool:{tool_id}",
            provider="hub",
            component=component_id,
            runtime=runtime_by_module.get(component_id),
            model=None,
            dependencies=[component_capability],
            version=source_component.get("version"),
            states=states,
            reason="This tool is gated by its exact component dependency and current bounded evidence.",
            next_action="Resolve the exact blocker shown below before requesting this tool.",
            fingerprint=fingerprint,
            evidence_completed=smoke_current and fingerprint is not None,
        )

    return CapabilityGraph(records.values())


def build_capability_graph(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build a public V2 snapshot from validated server-owned descriptors."""

    return CapabilityGraph(records).snapshot()


__all__ = [
    "CAPABILITY_GRAPH_SCHEMA_VERSION",
    "INSTALL_STATES",
    "RUNTIME_STATES",
    "VERIFICATION_STATES",
    "OPERATIONAL_STATES",
    "SAFE_ACTION_IDS",
    "CapabilityGraph",
    "CapabilityGraphError",
    "build_capability_graph",
    "build_component_capability_graph",
    "validate_capability_descriptor",
]
