"""Unified, server-owned Component Lifecycle Engine V2.

This is an additive facade over the durable V8 component-operation journal.
It owns one finite lifecycle vocabulary for all catalog-backed models and
runtimes, while retaining V8 as the only executor for operations it already
supports.  No lifecycle read starts a process, imports a provider, downloads
bytes, probes a GPU, or opens a user path.  Actions without a registered,
server-owned adapter stay explicitly blocked rather than becoming a fake
``RUNNING`` or ``OPERATIONAL`` result.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re
from typing import Any

from src.services.capability_graph import CapabilityGraph


COMPONENT_LIFECYCLE_SCHEMA_VERSION = "component-lifecycle.v2"
LIFECYCLE_ACTIONS = (
    "DISCOVER",
    "INSPECT",
    "PLAN_INSTALL",
    "VERIFY_SOURCE",
    "INSTALL",
    "VERIFY_INSTALL",
    "START",
    "HEALTH_CHECK",
    "STOP",
    "UPDATE",
    "REPAIR",
    "UNINSTALL",
    "ROLLBACK",
)
_ACTION_SET = frozenset(LIFECYCLE_ACTIONS)
_PLAN_ACTIONS = frozenset({"PLAN_INSTALL", "VERIFY_INSTALL", "UPDATE", "REPAIR", "UNINSTALL"})
_READ_ONLY_ACTIONS = frozenset({"DISCOVER", "INSPECT"})
SUPPORTED_LIFECYCLE_CAPABILITY_KINDS = ("model", "runtime")
_LIFECYCLE_CAPABILITY_ID = re.compile(r"^(?:model|runtime|component):[a-z][a-z0-9._-]{1,95}$")
_GRAPH_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9._-]{1,31}:[a-z][a-z0-9._-]{1,95}$")
_COMPONENT_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_PLAN_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_OPERATION_ID = re.compile(r"^compop_[a-f0-9]{32}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])"
)
_PUBLIC_OPERATION_STATES = frozenset({"planned", "executing", "verifying", "committed", "failed", "blocked", "cancelled"})
_PUBLIC_PLAN_STATUSES = frozenset({"planned", "completed", "unavailable", "conflict", "invalid", "error"})


class ComponentLifecycleEngineError(ValueError):
    """Raised when a caller violates the closed V2 lifecycle contract."""


def _safe_text(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if not 1 <= len(candidate) <= 320 or any(ord(char) < 32 for char in candidate):
        return fallback
    return fallback if _UNSAFE_TEXT.search(candidate) else candidate


def _safe_component_id(value: object) -> str | None:
    return value if isinstance(value, str) and _COMPONENT_ID.fullmatch(value) else None


def _safe_capability_id(value: object) -> str | None:
    return value if isinstance(value, str) and _LIFECYCLE_CAPABILITY_ID.fullmatch(value) else None


def _safe_blockers(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value[:32]:
        if not isinstance(item, Mapping):
            continue
        capability_id = str(item.get("capability_id") or "")
        code = str(item.get("code") or "")
        state = str(item.get("operational_state") or "")
        if _GRAPH_CAPABILITY_ID.fullmatch(capability_id) is None or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", code):
            continue
        if state not in {"DISCOVERED", "REGISTERED", "INSTALLED", "INSTALLED_UNVERIFIED", "VERIFIED", "STARTABLE", "RUNNING", "OPERATIONAL", "DEGRADED", "UNAVAILABLE", "BROKEN"}:
            continue
        result.append({
            "capability_id": capability_id,
            "code": code,
            "operational_state": state,
            "reason": _safe_text(item.get("reason"), "Capability evidence is not available."),
            "next_action": _safe_text(item.get("next_action"), "Review the exact dependency before requesting execution."),
        })
    return result


def _component_kind(capability_id: str) -> tuple[str, str] | None:
    prefix, _, component_id = capability_id.partition(":")
    if prefix not in {"model", "runtime", "component"} or _safe_component_id(component_id) is None:
        return None
    return component_id, prefix


def _planner_projection(value: object) -> dict[str, Any] | None:
    """Detach the small V8 operation/plan projection accepted by this facade."""

    if not isinstance(value, Mapping):
        return None
    operation_id = value.get("operation_id")
    plan_id = value.get("plan_id")
    fingerprint = value.get("plan_fingerprint") or value.get("expected_state_fingerprint")
    component_id = _safe_component_id(value.get("component_id"))
    component_type = value.get("component_type")
    action = value.get("action")
    status = value.get("status")
    operation_state = value.get("operation_state")
    if (
        not isinstance(operation_id, str)
        or _OPERATION_ID.fullmatch(operation_id) is None
        or not isinstance(plan_id, str)
        or _PLAN_ID.fullmatch(plan_id) is None
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
        or component_id is None
        or component_type not in {"model", "runtime"}
        or not isinstance(action, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", action)
        or not isinstance(status, str)
        or status not in _PUBLIC_PLAN_STATUSES
        or not isinstance(operation_state, str)
        or operation_state not in _PUBLIC_OPERATION_STATES
    ):
        return None
    return {
        "operation_id": operation_id,
        "plan_id": plan_id,
        "plan_fingerprint": fingerprint,
        "component_id": component_id,
        "component_type": component_type,
        "action": action,
        "status": status[:64],
        "operation_state": operation_state,
        "reason": _safe_text(value.get("reason"), "The V8 operation was created as a server-owned plan."),
        "next_action": _safe_text(value.get("next_action"), "Review the plan and use the existing explicit confirmation flow."),
        "execution": "not_run",
        "dry_run": True,
    }


class ComponentLifecycleEngine:
    """Expose one lifecycle contract without becoming a new execution path."""

    def __init__(self, *, graph: CapabilityGraph) -> None:
        if not isinstance(graph, CapabilityGraph):
            raise ComponentLifecycleEngineError("capability_graph_required")
        self._graph = graph

    def _record(self, capability_id: object) -> dict[str, Any] | None:
        identifier = _safe_capability_id(capability_id)
        if identifier is None:
            return None
        value = self._graph.capability(identifier)
        return deepcopy(value) if isinstance(value, Mapping) else None

    @staticmethod
    def _blocker_summary(blockers: list[dict[str, str]], *, capability_id: object) -> tuple[str, str]:
        if blockers:
            # A graph includes the selected capability itself when it is not
            # operational.  Action guidance is more useful when it names the
            # first *downstream* hard dependency, e.g. worker:whisper, while
            # preserving a self-blocker as the fallback when no child exists.
            children = [item for item in blockers if item["capability_id"] != capability_id]
            candidates = children or blockers
            rank = {"BROKEN": 0, "UNAVAILABLE": 1, "DEGRADED": 2, "DISCOVERED": 3, "REGISTERED": 4}
            blocker = sorted(candidates, key=lambda item: (rank.get(item["operational_state"], 5), item["capability_id"]))[0]
            return (
                f"The exact dependency {blocker['capability_id']} is {blocker['operational_state']}.",
                blocker["next_action"],
            )
        return (
            "No registered server-owned lifecycle adapter can perform this action yet.",
            "Keep the action blocked until a separately reviewed lifecycle adapter is registered.",
        )

    def _action_matrix(self, record: Mapping[str, Any], component_type: str | None) -> list[dict[str, str]]:
        blockers = _safe_blockers(record.get("blockers"))
        blocker_reason, blocker_next_action = self._blocker_summary(blockers, capability_id=record.get("capability_id"))
        matrix: list[dict[str, str]] = []
        for action in LIFECYCLE_ACTIONS:
            if action in _READ_ONLY_ACTIONS:
                availability = "read_only"
                reason = "This action reads the bounded server-owned capability projection only."
                next_action = "Review the exact capability state and blockers before requesting a lifecycle plan."
            elif action in _PLAN_ACTIONS and component_type in {"model", "runtime"}:
                availability = "plan_available"
                reason = "This action can create only an existing V8 server-owned plan; it does not execute from this endpoint."
                next_action = "Review the resulting opaque plan and use the existing explicit confirmation flow if authorized."
            elif action == "VERIFY_SOURCE":
                availability = "blocked"
                reason = "No registered server-owned source-verification adapter can verify this source from the lifecycle engine yet."
                next_action = "Review the bounded catalog/source acceptance record; do not treat metadata as completed source verification."
            elif action == "INSTALL":
                availability = "plan_required"
                reason = "Installation requires a prior PLAN_INSTALL record and explicit confirmation through the V8 lifecycle authority."
                next_action = "Create PLAN_INSTALL first; no installation is started by this lifecycle contract."
            else:
                availability = "blocked"
                reason = blocker_reason
                next_action = blocker_next_action
            matrix.append({
                "action": action,
                "availability": availability,
                "reason": reason,
                "next_action": next_action,
            })
        return matrix

    def inspect(self, capability_id: object) -> dict[str, Any] | None:
        record = self._record(capability_id)
        if record is None:
            return None
        identifier = str(record["capability_id"])
        kind = _component_kind(identifier)
        component_id, component_type = kind if kind is not None else (None, None)
        blockers = _safe_blockers(record.get("blockers"))
        return {
            "schema_version": COMPONENT_LIFECYCLE_SCHEMA_VERSION,
            "capability_id": identifier,
            "component_id": component_id,
            "component_type": component_type,
            "lifecycle_eligible": component_type in SUPPORTED_LIFECYCLE_CAPABILITY_KINDS,
            "supported_capability_kinds": list(SUPPORTED_LIFECYCLE_CAPABILITY_KINDS),
            "lifecycle_limitation": None if component_type in SUPPORTED_LIFECYCLE_CAPABILITY_KINDS else "Component capability discovery is inspect-only in V2; no component START/STOP/INSTALL adapter is registered.",
            "install_state": record.get("install_state"),
            "runtime_state": record.get("runtime_state"),
            "verification_state": record.get("verification_state"),
            "operational_state": record.get("operational_state"),
            "blockers": blockers,
            "actions": self._action_matrix(record, component_type),
            "reason": _safe_text(record.get("reason"), "Capability evidence is not available."),
            "next_action": _safe_text(record.get("next_action"), "Review the exact capability dependency before planning."),
            "execution": "not_run",
            "dry_run": True,
        }

    def snapshot(self) -> dict[str, Any]:
        components: list[dict[str, Any]] = []
        for identifier in self._graph.capability_ids:
            if _safe_capability_id(identifier) is None:
                continue
            item = self.inspect(identifier)
            if item is not None:
                components.append(item)
        counts = {
            "lifecycle_eligible": sum(1 for item in components if item["lifecycle_eligible"]),
            "non_operational": sum(1 for item in components if item["operational_state"] != "OPERATIONAL"),
            "plan_available": sum(1 for item in components if any(action["availability"] == "plan_available" for action in item["actions"])),
        }
        return {
            "schema_version": COMPONENT_LIFECYCLE_SCHEMA_VERSION,
            "status": "completed",
            "components": components,
            "counts": counts,
            "supported_actions": list(LIFECYCLE_ACTIONS),
            "supported_capability_kinds": list(SUPPORTED_LIFECYCLE_CAPABILITY_KINDS),
            "reason": "The lifecycle engine is a server-owned planning facade; only existing V8 component plans can be created here.",
            "next_action": "Inspect an eligible model or runtime and create only the explicit plan supported by its current V8 authority.",
            "execution": "not_run",
            "dry_run": True,
        }

    def plan(self, capability_id: object, action: object, *, planner: Any | None = None) -> dict[str, Any] | None:
        detail = self.inspect(capability_id)
        if detail is None:
            return None
        requested = action if isinstance(action, str) and action in _ACTION_SET else None
        base = {
            "schema_version": COMPONENT_LIFECYCLE_SCHEMA_VERSION,
            "capability_id": detail["capability_id"],
            "component_id": detail["component_id"],
            "component_type": detail["component_type"],
            "action": requested or "",
            "execution": "not_run",
            "dry_run": True,
        }
        if requested is None:
            return {
                **base,
                "status": "invalid",
                "code": "invalid_lifecycle_action",
                "lifecycle_state": "BLOCKED",
                "reason": "The lifecycle action is not part of the fixed V2 contract.",
                "next_action": "Choose one action published by the server-owned lifecycle record.",
            }
        action_detail = next((item for item in detail["actions"] if item["action"] == requested), None)
        if action_detail is None:
            return {
                **base,
                "status": "invalid",
                "code": "invalid_lifecycle_action",
                "lifecycle_state": "BLOCKED",
                "reason": "The lifecycle action is not published for this capability.",
                "next_action": "Inspect the server-owned lifecycle record and choose a published action.",
            }
        if requested not in _PLAN_ACTIONS:
            return {
                **base,
                "status": "unavailable",
                "code": "lifecycle_action_not_plannable",
                "lifecycle_state": "BLOCKED",
                "reason": "This lifecycle action is read-only or has no registered durable V8 execution adapter.",
                "next_action": action_detail["next_action"],
            }
        component_id = detail["component_id"]
        component_type = detail["component_type"]
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"} or planner is None:
            return {
                **base,
                "status": "unavailable",
                "code": "lifecycle_planner_unavailable",
                "lifecycle_state": "BLOCKED",
                "reason": "No eligible server-owned V8 planner is available for this capability.",
                "next_action": "Restore the managed planner and inspect the capability again before requesting a plan.",
            }
        try:
            planner_instance = planner() if callable(planner) else planner
            if requested == "PLAN_INSTALL":
                raw = planner_instance.plan_install(component_id, component_type=component_type)
            elif requested == "VERIFY_INSTALL":
                raw = planner_instance.plan_verify(component_id, component_type=component_type)
            else:
                raw = planner_instance.plan_maintenance(component_id, action=requested.lower())
        except Exception:
            return {
                **base,
                "status": "unavailable",
                "code": "lifecycle_plan_unavailable",
                "lifecycle_state": "BLOCKED",
                "reason": "The server-owned lifecycle planner did not publish a usable plan for this capability.",
                "next_action": "Review current capability blockers and create a fresh plan only after the planner is available.",
            }
        plan = _planner_projection(raw)
        if plan is None:
            return {
                **base,
                "status": "unavailable",
                "code": "lifecycle_plan_contract_incomplete",
                "lifecycle_state": "BLOCKED",
                "reason": "The lifecycle planner returned an incomplete public operation contract.",
                "next_action": "Keep execution blocked and restore a complete server-owned V8 plan contract.",
            }
        return {
            **base,
            "status": "planned",
            "lifecycle_state": "PLANNED",
            "plan": plan,
            "reason": "A new V8 operation plan was created; it has not executed.",
            "next_action": "Review the opaque plan and use the existing explicit confirmation endpoint only if authorized.",
        }


__all__ = [
    "COMPONENT_LIFECYCLE_SCHEMA_VERSION",
    "LIFECYCLE_ACTIONS",
    "SUPPORTED_LIFECYCLE_CAPABILITY_KINDS",
    "ComponentLifecycleEngine",
    "ComponentLifecycleEngineError",
]
