"""Local, sanitized evidence for bounded direct-workflow smoke checks.

The file is deliberately ignored because it represents this machine's observed
runtime state.  It stores no inputs, output paths, logs, credentials or model
metadata—only the tool name and when a completed direct job was recorded.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.shared.paths.registry import CONFIG_ROOT


STATE_PATH = CONFIG_ROOT / "tool_smoke_v3.local.json"
_LOCK = threading.RLock()

ACCEPTANCE_OPT_IN_ENV = "LOCALAIHUB_RUN_ACCEPTANCE_RUNTIME"
ACCEPTANCE_APPROVAL_ENV = "LOCALAIHUB_ACCEPTANCE_APPROVAL_PATH"
ACCEPTANCE_TEMP_ENV = "LOCALAIHUB_ACCEPTANCE_TEMP_ROOT"
ACCEPTANCE_APPROVAL_ID = "UXNW-V5-ACCEPT-RUNTIME-002-cpu-1"
ACCEPTANCE_APPROVAL_VERSION = 2
ACCEPTANCE_AUTHORIZED_BY = "Active UX, Node Workflow & Functional Smoke user goal"
ACCEPTANCE_RELEASE_BRANCH = "feature/local-ai-hub-v5"
ACCEPTANCE_RELEASE_HEAD = "c3ba39d3219778217c56f448ed69cdde3bd3bd70"
ACCEPTANCE_BRANCH = "feature/local-ai-hub-v5-lah2-accept-runtime"
ACCEPTANCE_OBJECTIVE = "One task-owned CPU FFmpeg media acceptance pipeline proving the closed opaque-artifact boundary and output lifecycle."
ACCEPTANCE_MAX_WALL_SECONDS = 60.0
ACCEPTANCE_INPUT_MAX_BYTES = 1 * 1024 * 1024
ACCEPTANCE_OVERLAY_MAX_BYTES = 128 * 1024
ACCEPTANCE_OUTPUT_MAX_BYTES = 4 * 1024 * 1024
ACCEPTANCE_OPERATIONS = ["video_grade", "logo_overlay", "encode"]
ACCEPTANCE_DIAGNOSTIC_VERSION = "logo_overlay_failure.v1"
ACCEPTANCE_DIAGNOSTIC_TAIL_BYTES = 4096
ACCEPTANCE_DIAGNOSTIC_CLASSES = (
    "filter_graph",
    "image_decode",
    "stream_mapping",
    "encoder_or_mux",
    "filesystem",
    "timeout",
    "unknown",
)
ACCEPTANCE_PROHIBITED = [
    "GPU launch",
    "model/provider/SAM2/AnimeSR/RIFE execution",
    "benchmark or stress test",
    "source overwrite",
    "raw client filesystem paths",
    "external network",
    "dependency or model installation",
    "driver/CUDA/config changes",
    "broad process termination",
]

RUNTIME_EVIDENCE_STATE_PATH = CONFIG_ROOT / "tool_smoke_runtime_evidence.v1.local.json"
RUNTIME_EVIDENCE_SCHEMA_VERSION = "tool_smoke_runtime_evidence.v1"
RUNTIME_EVIDENCE_SUBJECT = "media_overlay_cpu_acceptance"
RUNTIME_EVIDENCE_MAX_BYTES = 16 * 1024
RUNTIME_EVIDENCE_SOURCE = {
    "branch": ACCEPTANCE_BRANCH,
    "base": ACCEPTANCE_RELEASE_HEAD,
    "head": "54f954798ec21ad8fd3ffb013d450a5f5f7646ff",
    "tree": "4ecdb3f6b35155ff17b1bfb5300007abffa9a4d1",
}
RUNTIME_EVIDENCE_RUNTIME_CONTRACT = {
    "subject": RUNTIME_EVIDENCE_SUBJECT,
    "cpu_only": True,
    "wall_seconds": 60,
    "input": {
        "dimensions": "16x16",
        "duration_seconds": 1,
        "maximum_fps": 8,
        "maximum_bytes": ACCEPTANCE_INPUT_MAX_BYTES,
    },
    "overlay": {"maximum_bytes": ACCEPTANCE_OVERLAY_MAX_BYTES},
    "output": {"maximum_bytes": ACCEPTANCE_OUTPUT_MAX_BYTES},
    "operations": list(ACCEPTANCE_OPERATIONS),
    "maximum_pipeline_jobs": 1,
    "no_retry": True,
}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


RUNTIME_EVIDENCE_CONTRACT_FINGERPRINT = hashlib.sha256(_canonical_json_bytes(RUNTIME_EVIDENCE_RUNTIME_CONTRACT)).hexdigest()
RUNTIME_EVIDENCE_OUTCOMES = ("completed", "error", "unavailable", "blocked", "not_run")
RUNTIME_EVIDENCE_EXECUTIONS = ("completed", "attempted", "not_run")
RUNTIME_EVIDENCE_OPERATION_SCOPE_SCHEMA_VERSION = "runtime-operation-scope.v1"
RUNTIME_EVIDENCE_OPERATION_SCOPE = tuple(ACCEPTANCE_OPERATIONS)
_RUNTIME_EVIDENCE_RECORD_KEYS = frozenset({
    "schema_version",
    "source",
    "runtime_contract",
    "contract_fingerprint",
    "subject",
    "outcome",
    "execution",
    "failure_class",
    "operations",
    "invocation_count",
    "cleanup",
    "artifact_published",
    "source_overwrite_checked",
    "source_overwritten",
})
_RUNTIME_EVIDENCE_CLEANUP_KEYS = frozenset({"processes_remaining", "temp_cleaned"})

_ACCEPTANCE_DIAGNOSTIC_PATTERNS = (
    ("filter_graph", ("error reinitializing filters", "failed to configure output pad", "no such filter", "error while filtering")),
    ("image_decode", ("error while decoding image", "failed to decode image", "invalid png", "png: crc error", "could not decode image")),
    ("stream_mapping", ("stream map", "matches no streams", "cannot map stream", "option map")),
    ("encoder_or_mux", ("unknown encoder", "encoder not found", "could not find tag for codec", "muxer does not support", "error writing trailer", "could not write header")),
    ("filesystem", ("no such file or directory", "permission denied", "access is denied", "cannot open output file", "failed to open output")),
    ("timeout", ("timed out", "timeout", "killed by timeout")),
)


class AcceptanceFailure(RuntimeError):
    """Internal bounded acceptance failure; never expose its detail publicly."""

    def __init__(self, code: str, *, unavailable: bool = False, source_overwritten: bool | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.unavailable = unavailable
        self.source_overwritten = source_overwritten


class _AcceptanceOwner:
    """Own only child processes started by one acceptance invocation."""

    def __init__(self, deadline: float) -> None:
        self.job_id = "acceptance_cpu_media"
        self._deadline = deadline
        self._active: dict[int, Any] = {}
        self._pending_commands: dict[str, dict[str, Any]] = {}
        self._records: list[dict[str, Any]] = []

    @property
    def cancelled(self) -> bool:
        return time.monotonic() >= self._deadline

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def note_command(self, label: str, command: Any) -> None:
        values = [str(item) for item in command]
        digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()
        self._pending_commands[label] = {
            "command_digest": digest,
            "command_class": Path(values[0]).name if values else "unknown",
        }

    def attach_process(self, process: Any, label: str) -> None:
        pid = int(getattr(process, "pid", 0) or 0)
        details = self._pending_commands.pop(label, {"command_digest": None, "command_class": label})
        record = {
            "pid": pid,
            "parent_pid": os.getpid(),
            "label": label,
            **details,
            "detached": False,
            "returncode": None,
            "process": process,
        }
        self._records.append(record)
        self._active[id(process)] = process

    def detach_process(self, process: Any) -> None:
        self._active.pop(id(process), None)
        for record in self._records:
            if record.get("process") is process:
                record["detached"] = True
                record["returncode"] = process.poll()
                break

    def stop_all(self) -> None:
        from src.services.process_manager.managed import terminate_owned_process

        for process in list(self._active.values()):
            terminate_owned_process(process)
            self.detach_process(process)

    def lifecycle(self) -> list[dict[str, Any]]:
        return [
            {
                "pid": record["pid"],
                "parent_pid": record["parent_pid"],
                "label": record["label"],
                "command_class": record["command_class"],
                "command_digest": record["command_digest"],
                "detached": bool(record["detached"]),
                "returncode": record["returncode"],
            }
            for record in self._records
        ]

    def clean(self) -> bool:
        return not self._active and all(bool(record["detached"]) for record in self._records)


@contextmanager
def _temporary_attribute(target: Any, name: str, value: Any):
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


def _exact_json_value(value: Any, expected: Any) -> bool:
    """Compare approval JSON with type-sensitive, deterministic semantics."""

    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(_exact_json_value(value[key], expected[key]) for key in expected)
    if isinstance(expected, list):
        return len(value) == len(expected) and all(_exact_json_value(item, wanted) for item, wanted in zip(value, expected))
    return value == expected


def _runtime_evidence_record_valid(record: object) -> bool:
    """Validate the private evidence record without reading or writing state."""

    if type(record) is not dict or set(record) != set(_RUNTIME_EVIDENCE_RECORD_KEYS):
        return False
    if not _exact_json_value(record.get("schema_version"), RUNTIME_EVIDENCE_SCHEMA_VERSION):
        return False
    if not _exact_json_value(record.get("source"), RUNTIME_EVIDENCE_SOURCE):
        return False
    if not _exact_json_value(record.get("runtime_contract"), RUNTIME_EVIDENCE_RUNTIME_CONTRACT):
        return False
    if not _exact_json_value(record.get("contract_fingerprint"), RUNTIME_EVIDENCE_CONTRACT_FINGERPRINT):
        return False
    if not _exact_json_value(record.get("subject"), RUNTIME_EVIDENCE_SUBJECT):
        return False
    if type(record.get("outcome")) is not str or record["outcome"] not in RUNTIME_EVIDENCE_OUTCOMES:
        return False
    if type(record.get("execution")) is not str or record["execution"] not in RUNTIME_EVIDENCE_EXECUTIONS:
        return False
    failure_class = record.get("failure_class")
    if failure_class is not None and (type(failure_class) is not str or failure_class not in ACCEPTANCE_DIAGNOSTIC_CLASSES):
        return False
    if not _exact_json_value(record.get("operations"), ACCEPTANCE_OPERATIONS):
        return False
    invocation_count = record.get("invocation_count")
    if type(invocation_count) is not int or invocation_count not in {0, 1}:
        return False
    cleanup = record.get("cleanup")
    if type(cleanup) is not dict or set(cleanup) != set(_RUNTIME_EVIDENCE_CLEANUP_KEYS):
        return False
    processes_remaining = cleanup.get("processes_remaining")
    if type(processes_remaining) is not int or processes_remaining < 0:
        return False
    if type(cleanup.get("temp_cleaned")) is not bool:
        return False
    if any(type(record.get(key)) is not bool for key in ("artifact_published", "source_overwrite_checked")):
        return False
    if record["source_overwrite_checked"]:
        if type(record.get("source_overwritten")) is not bool:
            return False
    elif record.get("source_overwritten") is not None:
        return False

    outcome = record["outcome"]
    execution = record["execution"]
    if outcome == "completed":
        return (
            execution == "completed"
            and failure_class is None
            and invocation_count == 1
            and cleanup["processes_remaining"] == 0
            and cleanup["temp_cleaned"]
            and record["artifact_published"]
            and record["source_overwrite_checked"]
            and not record["source_overwritten"]
        )
    if execution == "attempted":
        return outcome in {"error", "unavailable"} and invocation_count == 1 and failure_class in ACCEPTANCE_DIAGNOSTIC_CLASSES
    if execution == "not_run":
        return outcome in {"blocked", "not_run"} and invocation_count == 0 and failure_class is None and not record["artifact_published"]
    return False


def _runtime_evidence_parent_is_safe(target: Path) -> bool:
    """Reject state paths that cross a symlink or an unexpected root."""

    if not target.is_absolute() or target.is_symlink():
        return False
    parent = target.parent
    while True:
        if parent.is_symlink():
            return False
        next_parent = parent.parent
        if next_parent == parent:
            break
        parent = next_parent
    return target.parent.is_dir()


def _no_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_runtime_evidence(path: Path | None = None) -> dict[str, Any] | None:
    """Read the private evidence file without side effects; invalid state is absent."""

    target = Path(path) if path is not None else RUNTIME_EVIDENCE_STATE_PATH
    try:
        if not _runtime_evidence_parent_is_safe(target) or not target.is_file():
            return None
        if target.stat().st_size > RUNTIME_EVIDENCE_MAX_BYTES:
            return None
        with target.open("rb") as stream:
            payload = stream.read(RUNTIME_EVIDENCE_MAX_BYTES + 1)
        if len(payload) > RUNTIME_EVIDENCE_MAX_BYTES:
            return None
        record = json.loads(payload.decode("utf-8"), object_pairs_hook=_no_duplicate_json_pairs)
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return record if _runtime_evidence_record_valid(record) else None


_RUNTIME_EVIDENCE_LOCK = threading.RLock()


def _write_runtime_evidence(record: object, *, path: Path | None = None) -> bool:
    """Atomically write only a current, bounded, fully validated evidence record."""

    if not _runtime_evidence_record_valid(record):
        return False
    try:
        payload = _canonical_json_bytes(record) + b"\n"
        if len(payload) > RUNTIME_EVIDENCE_MAX_BYTES:
            return False
        target = Path(path) if path is not None else RUNTIME_EVIDENCE_STATE_PATH
        if not _runtime_evidence_parent_is_safe(target) or not target.parent.is_dir():
            return False
    except (OSError, TypeError, ValueError):
        return False

    temporary: Path | None = None
    with _RUNTIME_EVIDENCE_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass


def _runtime_evidence_record(result: dict[str, Any]) -> dict[str, Any]:
    """Convert an already scrubbed acceptance result into the private schema."""

    execution = result.get("execution") if result.get("execution") in RUNTIME_EVIDENCE_EXECUTIONS else "not_run"
    status = result.get("status")
    if status == "completed" and execution == "completed":
        outcome = "completed"
    elif execution == "attempted" and status in {"error", "unavailable"}:
        outcome = status
    elif status == "blocked":
        outcome = "blocked"
    elif status == "not_run":
        outcome = "not_run"
    else:
        outcome = "error" if execution == "attempted" else "not_run"

    diagnostic = result.get("diagnostic")
    failure_class = None
    if isinstance(diagnostic, dict) and diagnostic.get("version") == ACCEPTANCE_DIAGNOSTIC_VERSION:
        candidate = diagnostic.get("class")
        if candidate in ACCEPTANCE_DIAGNOSTIC_CLASSES:
            failure_class = candidate
    if execution == "attempted" and failure_class is None:
        failure_class = "unknown"

    remaining = result.get("processes_remaining")
    processes_remaining = remaining if type(remaining) is int and remaining >= 0 else 1
    temp_cleaned = result.get("temp_cleaned") is True
    artifacts = result.get("artifacts")
    encoded = artifacts.get("encoded") if isinstance(artifacts, dict) else None
    artifact_published = bool(
        outcome == "completed"
        and isinstance(encoded, dict)
        and isinstance(encoded.get("id"), str)
        and encoded["id"].startswith("artifact_")
        and len(encoded["id"]) == len("artifact_") + 32
        and type(encoded.get("size_bytes")) is int
        and isinstance(encoded.get("sha256"), str)
        and len(encoded["sha256"]) == 64
    )
    source_overwrite_checked = type(result.get("source_overwritten")) is bool
    source_overwritten = result.get("source_overwritten") if source_overwrite_checked else None
    return {
        "schema_version": RUNTIME_EVIDENCE_SCHEMA_VERSION,
        "source": dict(RUNTIME_EVIDENCE_SOURCE),
        "runtime_contract": json.loads(_canonical_json_bytes(RUNTIME_EVIDENCE_RUNTIME_CONTRACT).decode("utf-8")),
        "contract_fingerprint": RUNTIME_EVIDENCE_CONTRACT_FINGERPRINT,
        "subject": RUNTIME_EVIDENCE_SUBJECT,
        "outcome": outcome,
        "execution": execution,
        "failure_class": failure_class,
        "operations": list(ACCEPTANCE_OPERATIONS),
        "invocation_count": 1 if execution in {"completed", "attempted"} else 0,
        "cleanup": {"processes_remaining": processes_remaining, "temp_cleaned": temp_cleaned},
        "artifact_published": artifact_published,
        "source_overwrite_checked": source_overwrite_checked,
        "source_overwritten": source_overwritten,
    }


def _persist_runtime_evidence(result: dict[str, Any]) -> dict[str, Any]:
    """Persist after the caller's cleanup while preserving the actual result."""

    try:
        _write_runtime_evidence(_runtime_evidence_record(result))
    except (OSError, TypeError, ValueError):
        pass
    return result


