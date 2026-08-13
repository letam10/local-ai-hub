"""Static manager-parent controller boundary for local recovery preflight.

This module owns no Config writer and no recovery engine.  Its child-pipe
protocol is an authenticated, bounded framing contract for a future manager
process; every public preflight projection remains blocked/not_run in this
package.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.shared import canonical_git_integrity
from src.shared import machine_local_recovery_executor as executor


SCHEMA_VERSION = "machine-recovery-controller.v1"
FRAME_SCHEMA = "manager-child-pipe.v1"
EXPECTED_CANONICAL_HEAD = "ca998106fe2319da6b41fe1c73c6df834d65b2c8"
EXPECTED_CANONICAL_TREE = "0d6852a20d78701b948cedcd0f970b4d3bb5e27c8"
EXPECTED_MANIFEST_DIGEST = "4436315c32f76d469ca86095adb1c8fab968f285347fc0b0395e035c4e037936"
EXPECTED_GUARD_CODE = "CANONICAL_PRESERVATION_REQUIRED"
EXPECTED_CONSUMER_COUNT = 4
TARGETS = executor.TARGETS
FRAME_KINDS = frozenset({"challenge", "response", "ack", "close", "error"})
PAYLOAD_STATUSES = frozenset({"ack", "close", "not_run"})
MAX_FRAME_BYTES = 8192
SESSION_TTL_SECONDS = 30
MAX_CLOCK_SKEW_SECONDS = 2
_PIPE_MARKER = object()


class ControllerError(ValueError):
    """Finite protocol or binding refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hex_digest(value: object, length: int = 64) -> bool:
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


@dataclass(frozen=True)
class SessionBinding:
    controller_head: str
    controller_tree: str
    controller_script_sha256: str
    executor_head: str
    executor_tree: str
    executor_script_sha256: str
    canonical_head: str
    canonical_tree: str
    preservation_digest: str
    consumer_bindings_digest: str
    plan_fingerprint: str
    target_names: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SessionBinding":
        fields = (
            "controller_head", "controller_tree", "controller_script_sha256",
            "executor_head", "executor_tree", "executor_script_sha256",
            "canonical_head", "canonical_tree", "preservation_digest",
            "consumer_bindings_digest", "plan_fingerprint", "target_names",
        )
        if not isinstance(value, Mapping) or set(value) != set(fields):
            raise ControllerError("binding_shape_invalid")
        if any(
            not (
                _hex_digest(value[field], 40)
                or (field.endswith("_tree") and _hex_digest(value[field], 41))
            )
            if field.endswith("_head") or field.endswith("_tree")
            else not _hex_digest(value[field], 64)
            for field in fields
            if field != "target_names"
        ):
            raise ControllerError("binding_digest_invalid")
        if not isinstance(value["target_names"], (list, tuple)) or tuple(value["target_names"]) != TARGETS:
            raise ControllerError("target_set_mismatch")
        if value["canonical_head"] != EXPECTED_CANONICAL_HEAD or value["canonical_tree"] != EXPECTED_CANONICAL_TREE:
            raise ControllerError("canonical_binding_mismatch")
        if value["preservation_digest"] != EXPECTED_MANIFEST_DIGEST:
            raise ControllerError("preservation_manifest_mismatch")
        normalized = {field: value[field] for field in fields}
        normalized["target_names"] = tuple(normalized["target_names"])
        return cls(**normalized)

    def fingerprint(self) -> str:
        return _digest({field: getattr(self, field) for field in self.__dataclass_fields__})


class ParentPipe:
    """Opaque marker for the manager-owned inherited child pipe."""

    def __init__(self, marker: object) -> None:
        self._marker = marker


def open_parent_pipe() -> ParentPipe:
    return ParentPipe(_PIPE_MARKER)


def _frame_mac(key: bytes, body: Mapping[str, Any], binding_digest: str, transcript: str) -> str:
    signed = _canonical({"body": body, "binding": binding_digest, "transcript": transcript})
    return hmac.new(key, signed, hashlib.sha256).hexdigest()


def _validate_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ControllerError("frame_payload_invalid")
    try:
        keys = set(payload)
    except (TypeError, ValueError):
        raise ControllerError("frame_payload_invalid")
    if keys not in (set(), {"status"}) or (keys == {"status"} and payload.get("status") not in PAYLOAD_STATUSES):
        raise ControllerError("frame_payload_invalid")
    return dict(payload)


