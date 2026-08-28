"""Plan-only Workflow Runtime V2 contract.

This module is deliberately a preflight boundary, not a second graph runner.
It reuses the existing typed Node Studio validator and emits only bounded,
path-free plans.  A future server-owned execution owner may consume a READY
plan, but no browser request through this service can launch a worker, media
tool, provider, model, or GPU workload.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
import hashlib
import json
import re
from typing import Any

from src.services.node_studio.registry import NodeDefinition, get_definition
from src.services.node_studio.schema import validate_graph

from .migration import WORKFLOW_GRAPH_V2_SCHEMA_VERSION, migrate_graph


WORKFLOW_RUNTIME_V2_SCHEMA_VERSION = "workflow-runtime.v2"
WORKFLOW_RUNTIME_V2_EXECUTION_MODE = "plan_only"
WORKFLOW_STATES = ("DRAFT", "VALIDATED", "READY", "RUNNING", "FAILED", "COMPLETED")
_ARTIFACT_ID = re.compile(r"^artifact_[a-f0-9]{32}$")
_PROJECT_ID = re.compile(r"^project_[a-f0-9]{32}$")
_WORKFLOW_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){0,11}$")
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_SAFE_NODE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
_MAX_GRAPH_BYTES = 512 * 1024
_MAX_UNKNOWN_NODES = 32


# The mapping is deliberately finite and lives on the server.  It is used
# only to explain preflight blockers; it is not an execution adapter lookup.
_RUNNER_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "media": ("engine:ffmpeg",),
    "image_upscale": ("engine:ffmpeg",),
    "frame_interpolate": ("engine:ffmpeg",),
    "video_transform": ("engine:ffmpeg",),
    "video_grade": ("engine:ffmpeg",),
    "logo_overlay": ("engine:ffmpeg",),
    "audio_loudness": ("engine:ffmpeg",),
    "video_upscale": ("engine:ffmpeg",),
    "encode": ("engine:ffmpeg",),
    "probe": ("engine:ffmpeg",),
    "probe_audio": ("engine:ffmpeg",),
    "animesr": ("runtime:animesr", "model:animesr-v2", "resource:gpu"),
    "sam2_segment": ("runtime:sam2", "resource:gpu"),
    "sam2_track": ("runtime:sam2", "resource:gpu"),
    "grounding": ("runtime:grounding-dino", "resource:gpu"),
    "rfdetr": ("runtime:rf-detr", "resource:gpu"),
    "flux": ("runtime:comfyui", "resource:gpu"),
    "qwen": ("runtime:comfyui", "resource:gpu"),
}

# Profiles remain server-owned Resource Scheduler declarations. This mapping
# selects a declared runtime/provider slot; it never carries client estimates
# or a VRAM quantity.
_RUNNER_RESOURCE_SLOTS: dict[str, tuple[str, ...]] = {
    "animesr": ("animesr", "video"),
    "video_upscale": ("animesr", "video"),
    "frame_interpolate": ("animesr", "video"),
    "encode": ("animesr", "video"),
    "grounding": ("vision",),
    "rfdetr": ("vision",),
    "sam2_segment": ("vision",),
    "sam2_track": ("vision",),
    "flux": ("vision",),
    "qwen": ("vision",),
    "comfyui_workflow": ("vision",),
}

_SAFE_ERROR_CONTEXT = frozenset({
    "code", "node_id", "edge_id", "node_index", "edge_index", "port",
    "property", "source_type", "target_type", "nodes",
})


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _canonical_bytes(value: object) -> bytes | None:
    try:
        raw = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        return None
    return raw if len(raw) <= _MAX_GRAPH_BYTES else None


def _safe_validation_errors(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value[:64]:
        if not isinstance(item, Mapping):
            continue
        row: dict[str, Any] = {}
        for key in _SAFE_ERROR_CONTEXT:
            candidate = item.get(key)
            if key == "code" and isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_:-]{1,80}", candidate):
                row[key] = candidate
            elif key in {"node_id", "edge_id", "port", "property"} and isinstance(candidate, str) and len(candidate) <= 100 and "\\" not in candidate and "/" not in candidate:
                row[key] = candidate
            elif key in {"node_index", "edge_index"} and isinstance(candidate, int) and not isinstance(candidate, bool) and 0 <= candidate <= 10_000:
                row[key] = candidate
            elif key in {"source_type", "target_type"} and isinstance(candidate, str) and re.fullmatch(r"[A-Z_]{1,32}", candidate):
                row[key] = candidate
            elif key == "nodes" and isinstance(candidate, list):
                row[key] = [node for node in candidate[:32] if isinstance(node, str) and _SAFE_NODE_ID.fullmatch(node)]
        if row:
            result.append(row)
    return result


def _unknown_nodes(graph: object) -> list[dict[str, str]]:
    if not isinstance(graph, Mapping) or not isinstance(graph.get("nodes"), list):
        return []
    preserved: list[dict[str, str]] = []
    for node in graph["nodes"][:256]:
        if not isinstance(node, Mapping):
            continue
        node_id = node.get("id")
        node_type = node.get("type")
        if not isinstance(node_id, str) or not _SAFE_NODE_ID.fullmatch(node_id):
            continue
        if not isinstance(node_type, str) or not node_type or len(node_type) > 100:
            continue
        if get_definition(node_type) is None:
            preserved.append({"node_id": node_id, "node_type": node_type, "state": "preserved_unexecutable"})
        if len(preserved) >= _MAX_UNKNOWN_NODES:
            break
    return preserved


def _capability_states(snapshot: object) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    values = snapshot.get("capabilities") if isinstance(snapshot, Mapping) else None
    if not isinstance(values, list):
        return result
    for item in values[:2_000]:
        if not isinstance(item, Mapping):
            continue
        capability_id = item.get("capability_id")
        if not isinstance(capability_id, str) or len(capability_id) > 160:
            continue
        state = item.get("operational_state")
        result[capability_id] = {
            "operational_state": state if isinstance(state, str) and len(state) <= 48 else "UNKNOWN",
            "reason": item.get("reason") if isinstance(item.get("reason"), str) and len(item["reason"]) <= 320 else "",
            "next_action": item.get("next_action") if isinstance(item.get("next_action"), str) and len(item["next_action"]) <= 320 else "",
        }
    return result


def _public_node_contract(definition: NodeDefinition) -> dict[str, Any]:
    requirements = _RUNNER_CAPABILITIES.get(definition.runner, ())
    return {
        "node_type": definition.type,
        "node_version": definition.version,
        "runner": definition.runner,
        "capability_requirements": list(requirements),
        "resource_requirements": {
            "requires_gpu": bool(definition.heavy),
            "exclusive_gpu": bool(definition.heavy),
            "execution_class": "heavy" if definition.heavy else "lightweight",
        },
        "availability": {
            "status": definition.status,
            "reason": definition.status_reason or ("Node is registered for plan-only preflight." if definition.status == "operational" else "Node adapter is not operational."),
            "next_action": definition.status_action or ("Inspect capability preflight before dispatch." if definition.status == "operational" else "Review the declared adapter limitation."),
        },
    }


def _artifact_media_type(record: Mapping[str, Any]) -> str | None:
    value = record.get("media_type") or record.get("type")
    if not isinstance(value, str) or len(value) > 120:
        return None
    lowered = value.casefold()
    if lowered.startswith("image/"):
        return "IMAGE"
    if lowered.startswith("video/"):
        return "VIDEO"
    if lowered.startswith("audio/"):
        return "AUDIO"
    if lowered in {"application/json", "application/x-local-ai-hub-metadata"}:
        return "METADATA"
    return None


class WorkflowRuntimeV2:
    """Build deterministic graph/capability/resource/artifact preflight plans."""

    def __init__(
        self,
        *,
        capability_snapshot: Callable[[], Mapping[str, Any]] | None = None,
        resource_snapshot: Callable[[], Mapping[str, Any]] | None = None,
        artifact_describer: Callable[[str], Mapping[str, Any] | None] | None = None,
        durable_admit: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        dispatch_binding: Mapping[str, Any] | None = None,
    ) -> None:
        self._capability_snapshot = capability_snapshot or (lambda: {})
        self._resource_snapshot = resource_snapshot or (lambda: {})
        self._artifact_describer = artifact_describer or (lambda _artifact_id: None)
        self._durable_admit = durable_admit
        self._dispatch_binding = self._validated_dispatch_binding(dispatch_binding)

    def contract(self) -> dict[str, Any]:
        return {
            "schema_version": WORKFLOW_RUNTIME_V2_SCHEMA_VERSION,
            "status": "partial",
            "workflow_states": list(WORKFLOW_STATES),
            "graph_schema_versions": [1, WORKFLOW_GRAPH_V2_SCHEMA_VERSION],
            "execution_mode": WORKFLOW_RUNTIME_V2_EXECUTION_MODE,
            "execution": "not_run",
            "dry_run": True,
            "reason": "Workflow Runtime V2 currently creates only server-owned preflight plans; no execution owner is registered by this surface.",
            "next_action": "Validate a graph and review its capability, resource and artifact plan before separately requesting dispatch authority.",
        }

    def preflight(self, payload: object) -> dict[str, Any]:
        """Validate a graph without persisting, reserving, or executing it."""

        if isinstance(payload, Mapping) and "graph" in payload:
            if set(payload) - {"graph", "project_id", "workflow_id"}:
                return self._invalid("workflow_runtime_payload_invalid")
            graph = payload.get("graph")
            project_id = payload.get("project_id")
            workflow_reference = payload.get("workflow_id")
        else:
            graph = payload
            project_id = None
            workflow_reference = None
        if project_id is not None and (not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id)):
            return self._invalid("workflow_runtime_project_invalid")
        if workflow_reference is not None and (not isinstance(workflow_reference, str) or not _WORKFLOW_ID.fullmatch(workflow_reference)):
            return self._invalid("workflow_runtime_reference_invalid")
        migration_view = migrate_graph(graph)
        if migration_view.get("accepted") is not True:
            return self._invalid(str(migration_view.get("code") or "workflow_runtime_graph_invalid"))
        runtime_graph = migration_view["runtime_graph"]
        encoded = _canonical_bytes(migration_view["v2_graph"])
        if encoded is None:
            return self._invalid("workflow_runtime_graph_bounds")

        unknown = _unknown_nodes(runtime_graph)
        validation = validate_graph(runtime_graph, require_runnable=True)
        graph_value = validation.get("graph") if isinstance(validation, Mapping) else {}
        errors = _safe_validation_errors(validation.get("errors") if isinstance(validation, Mapping) else None)
        workflow_fingerprint = hashlib.sha256(encoded).hexdigest()
        workflow_id = workflow_reference or f"workflow_{workflow_fingerprint[:32]}"
        migration = {
            "status": "compatible" if not unknown and isinstance(graph_value, Mapping) and graph_value.get("schema_version") == 1 else "manual_review" if unknown else "unsupported",
            "source_schema_version": migration_view["source_schema_version"],
            "target_schema_version": WORKFLOW_GRAPH_V2_SCHEMA_VERSION,
            "runtime_schema_version": 1,
            "actions": list(migration_view["actions"]),
            "unknown_nodes": unknown,
            "preservation": "unknown nodes remain in the original draft/library record and are never discarded by preflight.",
        }
        if not validation.get("valid"):
            return {
                "schema_version": WORKFLOW_RUNTIME_V2_SCHEMA_VERSION,
                "status": "completed",
                "workflow_id": workflow_id,
                "project_id": project_id,
                "workflow_state": "DRAFT",
                "valid": False,
                "validation": {"errors": errors},
                "migration": migration,
                "node_contracts": [],
                "capability_plan": {"status": "not_run", "requirements": [], "blockers": []},
                "resource_plan": {"status": "not_run", "reservations": [], "reason": "Graph validation must pass before resource planning."},
                "artifact_plan": {"status": "not_run", "artifacts": [], "missing": []},
                "dispatch": self._dispatch_unavailable(),
                "execution": "not_run",
                "dry_run": True,
                "reason": "Workflow remains a draft because its graph is invalid or contains unsupported nodes.",
                "next_action": "Fix the typed graph validation errors; unknown nodes are preserved but cannot execute.",
            }

        nodes = graph_value.get("nodes") if isinstance(graph_value, Mapping) else []
        node_contracts: list[dict[str, Any]] = []
        requirements: set[str] = set()
        artifact_requests: list[tuple[str, str, str]] = []
        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                node_id = node.get("id")
                definition = get_definition(str(node.get("type") or ""))
                if not isinstance(node_id, str) or definition is None:
                    continue
                contract = _public_node_contract(definition)
                contract["node_id"] = node_id
                node_contracts.append(contract)
                requirements.update(contract["capability_requirements"])
                data = node.get("data")
                if definition.runner == "load_artifact" and isinstance(data, Mapping):
                    artifact_id = data.get("asset_id")
                    if isinstance(artifact_id, str) and _ARTIFACT_ID.fullmatch(artifact_id) and definition.outputs:
                        artifact_requests.append((node_id, artifact_id, definition.outputs[0].type))

        capability_plan = self._capability_plan(sorted(requirements))
        resource_plan = self._resource_plan(node_contracts)
        artifact_plan = self._artifact_plan(artifact_requests)
        blockers = capability_plan["blockers"] + resource_plan["blockers"] + artifact_plan["blockers"]
        state = "READY" if not blockers else "VALIDATED"
        return {
            "schema_version": WORKFLOW_RUNTIME_V2_SCHEMA_VERSION,
            "status": "completed",
            "workflow_id": workflow_id,
            "project_id": project_id,
            "workflow_state": state,
            "valid": True,
            "validation": {"errors": []},
            "migration": migration,
            "node_contracts": node_contracts,
            "capability_plan": capability_plan,
            "resource_plan": resource_plan,
            "artifact_plan": artifact_plan,
            "dispatch": self._dispatch_unavailable(),
            "execution": "not_run",
            "dry_run": True,
            "reason": "Workflow preflight is complete; it has not reserved resources, written a job, or executed a node.",
            "next_action": "Review blockers and obtain a separately registered server-owned workflow execution owner before any dispatch.",
        }

    def dispatch(self, payload: object) -> dict[str, Any]:
        """Request durable admission only through a server-owned binding.

        This method never starts a worker. It creates no record unless a
        separately supplied owner binding, a READY preflight, and the existing
        Durable Job Engine admission contract all agree. The browser cannot
        choose owner, worker, resource profile, command or runtime.
        """

        preflight = self.preflight(payload)
        if preflight.get("status") != "completed" or preflight.get("workflow_state") != "READY":
            return {
                "status": "unavailable",
                "code": "workflow_runtime_preflight_not_ready",
                "preflight": preflight,
                "execution": "not_run",
                "dry_run": True,
                "reason": "Workflow dispatch is refused until graph, capability, resource and artifact preflight are READY.",
                "next_action": "Resolve every preflight blocker before requesting a server-owned workflow admission.",
            }
        binding = self._dispatch_binding
        if binding is None or self._durable_admit is None:
            return {
                "status": "unavailable",
                "code": "workflow_runtime_execution_owner_unavailable",
                "preflight": preflight,
                "execution": "not_run",
                "dry_run": True,
                "reason": "No server-owned Workflow Runtime V2 execution owner is registered, so no durable job was created.",
                "next_action": "Register and verify a trusted owner separately; do not treat this preflight as an executed workflow.",
            }
        profile_id = self._dispatch_profile(preflight, binding)
        if profile_id is None:
            return {
                "status": "unavailable",
                "code": "workflow_runtime_profile_unavailable",
                "preflight": preflight,
                "execution": "not_run",
                "dry_run": True,
                "reason": "The READY graph needs more than one incompatible scheduler profile or no server-owned profile binding exists.",
                "next_action": "Implement a trusted phased reservation coordinator or bind one reviewed profile before dispatch.",
            }
        artifacts = preflight.get("artifact_plan", {}).get("artifacts", []) if isinstance(preflight.get("artifact_plan"), Mapping) else []
        input_artifact_ids = [item.get("artifact_id") for item in artifacts if isinstance(item, Mapping) and isinstance(item.get("artifact_id"), str) and _ARTIFACT_ID.fullmatch(item["artifact_id"])]
        request = {
            "workflow_id": preflight["workflow_id"],
            "input_artifact_ids": input_artifact_ids,
            "artifact_refs": [],
            "execution_owner": binding["execution_owner"],
            "worker_id": binding["worker_id"],
            "resource_profile_id": profile_id,
        }
        try:
            result = self._durable_admit(request)
        except Exception:
            result = None
        if not isinstance(result, Mapping):
            return {
                "status": "unavailable",
                "code": "workflow_runtime_durable_admission_unavailable",
                "preflight": preflight,
                "execution": "not_run",
                "dry_run": True,
                "reason": "The server-owned Durable Job Engine did not return an admission result.",
                "next_action": "Inspect the trusted execution-owner and durable-job boundary before retrying.",
            }
        return {
            "status": result.get("status") if isinstance(result.get("status"), str) else "unavailable",
            "preflight": preflight,
            "job": result.get("job") if isinstance(result.get("job"), Mapping) else None,
            "execution": result.get("execution") if isinstance(result.get("execution"), str) else "not_run",
            "dry_run": result.get("dry_run") is not False,
            "reason": "A durable admission was requested through a server-owned owner; a worker has not been started by Workflow Runtime V2." if result.get("status") == "accepted" else "Durable admission was not accepted; no workflow execution was started.",
            "next_action": "The trusted owner must claim the exact reservation before any workflow can be reported as running." if result.get("status") == "accepted" else "Inspect the durable admission result and preflight before trying again.",
        }

    @staticmethod
    def _invalid(code: str) -> dict[str, Any]:
        return {
            "schema_version": WORKFLOW_RUNTIME_V2_SCHEMA_VERSION,
            "status": "invalid",
            "workflow_state": "DRAFT",
            "error": code,
            "execution": "not_run",
            "dry_run": True,
        }

    @staticmethod
    def _validated_dispatch_binding(value: object) -> dict[str, str] | None:
        if not isinstance(value, Mapping) or set(value) != {"execution_owner", "worker_id", "light_profile_id"}:
            return None
        owner = value.get("execution_owner")
        worker = value.get("worker_id")
        profile = value.get("light_profile_id")
        owner_pattern = re.compile(r"^[a-z][a-z0-9._:-]{1,95}$")
        worker_pattern = re.compile(r"^[a-z][a-z0-9._:-]{1,95}$")
        return {"execution_owner": owner, "worker_id": worker, "light_profile_id": profile} if isinstance(owner, str) and owner_pattern.fullmatch(owner) and isinstance(worker, str) and worker_pattern.fullmatch(worker) and isinstance(profile, str) and _PROFILE_ID.fullmatch(profile) else None

    @staticmethod
    def _dispatch_profile(preflight: Mapping[str, Any], binding: Mapping[str, str]) -> str | None:
        resource_plan = preflight.get("resource_plan")
        reservations = resource_plan.get("reservations") if isinstance(resource_plan, Mapping) else []
        profile_ids = {item.get("profile_id") for item in reservations if isinstance(item, Mapping) and isinstance(item.get("profile_id"), str) and _PROFILE_ID.fullmatch(item["profile_id"])} if isinstance(reservations, list) else set()
        if not profile_ids:
            return binding["light_profile_id"]
        return next(iter(profile_ids)) if len(profile_ids) == 1 else None

    @staticmethod
    def _dispatch_unavailable() -> dict[str, str]:
        return {
            "status": "unavailable",
            "reason": "No server-owned Workflow Runtime V2 execution owner is registered.",
            "next_action": "Keep this preflight as a plan until a trusted execution owner has been separately implemented and verified.",
        }

    def _capability_plan(self, requirements: list[str]) -> dict[str, Any]:
        try:
            snapshot = self._capability_snapshot()
        except Exception:
            snapshot = {}
        states = _capability_states(snapshot)
        rows: list[dict[str, str]] = []
        blockers: list[dict[str, str]] = []
        for capability_id in requirements:
            record = states.get(capability_id)
            operational_state = record.get("operational_state") if record else "NOT_REGISTERED"
            ready = operational_state == "OPERATIONAL"
            row = {"capability_id": capability_id, "operational_state": operational_state, "state": "ready" if ready else "blocked"}
            rows.append(row)
            if not ready:
                blockers.append({
                    "kind": "capability",
                    "capability_id": capability_id,
                    "code": "capability_not_operational",
                    "reason": record.get("reason") if record and record.get("reason") else "Capability has no current operational evidence.",
                    "next_action": record.get("next_action") if record and record.get("next_action") else "Verify the exact capability before requesting workflow dispatch.",
                })
        return {"status": "ready" if not blockers else "blocked", "requirements": rows, "blockers": blockers}

    def _resource_plan(self, node_contracts: list[dict[str, Any]]) -> dict[str, Any]:
        heavy = [item for item in node_contracts if isinstance(item.get("resource_requirements"), Mapping) and item["resource_requirements"].get("requires_gpu") is True]
        if not heavy:
            return {"status": "ready", "reservations": [], "blockers": [], "policy": "No heavy GPU node is present in this graph; future lightweight dispatch still requires a server-owned owner."}
        try:
            snapshot = self._resource_snapshot()
        except Exception:
            snapshot = {}
        policy = snapshot.get("policy") if isinstance(snapshot, Mapping) else {}
        inventory = snapshot.get("inventory") if isinstance(snapshot, Mapping) else {}
        profiles = snapshot.get("profiles") if isinstance(snapshot, Mapping) else []
        max_heavy = policy.get("max_heavy_gpu_jobs") if isinstance(policy, Mapping) else None
        active_heavy = self._active_heavy(snapshot)
        valid_policy = isinstance(max_heavy, int) and not isinstance(max_heavy, bool) and max_heavy >= 1
        valid_active = isinstance(active_heavy, int) and not isinstance(active_heavy, bool) and active_heavy >= 0
        available = max(0, max_heavy - active_heavy) if valid_policy and valid_active else 0
        blockers: list[dict[str, str]] = []
        inventory_ready = isinstance(inventory, Mapping) and inventory.get("status") == "available"
        if not valid_policy or not valid_active or not inventory_ready:
            blockers.append({
                "kind": "resource",
                "code": "resource_snapshot_unavailable",
                "reason": "The server-owned scheduler has no current bounded heavy-GPU capacity projection.",
                "next_action": "Refresh the server-owned resource inventory before a future dispatch request.",
            })
        elif available < 1:
            blockers.append({
                "kind": "resource",
                "code": "heavy_gpu_slot_unavailable",
                "reason": "The scheduler policy has no available exclusive heavy-GPU slot.",
                "next_action": "Wait for an existing reservation to complete or cancel through its owner.",
            })
        reservations: list[dict[str, Any]] = []
        available_profiles = [item for item in profiles if isinstance(item, Mapping)] if isinstance(profiles, list) else []
        for item in heavy:
            node_id = item.get("node_id")
            runner = item.get("runner")
            if not isinstance(node_id, str) or not isinstance(runner, str):
                continue
            profile = self._profile_for_runner(available_profiles, runner)
            if profile is None:
                blockers.append({
                    "kind": "resource",
                    "node_id": node_id,
                    "code": "resource_profile_unavailable",
                    "reason": "No server-owned exclusive GPU profile matches this node's declared runtime/provider slot.",
                    "next_action": "Register a reviewed scheduler profile before requesting workflow dispatch.",
                })
                continue
            reservations.append({
                "kind": "gpu",
                "mode": "exclusive",
                "node_ids": [node_id],
                "profile_id": profile["profile_id"],
                "state": "not_reserved",
                "max_concurrent": 1,
            })
        return {
            "status": "ready" if not blockers else "blocked",
            "reservations": reservations,
            "available_heavy_slots": available if valid_policy and valid_active else None,
            "blockers": blockers,
            "policy": "This is a dry-run reservation plan; it does not probe a device or reserve VRAM.",
        }

    @staticmethod
    def _active_heavy(snapshot: object) -> int | None:
        if not isinstance(snapshot, Mapping):
            return None
        jobs = snapshot.get("jobs")
        if not isinstance(jobs, list):
            return None
        active_states = {"PREPARING", "RUNNING", "PAUSED", "CANCELLING"}
        total = 0
        for item in jobs:
            if not isinstance(item, Mapping) or item.get("state") not in active_states:
                continue
            profile = item.get("profile")
            if isinstance(profile, Mapping) and profile.get("gpu_required") is True and profile.get("exclusive") is True:
                total += 1
        return total

    @staticmethod
    def _profile_for_runner(profiles: list[Mapping[str, Any]], runner: str) -> dict[str, str] | None:
        desired_slots = _RUNNER_RESOURCE_SLOTS.get(runner, ())
        for desired in desired_slots:
            for profile in profiles:
                if profile.get("gpu_required") is not True or profile.get("exclusive") is not True:
                    continue
                if profile.get("runtime_slot") != desired and profile.get("provider_slot") != desired:
                    continue
                profile_id = profile.get("profile_id")
                if isinstance(profile_id, str) and _PROFILE_ID.fullmatch(profile_id):
                    return {"profile_id": profile_id}
        return None

    def _artifact_plan(self, requests: list[tuple[str, str, str]]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        blockers: list[dict[str, str]] = []
        for node_id, artifact_id, expected_type in requests:
            try:
                record = self._artifact_describer(artifact_id)
            except Exception:
                record = None
            media_type = _artifact_media_type(record) if isinstance(record, Mapping) else None
            found = isinstance(record, Mapping)
            compatible = found and (media_type == expected_type or expected_type == "METADATA")
            rows.append({
                "node_id": node_id,
                "artifact_id": artifact_id,
                "expected_type": expected_type,
                "media_type": media_type,
                "state": "ready" if compatible else "missing" if not found else "type_mismatch",
            })
            if not compatible:
                blockers.append({
                    "kind": "artifact",
                    "artifact_id": artifact_id,
                    "node_id": node_id,
                    "code": "artifact_not_found" if not found else "artifact_type_mismatch",
                    "reason": "The opaque artifact is not available with the type required by this node.",
                    "next_action": "Select an existing Hub artifact with the required typed socket media.",
                })
        return {"status": "ready" if not blockers else "blocked", "artifacts": rows, "missing": [item["artifact_id"] for item in rows if item["state"] == "missing"], "blockers": blockers}


__all__ = ["WORKFLOW_RUNTIME_V2_EXECUTION_MODE", "WORKFLOW_RUNTIME_V2_SCHEMA_VERSION", "WORKFLOW_STATES", "WorkflowRuntimeV2"]