def runtime_evidence_passed(record: object | None = None, *, path: Path | None = None) -> bool:
    """Strictly verify a completed exact evidence record for future promotion."""

    candidate = read_runtime_evidence(path) if record is None else record
    if not _runtime_evidence_record_valid(candidate):
        return False
    return (
        candidate["outcome"] == "completed"
        and candidate["execution"] == "completed"
        and candidate["invocation_count"] == 1
        and candidate["failure_class"] is None
        and candidate["cleanup"]["processes_remaining"] == 0
        and candidate["cleanup"]["temp_cleaned"]
        and candidate["artifact_published"]
        and candidate["source_overwrite_checked"]
        and not candidate["source_overwritten"]
    )


def runtime_evidence_operation_scope(record: object | None = None, *, path: Path | None = None) -> dict[str, Any]:
    """Project strict evidence into the exact operation scope it can support.

    This is a read-only server-owned projection.  A completed record can only
    mark the three operations in ``ACCEPTANCE_OPERATIONS`` operational; it can
    never promote the generic media tool or an unrelated Node Studio node.
    """

    candidate = read_runtime_evidence(path) if record is None else record
    verified = runtime_evidence_passed(candidate)
    operation_status = {
        operation: "operational" if verified else "partial"
        for operation in RUNTIME_EVIDENCE_OPERATION_SCOPE
    }
    if verified:
        return {
            "schema_version": RUNTIME_EVIDENCE_OPERATION_SCOPE_SCHEMA_VERSION,
            "subject": RUNTIME_EVIDENCE_SUBJECT,
            "status": "operational",
            "execution": "completed",
            "evidence_verified": True,
            "operations": list(RUNTIME_EVIDENCE_OPERATION_SCOPE),
            "available_operations": list(RUNTIME_EVIDENCE_OPERATION_SCOPE),
            "operation_status": operation_status,
            "reason": "A completed exact evidence record covers only the listed media operations.",
            "next_action": "Use only the listed operations with opaque artifacts; keep all other media tools partial.",
        }
    return {
        "schema_version": RUNTIME_EVIDENCE_OPERATION_SCOPE_SCHEMA_VERSION,
        "subject": RUNTIME_EVIDENCE_SUBJECT,
        "status": "unavailable",
        "execution": "not_run",
        "evidence_verified": False,
        "operations": list(RUNTIME_EVIDENCE_OPERATION_SCOPE),
        "available_operations": [],
        "operation_status": operation_status,
        "reason": "No completed exact evidence is available for the listed media operations.",
        "next_action": "Keep the listed operations and all other media tools partial until separately evidenced.",
    }