def _validate_frame(frame: object) -> dict[str, Any]:
    if not isinstance(frame, Mapping):
        raise ControllerError("frame_shape_invalid")
    try:
        if set(frame) != {"schema", "session_id", "seq", "nonce", "kind", "payload", "mac"}:
            raise ControllerError("frame_shape_invalid")
    except (TypeError, ValueError):
        raise ControllerError("frame_shape_invalid")
    if frame.get("schema") != FRAME_SCHEMA or not isinstance(frame.get("session_id"), str) or len(frame["session_id"]) != 32:
        raise ControllerError("frame_schema_invalid")
    if not isinstance(frame.get("seq"), int) or frame["seq"] < 0 or not isinstance(frame.get("nonce"), str) or len(frame["nonce"]) != 32:
        raise ControllerError("frame_sequence_invalid")
    payload = _validate_payload(frame.get("payload"))
    if frame.get("kind") not in FRAME_KINDS or not isinstance(frame.get("mac"), str) or len(frame["mac"]) != 64:
        raise ControllerError("frame_enum_invalid")
    try:
        frame_size = len(_canonical(frame))
    except (TypeError, ValueError):
        raise ControllerError("frame_shape_invalid")
    if frame_size > MAX_FRAME_BYTES:
        raise ControllerError("frame_oversize")
    value = dict(frame)
    value["payload"] = payload
    return value


