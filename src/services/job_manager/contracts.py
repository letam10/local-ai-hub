"""Closed serializable contracts for the V5 durable work engine.

The durable engine deliberately accepts descriptors, never executable client
objects.  An adapter identifier is only resolved against a server-owned
registry after this module has validated and normalized the descriptor.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any


JOB_SPEC_VERSION = "job-spec.v1"
EXECUTION_DESCRIPTOR_VERSION = "execution-descriptor.v1"
JOB_RECORD_VERSION = "durable-job.v1"

MAX_JOB_SPEC_BYTES = 64 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_ARGUMENT_DEPTH = 6
MAX_ARGUMENT_ITEMS = 128
MAX_ARGUMENT_STRING = 1024
MAX_CPU_SLOTS = 8
MAX_GPU_SLOTS = 1
MAX_RAM_MB = 256 * 1024
MAX_DISK_MB = 1024 * 1024

JOB_STATES = frozenset(
    {
        "queued",
        "starting",
        "running",
        "cancelling",
        "completed",
        "failed",
        "unavailable",
        "interrupted",
    }
)
TERMINAL_JOB_STATES = frozenset({"completed", "failed", "unavailable", "interrupted"})
RETRYABLE_JOB_STATES = frozenset({"failed", "unavailable", "interrupted"})

_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
_ARTIFACT_ID = re.compile(r"artifact_[a-f0-9]{32}")
_FORBIDDEN_KEY = re.compile(
    r"(?:^|_)(?:path|paths|file|filepath|directory|root|location|uri|url|href|"
    r"command|cmd|shell|executable|runner|callable|function|import|module|script|"
    r"token|password|secret|api_?key|credential|env|environment|host|user|"
    r"media|image|video|audio|blob|attachment|weights|checkpoint|model)(?:$|_)",
    re.IGNORECASE,
)
_UNSAFE_VALUE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|[\\/])\.\.(?:[\\/]|$)|"
    r"(?:^|\s)/(?:\S*)|(?:file|data):|https?://|(?:[?&](?:token|secret|password|key)=)|"
    r"(?:\b(?:bearer|sk)-[A-Za-z0-9._-]{8,})|(?:^[A-Za-z0-9+/]{128,}={0,2}$))",
    re.IGNORECASE,
)


class JobContractError(ValueError):
    """Fixed, non-reflecting validation error for a durable job contract."""

    def __init__(self, code: str, action: str) -> None:
        super().__init__(code)
        self.code = code
        self.action = action

    def public(self) -> dict[str, str]:
        return {"code": self.code, "action": self.action}


def _error(code: str, action: str = "Create a new allowlisted job descriptor.") -> None:
    raise JobContractError(code, action)


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation or reject non-serializable input."""

    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise JobContractError("INVALID_SERIALIZATION", "Use a bounded JSON-compatible value.") from exc


def detached_json(value: Any) -> Any:
    """Return a JSON detached copy after bounded serializability validation."""

    return json.loads(canonical_json(value))


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _error(code)
    return value


def _safe_string(value: str, *, code: str = "UNSAFE_ARGUMENT") -> str:
    if not value or len(value) > MAX_ARGUMENT_STRING or "\x00" in value:
        _error(code)
    if any(ord(character) < 32 for character in value):
        _error(code)
    if _UNSAFE_VALUE.search(value):
        _error(code)
    return value


def _safe_key(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _error("INVALID_ARGUMENT_KEY")
    canonical = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    canonical = re.sub(r"[^a-z0-9]", "_", canonical.lower()).strip("_")
    if canonical in {"__proto__", "constructor", "prototype"} or _FORBIDDEN_KEY.search(canonical):
        _error("FORBIDDEN_ARGUMENT_FIELD")
    return value


def _validate_argument(value: Any, *, depth: int = 0, counter: list[int] | None = None) -> Any:
    if depth > MAX_ARGUMENT_DEPTH:
        _error("ARGUMENT_DEPTH_EXCEEDED")
    items = counter if counter is not None else [0]
    items[0] += 1
    if items[0] > MAX_ARGUMENT_ITEMS:
        _error("ARGUMENT_ITEM_LIMIT")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > 9_007_199_254_740_991:
            _error("UNSAFE_ARGUMENT")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _error("UNSAFE_ARGUMENT")
        return value
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, list):
        if len(value) > MAX_ARGUMENT_ITEMS:
            _error("ARGUMENT_ITEM_LIMIT")
        return [_validate_argument(item, depth=depth + 1, counter=items) for item in value]
    if type(value) is dict:
        if len(value) > MAX_ARGUMENT_ITEMS:
            _error("ARGUMENT_ITEM_LIMIT")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            safe_key = _safe_key(key)
            if safe_key in normalized:
                _error("DUPLICATE_ARGUMENT_FIELD")
            normalized[safe_key] = _validate_argument(item, depth=depth + 1, counter=items)
        return normalized
    _error("UNSAFE_ARGUMENT")