def _runtime_evidence_unavailable_projection() -> dict[str, Any]:
    return {
        "schema_version": "runtime-evidence-projection.v1",
        "subject": RUNTIME_EVIDENCE_SUBJECT,
        "operations": list(ACCEPTANCE_OPERATIONS),
        "status": "unavailable",
        "outcome": "not_run",
        "execution": "not_run",
        "failure_class": None,
        "invocation_count": 0,
        "cleanup": {"processes_remaining": 0, "temp_cleaned": True},
        "artifact_published": False,
        "source_overwrite_checked": False,
        "source_overwritten": None,
        "reason": "No bounded media acceptance evidence is available.",
        "next_action": "Keep media operations partial until a separately authorized bounded acceptance completes.",
    }


def runtime_evidence_projection(*, path: Path | None = None) -> dict[str, Any]:
    """Return a safe local-only summary; reading never writes or starts runtime work."""

    record = read_runtime_evidence(path)
    if not _runtime_evidence_record_valid(record):
        return _runtime_evidence_unavailable_projection()
    cleanup = {
        "processes_remaining": 0 if record["cleanup"]["processes_remaining"] == 0 else 1,
        "temp_cleaned": record["cleanup"]["temp_cleaned"],
    }
    if runtime_evidence_passed(record):
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": RUNTIME_EVIDENCE_SUBJECT,
            "operations": list(ACCEPTANCE_OPERATIONS),
            "status": "operational",
            "outcome": "completed",
            "execution": "completed",
            "failure_class": None,
            "invocation_count": 1,
            "cleanup": cleanup,
            "artifact_published": True,
            "source_overwrite_checked": True,
            "source_overwritten": record["source_overwritten"],
            "reason": "A bounded media acceptance completed for the approved source.",
            "next_action": "Use the existing allowlisted media operations with opaque artifacts.",
        }
    if record["execution"] == "attempted" and record["outcome"] in {"error", "unavailable"}:
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": RUNTIME_EVIDENCE_SUBJECT,
            "operations": list(ACCEPTANCE_OPERATIONS),
            "status": "unavailable",
            "outcome": "error",
            "execution": "attempted",
            "failure_class": record["failure_class"] if record["failure_class"] in ACCEPTANCE_DIAGNOSTIC_CLASSES else "unknown",
            "invocation_count": 1,
            "cleanup": cleanup,
            "artifact_published": False,
            "source_overwrite_checked": record["source_overwrite_checked"],
            "source_overwritten": record["source_overwritten"],
            "reason": "The last bounded media acceptance stopped before a publishable output.",
            "next_action": "Keep media operations partial; request a new exact-source approval before any future attempt.",
        }
    if record["execution"] == "not_run" and record["outcome"] in {"blocked", "not_run"}:
        if record["outcome"] == "blocked":
            reason = "The bounded media acceptance was blocked before execution."
            next_action = "Keep media operations partial; obtain a fresh exact-source approval before any attempt."
        else:
            reason = "No bounded media acceptance invocation was recorded."
            next_action = "Keep media operations partial until a separately authorized bounded acceptance is recorded."
        return {
            "schema_version": "runtime-evidence-projection.v1",
            "subject": RUNTIME_EVIDENCE_SUBJECT,
            "operations": list(ACCEPTANCE_OPERATIONS),
            "status": "unavailable",
            "outcome": record["outcome"],
            "execution": "not_run",
            "failure_class": None,
            "invocation_count": 0,
            "cleanup": cleanup,
            "artifact_published": False,
            "source_overwrite_checked": record["source_overwrite_checked"],
            "source_overwritten": record["source_overwritten"],
            "reason": reason,
            "next_action": next_action,
        }
    return _runtime_evidence_unavailable_projection()


