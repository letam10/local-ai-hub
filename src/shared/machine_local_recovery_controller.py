"""Manager-parent-only static recovery controller boundary.

The only transport in this module is a parent-created multiprocessing pipe
used by a spawned synthetic child.  Direct CLI use remains a fixed no-op.
There is no Config writer, apply/resume path, or operational readiness result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import multiprocessing
import secrets
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.shared import canonical_git_integrity
from src.shared import machine_local_recovery_executor as executor


SCHEMA_VERSION = "machine-recovery-controller.v2"
FRAME_SCHEMA = "manager-child-pipe.v2"
EXPECTED_CANONICAL_HEAD = "ca998106fe2319da6b41fe1c73c6df834d65b2c8"
EXPECTED_CANONICAL_TREE = "0d6852a20d78701b948cedcd0f970b4d3bb5e27c8"
EXPECTED_CONTROLLER_HEAD = "de12ff153750756ab9e49375750ea3398293943a"
EXPECTED_CONTROLLER_TREE = "dde8f44e7ac4959295651cd4eee68e6f51d0f9f3"
EXPECTED_MANIFEST_DIGEST = "4436315c32f76d469ca86095adb1c8fab968f285347fc0b0395e035c4e037936"
EXPECTED_GUARD_CODE = "CANONICAL_PRESERVATION_REQUIRED"
TARGETS = executor.TARGETS
CONSUMER_FILES = executor.CONSUMER_FILES
FRAME_KINDS = frozenset({"challenge", "response", "ack", "close", "error"})
PAYLOAD_STATUSES = frozenset({"ack", "close", "not_run"})
MAX_FRAME_BYTES = 8192
SESSION_TTL_SECONDS = 10
MAX_CLOCK_SKEW_SECONDS = 1
_PIPE_MARKER = object()


class ControllerError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _MeasuredBinding:
    controller_head: str
    controller_tree: str
    controller_module_sha256: str
    controller_script_sha256: str
    executor_head: str
    executor_tree: str
    executor_module_sha256: str
    executor_script_sha256: str
    canonical_head: str
    canonical_tree: str
    preservation_digest: str
    consumer_bindings_digest: str
    plan_fingerprint: str

    def fingerprint(self) -> str:
        return _digest({key: getattr(self, key) for key in self.__dataclass_fields__})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _hex(value: object, lengths: tuple[int, ...] = (64,)) -> bool:
    return isinstance(value, str) and len(value) in lengths and all(char in "0123456789abcdef" for char in value)


def _safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative or relative.startswith(("/", "~")) or ":" in relative or ".." in Path(relative).parts:
        raise ControllerError("relative_path_invalid")
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current = current / part
        try:
            item = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
            raise ControllerError("reparse_target")
    return path


def _git_identity(root: Path) -> tuple[str, str]:
    values = []
    for args in (("rev-parse", "HEAD"), ("rev-parse", "HEAD^{tree}")):
        result = subprocess.run(["git", *args], cwd=root, check=False, capture_output=True, text=True, timeout=3)
        if result.returncode != 0 or len(result.stdout.strip()) > 128:
            raise ControllerError("git_identity_unavailable")
        values.append(result.stdout.strip())
    return values[0], values[1]


def _hash_file(root: Path, relative: str) -> str:
    path = _safe_child(root, relative)
    try:
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(item.st_mode):
            raise ControllerError("reparse_target")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except ControllerError:
        raise
    except OSError as exc:
        raise ControllerError("binding_file_unavailable") from exc


def _measure_manifest(root: Path, rows: Sequence[Mapping[str, Any]]) -> str:
    if not isinstance(rows, Sequence) or len(rows) != executor.MANIFEST_COUNT:
        raise ControllerError("preservation_manifest_count")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise ControllerError("preservation_manifest_shape")
        relative = row.get("relative_path")
        if relative in seen:
            raise ControllerError("preservation_manifest_duplicate")
        path = _safe_child(root, relative)
        try:
            item = path.lstat()
            if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(item.st_mode):
                raise ControllerError("preservation_manifest_reparse")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except ControllerError:
            raise
        except OSError as exc:
            raise ControllerError("preservation_manifest_unavailable") from exc
        if row.get("size") != item.st_size or row.get("sha256") != digest:
            raise ControllerError("preservation_manifest_drift")
        normalized.append({"relative_path": relative, "sha256": digest, "size": item.st_size})
        seen.add(relative)
    normalized.sort(key=lambda value: value["relative_path"])
    digest = _digest(normalized)
    if digest != EXPECTED_MANIFEST_DIGEST:
        raise ControllerError("preservation_manifest_mismatch")
    return digest


def _measure_binding(root: Path, rows: Sequence[Mapping[str, Any]], plan_fingerprint: str) -> _MeasuredBinding:
    if not _hex(plan_fingerprint):
        raise ControllerError("plan_fingerprint_invalid")
    head, tree = _git_identity(root)
    if head != EXPECTED_CONTROLLER_HEAD or tree != EXPECTED_CONTROLLER_TREE:
        raise ControllerError("controller_identity_mismatch")
    controller_module = "src/shared/machine_local_recovery_controller.py"
    controller_script = "scripts/manager_machine_local_recovery_controller.py"
    executor_module = "src/shared/machine_local_recovery_executor.py"
    executor_script = "scripts/manager_machine_local_recovery_executor.py"
    consumer_rows = []
    for relative in CONSUMER_FILES:
        path = _safe_child(root, relative)
        consumer_rows.append({"relative_path": relative, "sha256": _hash_file(root, relative), "size": path.stat().st_size})
    consumer_digest = _digest(consumer_rows)
    preservation_digest = _measure_manifest(root, rows)
    return _MeasuredBinding(
        controller_head=head,
        controller_tree=tree,
        controller_module_sha256=_hash_file(root, controller_module),
        controller_script_sha256=_hash_file(root, controller_script),
        executor_head=head,
        executor_tree=tree,
        executor_module_sha256=_hash_file(root, executor_module),
        executor_script_sha256=_hash_file(root, executor_script),
        canonical_head=EXPECTED_CANONICAL_HEAD,
        canonical_tree=EXPECTED_CANONICAL_TREE,
        preservation_digest=preservation_digest,
        consumer_bindings_digest=consumer_digest,
        plan_fingerprint=plan_fingerprint,
    )


def _payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ControllerError("frame_payload_invalid")
    try:
        keys = set(value)
    except (TypeError, ValueError):
        raise ControllerError("frame_payload_invalid")
    if keys not in (set(), {"status"}) or (keys == {"status"} and value.get("status") not in PAYLOAD_STATUSES):
        raise ControllerError("frame_payload_invalid")
    return dict(value)


def _frame(body: Mapping[str, Any], key: bytes, binding: str, transcript: str) -> dict[str, Any]:
    value = dict(body)
    value["mac"] = hmac.new(key, _canonical({"body": value, "binding": binding, "transcript": transcript}), hashlib.sha256).hexdigest()
    return value


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
    if frame.get("kind") not in FRAME_KINDS or not isinstance(frame.get("mac"), str) or len(frame["mac"]) != 64:
        raise ControllerError("frame_enum_invalid")
    value = dict(frame)
    value["payload"] = _payload(frame.get("payload"))
    try:
        if len(_canonical(value)) > MAX_FRAME_BYTES:
            raise ControllerError("frame_oversize")
    except (TypeError, ValueError):
        raise ControllerError("frame_shape_invalid")
    return value


def _blocked(reason: str) -> dict[str, Any]:
    return {"status": "apply_blocked", "execution": "not_run", "dry_run": True, "apply_allowed": False, "reason": reason, "next_action": "manager_controller_required"}


def _child_entry(connection: Any, session_id: str, key: bytes, binding_digest: str, wait_seconds: float = SESSION_TTL_SECONDS) -> None:
    """Spawn target; the connection is the only inherited authority."""

    try:
        if not connection.poll(wait_seconds):
            return
        frame = _validate_frame(connection.recv())
        if frame["session_id"] != session_id or frame["seq"] != 0 or frame["kind"] != "challenge":
            return
        supplied = frame.pop("mac")
        expected = _frame(frame, key, binding_digest, "")["mac"]
        if not hmac.compare_digest(supplied, expected):
            return
        response = {
            "schema": FRAME_SCHEMA,
            "session_id": session_id,
            "seq": 1,
            "nonce": secrets.token_hex(16),
            "kind": "response",
            "payload": {"status": "ack"},
        }
        connection.send(_frame(response, key, binding_digest, _digest(frame)))
    except (ControllerError, EOFError, OSError):
        return
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _run_child_session(binding: _MeasuredBinding, timeout: float = 3.0, fault: str | None = None) -> str:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    session_id = secrets.token_hex(16)
    key = secrets.token_bytes(32)
    digest = binding.fingerprint()
    child_wait = max(timeout * 2, 0.2) if fault == "timeout" else SESSION_TTL_SECONDS
    process = context.Process(target=_child_entry, args=(child, session_id, key, digest, child_wait), daemon=True)
    process.start()
    child.close()
    challenge = {
        "schema": FRAME_SCHEMA,
        "session_id": session_id,
        "seq": 0,
        "nonce": secrets.token_hex(16),
        "kind": "challenge",
        "payload": {},
    }
    try:
        challenge_frame = _frame(challenge, key, digest, "")
        challenge_transcript = _digest(challenge)
        if fault == "tamper":
            challenge_frame["mac"] = "0" * 64
        if fault == "disconnect":
            parent.close()
            return "child_disconnected"
        parent.send(challenge_frame)
        if not parent.poll(timeout):
            return "child_timeout"
        response = _validate_frame(parent.recv())
        supplied = response.pop("mac")
        expected = _frame(response, key, digest, challenge_transcript)["mac"]
        if not hmac.compare_digest(supplied, expected) or response["session_id"] != session_id or response["seq"] != 1 or response["kind"] != "response" or response["nonce"] == challenge["nonce"]:
            return "frame_auth_invalid"
        return "accepted"
    except (ControllerError, EOFError, OSError):
        return "child_protocol_refused"
    finally:
        try:
            parent.close()
        except OSError:
            pass
        process.join(timeout=max(timeout, SESSION_TTL_SECONDS + MAX_CLOCK_SKEW_SECONDS + 1))


def _hub_state(probe: Callable[[], Mapping[str, Any]] | None) -> tuple[bool, str]:
    if probe is None:
        return False, "active_hub_probe_required"
    try:
        value = probe()
    except Exception:
        return False, "active_hub_probe_failed"
    if not isinstance(value, Mapping) or value.get("known") is not True or not isinstance(value.get("active"), bool):
        return False, "active_hub_probe_unknown"
    return value["active"], ""


def run_parent_preflight(
    *,
    private_root: Path,
    canonical_root: Path,
    config_root: Path,
    preservation_manifest: Sequence[Mapping[str, Any]],
    plan_fingerprint: str,
    hub_probe: Callable[[], Mapping[str, Any]] | None,
    canonical_integrity_reader: Callable[[], Mapping[str, Any]] = canonical_git_integrity.inspect_canonical,
    git_runner: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Collect bounded parent evidence, run the child handshake, and block apply."""

    try:
        binding = _measure_binding(private_root, preservation_manifest, plan_fingerprint)
        guard = canonical_integrity_reader()
        if not isinstance(guard, Mapping):
            return _blocked("canonical_guard_invalid")
        consumer_hashes = {}
        for relative in CONSUMER_FILES:
            path = _safe_child(private_root, relative)
            consumer_hashes[relative] = {"sha256": _hash_file(private_root, relative), "size": path.stat().st_size}
        snapshot = executor.inspect(canonical_root=canonical_root, config_root=config_root, preservation_manifest=preservation_manifest, consumer_hashes=consumer_hashes, git_runner=git_runner)
        planned = executor.plan(snapshot, executor_head=binding.executor_head, executor_tree=binding.executor_tree, executor_script_sha256=binding.executor_script_sha256)
        active, hub_error = _hub_state(hub_probe)
        blockers: list[str] = []
        if guard.get("operation_code") != EXPECTED_GUARD_CODE or guard.get("dirty") is not True:
            blockers.append("canonical_preservation_guard_required")
        if hub_error:
            blockers.append(hub_error)
        elif active:
            blockers.append("active_hub")
        if snapshot.get("status") != "available":
            blockers.append("snapshot_unavailable")
        if snapshot.get("canonical_head") != EXPECTED_CANONICAL_HEAD or snapshot.get("canonical_tree") != EXPECTED_CANONICAL_TREE:
            blockers.append("canonical_identity_mismatch")
        if snapshot.get("preservation_digest") != EXPECTED_MANIFEST_DIGEST or snapshot.get("preservation_count") != executor.MANIFEST_COUNT:
            blockers.append("preservation_manifest_mismatch")
        if snapshot.get("consumer_digest") != binding.consumer_bindings_digest:
            blockers.append("source_consumer_mismatch")
        if snapshot.get("consumer_count", len(CONSUMER_FILES)) != len(CONSUMER_FILES):
            blockers.append("consumer_binding_count_mismatch")
        if planned.get("status") != "planned":
            blockers.append("plan_unavailable")
        elif planned.get("plan_fingerprint") != binding.plan_fingerprint or planned.get("plan_fingerprint") != plan_fingerprint:
            blockers.append("plan_binding_mismatch")
        if not isinstance(snapshot.get("targets"), list) or {row.get("target") for row in snapshot["targets"] if isinstance(row, Mapping)} != set(TARGETS):
            blockers.append("target_set_mismatch")
        transport = _run_child_session(binding)
        if transport != "accepted":
            blockers.append("child_transport_" + transport)
        return _blocked(blockers[0] if blockers else "manager_controller_required")
    except ControllerError as exc:
        return _blocked(exc.code)
    except (OSError, ValueError, TypeError):
        return _blocked("controller_evidence_unavailable")


def main() -> int:
    print("manager_recovery_controller_noop: authenticated parent pipe required; execution not_run")
    return 0


__all__ = ["run_parent_preflight", "ControllerError", "EXPECTED_CANONICAL_HEAD", "EXPECTED_CANONICAL_TREE", "EXPECTED_MANIFEST_DIGEST", "TARGETS"]


if __name__ == "__main__":
    raise SystemExit(main())
