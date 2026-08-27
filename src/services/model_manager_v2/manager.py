"""Server-owned Model Manager V2 inventory and lifecycle-plan facade.

Model Manager V2 is deliberately additive to the V7 catalog and V8 durable
component-operation journal.  It turns bounded catalog observations into a
single typed model projection, detects only explicit server-owned metadata
collisions, and creates an existing V8 plan only after an explicit POST.

It never recursively scans a Models tree, accepts a browser filesystem path,
loads a model, starts inference, contacts a provider, or writes a registry on
GET.  Full filesystem duplicate/move discovery remains a separately
authorized future operation; an absent observation is reported as unknown,
not as no duplicate exists.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any

from src.services.component_lifecycle_engine import ComponentLifecycleEngine


MODEL_MANAGER_V2_SCHEMA_VERSION = "model-manager.v2"
MODEL_V2_ACTIONS = (
    "PLAN_INSTALL",
    "IMPORT_EXISTING",
    "REGISTER_EXISTING",
    "VERIFY_CHECKSUM",
    "CHECK_DUPLICATES",
    "CHECK_MOVED",
    "REPAIR_REGISTRY",
    "SAFE_REMOVE",
    "UPDATE_METADATA",
    "REVIEW_LICENSE",
    "CHECK_COMPATIBILITY",
)
_ACTION_SET = frozenset(MODEL_V2_ACTIONS)
_MODEL_ID = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_SELECTION_ID = re.compile(r"^selection_[a-f0-9]{32}$")
_OPERATION_ID = re.compile(r"^compop_[a-f0-9]{32}$")
_PLAN_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_FINGERPRINT = re.compile(r"^[a-f0-9]{64}$")
_SAFE_CODE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_UNSAFE_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|(?:https?|file|data):|bearer\s|\b(?:api[_-]?key|secret|password|token)\b|\.\.[\\/])"
)
_MODEL_STATES = frozenset({
    "DISCOVERED", "NOT_INSTALLED", "INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED",
    "OPERATIONAL", "PARTIAL", "UNAVAILABLE", "BROKEN", "UPDATE_AVAILABLE",
})
_CATEGORIES = frozenset({"Image", "Video", "Audio", "Vision", "LLM", "Utility"})
_MAX_MODELS = 256
_MAX_FILES = 64
_MAX_OBSERVATIONS = 512
_MAX_INT = (1 << 63) - 1


class ModelManagerV2Error(ValueError):
    """Raised when a server-owned V2 model projection is malformed."""


def _safe_text(value: object, fallback: str, *, maximum: int = 320) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if not 1 <= len(candidate) <= maximum or any(ord(char) < 32 for char in candidate):
        return fallback
    return fallback if _UNSAFE_TEXT.search(candidate) else candidate


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _MODEL_ID.fullmatch(value) else None


def _safe_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_INT else None


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _category(value: object, *, modules: object = None) -> str:
    candidate = str(value or "").casefold()
    module_names = " ".join(str(item).casefold() for item in modules if isinstance(item, str)) if isinstance(modules, list) else ""
    source = f"{candidate} {module_names}"
    if any(token in source for token in ("image", "flux", "qwen-image")):
        return "Image"
    if any(token in source for token in ("video", "upscale", "rife", "animesr", "esrgan")):
        return "Video"
    if any(token in source for token in ("audio", "voice", "speech", "whisper", "tts")):
        return "Audio"
    if any(token in source for token in ("vision", "segment", "ocr", "dino", "detect", "parser")):
        return "Vision"
    if any(token in source for token in ("llm", "language", "chat")):
        return "LLM"
    return "Utility"


def _precision(value: object) -> str:
    candidate = str(value or "").casefold()
    for token, label in (("fp8", "FP8"), ("bf16", "BF16"), ("fp16", "FP16"), ("float16", "FP16"), ("int8", "INT8"), ("q8", "Q8"), ("q4", "Q4")):
        if token in candidate:
            return label
    return "unknown"


def _file_format(leaves: object) -> str:
    suffixes: set[str] = set()
    if isinstance(leaves, list):
        for item in leaves[:_MAX_FILES]:
            if not isinstance(item, Mapping):
                continue
            # The source relative leaf is used only to classify an extension;
            # it is never included in the public V2 projection.
            raw = item.get("relative_leaf") or item.get("relative_path")
            if isinstance(raw, str):
                suffix = PurePosixPath(raw.replace("\\", "/")).suffix.casefold().lstrip(".")
                if re.fullmatch(r"[a-z0-9]{1,16}", suffix or ""):
                    suffixes.add(suffix)
    return ", ".join(sorted(suffixes)) if suffixes else "unknown"


def _canonical_location(model_id: str) -> str:
    return f"model:{model_id}"


def _source_projection(value: object, model_id: str) -> dict[str, str]:
    source = value if isinstance(value, Mapping) else {}
    status = str(source.get("status") or "UNKNOWN").upper()
    if not _SAFE_CODE.fullmatch(status):
        status = "UNKNOWN"
    identity = _safe_text(source.get("source_identity"), f"catalog:{model_id}", maximum=128)
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", identity):
        identity = f"catalog:{model_id}"
    return {"status": status, "identity": identity, "kind": "catalog_bound"}


def _license_projection(value: object) -> dict[str, str]:
    license_value = value if isinstance(value, Mapping) else {}
    state = _safe_text(license_value.get("state"), "review_required", maximum=64)
    spdx = _safe_text(license_value.get("spdx_id"), "unknown", maximum=64)
    return {"state": state, "spdx_id": spdx}


def _catalog_files(model_id: str, leaves: object) -> tuple[list[dict[str, object]], str]:
    public: list[dict[str, object]] = []
    checksum_declared = False
    if isinstance(leaves, list):
        for index, leaf in enumerate(leaves[:_MAX_FILES]):
            if not isinstance(leaf, Mapping):
                continue
            size = _safe_integer(leaf.get("size_bytes"))
            if size is None:
                size = _safe_integer(leaf.get("observed_size_bytes"))
            verification = str(leaf.get("verification") or "unverified").casefold()
            checksum = str(leaf.get("sha256") or leaf.get("expected_sha256") or "").casefold()
            if _FINGERPRINT.fullmatch(checksum):
                checksum_declared = True
            public.append({
                "file_id": f"model-file-{_digest([model_id, index])[:16]}",
                "present": leaf.get("present") is True,
                "size_bytes": size,
                "verification": "declared" if verification == "verified" or _FINGERPRINT.fullmatch(checksum) else "not_declared",
            })
    return public, "declared" if checksum_declared else "not_declared"


def _state(value: object, *, operational: object) -> str:
    status = str(value or "").upper()
    if status == "OPERATIONAL" and operational is True:
        return "OPERATIONAL"
    if status in {"INSTALLED", "INSTALLED_UNVERIFIED"}:
        # Fixed-leaf presence alone is deliberately not a checksum or runtime
        # verification claim in V2.
        return "INSTALLED_UNVERIFIED"
    if status in {"NOT_INSTALLED", "PARTIAL", "UNAVAILABLE", "BROKEN", "UPDATE_AVAILABLE"}:
        return status
    return "DISCOVERED"


def _safe_observation(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) - {"model_id", "location_id", "size_bytes", "sha256", "present"}:
        return None
    model_id = _safe_id(value.get("model_id"))
    location_id = value.get("location_id")
    size = _safe_integer(value.get("size_bytes"))
    digest = value.get("sha256")
    if model_id is None or not isinstance(location_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", location_id):
        return None
    if digest is not None and (not isinstance(digest, str) or _FINGERPRINT.fullmatch(digest) is None):
        return None
    if value.get("present") is not True:
        return None
    return {"model_id": model_id, "location_id": location_id, "size_bytes": size, "sha256": digest}


def _observations(values: Iterable[Mapping[str, Any]] | None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in values or []:
        observed = _safe_observation(item)
        if observed is not None:
            result.append(observed)
        if len(result) >= _MAX_OBSERVATIONS:
            break
    return result


def _operation_projection(value: object, *, expected_model_id: str) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    operation_id = value.get("operation_id")
    plan_id = value.get("plan_id")
    fingerprint = value.get("plan_fingerprint") or value.get("expected_state_fingerprint")
    component_id = _safe_id(value.get("component_id"))
    component_type = value.get("component_type")
    action = value.get("action")
    state = value.get("operation_state")
    if (
        not isinstance(operation_id, str)
        or _OPERATION_ID.fullmatch(operation_id) is None
        or not isinstance(plan_id, str)
        or _PLAN_ID.fullmatch(plan_id) is None
        or not isinstance(fingerprint, str)
        or _FINGERPRINT.fullmatch(fingerprint) is None
        or component_id != expected_model_id
        or component_type != "model"
        or not isinstance(action, str)
        or not re.fullmatch(r"[a-z][a-z0-9_-]{1,47}", action)
        or state != "planned"
    ):
        return None
    return {
        "operation_id": operation_id,
        "plan_id": plan_id,
        "plan_fingerprint": fingerprint,
        "component_id": component_id,
        "component_type": "model",
        "action": action,
        "operation_state": "planned",
        "execution": "not_run",
        "dry_run": True,
    }


class ModelManagerV2:
    """Build a path-free model inventory from bounded server-owned catalog data."""

    def __init__(
        self,
        *,
        catalog_snapshot: Mapping[str, Any],
        lifecycle_engine: ComponentLifecycleEngine,
        observations: Iterable[Mapping[str, Any]] | None = None,
    ) -> None:
        if not isinstance(catalog_snapshot, Mapping) or not isinstance(lifecycle_engine, ComponentLifecycleEngine):
            raise ModelManagerV2Error("model_manager_v2_dependencies_invalid")
        raw_models = catalog_snapshot.get("models")
        if not isinstance(raw_models, list) or len(raw_models) > _MAX_MODELS:
            raise ModelManagerV2Error("model_manager_v2_catalog_invalid")
        records: dict[str, dict[str, object]] = {}
        for raw in raw_models:
            record = self._record_from_catalog(raw)
            if record is None:
                continue
            model_id = str(record["model_id"])
            if model_id in records:
                raise ModelManagerV2Error("model_manager_v2_duplicate_model")
            records[model_id] = record
        self._records = {key: records[key] for key in sorted(records)}
        self._lifecycle_engine = lifecycle_engine
        self._observations = _observations(observations)

    @staticmethod
    def _record_from_catalog(value: object) -> dict[str, object] | None:
        if not isinstance(value, Mapping):
            return None
        model_id = _safe_id(value.get("model_id"))
        if model_id is None:
            return None
        modules = [item for item in value.get("modules", [])[:16] if _safe_id(item) is not None] if isinstance(value.get("modules"), list) else []
        leaves, checksum_state = _catalog_files(model_id, value.get("leaves"))
        state = _state(value.get("status"), operational=value.get("operational"))
        expected_disk = _safe_integer(value.get("expected_disk_size_bytes"))
        expected_download = _safe_integer(value.get("expected_download_size_bytes"))
        installed_size = _safe_integer(value.get("installed_size_bytes"))
        runtime_id = _safe_id(value.get("runtime_id"))
        source = _source_projection(value.get("source_availability"), model_id)
        license_value = _license_projection(value.get("license"))
        identity_material = {
            "model_id": model_id,
            "provider": _safe_text(value.get("provider"), "unknown", maximum=96),
            "version": _safe_text(value.get("version"), "unknown", maximum=96),
            "runtime_id": runtime_id,
            "expected_disk_size_bytes": expected_disk,
            "source": source,
            "license": license_value,
        }
        return {
            "model_id": model_id,
            "display_name": _safe_text(value.get("display_name"), model_id, maximum=160),
            "family": _safe_text(value.get("category"), "unknown", maximum=96),
            "category": _category(value.get("category"), modules=modules),
            "provider": _safe_text(value.get("provider"), "unknown", maximum=96),
            "purpose": _safe_text(value.get("notes"), "Catalog-backed local AI capability.", maximum=240),
            "source": source,
            "license": license_value,
            "format": _file_format(value.get("leaves")),
            "precision": _precision(f"{model_id} {value.get('version') or ''}"),
            "size_bytes": installed_size if installed_size is not None else expected_disk,
            "expected_download_size_bytes": expected_download,
            "files": leaves,
            "canonical_location": {"location_class": "models_root", "location_id": _canonical_location(model_id)},
            "installed": state in {"INSTALLED_UNVERIFIED", "INSTALLED_VERIFIED", "OPERATIONAL"},
            "verified": state in {"INSTALLED_VERIFIED", "OPERATIONAL"},
            "verification_state": "runtime_evidence" if state == "OPERATIONAL" else "not_verified",
            "runtime_compatibility": {
                "runtime_id": runtime_id,
                "status": "requires_runtime_verification" if runtime_id else "runtime_not_declared",
            },
            "vram_estimate_mb": _safe_integer(value.get("minimum_vram_mb")),
            "ram_estimate_mb": None,
            "last_verified": None,
            "checksum": {"state": checksum_state, "identity_digest": _digest(identity_material)},
            "update_available": state == "UPDATE_AVAILABLE",
            "state": state,
            "reason": _safe_text(value.get("reason"), "The model catalog has no bounded local observation yet."),
            "next_action": _safe_text(value.get("next_action"), "Review the model capability and a server-owned plan before execution."),
            "modules": modules,
        }

    def _record(self, model_id: object) -> dict[str, object] | None:
        identifier = _safe_id(model_id)
        if identifier is None:
            return None
        record = self._records.get(identifier)
        return deepcopy(record) if record is not None else None

    def _analysis(self, record: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        model_id = str(record["model_id"])
        canonical = str(record["canonical_location"]["location_id"]) if isinstance(record.get("canonical_location"), Mapping) else _canonical_location(model_id)
        model_observations = [item for item in self._observations if item["model_id"] == model_id]
        foreign_locations = [item for item in model_observations if item["location_id"] != canonical]
        digest = record.get("checksum", {}).get("identity_digest") if isinstance(record.get("checksum"), Mapping) else None
        size = record.get("size_bytes")
        duplicates: list[dict[str, object]] = []
        for item in self._observations:
            if item["model_id"] == model_id and item["location_id"] == canonical:
                continue
            same_digest = isinstance(item.get("sha256"), str) and item.get("sha256") == digest
            same_size = isinstance(size, int) and size > 0 and item.get("size_bytes") == size
            if same_digest or (same_size and item["model_id"] == model_id):
                duplicates.append({
                    "model_id": item["model_id"],
                    "location_id": item["location_id"],
                    "match": "digest" if same_digest else "size_and_identity",
                })
        duplicate = {
            "state": "candidate_detected" if duplicates else "not_scanned" if not self._observations else "not_detected",
            "candidates": duplicates[:16],
            "reason": "A matching sanitized observation may duplicate this model; no bytes were copied or removed." if duplicates else "No broad filesystem duplicate scan ran; only server-owned bounded observations were evaluated." if not self._observations else "No duplicate was detected among the supplied bounded observations.",
        }
        moved = {
            "state": "possible_moved" if foreign_locations else "not_scanned" if not self._observations else "not_detected",
            "locations": [{"location_id": item["location_id"]} for item in foreign_locations[:16]],
            "reason": "A matching model identity was observed at a noncanonical opaque location; native selection is required before any import or registry repair." if foreign_locations else "No broad filesystem move scan ran; only server-owned bounded observations were evaluated." if not self._observations else "No noncanonical bounded observation was detected for this model.",
        }
        return duplicate, moved

    def _compatibility(self, record: Mapping[str, object]) -> dict[str, object]:
        runtime = record.get("runtime_compatibility") if isinstance(record.get("runtime_compatibility"), Mapping) else {}
        runtime_id = runtime.get("runtime_id") if isinstance(runtime, Mapping) else None
        graph_record = self._lifecycle_engine.inspect(f"model:{record['model_id']}")
        blockers = graph_record.get("blockers", []) if isinstance(graph_record, Mapping) and isinstance(graph_record.get("blockers"), list) else []
        return {
            "runtime_id": runtime_id if isinstance(runtime_id, str) else None,
            "status": "blocked" if blockers else "requires_runtime_verification" if runtime_id else "runtime_not_declared",
            "vram_estimate_mb": record.get("vram_estimate_mb"),
            "ram_estimate_mb": record.get("ram_estimate_mb"),
            "blockers": deepcopy(blockers[:16]),
            "reason": "Compatibility is a catalog/Capability Graph preflight only; it does not probe GPU, RAM, runtime import, or model loading.",
            "next_action": "Resolve the exact runtime or resource blocker before a separately authorized workload.",
        }

    def detail(self, model_id: object) -> dict[str, object] | None:
        record = self._record(model_id)
        if record is None:
            return None
        duplicate, moved = self._analysis(record)
        compatibility = self._compatibility(record)
        record.update({
            "schema_version": MODEL_MANAGER_V2_SCHEMA_VERSION,
            "duplicate_analysis": duplicate,
            "moved_analysis": moved,
            "runtime_compatibility": compatibility,
            "available_actions": list(MODEL_V2_ACTIONS),
            "execution": "not_run",
            "dry_run": True,
        })
        return record

    def snapshot(self) -> dict[str, object]:
        records = [self.detail(model_id) for model_id in self._records]
        public_records = [item for item in records if item is not None]
        counts = {state: sum(1 for item in public_records if item["state"] == state) for state in sorted(_MODEL_STATES)}
        return {
            "schema_version": MODEL_MANAGER_V2_SCHEMA_VERSION,
            "status": "completed",
            "records": public_records,
            "counts": counts,
            "categories": sorted(_CATEGORIES),
            "available_actions": list(MODEL_V2_ACTIONS),
            "reason": "Model Manager V2 composes bounded catalog/graph observations; it does not scan all model storage, download, load, or execute a model.",
            "next_action": "Inspect a model, review its exact dependency/duplicate analysis, then create only a server-owned V8 plan when appropriate.",
            "execution": "not_run",
            "dry_run": True,
        }

    def preflight(self, model_id: object) -> dict[str, object] | None:
        detail = self.detail(model_id)
        if detail is None:
            return None
        duplicate = detail["duplicate_analysis"] if isinstance(detail.get("duplicate_analysis"), Mapping) else {}
        installed = detail.get("installed") is True
        duplicate_candidate = duplicate.get("state") == "candidate_detected"
        download_blocked = installed or duplicate_candidate
        return {
            "schema_version": MODEL_MANAGER_V2_SCHEMA_VERSION,
            "model_id": detail["model_id"],
            "status": "blocked" if download_blocked else "review_required",
            "download_eligible": False,
            "download_blocked": download_blocked,
            "installed_or_observed": installed,
            "duplicate_state": duplicate.get("state"),
            "source": detail["source"],
            "license": detail["license"],
            "runtime_compatibility": detail["runtime_compatibility"],
            "reason": "A managed installation or duplicate candidate exists; a multi-GB download must not be duplicated." if download_blocked else "No automatic download is authorized; source, license, identity, compatibility and existing-install checks remain required.",
            "next_action": "Review the existing managed record or native import/reuse plan instead of downloading duplicate model bytes." if download_blocked else "Use a native selection or reviewed V8 plan only after all preflight requirements are satisfied.",
            "execution": "not_run",
            "dry_run": True,
        }

    def plan(
        self,
        model_id: object,
        action: object,
        *,
        planner: Any | None = None,
        selection_id: object = None,
    ) -> dict[str, object] | None:
        detail = self.detail(model_id)
        if detail is None:
            return None
        requested = action if isinstance(action, str) and action in _ACTION_SET else None
        base: dict[str, object] = {
            "schema_version": MODEL_MANAGER_V2_SCHEMA_VERSION,
            "model_id": detail["model_id"],
            "action": requested or "",
            "execution": "not_run",
            "dry_run": True,
        }
        if requested is None:
            return {**base, "status": "invalid", "code": "model_v2_action_invalid", "reason": "The requested model action is not part of the fixed V2 contract.", "next_action": "Choose one action published by Model Manager V2."}
        if requested == "CHECK_DUPLICATES":
            return {**base, "status": "completed", "analysis": detail["duplicate_analysis"], "reason": "Only bounded server-owned observations were compared.", "next_action": "Review the candidate identity before any import, reuse, or removal decision."}
        if requested == "CHECK_MOVED":
            return {**base, "status": "completed", "analysis": detail["moved_analysis"], "reason": "Only bounded server-owned observations were evaluated for a move signal.", "next_action": "Use a native selection before any import or registry change."}
        if requested == "REVIEW_LICENSE":
            return {**base, "status": "completed", "license": detail["license"], "reason": "License review is metadata-only and does not enable installation.", "next_action": "Complete the separately governed license review before an install plan can become eligible."}
        if requested == "CHECK_COMPATIBILITY":
            return {**base, "status": "completed", "compatibility": detail["runtime_compatibility"], "reason": "Compatibility is a static capability preflight, not a hardware or model execution test.", "next_action": "Resolve exact runtime/resource blockers before a separately authorized workload."}
        if requested == "UPDATE_METADATA":
            return {**base, "status": "unavailable", "code": "metadata_update_adapter_unavailable", "reason": "No typed V2 metadata-update adapter is registered; no config or registry was changed.", "next_action": "Use the tracked catalog review workflow until Configuration V2 provides a revisioned metadata writer."}
        if planner is None:
            return {**base, "status": "unavailable", "code": "model_v2_planner_unavailable", "reason": "The server-owned V8 component planner is unavailable.", "next_action": "Restore the managed planner before requesting an import, reuse, verification, or maintenance plan."}
        try:
            planner_instance = planner() if callable(planner) else planner
            model = str(detail["model_id"])
            if requested in {"PLAN_INSTALL", "VERIFY_CHECKSUM", "SAFE_REMOVE"}:
                lifecycle_action = {"PLAN_INSTALL": "PLAN_INSTALL", "VERIFY_CHECKSUM": "VERIFY_INSTALL", "SAFE_REMOVE": "UNINSTALL"}[requested]
                lifecycle = self._lifecycle_engine.plan(f"model:{model}", lifecycle_action, planner=planner_instance)
                if not isinstance(lifecycle, Mapping) or lifecycle.get("status") != "planned" or not isinstance(lifecycle.get("plan"), Mapping):
                    return {**base, "status": "unavailable", "code": "model_v2_lifecycle_plan_unavailable", "reason": "The existing V8 lifecycle authority did not publish a usable plan.", "next_action": "Review exact model blockers and create a fresh V8 plan when the server-owned planner is available."}
                return {**base, "status": "planned", "plan": deepcopy(lifecycle["plan"]), "reason": "A durable V8 plan was created; it has not executed.", "next_action": "Review the opaque V8 operation and use the existing explicit confirmation flow if authorized."}
            if requested == "REGISTER_EXISTING" or requested == "REPAIR_REGISTRY":
                raw = planner_instance.plan_reuse(model, component_type="model")
            elif requested == "IMPORT_EXISTING":
                if not isinstance(selection_id, str) or _SELECTION_ID.fullmatch(selection_id) is None:
                    return {**base, "status": "unavailable", "code": "native_selection_required", "reason": "Import requires a fresh opaque selection issued by the native picker; browser paths are not accepted.", "next_action": "Choose the source through the native picker, then create an import plan with its opaque selection ID."}
                raw = planner_instance.plan_import(selection_id, mode="COPY_INTO_MANAGED_MODELS")
            else:
                return {**base, "status": "unavailable", "code": "model_v2_action_not_plannable", "reason": "No server-owned V8 plan mapping is registered for this model action.", "next_action": "Use only the published metadata review or lifecycle plan actions."}
        except Exception:
            return {**base, "status": "unavailable", "code": "model_v2_plan_unavailable", "reason": "The server-owned model planner did not publish a usable plan.", "next_action": "Review the model record and create a fresh plan only when the planner is available."}
        plan = _operation_projection(raw, expected_model_id=str(detail["model_id"]))
        if plan is None:
            return {**base, "status": "unavailable", "code": "model_v2_plan_contract_incomplete", "reason": "The model planner returned an incomplete durable operation contract.", "next_action": "Keep execution blocked until a complete server-owned V8 plan is available."}
        return {**base, "status": "planned", "plan": plan, "reason": "A durable V8 plan was created; it has not executed.", "next_action": "Review the opaque V8 operation and use the existing explicit confirmation flow if authorized."}


__all__ = [
    "MODEL_MANAGER_V2_SCHEMA_VERSION",
    "MODEL_V2_ACTIONS",
    "ModelManagerV2",
    "ModelManagerV2Error",
]