def _is_git_identity(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def _approval_guard(approval_path: Path, repo_root: Path) -> tuple[bool, str]:
    try:
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "approval_unavailable"
    if not isinstance(approval, dict):
        return False, "approval_invalid"
    release = approval.get("release") if isinstance(approval.get("release"), dict) else {}
    source = approval.get("source") if isinstance(approval.get("source"), dict) else {}
    fixed_scopes = (
        ("release", release, {"branch": ACCEPTANCE_RELEASE_BRANCH, "head": ACCEPTANCE_RELEASE_HEAD}),
        ("allowed_input", approval.get("allowed_input"), {
            "kind": "synthetic task-owned video",
            "maximum_dimensions": "16x16",
            "maximum_duration_seconds": 1,
            "maximum_fps": 8,
            "maximum_bytes": ACCEPTANCE_INPUT_MAX_BYTES,
        }),
        ("allowed_output", approval.get("allowed_output"), {
            "maximum_bytes": ACCEPTANCE_OUTPUT_MAX_BYTES,
            "operations": ACCEPTANCE_OPERATIONS,
            "maximum_pipeline_jobs": 1,
        }),
        ("limits", approval.get("limits"), {
            "wall_seconds": 60,
            "no_retry": True,
            "temporary_root": "task-owned only",
            "stop_authority": "manager or user",
            "cleanup_owner": "LAH 2",
        }),
        ("preflight", approval.get("preflight"), {
            "require_existing_canonical_ffmpeg": True,
            "require_no_download_or_install": True,
            "require_free_space_check": True,
            "require_no_interference_with_user_owned_processes": True,
            "require_opaque_artifact_ids": True,
        }),
        ("gpu", approval.get("gpu"), {
            "status": "not_authorized_by_this_approval",
            "next_action": "Read-only RTX 4060/runtime/checkpoint preflight may be reported for a separately bound approval.",
        }),
    )
    if (
        not _exact_json_value(approval.get("approval_id"), ACCEPTANCE_APPROVAL_ID)
        or not _exact_json_value(approval.get("approval_version"), ACCEPTANCE_APPROVAL_VERSION)
        or approval.get("status") != "approved"
        or not _exact_json_value(approval.get("authorized_by"), ACCEPTANCE_AUTHORIZED_BY)
        or not _exact_json_value(approval.get("owner"), "LAH 2")
        or not _exact_json_value(approval.get("objective"), ACCEPTANCE_OBJECTIVE)
        or any(not _exact_json_value(value, expected) for _name, value, expected in fixed_scopes)
        or not _exact_json_value(approval.get("prohibited"), ACCEPTANCE_PROHIBITED)
        or set(source) != {"branch", "base", "head", "tree"}
        or source.get("branch") != ACCEPTANCE_BRANCH
        or source.get("base") != ACCEPTANCE_RELEASE_HEAD
        or not _is_git_identity(source.get("head"))
        or not _is_git_identity(source.get("tree"))
    ):
        return False, "approval_contract_mismatch"
    try:
        branch_result = subprocess.run(["git", "branch", "--show-current"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        head_result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        tree_result = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        status_result = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        merge_base = subprocess.run(["git", "merge-base", source["base"], "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
        ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", ACCEPTANCE_RELEASE_HEAD, "HEAD"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False, "git_guard_unavailable"
    if (
        branch_result.returncode != 0
        or head_result.returncode != 0
        or tree_result.returncode != 0
        or status_result.returncode != 0
        or branch_result.stdout.strip() != source["branch"]
        or head_result.stdout.strip() != source["head"]
        or tree_result.stdout.strip() != source["tree"]
        or status_result.stdout.strip()
        or merge_base.returncode != 0
        or merge_base.stdout.strip() != source["base"]
        or ancestry.returncode != 0
    ):
        return False, "git_guard_mismatch"
    return True, "ok"


def _artifact_evidence(artifact: dict[str, Any], *, maximum_bytes: int) -> dict[str, Any]:
    artifact_id = artifact.get("id")
    size = artifact.get("size_bytes")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("artifact_") or not isinstance(size, int) or size < 0 or size > maximum_bytes:
        raise AcceptanceFailure("artifact_bound_exceeded")
    return {
        "id": artifact_id,
        "media_type": str(artifact.get("media_type") or "application/octet-stream"),
        "size_bytes": size,
        "sha256": artifact.get("sha256"),
    }


def _tracked_run_hidden(owner: _AcceptanceOwner):
    from src.services.process_manager.managed import terminate_owned_process
    from src.services.process_manager.windows import popen_hidden

    def run_hidden(command: Any, *, cwd: Any = None, env: dict[str, str] | None = None, timeout: float | None = None, check: bool = False, capture_output: bool = False, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        label = "ffmpeg_capability"
        owner.note_command(label, command)
        if owner.remaining() <= 0:
            raise subprocess.TimeoutExpired(command, 0)
        if capture_output:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
        process = popen_hidden(command, cwd=cwd, env=env, **kwargs)
        owner.attach_process(process, label)
        try:
            effective_timeout = owner.remaining() if timeout is None else min(float(timeout), owner.remaining())
            try:
                stdout, stderr = process.communicate(timeout=max(0.01, effective_timeout))
            except subprocess.TimeoutExpired:
                terminate_owned_process(process)
                raise
            result = subprocess.CompletedProcess([str(item) for item in command], process.returncode or 0, stdout, stderr)
        finally:
            owner.detach_process(process)
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args, result.stdout, result.stderr)
        return result

    return run_hidden


def _register_output(path: Path, *, media_type: str, maximum_bytes: int, artifact_store: Any) -> dict[str, Any]:
    try:
        resolved = path.resolve()
        size = resolved.stat().st_size
        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    except OSError:
        raise AcceptanceFailure("output_unreadable") from None
    if size > maximum_bytes:
        raise AcceptanceFailure("output_bound_exceeded")
    artifact = artifact_store.register_path(resolved, media_type=media_type, sha256=digest)
    if not isinstance(artifact, dict):
        raise AcceptanceFailure("output_registration_failed")
    return _artifact_evidence(artifact, maximum_bytes=maximum_bytes)


def _classify_logo_overlay_failure(task_root: Path) -> dict[str, str]:
    """Return only a versioned safe class from the exact task-owned log tail."""

    unknown = {"version": ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"}
    try:
        root = task_root.resolve()
        logs = root / "logs"
        workers = logs / "workers"
        expected_logs = root / "logs"
        expected_workers = expected_logs / "workers"
        if logs.is_symlink() or workers.is_symlink():
            return unknown
        logs = logs.resolve()
        workers = workers.resolve()
        if logs != expected_logs or workers != expected_workers:
            return unknown
        log_path = (workers / "ffmpeg_logo_overlay_acceptance_cpu_media.log")
        if log_path.is_symlink():
            return unknown
        log_path = log_path.resolve()
        log_path.relative_to(root)
        if log_path.parent != expected_workers or not log_path.is_file():
            return unknown
        with log_path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - ACCEPTANCE_DIAGNOSTIC_TAIL_BYTES), os.SEEK_SET)
            tail = stream.read(ACCEPTANCE_DIAGNOSTIC_TAIL_BYTES)
    except (OSError, ValueError, RuntimeError):
        return unknown
    text = tail.decode("utf-8", errors="replace").lower()
    for class_name, patterns in _ACCEPTANCE_DIAGNOSTIC_PATTERNS:
        if any(pattern in text for pattern in patterns):
            return {"version": ACCEPTANCE_DIAGNOSTIC_VERSION, "class": class_name}
    return unknown


def _run_cpu_pipeline(task_root: Path, ffmpeg: Path, owner: _AcceptanceOwner) -> dict[str, Any]:
    from PIL import Image

    from src.modules.media_editor.backend import adapter
    from src.services import artifact_store
    from src.services.process_manager import managed

    upload_root = task_root / "uploads"
    output_root = task_root / "outputs"
    log_root = task_root / "logs"
    index_path = task_root / "artifacts.json"
    for directory in (upload_root, output_root, log_root):
        directory.mkdir(parents=True, exist_ok=True)
    seed_path = task_root / "seed.mp4"
    overlay_path = task_root / "overlay.png"
    seed_command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=blue:s=16x16:r=4", "-t", "1",
        "-pix_fmt", "yuv420p", "-an", "-c:v", "libx264", str(seed_path),
    ]
    owner.note_command("acceptance_seed", seed_command)
    code, _output = managed.run_command(seed_command, label="acceptance_seed", owner=owner, timeout_seconds=min(15.0, owner.remaining()))
    if code != 0 or not seed_path.is_file():
        raise AcceptanceFailure("synthetic_seed_failed", unavailable=code == -1)
    if seed_path.stat().st_size > ACCEPTANCE_INPUT_MAX_BYTES:
        raise AcceptanceFailure("input_bound_exceeded")
    Image.new("RGBA", (4, 4), (255, 32, 32, 220)).save(overlay_path, format="PNG")
    if overlay_path.stat().st_size > ACCEPTANCE_OVERLAY_MAX_BYTES:
        raise AcceptanceFailure("overlay_bound_exceeded")
    source_before = hashlib.sha256(seed_path.read_bytes()).hexdigest()

    original_run_command = adapter.run_command

    def tracked_run_command(command: Any, *, label: str, **kwargs: Any):
        owner.note_command(label, command)
        return original_run_command(command, label=label, **kwargs)

    with (
        _temporary_attribute(artifact_store, "UPLOAD_ROOT", upload_root),
        _temporary_attribute(artifact_store, "OUTPUT_ROOT", output_root),
        _temporary_attribute(artifact_store, "INDEX_PATH", index_path),
        _temporary_attribute(adapter, "OUTPUT_ROOT", output_root),
        _temporary_attribute(adapter, "TEMP_ROOT", task_root / "temp"),
        _temporary_attribute(managed, "LOG_ROOT", log_root),
        _temporary_attribute(adapter, "run_hidden", _tracked_run_hidden(owner)),
        _temporary_attribute(adapter, "run_command", tracked_run_command),
    ):
        uploaded = artifact_store.stage_upload("seed.mp4", seed_path.read_bytes(), "video/mp4")
        overlay = artifact_store.stage_upload("overlay.png", overlay_path.read_bytes(), "image/png")
        source_evidence = _artifact_evidence(uploaded, maximum_bytes=ACCEPTANCE_INPUT_MAX_BYTES)
        overlay_evidence = _artifact_evidence(overlay, maximum_bytes=ACCEPTANCE_OVERLAY_MAX_BYTES)
        source_artifact_path = artifact_store.resolve(source_evidence["id"])
        if source_artifact_path is None:
            raise AcceptanceFailure("source_artifact_unavailable")
        source_artifact_before = hashlib.sha256(source_artifact_path.read_bytes()).hexdigest()

        def run_closed(operation: str, payload: dict[str, Any], media_type: str) -> dict[str, Any]:
            result = adapter.run_operation(payload, owner)
            if result.get("status") != "completed":
                raise AcceptanceFailure(f"{operation}_failed", unavailable=result.get("status") == "unavailable")
            output_value = result.get("output")
            if not isinstance(output_value, str):
                raise AcceptanceFailure(f"{operation}_missing_output")
            output_path = Path(output_value)
            try:
                output_path.resolve().relative_to(output_root.resolve())
            except (OSError, ValueError):
                raise AcceptanceFailure(f"{operation}_output_scope") from None
            return _register_output(output_path, media_type=media_type, maximum_bytes=ACCEPTANCE_OUTPUT_MAX_BYTES, artifact_store=artifact_store)

        graded = run_closed(
            "video_grade",
            {
                "operation": "video_grade",
                "source_artifact_id": source_evidence["id"],
                "brightness": 0.1,
                "contrast": 1.05,
                "saturation": 1.0,
                "gamma": 1.0,
                "denoise": "off",
                "sharpen": "off",
            },
            "video/mp4",
        )
        overlaid = run_closed(
            "logo_overlay",
            {
                "operation": "logo_overlay",
                "source_artifact_id": graded["id"],
                "overlay_artifact_id": overlay_evidence["id"],
                "position": "bottom_right",
                "opacity": 0.5,
            },
            "video/mp4",
        )
        encoded_source = artifact_store.resolve(overlaid["id"])
        if encoded_source is None:
            raise AcceptanceFailure("encode_source_unavailable")
        encode_result = adapter.run_operation(
            {
                "operation": "encode",
                "path": str(encoded_source),
                "container": "mp4",
                "codec": "libx264",
                "prefer_gpu": False,
                "preset": "ultrafast",
                "rate_control": "quality",
                "quality": 23,
                "timeout_seconds": min(20.0, owner.remaining()),
            },
            owner,
        )
        if encode_result.get("status") != "completed":
            raise AcceptanceFailure("encode_unavailable" if encode_result.get("status") == "unavailable" else "encode_failed", unavailable=encode_result.get("status") == "unavailable")
        encoded_value = encode_result.get("output")
        if not isinstance(encoded_value, str):
            raise AcceptanceFailure("encode_missing_output")
        encoded_path = Path(encoded_value)
        try:
            encoded_path.resolve().relative_to(output_root.resolve())
        except (OSError, ValueError):
            raise AcceptanceFailure("encode_output_scope") from None
        encoded = _register_output(encoded_path, media_type="video/mp4", maximum_bytes=ACCEPTANCE_OUTPUT_MAX_BYTES, artifact_store=artifact_store)
        if hashlib.sha256(seed_path.read_bytes()).hexdigest() != source_before or hashlib.sha256(source_artifact_path.read_bytes()).hexdigest() != source_artifact_before:
            raise AcceptanceFailure("source_overwritten", source_overwritten=True)
        return {
            "pipeline": ["video_grade", "logo_overlay", "encode"],
            "artifacts": {"source": source_evidence, "overlay": overlay_evidence, "graded": graded, "overlaid": overlaid, "encoded": encoded},
            "source_overwritten": False,
        }


def run_cpu_media_acceptance(*, opt_in: bool | None = None, approval_path: Path | None = None, task_root: Path | None = None, repo_root: Path | None = None) -> dict[str, Any]:
    """Run exactly one approved CPU media pipeline when explicitly opted in."""

    enabled = os.environ.get(ACCEPTANCE_OPT_IN_ENV) == "1" if opt_in is None else bool(opt_in)
    if not enabled:
        return {"status": "not_run", "execution": "not_run", "reason": "CPU acceptance opt-in is not enabled."}
    repo = repo_root or Path(__file__).resolve().parents[2]
    approval = approval_path or (Path(os.environ[ACCEPTANCE_APPROVAL_ENV]) if os.environ.get(ACCEPTANCE_APPROVAL_ENV) else None)
    root = task_root or (Path(os.environ[ACCEPTANCE_TEMP_ENV]) if os.environ.get(ACCEPTANCE_TEMP_ENV) else None)
    if approval is None or root is None:
        return {"status": "blocked", "execution": "not_run", "reason": "Acceptance approval and task-owned temp root are required."}
    if not root.is_absolute():
        return {"status": "blocked", "execution": "not_run", "failure_code": "task_root_not_absolute", "reason": "Task-owned temp root must be absolute."}
    ok, guard_code = _approval_guard(approval, repo)
    if not ok:
        return {"status": "blocked", "execution": "not_run", "failure_code": guard_code, "reason": "Acceptance approval or exact branch guard did not pass."}
    if root.exists():
        return {"status": "blocked", "execution": "not_run", "failure_code": "task_root_exists", "reason": "Task-owned temp root must not pre-exist."}
    try:
        from src.modules.media_editor.backend import adapter

        ffmpeg, ffprobe = adapter._paths()
    except (OSError, TypeError, ValueError):
        ffmpeg, ffprobe = None, None
    if ffmpeg is None or not ffmpeg.is_file() or ffprobe is None or not ffprobe.is_file():
        return {"status": "unavailable", "execution": "not_run", "failure_code": "canonical_ffmpeg_unavailable", "reason": "Canonical FFmpeg and FFprobe are not both configured and present."}
    try:
        free_bytes = shutil.disk_usage(root.parent).free
    except OSError:
        return {"status": "blocked", "execution": "not_run", "failure_code": "disk_check_failed", "reason": "Free-space preflight could not be completed."}
    if free_bytes <= ACCEPTANCE_OUTPUT_MAX_BYTES + ACCEPTANCE_INPUT_MAX_BYTES + ACCEPTANCE_OVERLAY_MAX_BYTES:
        return {"status": "blocked", "execution": "not_run", "failure_code": "disk_space_low", "reason": "Free-space preflight did not leave the bounded safety margin."}
    started = time.monotonic()
    owner: _AcceptanceOwner | None = None
    lifecycle: list[dict[str, Any]] = []
    cleaned = False
    created = False
    try:
        root.mkdir(parents=True, exist_ok=False)
        created = True
        owner = _AcceptanceOwner(started + ACCEPTANCE_MAX_WALL_SECONDS)
        evidence = _run_cpu_pipeline(root, ffmpeg, owner)
        lifecycle = owner.lifecycle()
        owner.stop_all()
        cleaned = True
        shutil.rmtree(root)
        return _persist_runtime_evidence({"status": "completed", "execution": "completed", "approval_id": ACCEPTANCE_APPROVAL_ID, "release_head": ACCEPTANCE_RELEASE_HEAD, "wall_seconds": round(time.monotonic() - started, 3), "process_lifecycle": lifecycle, "processes_remaining": 0 if owner.clean() else len(lifecycle), "temp_cleaned": cleaned, **evidence})
    except AcceptanceFailure as exc:
        if owner is not None:
            owner.stop_all()
            lifecycle = owner.lifecycle()
        diagnostic = _classify_logo_overlay_failure(root) if exc.code == "logo_overlay_failed" else None
        if created and root.exists():
            try:
                shutil.rmtree(root)
            except OSError:
                pass
        cleaned = created and not root.exists()
        result = {"status": "unavailable" if exc.unavailable else "error", "execution": "attempted", "failure_code": exc.code, "reason": "CPU acceptance pipeline did not complete.", "process_lifecycle": lifecycle, "processes_remaining": 0 if owner is None or owner.clean() else 1, "temp_cleaned": cleaned}
        if exc.source_overwritten is not None:
            result["source_overwritten"] = exc.source_overwritten
        if diagnostic is not None:
            result["diagnostic"] = diagnostic
        return _persist_runtime_evidence(result)
    except (OSError, subprocess.SubprocessError, ValueError):
        if owner is not None:
            owner.stop_all()
            lifecycle = owner.lifecycle()
        if created and root.exists():
            try:
                shutil.rmtree(root)
            except OSError:
                pass
        cleaned = created and not root.exists()
        return _persist_runtime_evidence({"status": "error", "execution": "attempted", "failure_code": "acceptance_internal_error", "reason": "CPU acceptance pipeline did not complete.", "process_lifecycle": lifecycle, "processes_remaining": 0 if owner is None or owner.clean() else 1, "temp_cleaned": cleaned})
    finally:
        if owner is not None:
            owner.stop_all()
        if created and root.exists():
            try:
                shutil.rmtree(root)
                cleaned = not root.exists()
            except OSError:
                cleaned = False


def _load() -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def record_completed(tool: str) -> None:
    """Persist the outcome of one completed direct job without private data."""

    if not tool:
        return
    with _LOCK:
        state = _load()
        state[tool] = {
            "status": "completed",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "evidence": "bounded_direct_job",
        }
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(STATE_PATH)


def passed(tool: str) -> bool:
    with _LOCK:
        value = _load().get(tool)
    return isinstance(value, dict) and value.get("status") == "completed" and value.get("evidence") == "bounded_direct_job"
