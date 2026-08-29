"""Closed, non-executing first-party provider-adapter contracts.

The existing V8 module adapters own any legacy worker implementation.  This
registry is intentionally *not* an import/dispatch facade for them: it offers
one finite vocabulary for workflow/lifecycle preflight and publishes only
Capability Graph and Resource Scheduler projections already owned by the
server.  It lets a future execution owner bind deliberately without making a
browser request an execution authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


PROVIDER_ADAPTERS_V2_SCHEMA_VERSION = "provider-adapters.v2"
EXECUTION_NOT_RUN = "not_run"
_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_TYPE_SET = frozenset({"IMAGE", "MASK", "VIDEO", "AUDIO", "TEXT", "NUMBER", "BOOLEAN", "MODEL", "METADATA"})
_OPERATIONAL = "OPERATIONAL"
_TERMINAL_BLOCKERS = frozenset({"UNAVAILABLE", "BROKEN"})


@dataclass(frozen=True)
class ProviderAdapterSpec:
    """A static first-party integration point, never a module import path."""

    adapter_id: str
    display_name: str
    provider: str
    component: str
    category: str
    input_types: tuple[str, ...]
    output_types: tuple[str, ...]
    capability_ids: tuple[str, ...]
    resource_profile_id: str


# Keep the set explicit.  A plugin/discovered Python module must never appear
# here merely because it is present on the machine or in a configuration file.
_SPECS: tuple[ProviderAdapterSpec, ...] = (
    ProviderAdapterSpec("vision.sam2", "SAM 2", "first_party", "sam2", "vision", ("IMAGE", "MASK", "METADATA"), ("MASK", "METADATA"), ("component:sam2", "worker:sam2", "runtime:sam2", "model:sam2.1-hiera-small"), "vision_gpu_plan"),
    ProviderAdapterSpec("vision.omniparser", "OmniParser", "first_party", "vision", "vision", ("IMAGE",), ("METADATA",), ("component:vision", "worker:vision", "runtime:omniparser", "model:omniparser-v2"), "vision_gpu_plan"),
    ProviderAdapterSpec("vision.rfdetr", "RF-DETR", "first_party", "vision", "vision", ("IMAGE", "VIDEO"), ("METADATA",), ("component:vision", "worker:vision", "runtime:rfdetr", "model:rfdetr-base"), "vision_gpu_plan"),
    ProviderAdapterSpec("vision.grounding_dino", "Grounding DINO", "first_party", "vision", "vision", ("IMAGE", "TEXT"), ("METADATA",), ("component:vision", "worker:vision", "runtime:grounding-dino", "model:grounding-dino-base"), "vision_gpu_plan"),
    ProviderAdapterSpec("vision.ocr", "PaddleOCR", "first_party", "ocr", "vision", ("IMAGE", "VIDEO"), ("TEXT", "METADATA"), ("component:ocr", "worker:ocr", "runtime:paddleocr-vl", "model:paddleocr-vl-0.9b"), "vision_gpu_plan"),
    ProviderAdapterSpec("audio.whisper", "Faster-Whisper", "first_party", "whisper", "audio", ("AUDIO", "VIDEO"), ("TEXT", "METADATA"), ("component:whisper", "worker:whisper", "runtime:faster-whisper", "model:faster-whisper-large-v3"), "audio_gpu_plan"),
    ProviderAdapterSpec("audio.qwen3_tts", "Qwen3-TTS", "first_party", "voice", "audio", ("TEXT",), ("AUDIO", "METADATA"), ("component:voice", "worker:voice", "runtime:qwen3-tts", "model:qwen3-tts-1.7b"), "audio_gpu_plan"),
    ProviderAdapterSpec("audio.seed_vc", "Seed-VC", "first_party", "voice", "audio", ("AUDIO", "TEXT"), ("AUDIO", "METADATA"), ("component:voice", "worker:voice", "runtime:seed-vc", "model:seed-vc-1"), "audio_gpu_plan"),
    ProviderAdapterSpec("image.comfyui", "ComfyUI", "first_party", "image_generation", "image", ("IMAGE", "TEXT", "MODEL"), ("IMAGE", "METADATA"), ("component:image_generation", "worker:image_generation", "runtime:comfyui"), "image_gpu_plan"),
    ProviderAdapterSpec("image.flux", "FLUX", "first_party", "image_generation", "image", ("TEXT", "IMAGE"), ("IMAGE", "METADATA"), ("component:image_generation", "worker:image_generation", "runtime:comfyui", "model:flux-2-klein-base-4b-fp8"), "image_gpu_plan"),
    ProviderAdapterSpec("image.qwen_image", "Qwen Image", "first_party", "image_generation", "image", ("TEXT", "IMAGE"), ("IMAGE", "METADATA"), ("component:image_generation", "worker:image_generation", "runtime:comfyui", "model:qwen-image-2512-fp8"), "image_gpu_plan"),
    ProviderAdapterSpec("video.animesr", "AnimeSR", "first_party", "animesr", "video", ("VIDEO", "IMAGE"), ("VIDEO", "IMAGE", "METADATA"), ("component:animesr", "worker:animesr", "runtime:animesr", "model:animesr-v2"), "video_gpu_plan"),
    ProviderAdapterSpec("media.ffmpeg", "FFmpeg helper", "first_party", "media_editor", "media", ("IMAGE", "VIDEO", "AUDIO", "TEXT"), ("IMAGE", "VIDEO", "AUDIO", "METADATA"), ("component:media_editor", "worker:media_editor", "runtime:ffmpeg"), "media_cpu_plan"),
)
_BY_ID = {item.adapter_id: item for item in _SPECS}


def _safe_id(value: object) -> str | None:
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _capability_index(snapshot: object) -> dict[str, Mapping[str, Any]]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    rows = source.get("capabilities")
    if not isinstance(rows, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = row.get("capability_id")
        if isinstance(identifier, str) and identifier.startswith(("component:", "worker:", "runtime:", "model:")):
            result[identifier] = row
    return result


def _resource_profiles(snapshot: object) -> set[str]:
    source = snapshot if isinstance(snapshot, Mapping) else {}
    rows = source.get("profiles")
    if not isinstance(rows, list):
        return set()
    return {
        str(row.get("profile_id"))
        for row in rows
        if isinstance(row, Mapping) and _safe_id(row.get("profile_id")) is not None
    }


class ProviderAdapterRegistry:
    """Finite first-party Adapter API with safe non-executing default methods."""

    def __init__(
        self,
        *,
        capability_snapshot: Callable[[], Mapping[str, Any]] | None = None,
        resource_snapshot: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._capability_snapshot = capability_snapshot or (lambda: {"capabilities": []})
        self._resource_snapshot = resource_snapshot or (lambda: {"profiles": []})

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(item.adapter_id for item in _SPECS)

    def _dependencies(self, spec: ProviderAdapterSpec) -> dict[str, Any]:
        index = _capability_index(self._capability_snapshot())
        rows: list[dict[str, str]] = []
        terminal = False
        incomplete = False
        for capability_id in spec.capability_ids:
            source = index.get(capability_id)
            if source is None:
                state = "MISSING"
                reason = "The required Capability Graph record is not available."
                next_action = "Restore the exact server-owned dependency record before binding this adapter."
                terminal = True
            else:
                state = str(source.get("operational_state") or "UNAVAILABLE")
                reason = str(source.get("reason") or "Capability verification evidence is unavailable.")[:320]
                next_action = str(source.get("next_action") or "Inspect the server-owned capability record.")[:320]
                terminal = terminal or state in _TERMINAL_BLOCKERS
                incomplete = incomplete or state != _OPERATIONAL
            rows.append({"capability_id": capability_id, "operational_state": state, "reason": reason, "next_action": next_action})
        if not rows or terminal:
            state = "UNAVAILABLE"
        elif incomplete:
            state = "DEGRADED"
        else:
            # This means dependencies have current Capability Graph proof, not
            # that this registry owns or has started an executable provider.
            state = "READY_FOR_BINDING"
        return {"state": state, "requirements": rows}

    def _resource_estimate(self, spec: ProviderAdapterSpec) -> dict[str, Any]:
        known = spec.resource_profile_id in _resource_profiles(self._resource_snapshot())
        return {
            "profile_id": spec.resource_profile_id,
            "state": "declared" if known else "not_published",
            "reason": "Resource requirements are server-owned declared profile metadata; no reservation or hardware probe was made.",
            "reservation": "not_reserved",
        }

    def _detail(self, spec: ProviderAdapterSpec) -> dict[str, Any]:
        dependencies = self._dependencies(spec)
        return {
            "schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
            "adapter_id": spec.adapter_id,
            "display_name": spec.display_name,
            "provider": spec.provider,
            "component": spec.component,
            "category": spec.category,
            "contract": {
                "methods": ["discover", "availability", "dependencies", "start", "stop", "health", "estimate_resources", "validate_input", "execute", "cancel", "collect_artifacts"],
                "input_types": list(spec.input_types),
                "output_types": list(spec.output_types),
                "execution_owner": "server_owned_required",
            },
            "availability": {
                "state": dependencies["state"],
                "execution_owner_state": "UNBOUND",
                "reason": "This first-party adapter is a preflight contract only until a separately verified server-owned execution owner is registered.",
            },
            "dependencies": dependencies,
            "resource_estimate": self._resource_estimate(spec),
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
            "reason": "The adapter publishes a closed preflight contract and never imports or invokes a legacy provider from this registry.",
            "next_action": "Inspect dependency blockers, then bind a separately reviewed server-owned execution owner before any runtime action.",
        }

    def snapshot(self) -> dict[str, Any]:
        adapters = [self._detail(spec) for spec in _SPECS]
        counts = {
            state: sum(1 for item in adapters if item["availability"]["state"] == state)
            for state in ("READY_FOR_BINDING", "DEGRADED", "UNAVAILABLE")
        }
        return {
            "schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
            "status": "completed",
            "adapters": adapters,
            "counts": counts,
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
            "reason": "First-party provider adapters are closed server-owned contracts; they do not execute a provider on discovery.",
            "next_action": "Use a specific adapter preflight to inspect typed input, capability and resource requirements.",
        }

    def discover(self, adapter_id: object) -> dict[str, Any] | None:
        identifier = _safe_id(adapter_id)
        spec = _BY_ID.get(identifier or "")
        return deepcopy(self._detail(spec)) if spec is not None else None

    def availability(self, adapter_id: object) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        return {"schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION, "adapter_id": detail["adapter_id"], "availability": detail["availability"], "execution": EXECUTION_NOT_RUN, "dry_run": True}

    def dependencies(self, adapter_id: object) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        return {"schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION, "adapter_id": detail["adapter_id"], "dependencies": detail["dependencies"], "execution": EXECUTION_NOT_RUN, "dry_run": True}

    def health(self, adapter_id: object) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        return {
            "schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
            "adapter_id": detail["adapter_id"],
            "status": "unavailable",
            "reason": "No server-owned provider process is bound for a health check.",
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def estimate_resources(self, adapter_id: object) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        return {"schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION, "adapter_id": detail["adapter_id"], "resource_estimate": detail["resource_estimate"], "execution": EXECUTION_NOT_RUN, "dry_run": True}

    def validate_input(self, adapter_id: object, payload: object) -> dict[str, Any] | None:
        identifier = _safe_id(adapter_id)
        spec = _BY_ID.get(identifier or "")
        if spec is None:
            return None
        if not isinstance(payload, Mapping) or set(payload) - {"input_types"}:
            return {"status": "invalid", "reason": "Adapter preflight accepts only a bounded input_types array; paths, URLs, commands and provider payloads are not accepted.", "accepted_types": list(spec.input_types), "execution": EXECUTION_NOT_RUN, "dry_run": True}
        values = payload.get("input_types", [])
        if not isinstance(values, list) or len(values) > 12 or any(not isinstance(item, str) or item not in _TYPE_SET for item in values):
            return {"status": "invalid", "reason": "input_types must be a bounded list of known typed-socket values.", "accepted_types": list(spec.input_types), "execution": EXECUTION_NOT_RUN, "dry_run": True}
        rejected = sorted(set(values) - set(spec.input_types))
        return {
            "status": "valid" if not rejected else "invalid",
            "provided_types": list(values),
            "accepted_types": list(spec.input_types),
            "rejected_types": rejected,
            "reason": "Typed input validation passed; no artifact, media byte, path, provider request or model was read." if not rejected else "One or more typed inputs are not accepted by this adapter.",
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def preflight(self, adapter_id: object, payload: object) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        input_validation = self.validate_input(adapter_id, payload)
        if input_validation is None:
            return None
        return {
            "schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
            "status": "completed" if input_validation["status"] == "valid" else "invalid",
            "adapter": detail,
            "input_validation": input_validation,
            "dispatch": {
                "status": "unavailable",
                "code": "provider_adapter_execution_owner_unavailable",
                "actual_execution": False,
                "reason": "The V2 registry has no execution owner and cannot start a provider, worker, model or GPU workload.",
            },
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def _unavailable_operation(self, adapter_id: object, operation: str) -> dict[str, Any] | None:
        detail = self.discover(adapter_id)
        if detail is None:
            return None
        return {
            "schema_version": PROVIDER_ADAPTERS_V2_SCHEMA_VERSION,
            "adapter_id": detail["adapter_id"],
            "operation": operation,
            "status": "unavailable",
            "code": "provider_adapter_execution_owner_unavailable",
            "reason": "This V2 contract has no server-owned provider execution owner.",
            "execution": EXECUTION_NOT_RUN,
            "dry_run": True,
        }

    def start(self, adapter_id: object) -> dict[str, Any] | None:
        return self._unavailable_operation(adapter_id, "start")

    def stop(self, adapter_id: object) -> dict[str, Any] | None:
        return self._unavailable_operation(adapter_id, "stop")

    def execute(self, adapter_id: object, payload: object) -> dict[str, Any] | None:
        del payload
        return self._unavailable_operation(adapter_id, "execute")

    def cancel(self, adapter_id: object, operation_id: object = None) -> dict[str, Any] | None:
        del operation_id
        return self._unavailable_operation(adapter_id, "cancel")

    def collect_artifacts(self, adapter_id: object, operation_id: object = None) -> dict[str, Any] | None:
        del operation_id
        return self._unavailable_operation(adapter_id, "collect_artifacts")


__all__ = ["PROVIDER_ADAPTERS_V2_SCHEMA_VERSION", "ProviderAdapterRegistry", "ProviderAdapterSpec"]