@dataclass(frozen=True)
class ResourceRequest:
    """Declarative capacity reservation; this class never probes hardware."""

    cpu_slots: int = 1
    gpu_slots: int = 0
    ram_mb: int = 0
    disk_mb: int = 0
    exclusive_group: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "ResourceRequest":
        if type(value) is not dict:
            _error("INVALID_RESOURCE_REQUEST")
        expected = {"cpu_slots", "gpu_slots", "ram_mb", "disk_mb", "exclusive_group"}
        if set(value) != expected:
            _error("INVALID_RESOURCE_REQUEST")

        def bounded_integer(name: str, maximum: int) -> int:
            candidate = value.get(name)
            if isinstance(candidate, bool) or not isinstance(candidate, int) or not 0 <= candidate <= maximum:
                _error("INVALID_RESOURCE_REQUEST")
            return candidate

        cpu_slots = bounded_integer("cpu_slots", MAX_CPU_SLOTS)
        if cpu_slots < 1:
            _error("INVALID_RESOURCE_REQUEST")
        exclusive_group = value.get("exclusive_group")
        if exclusive_group is not None:
            exclusive_group = _identifier(exclusive_group, "INVALID_RESOURCE_REQUEST")
        return cls(
            cpu_slots=cpu_slots,
            gpu_slots=bounded_integer("gpu_slots", MAX_GPU_SLOTS),
            ram_mb=bounded_integer("ram_mb", MAX_RAM_MB),
            disk_mb=bounded_integer("disk_mb", MAX_DISK_MB),
            exclusive_group=exclusive_group,
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "cpu_slots": self.cpu_slots,
            "gpu_slots": self.gpu_slots,
            "ram_mb": self.ram_mb,
            "disk_mb": self.disk_mb,
            "exclusive_group": self.exclusive_group,
        }


@dataclass(frozen=True)
class ExecutionDescriptor:
    """A safe reference to a server-registered adapter, never a callable."""

    adapter_id: str
    operation: str
    arguments: dict[str, Any]
    reconstructable: bool
    resources: ResourceRequest

    @classmethod
    def from_mapping(cls, value: Any) -> "ExecutionDescriptor":
        if type(value) is not dict:
            _error("INVALID_EXECUTION_DESCRIPTOR")
        expected = {"schema_version", "adapter_id", "operation", "arguments", "reconstructable", "resources"}
        if set(value) != expected or value.get("schema_version") != EXECUTION_DESCRIPTOR_VERSION:
            _error("INVALID_EXECUTION_DESCRIPTOR")
        adapter_id = _identifier(value.get("adapter_id"), "INVALID_ADAPTER_ID")
        operation = _identifier(value.get("operation"), "INVALID_OPERATION")
        if operation != "run":
            _error("UNSUPPORTED_OPERATION")
        if not isinstance(value.get("reconstructable"), bool):
            _error("INVALID_RECONSTRUCTABLE_FLAG")
        arguments = _validate_argument(value.get("arguments"))
        if type(arguments) is not dict:
            _error("INVALID_ARGUMENTS")
        if len(canonical_json(arguments).encode("utf-8")) > MAX_ARGUMENT_BYTES:
            _error("ARGUMENT_SIZE_EXCEEDED")
        return cls(
            adapter_id=adapter_id,
            operation=operation,
            arguments=detached_json(arguments),
            reconstructable=value["reconstructable"],
            resources=ResourceRequest.from_mapping(value.get("resources")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_DESCRIPTOR_VERSION,
            "adapter_id": self.adapter_id,
            "operation": self.operation,
            "arguments": detached_json(self.arguments),
            "reconstructable": self.reconstructable,
            "resources": self.resources.to_mapping(),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "operation": self.operation,
            "reconstructable": self.reconstructable,
            "resources": self.resources.to_mapping(),
        }


@dataclass(frozen=True)
class JobSpec:
    """Versioned durable request containing only a declarative descriptor."""

    tool: str
    descriptor: ExecutionDescriptor

    @classmethod
    def from_mapping(cls, value: Any) -> "JobSpec":
        if type(value) is not dict:
            _error("INVALID_JOB_SPEC")
        expected = {"schema_version", "tool", "descriptor"}
        if set(value) != expected or value.get("schema_version") != JOB_SPEC_VERSION:
            _error("INVALID_JOB_SPEC")
        if len(canonical_json(value).encode("utf-8")) > MAX_JOB_SPEC_BYTES:
            _error("JOB_SPEC_SIZE_EXCEEDED")
        return cls(
            tool=_identifier(value.get("tool"), "INVALID_TOOL"),
            descriptor=ExecutionDescriptor.from_mapping(value.get("descriptor")),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": JOB_SPEC_VERSION,
            "tool": self.tool,
            "descriptor": self.descriptor.to_mapping(),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.to_mapping()).encode("utf-8")).hexdigest()

    def summary(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "descriptor": self.descriptor.summary(),
            "fingerprint": self.fingerprint,
        }


def validate_job_spec(value: Any) -> JobSpec:
    """Validate a client descriptor without reflecting client-provided values."""

    return JobSpec.from_mapping(value)


def is_artifact_id(value: Any) -> bool:
    """Expose the opaque artifact-ID predicate for trusted adapters only."""

    return isinstance(value, str) and bool(_ARTIFACT_ID.fullmatch(value))