class ChildPipeEndpoint:
    """The only object that can construct a response for a live session."""

    def __init__(self, session_id: str, key: bytes, binding_digest: str) -> None:
        self._session_id = session_id
        self._key = key
        self._binding_digest = binding_digest

    def frame(self, *, kind: str = "response", seq: int = 0, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if kind not in FRAME_KINDS or not isinstance(seq, int) or seq < 0:
            raise ControllerError("frame_enum_invalid")
        safe_payload = _validate_payload(payload or {})
        body = {
            "schema": FRAME_SCHEMA,
            "session_id": self._session_id,
            "seq": seq,
            "nonce": secrets.token_hex(16),
            "kind": kind,
            "payload": safe_payload,
        }
        body["mac"] = _frame_mac(self._key, body, self._binding_digest, "")
        return body


class ParentSession:
    """Short-lived authenticated protocol state; no execution capability."""

    def __init__(self, binding: SessionBinding, pipe: ParentPipe, *, clock: Callable[[], float] = time.time) -> None:
        if not isinstance(pipe, ParentPipe) or pipe._marker is not _PIPE_MARKER:
            raise ControllerError("parent_pipe_required")
        self._binding = binding
        self._binding_digest = binding.fingerprint()
        self._pipe = pipe
        self._session_id = secrets.token_hex(16)
        self._key = secrets.token_bytes(32)
        self._created = clock()
        self._clock = clock
        self._expected_seq = 0
        self._seen_nonces: set[str] = set()
        self._transcript = ""

    def challenge(self) -> dict[str, Any]:
        return self._make_frame("challenge", {})

    def child_endpoint(self) -> ChildPipeEndpoint:
        return ChildPipeEndpoint(self._session_id, self._key, self._binding_digest)

    def _make_frame(self, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "schema": FRAME_SCHEMA,
            "session_id": self._session_id,
            "seq": self._expected_seq,
            "nonce": secrets.token_hex(16),
            "kind": kind,
            "payload": dict(payload),
        }
        body["mac"] = _frame_mac(self._key, body, self._binding_digest, self._transcript)
        return body

    def receive(self, frame: object, pipe: ParentPipe) -> dict[str, Any]:
        if pipe is not self._pipe or not isinstance(pipe, ParentPipe) or pipe._marker is not _PIPE_MARKER:
            return _blocked("parent_pipe_required")
        if self._clock() > self._created + SESSION_TTL_SECONDS + MAX_CLOCK_SKEW_SECONDS:
            return _blocked("session_expired")
        try:
            value = _validate_frame(frame)
            if value["session_id"] != self._session_id or value["seq"] != self._expected_seq:
                raise ControllerError("sequence_or_session_mismatch")
            if value["nonce"] in self._seen_nonces:
                raise ControllerError("nonce_replay")
            supplied = value.pop("mac")
            expected = _frame_mac(self._key, value, self._binding_digest, self._transcript)
            if not hmac.compare_digest(supplied, expected):
                raise ControllerError("frame_auth_invalid")
            if value["kind"] not in {"response", "ack", "close"}:
                raise ControllerError("frame_kind_invalid")
            self._seen_nonces.add(value["nonce"])
            self._transcript = _digest({"prior": self._transcript, "frame": value})
            self._expected_seq += 1
            return {"status": "accepted", "execution": "not_run", "dry_run": True}
        except ControllerError as exc:
            return _blocked(exc.code)


def _blocked(reason: str) -> dict[str, Any]:
    return {"status": "apply_blocked", "execution": "not_run", "dry_run": True, "apply_allowed": False, "reason": reason, "next_action": "manager_controller_required"}


def inspect_plan_projection(
    *,
    canonical_state: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    binding: SessionBinding,
    plan_fingerprint: str,
    active_hub: bool,
) -> dict[str, Any]:
    """Return sanitized no-write evidence; never an apply-ready projection."""

    blockers: list[str] = []
    if canonical_state.get("code") != EXPECTED_GUARD_CODE or canonical_state.get("dirty") is not True:
        blockers.append("canonical_preservation_guard_required")
    if active_hub:
        blockers.append("active_hub")
    if snapshot.get("status") != "available":
        blockers.append("snapshot_unavailable")
    if snapshot.get("canonical_head") != EXPECTED_CANONICAL_HEAD or snapshot.get("canonical_tree") != EXPECTED_CANONICAL_TREE:
        blockers.append("canonical_identity_mismatch")
    if snapshot.get("preservation_digest") != EXPECTED_MANIFEST_DIGEST or snapshot.get("preservation_count") != executor.MANIFEST_COUNT:
        blockers.append("preservation_manifest_mismatch")
    consumer = snapshot.get("consumer_digest")
    if not _hex_digest(consumer) or consumer != binding.consumer_bindings_digest or snapshot.get("consumer_count") != EXPECTED_CONSUMER_COUNT:
        blockers.append("consumer_binding_mismatch")
    if binding.plan_fingerprint != plan_fingerprint:
        blockers.append("plan_binding_mismatch")
    if not isinstance(snapshot.get("targets"), list) or {row.get("target") for row in snapshot["targets"] if isinstance(row, Mapping)} != set(TARGETS):
        blockers.append("target_set_mismatch")
    reason = blockers[0] if blockers else "manager_controller_required"
    return _blocked(reason)


def collect_static_projection(
    *,
    canonical_root: Path,
    config_root: Path,
    preservation_manifest: list[Mapping[str, Any]],
    consumer_hashes: Mapping[str, Mapping[str, Any]],
    canonical_integrity_reader: Callable[[], Mapping[str, Any]] = canonical_git_integrity.inspect_canonical,
    git_runner: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Use only inspect_canonical and bounded executor inspection."""

    guard = canonical_integrity_reader()
    if not isinstance(guard, Mapping):
        return _blocked("canonical_guard_invalid")
    snapshot = executor.inspect(canonical_root=canonical_root, config_root=config_root, preservation_manifest=preservation_manifest, consumer_hashes=consumer_hashes, git_runner=git_runner)
    if isinstance(snapshot, Mapping):
        snapshot = {**snapshot, "consumer_count": len(consumer_hashes)}
    safe_snapshot = {
        "status": snapshot.get("status") if isinstance(snapshot, Mapping) and snapshot.get("status") in {"available", "unavailable"} else "unavailable",
        "canonical_identity_valid": isinstance(snapshot, Mapping) and snapshot.get("canonical_head") == EXPECTED_CANONICAL_HEAD and snapshot.get("canonical_tree") == EXPECTED_CANONICAL_TREE,
        "preservation_manifest_valid": isinstance(snapshot, Mapping) and snapshot.get("preservation_digest") == EXPECTED_MANIFEST_DIGEST and snapshot.get("preservation_count") == executor.MANIFEST_COUNT,
        "consumer_bindings_count": len(consumer_hashes),
        "fixed_target_count": len(TARGETS),
    }
    return {"guard": {"code": guard.get("code") if guard.get("code") == EXPECTED_GUARD_CODE else "guard_unavailable", "dirty": guard.get("dirty") is True}, "snapshot": safe_snapshot}


__all__ = [
    "EXPECTED_CANONICAL_HEAD", "EXPECTED_CANONICAL_TREE", "EXPECTED_MANIFEST_DIGEST",
    "EXPECTED_GUARD_CODE", "TARGETS", "SessionBinding", "ParentPipe", "ParentSession",
    "ChildPipeEndpoint", "ControllerError", "collect_static_projection", "inspect_plan_projection",
    "open_parent_pipe",
]
