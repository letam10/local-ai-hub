"""Read-only integrity guard for the protected LocalAIHub canonical checkout.

The guard is deliberately a control-plane boundary.  It can inspect the
canonical checkout, verify an explicitly issued disposable-worktree lease,
and return a decision.  It never owns a cleanup executor and never performs a
Git mutation, filesystem removal, process termination, or branch operation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit


CANONICAL_ROOT = Path(r"D:\LocalAIHub")
CANONICAL_POLICY_PATH = r"D:\LocalAIHub"
CANONICAL_DESIGNATION = "localaihub-canonical"
FORENSIC_SNAPSHOT_PATH = CANONICAL_ROOT / "Reports" / "canonical_git_integrity.local.json"
SCHEMA_VERSION = "canonical-git-integrity.v1"
MAX_GIT_OUTPUT_BYTES = 32 * 1024
MAX_FORENSIC_BYTES = 64 * 1024
MAX_FORENSIC_EVENTS = 32
GIT_TIMEOUT_SECONDS = 3.0

CODE_OK = "CANONICAL_OK"
CODE_ROOT_MISSING = "CANONICAL_ROOT_MISSING"
CODE_ROOT_INVALID = "CANONICAL_ROOT_INVALID"
CODE_ROOT_LINKED = "CANONICAL_ROOT_LINKED"
CODE_GIT_MISSING = "CANONICAL_GIT_MISSING"
CODE_GIT_INVALID = "CANONICAL_GIT_INVALID"
CODE_GIT_LINKED = "CANONICAL_GIT_LINKED"
CODE_TOP_LEVEL_MISMATCH = "CANONICAL_TOP_LEVEL_MISMATCH"
CODE_COMMON_DIR_MISMATCH = "CANONICAL_COMMON_DIR_MISMATCH"
CODE_GIT_QUERY_FAILED = "CANONICAL_GIT_QUERY_FAILED"
CODE_GIT_UNAVAILABLE = "CANONICAL_GIT_UNAVAILABLE"
CODE_GIT_TIMEOUT = "CANONICAL_GIT_TIMEOUT"
CODE_GIT_OUTPUT_OVERSIZE = "CANONICAL_GIT_OUTPUT_OVERSIZE"
CODE_METADATA_AMBIGUOUS = "CANONICAL_METADATA_AMBIGUOUS"
CODE_HEAD_INVALID = "CANONICAL_HEAD_INVALID"
CODE_BRANCH_INVALID = "CANONICAL_BRANCH_INVALID"
CODE_ORIGIN_MISSING = "CANONICAL_ORIGIN_MISSING"
CODE_ORIGIN_INVALID = "CANONICAL_ORIGIN_INVALID"
CODE_ORIGIN_CREDENTIALS = "CANONICAL_ORIGIN_CREDENTIALS"
CODE_ORIGIN_UNALLOWLISTED = "CANONICAL_ORIGIN_UNALLOWLISTED"
CODE_ORIGIN_PUSH_MISMATCH = "CANONICAL_ORIGIN_PUSH_MISMATCH"
CODE_WORKTREE_DIRTY = "CANONICAL_PRESERVATION_REQUIRED"
CODE_SNAPSHOT_INVALID = "FORENSIC_SNAPSHOT_INVALID"
CODE_SNAPSHOT_WRITE_FAILED = "FORENSIC_SNAPSHOT_WRITE_FAILED"
CODE_LEASE_INVALID = "WORKTREE_LEASE_INVALID"
CODE_TARGET_MISSING = "WORKTREE_TARGET_MISSING"
CODE_TARGET_NOT_DIRECTORY = "WORKTREE_TARGET_NOT_DIRECTORY"
CODE_TARGET_LINKED = "WORKTREE_TARGET_LINKED"
CODE_TARGET_RELATION_UNSAFE = "WORKTREE_TARGET_RELATION_UNSAFE"
CODE_TARGET_GITLINK_INVALID = "WORKTREE_GITLINK_INVALID"
CODE_TARGET_COMMON_DIR_MISMATCH = "WORKTREE_COMMON_DIR_MISMATCH"
CODE_COMMON_DIR_PROTECTED = "CANONICAL_COMMON_DIR_PROTECTED"
CODE_TARGET_TOP_LEVEL_MISMATCH = "WORKTREE_TOP_LEVEL_MISMATCH"
CODE_TARGET_BRANCH_MISMATCH = "WORKTREE_BRANCH_MISMATCH"
CODE_TARGET_HEAD_INVALID = "WORKTREE_HEAD_INVALID"
CODE_TARGET_BASE_INVALID = "WORKTREE_BASE_INVALID"
CODE_TARGET_BASE_NOT_ANCESTOR = "WORKTREE_BASE_NOT_ANCESTOR"
CODE_TARGET_DIRTY = "WORKTREE_PRESERVATION_REQUIRED"
CODE_PROCESS_PRESENT = "WORKTREE_OWNED_PROCESS_PRESENT"
CODE_PROCESS_UNKNOWN = "WORKTREE_OWNED_PROCESS_UNKNOWN"

_ALL_CODES = frozenset({
    CODE_OK,
    CODE_ROOT_MISSING,
    CODE_ROOT_INVALID,
    CODE_ROOT_LINKED,
    CODE_GIT_MISSING,
    CODE_GIT_INVALID,
    CODE_GIT_LINKED,
    CODE_TOP_LEVEL_MISMATCH,
    CODE_COMMON_DIR_MISMATCH,
    CODE_GIT_QUERY_FAILED,
    CODE_GIT_UNAVAILABLE,
    CODE_GIT_TIMEOUT,
    CODE_GIT_OUTPUT_OVERSIZE,
    CODE_METADATA_AMBIGUOUS,
    CODE_HEAD_INVALID,
    CODE_BRANCH_INVALID,
    CODE_ORIGIN_MISSING,
    CODE_ORIGIN_INVALID,
    CODE_ORIGIN_CREDENTIALS,
    CODE_ORIGIN_UNALLOWLISTED,
    CODE_ORIGIN_PUSH_MISMATCH,
    CODE_WORKTREE_DIRTY,
    CODE_SNAPSHOT_INVALID,
    CODE_SNAPSHOT_WRITE_FAILED,
    CODE_LEASE_INVALID,
    CODE_TARGET_MISSING,
    CODE_TARGET_NOT_DIRECTORY,
    CODE_TARGET_LINKED,
    CODE_TARGET_RELATION_UNSAFE,
    CODE_TARGET_GITLINK_INVALID,
    CODE_TARGET_COMMON_DIR_MISMATCH,
    CODE_COMMON_DIR_PROTECTED,
    CODE_TARGET_TOP_LEVEL_MISMATCH,
    CODE_TARGET_BRANCH_MISMATCH,
    CODE_TARGET_HEAD_INVALID,
    CODE_TARGET_BASE_INVALID,
    CODE_TARGET_BASE_NOT_ANCESTOR,
    CODE_TARGET_DIRTY,
    CODE_PROCESS_PRESENT,
    CODE_PROCESS_UNKNOWN,
})

_SAFE_BRANCH = re.compile(r"(?:feature|fix|docs|test|release)/[A-Za-z0-9][A-Za-z0-9._/-]{0,119}\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_OUTPUT_STATES = frozenset({"directory", "missing", "invalid", "linked", "valid", "safe", "allowlisted", "clean", "dirty", "unknown"})
_SNAPSHOT_KEYS = frozenset({"schema_version", "canonical_designation", "canonical_path", "events"})
_EVENT_KEYS = frozenset({
    "timestamp",
    "git_state",
    "head_state",
    "head_fingerprint",
    "branch_state",
    "origin_state",
    "origin_fingerprint",
    "worktree_state",
    "operation_code",
    "dirty",
    "event_type",
    "operation",
    "outcome",
    "target_kind",
})
_EVENT_TYPES = frozenset({"preflight", "assignment_decision", "cleanup_decision"})
_EVENT_OPERATIONS = frozenset({"canonical_preflight", "canonical_assignment", "owned_worktree_cleanup"})
_EVENT_OUTCOMES = frozenset({"ok", "refused", "preservation_required", "error"})
_EVENT_TARGET_KINDS = frozenset({"canonical", "disposable_worktree"})
_ALLOWED_GIT_COMMANDS = frozenset({"rev-parse", "symbolic-ref", "config", "status", "merge-base"})
_LEASE_TOKEN = object()
_SNAPSHOT_LOCK = threading.RLock()


@dataclass(frozen=True)
class _GitResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    code: str | None = None


@dataclass(frozen=True)
class OwnedTemporaryWorktree:
    """An in-memory lease issued by the trusted manager for one worktree."""

    target: Path
    branch: str
    base: str
    common_git_dir: Path
    _token: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class OwnedProcessProbe:
    """Positive, injected proof that this lease owns zero live processes."""

    known: bool
    owned_count: int
    scope: str = "owned-temporary-worktree"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_branch(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(_SAFE_BRANCH.fullmatch(value))
        and value not in {"feature/main", "feature/master"}
        and ".." not in value
        and "@{" not in value
        and "//" not in value
        and not value.endswith(("/", "."))
        and not value.endswith(".lock")
    )


def _is_link_or_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        attributes = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        return bool(attributes & 0x400)
    except OSError:
        return False


def _has_link_or_reparse_component(path: Path) -> bool:
    current = path
    while True:
        if _is_link_or_reparse(current):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _resolved(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _same_path(left: Path, right: Path) -> bool:
    left_resolved = _resolved(left)
    right_resolved = _resolved(right)
    if left_resolved is None or right_resolved is None:
        return False
    return os.path.normcase(str(left_resolved)) == os.path.normcase(str(right_resolved))


def _path_is_inside(path: Path, parent: Path) -> bool:
    try:
        _resolved(path).relative_to(_resolved(parent))  # type: ignore[union-attr]
        return True
    except (AttributeError, ValueError, TypeError):
        return False


def _protected_common_dir_relation(path: Path) -> bool | None:
    """Return True for canonical relations, False for safe, None if uncertain."""

    if not isinstance(path, Path) or not path.is_absolute() or _has_link_or_reparse_component(path):
        return None
    resolved_path = _resolved(path)
    if resolved_path is None:
        return None
    for protected in (CANONICAL_ROOT, CANONICAL_ROOT / ".git"):
        resolved_protected = _resolved(protected)
        if resolved_protected is None:
            return None
        if (
            _same_path(resolved_path, resolved_protected)
            or _path_is_inside(resolved_path, resolved_protected)
            or _path_is_inside(resolved_protected, resolved_path)
        ):
            return True
    return False


def _public_result(
    code: str,
    *,
    git_state: str = "unknown",
    head_state: str = "unknown",
    head_fingerprint: str | None = None,
    branch_state: str = "unknown",
    origin_state: str = "unknown",
    origin_fingerprint: str | None = None,
    worktree_state: str = "unknown",
    dirty: bool = False,
) -> dict[str, Any]:
    safe_code = code if code in _ALL_CODES else CODE_GIT_QUERY_FAILED
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_designation": CANONICAL_DESIGNATION,
        "git_state": git_state if git_state in _SAFE_OUTPUT_STATES else "unknown",
        "head_state": head_state if head_state in _SAFE_OUTPUT_STATES else "unknown",
        "head_fingerprint": head_fingerprint if isinstance(head_fingerprint, str) and _HEX64.fullmatch(head_fingerprint) else None,
        "branch_state": branch_state if branch_state in _SAFE_OUTPUT_STATES else "unknown",
        "origin_state": origin_state if origin_state in _SAFE_OUTPUT_STATES else "unknown",
        "origin_fingerprint": origin_fingerprint if isinstance(origin_fingerprint, str) and _HEX64.fullmatch(origin_fingerprint) else None,
        "worktree_state": worktree_state if worktree_state in _SAFE_OUTPUT_STATES else "unknown",
        "operation_code": safe_code,
        "dirty": dirty is True,
        "ok": safe_code == CODE_OK,
    }


def _run_git(root: Path, args: Sequence[str]) -> _GitResult:
    """Run one allowlisted, read-only Git query with bounded captured output."""

    if not isinstance(root, Path) or not root.is_absolute() or not args or args[0] not in _ALLOWED_GIT_COMMANDS:
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    safe_args = tuple(args)
    if any(not isinstance(item, str) or not item or "\x00" in item for item in safe_args):
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    if safe_args[0] == "config" and safe_args not in {
        ("config", "--get-all", "remote.origin.url"),
        ("config", "--get-all", "remote.origin.pushurl"),
    }:
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    if safe_args[0] == "symbolic-ref" and safe_args != ("symbolic-ref", "--quiet", "--short", "HEAD"):
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    if safe_args[0] == "status" and safe_args != ("status", "--porcelain=v1", "--untracked-files=all"):
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    if safe_args[0] == "merge-base" and (
        len(safe_args) != 4
        or safe_args[1] != "--is-ancestor"
        or not _HEX40.fullmatch(safe_args[2])
        or safe_args[3] != "HEAD"
    ):
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    if safe_args[0] == "rev-parse" and safe_args not in {
        ("rev-parse", "--show-toplevel"),
        ("rev-parse", "--git-dir"),
        ("rev-parse", "--git-common-dir"),
        ("rev-parse", "--verify", "HEAD^{commit}"),
    }:
        return _GitResult(2, code=CODE_GIT_QUERY_FAILED)
    git_executable = shutil.which("git")
    if not git_executable:
        return _GitResult(127, code=CODE_GIT_UNAVAILABLE)
    try:
        completed = subprocess.run(
            [git_executable, *safe_args],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        return _GitResult(127, code=CODE_GIT_UNAVAILABLE)
    except subprocess.TimeoutExpired:
        return _GitResult(124, code=CODE_GIT_TIMEOUT)
    except OSError:
        return _GitResult(1, code=CODE_GIT_QUERY_FAILED)
    stdout = completed.stdout if isinstance(completed.stdout, bytes) else b""
    stderr = completed.stderr if isinstance(completed.stderr, bytes) else b""
    if len(stdout) > MAX_GIT_OUTPUT_BYTES or len(stderr) > MAX_GIT_OUTPUT_BYTES:
        return _GitResult(1, code=CODE_GIT_OUTPUT_OVERSIZE)
    return _GitResult(int(completed.returncode), stdout, stderr)


def _text(result: _GitResult) -> str | None:
    if result.code is not None or result.returncode != 0:
        return None
    try:
        value = result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "\x00" in value or "\r" in value:
        return None
    return value.rstrip("\n")


def _query_code(result: _GitResult) -> str:
    if result.code in {CODE_GIT_UNAVAILABLE, CODE_GIT_TIMEOUT, CODE_GIT_OUTPUT_OVERSIZE}:
        return result.code
    return CODE_GIT_QUERY_FAILED


def _resolve_git_output(root: Path, value: str) -> Path | None:
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return _resolved(candidate)


def _normalize_origin(value: object) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value or any(char in value for char in ("\x00", "\r", "\n")):
        return None, CODE_ORIGIN_INVALID
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None, CODE_ORIGIN_INVALID
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return None, CODE_ORIGIN_CREDENTIALS
    if parsed.scheme != "https" or parsed.hostname is None or parsed.hostname.casefold() != "github.com":
        return None, CODE_ORIGIN_INVALID
    try:
        port = parsed.port
    except ValueError:
        return None, CODE_ORIGIN_INVALID
    if port is not None or parsed.query or parsed.fragment:
        return None, CODE_ORIGIN_INVALID
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.casefold() != "/letam10/local-ai-hub":
        return None, CODE_ORIGIN_UNALLOWLISTED
    return "github.com/letam10/local-ai-hub", CODE_OK


def _canonical_status() -> tuple[dict[str, Any], str | None]:
    root = CANONICAL_ROOT
    if not isinstance(root, Path) or not root.is_absolute() or not root.exists():
        return _public_result(CODE_ROOT_MISSING, git_state="missing"), None
    if _has_link_or_reparse_component(root):
        return _public_result(CODE_ROOT_LINKED, git_state="linked"), None
    if not root.is_dir():
        return _public_result(CODE_ROOT_INVALID, git_state="invalid"), None
    git_dir = root / ".git"
    if _is_link_or_reparse(git_dir):
        return _public_result(CODE_GIT_LINKED, git_state="linked"), None
    if not git_dir.exists():
        return _public_result(CODE_GIT_MISSING, git_state="missing"), None
    if not git_dir.is_dir():
        return _public_result(CODE_GIT_INVALID, git_state="invalid"), None
    top_result = _run_git(root, ("rev-parse", "--show-toplevel"))
    top_text = _text(top_result)
    top_path = _resolve_git_output(root, top_text or "") if top_text is not None else None
    if top_path is None:
        return _public_result(_query_code(top_result), git_state="directory"), None
    if not _same_path(top_path, root):
        return _public_result(CODE_TOP_LEVEL_MISMATCH, git_state="directory"), None
    git_result = _run_git(root, ("rev-parse", "--git-dir"))
    git_text = _text(git_result)
    git_path = _resolve_git_output(root, git_text or "") if git_text is not None else None
    if git_path is None:
        return _public_result(_query_code(git_result), git_state="directory"), None
    if not _same_path(git_path, git_dir):
        return _public_result(CODE_COMMON_DIR_MISMATCH, git_state="directory"), None
    common_result = _run_git(root, ("rev-parse", "--git-common-dir"))
    common_text = _text(common_result)
    common_path = _resolve_git_output(root, common_text or "") if common_text is not None else None
    if common_path is None:
        return _public_result(_query_code(common_result), git_state="directory"), None
    if not _same_path(common_path, git_dir):
        return _public_result(CODE_COMMON_DIR_MISMATCH, git_state="directory"), None
    head_result = _run_git(root, ("rev-parse", "--verify", "HEAD^{commit}"))
    head_text = _text(head_result)
    if head_text is None:
        return _public_result(_query_code(head_result), git_state="directory"), None
    head = head_text.strip()
    if not _HEX40.fullmatch(head):
        return _public_result(CODE_HEAD_INVALID, git_state="directory", head_state="invalid"), None
    branch_result = _run_git(root, ("symbolic-ref", "--quiet", "--short", "HEAD"))
    branch_text = _text(branch_result)
    if branch_text is None:
        return _public_result(_query_code(branch_result), git_state="directory", head_state="valid", head_fingerprint=_fingerprint(head)), None
    branch = branch_text.strip()
    if not _safe_branch(branch):
        return _public_result(
            CODE_BRANCH_INVALID,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="invalid",
        ), None
    origin_result = _run_git(root, ("config", "--get-all", "remote.origin.url"))
    origin_text = _text(origin_result)
    if origin_text is None:
        return _public_result(
            _query_code(origin_result),
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
        ), None
    origins = [line.strip() for line in origin_text.splitlines() if line.strip()]
    if not origins:
        return _public_result(
            CODE_ORIGIN_MISSING,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="missing",
        ), None
    if len(origins) != 1:
        return _public_result(
            CODE_METADATA_AMBIGUOUS,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="unknown",
        ), None
    normalized, origin_code = _normalize_origin(origins[0])
    if normalized is None:
        return _public_result(
            origin_code,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="invalid" if origin_code != CODE_ORIGIN_CREDENTIALS else "unknown",
        ), None
    push_result = _run_git(root, ("config", "--get-all", "remote.origin.pushurl"))
    if push_result.code in {CODE_GIT_UNAVAILABLE, CODE_GIT_TIMEOUT, CODE_GIT_OUTPUT_OVERSIZE}:
        return _public_result(
            push_result.code,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="allowlisted",
            origin_fingerprint=_fingerprint(normalized),
        ), None
    if push_result.code is not None:
        return _public_result(
            _query_code(push_result),
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="allowlisted",
            origin_fingerprint=_fingerprint(normalized),
        ), None
    if push_result.returncode == 1 and not push_result.stdout and not push_result.stderr:
        push_text = ""
    elif push_result.returncode != 0:
        return _public_result(
            _query_code(push_result),
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="allowlisted",
            origin_fingerprint=_fingerprint(normalized),
        ), None
    else:
        push_text = _text(push_result)
        if push_text is None:
            return _public_result(
                _query_code(push_result),
                git_state="directory",
                head_state="valid",
                head_fingerprint=_fingerprint(head),
                branch_state="safe",
                origin_state="allowlisted",
                origin_fingerprint=_fingerprint(normalized),
            ), None
    push_origins = [line.strip() for line in push_text.splitlines() if line.strip()]
    if len(push_origins) > 1:
        return _public_result(
            CODE_METADATA_AMBIGUOUS,
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="unknown",
        ), None
    if push_origins:
        push_normalized, push_code = _normalize_origin(push_origins[0])
        if push_normalized is None:
            return _public_result(
                push_code,
                git_state="directory",
                head_state="valid",
                head_fingerprint=_fingerprint(head),
                branch_state="safe",
                origin_state="unknown" if push_code == CODE_ORIGIN_CREDENTIALS else "invalid",
            ), None
        if push_normalized != normalized:
            return _public_result(
                CODE_ORIGIN_PUSH_MISMATCH,
                git_state="directory",
                head_state="valid",
                head_fingerprint=_fingerprint(head),
                branch_state="safe",
                origin_state="unknown",
            ), None
    status_result = _run_git(root, ("status", "--porcelain=v1", "--untracked-files=all"))
    status_text = _text(status_result)
    if status_text is None:
        return _public_result(
            _query_code(status_result),
            git_state="directory",
            head_state="valid",
            head_fingerprint=_fingerprint(head),
            branch_state="safe",
            origin_state="allowlisted",
            origin_fingerprint=_fingerprint(normalized),
        ), None
    dirty = bool(status_text)
    result = _public_result(
        CODE_WORKTREE_DIRTY if dirty else CODE_OK,
        git_state="directory",
        head_state="valid",
        head_fingerprint=_fingerprint(head),
        branch_state="safe",
        origin_state="allowlisted",
        origin_fingerprint=_fingerprint(normalized),
        worktree_state="dirty" if dirty else "clean",
        dirty=dirty,
    )
    return result, head


def inspect_canonical() -> dict[str, Any]:
    """Return only the fixed sanitized canonical identity projection."""

    result, _head = _canonical_status()
    return result


def _safe_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _valid_event(value: object) -> bool:
    if type(value) is not dict or set(value) != _EVENT_KEYS:
        return False
    if not _safe_timestamp(value.get("timestamp")):
        return False
    if not isinstance(value.get("git_state"), str) or value["git_state"] not in _SAFE_OUTPUT_STATES:
        return False
    if not isinstance(value.get("head_state"), str) or value["head_state"] not in _SAFE_OUTPUT_STATES:
        return False
    if value.get("head_fingerprint") is not None and (
        not isinstance(value.get("head_fingerprint"), str) or not _HEX64.fullmatch(value["head_fingerprint"])
    ):
        return False
    if not isinstance(value.get("branch_state"), str) or value["branch_state"] not in _SAFE_OUTPUT_STATES:
        return False
    if not isinstance(value.get("origin_state"), str) or value["origin_state"] not in _SAFE_OUTPUT_STATES:
        return False
    if value.get("origin_fingerprint") is not None and (
        not isinstance(value.get("origin_fingerprint"), str) or not _HEX64.fullmatch(value["origin_fingerprint"])
    ):
        return False
    if not isinstance(value.get("worktree_state"), str) or value["worktree_state"] not in _SAFE_OUTPUT_STATES:
        return False
    if not isinstance(value.get("operation_code"), str) or value["operation_code"] not in _ALL_CODES:
        return False
    if type(value.get("dirty")) is not bool:
        return False
    if not isinstance(value.get("event_type"), str) or value["event_type"] not in _EVENT_TYPES:
        return False
    if not isinstance(value.get("operation"), str) or value["operation"] not in _EVENT_OPERATIONS:
        return False
    if not isinstance(value.get("outcome"), str) or value["outcome"] not in _EVENT_OUTCOMES:
        return False
    if not isinstance(value.get("target_kind"), str) or value["target_kind"] not in _EVENT_TARGET_KINDS:
        return False
    if value["event_type"] == "preflight" and value["operation"] != "canonical_preflight":
        return False
    if value["event_type"] == "assignment_decision" and value["operation"] != "canonical_assignment":
        return False
    if value["event_type"] == "cleanup_decision" and value["operation"] != "owned_worktree_cleanup":
        return False
    expected_target = {
        "preflight": "canonical",
        "assignment_decision": "canonical",
        "cleanup_decision": "disposable_worktree",
    }[value["event_type"]]
    if value["target_kind"] != expected_target:
        return False
    return True


def _event_from_result(
    result: Mapping[str, Any],
    *,
    event_type: str = "preflight",
    operation: str = "canonical_preflight",
    target_kind: str = "canonical",
) -> dict[str, Any]:
    code = result.get("operation_code", CODE_GIT_QUERY_FAILED)
    dirty = result.get("dirty") is True
    outcome = "ok" if code == CODE_OK else "preservation_required" if dirty else "refused"
    return {
        "timestamp": _timestamp(),
        "git_state": result.get("git_state", "unknown"),
        "head_state": result.get("head_state", "unknown"),
        "head_fingerprint": result.get("head_fingerprint"),
        "branch_state": result.get("branch_state", "unknown"),
        "origin_state": result.get("origin_state", "unknown"),
        "origin_fingerprint": result.get("origin_fingerprint"),
        "worktree_state": result.get("worktree_state", "unknown"),
        "operation_code": result.get("operation_code", CODE_GIT_QUERY_FAILED),
        "dirty": dirty,
        "event_type": event_type,
        "operation": operation,
        "outcome": outcome,
        "target_kind": target_kind,
    }


def _read_snapshot_file(*, missing_is_empty: bool = False) -> dict[str, Any] | None:
    path = FORENSIC_SNAPSHOT_PATH
    if not isinstance(path, Path) or _has_link_or_reparse_component(path.parent) or _is_link_or_reparse(path):
        return None
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        if missing_is_empty:
            return {
                "schema_version": SCHEMA_VERSION,
                "canonical_designation": CANONICAL_DESIGNATION,
                "canonical_path": CANONICAL_POLICY_PATH,
                "events": [],
            }
        return None
    except OSError:
        return None
    if len(raw) > MAX_FORENSIC_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        type(value) is not dict
        or set(value) != _SNAPSHOT_KEYS
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("canonical_designation") != CANONICAL_DESIGNATION
        or value.get("canonical_path") != CANONICAL_POLICY_PATH
        or type(value.get("events")) is not list
        or len(value["events"]) > MAX_FORENSIC_EVENTS
        or any(not _valid_event(item) for item in value["events"])
    ):
        return None
    return value


def read_forensic_snapshot() -> dict[str, Any] | None:
    """Read a sanitized snapshot without writing, Git, or process access."""

    with _SNAPSHOT_LOCK:
        value = _read_snapshot_file()
        if value is None:
            return None
        return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


def write_forensic_snapshot(event: Mapping[str, Any]) -> bool:
    """Atomically append one already-sanitized event, or refuse unchanged."""

    if not isinstance(event, Mapping):
        return False
    candidate_event = dict(event)
    if not _valid_event(candidate_event):
        return False
    path = FORENSIC_SNAPSHOT_PATH
    with _SNAPSHOT_LOCK:
        existing = _read_snapshot_file(missing_is_empty=True)
        if existing is None:
            return False
        events = [*existing["events"], candidate_event][-MAX_FORENSIC_EVENTS:]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "canonical_designation": CANONICAL_DESIGNATION,
            "canonical_path": CANONICAL_POLICY_PATH,
            "events": events,
        }
        try:
            encoded = (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        except (TypeError, ValueError):
            return False
        if len(encoded) > MAX_FORENSIC_BYTES or not isinstance(path, Path) or _has_link_or_reparse_component(path.parent) or _is_link_or_reparse(path):
            return False
        temporary: Path | None = None
        try:
            if not path.parent.exists() or not path.parent.is_dir():
                return False
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            return True
        except (OSError, ValueError):
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return False


def preflight_canonical() -> dict[str, Any]:
    """Inspect canonical identity and persist only the fixed forensic event."""

    result = inspect_canonical()
    if not write_forensic_snapshot(_event_from_result(result)):
        return _public_result(
            CODE_SNAPSHOT_WRITE_FAILED,
            git_state=result.get("git_state", "unknown"),
            head_state=result.get("head_state", "unknown"),
            head_fingerprint=result.get("head_fingerprint"),
            branch_state=result.get("branch_state", "unknown"),
            origin_state=result.get("origin_state", "unknown"),
            origin_fingerprint=result.get("origin_fingerprint"),
            worktree_state=result.get("worktree_state", "unknown"),
            dirty=result.get("dirty") is True,
        )
    return result


def issue_owned_temporary_worktree(
    target: Path,
    *,
    branch: str,
    base: str,
    common_git_dir: Path,
) -> OwnedTemporaryWorktree | None:
    """Issue a lease without touching the target or canonical checkout."""

    if (
        not isinstance(target, Path)
        or not target.is_absolute()
        or not isinstance(common_git_dir, Path)
        or not common_git_dir.is_absolute()
        or not _safe_branch(branch)
        or not isinstance(base, str)
        or not _HEX40.fullmatch(base)
        or _protected_common_dir_relation(common_git_dir) is not False
    ):
        return None
    return OwnedTemporaryWorktree(target, branch, base, common_git_dir, _LEASE_TOKEN)


def _lease_is_valid(lease: object) -> bool:
    return (
        isinstance(lease, OwnedTemporaryWorktree)
        and lease._token is _LEASE_TOKEN
        and isinstance(lease.target, Path)
        and lease.target.is_absolute()
        and isinstance(lease.common_git_dir, Path)
        and lease.common_git_dir.is_absolute()
        and _safe_branch(lease.branch)
        and isinstance(lease.base, str)
        and bool(_HEX40.fullmatch(lease.base))
    )


def _decision(code: str, *, allowed: bool = False, dirty: bool = False) -> dict[str, Any]:
    safe_code = code if code in _ALL_CODES else CODE_GIT_QUERY_FAILED
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_code": safe_code,
        "action": "ALLOW_CONTROLLED_CLEANUP" if allowed else "REFUSE",
        "action_available": allowed is True,
        "target_state": "verified_disposable" if allowed else "unavailable",
        "dirty": dirty is True,
    }


def _audited_decision(
    decision: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None,
    event_type: str,
    operation: str,
    target_kind: str,
) -> dict[str, Any]:
    """Record a fixed decision before exposing it; logging failure refuses."""

    event_source: dict[str, Any] = dict(context) if isinstance(context, Mapping) else {}
    event_source.update(decision)
    event = _event_from_result(
        event_source,
        event_type=event_type,
        operation=operation,
        target_kind=target_kind,
    )
    try:
        written = write_forensic_snapshot(event)
    except Exception:
        written = False
    if not written:
        return _decision(CODE_SNAPSHOT_WRITE_FAILED, dirty=decision.get("dirty") is True)
    return dict(decision)


def _assignment_decision(canonical: Mapping[str, Any]) -> dict[str, Any]:
    code = canonical.get("operation_code", CODE_GIT_QUERY_FAILED)
    dirty = canonical.get("dirty") is True
    if code == CODE_OK:
        return {
            "schema_version": SCHEMA_VERSION,
            "operation_code": CODE_OK,
            "action": "OBSERVE_ONLY",
            "action_available": False,
            "target_state": "verified_clean_observation",
            "dirty": False,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "operation_code": code if code in _ALL_CODES else CODE_GIT_QUERY_FAILED,
        "action": "OBSERVE_ONLY" if dirty else "REFUSE",
        "action_available": False,
        "target_state": "preserved_dirty" if dirty else "unavailable",
        "dirty": dirty,
    }


def decide_canonical_assignment() -> dict[str, Any]:
    """Return a pure read-only assignment/integration preflight decision."""

    canonical = inspect_canonical()
    decision = _assignment_decision(canonical)
    return _audited_decision(
        decision,
        context=canonical,
        event_type="assignment_decision",
        operation="canonical_assignment",
        target_kind="canonical",
    )


def decide_owned_temporary_worktree_cleanup(
    lease: OwnedTemporaryWorktree,
    process_probe: Callable[[], OwnedProcessProbe],
) -> dict[str, Any]:
    """Return only a safe decision; never execute cleanup or process control."""

    canonical = inspect_canonical()

    def finish(code: str, *, allowed: bool = False, dirty: bool = False, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        decision = _decision(code, allowed=allowed, dirty=dirty)
        return _audited_decision(
            decision,
            context=context or canonical,
            event_type="cleanup_decision",
            operation="owned_worktree_cleanup",
            target_kind="disposable_worktree",
        )

    if canonical.get("operation_code") != CODE_OK:
        return finish(canonical.get("operation_code", CODE_GIT_QUERY_FAILED), dirty=canonical.get("dirty") is True)
    if not _lease_is_valid(lease):
        return finish(CODE_LEASE_INVALID)
    common_relation = _protected_common_dir_relation(lease.common_git_dir)
    if common_relation is not False:
        return finish(CODE_COMMON_DIR_PROTECTED)
    target = lease.target
    canonical_root = CANONICAL_ROOT
    if _has_link_or_reparse_component(target):
        return finish(CODE_TARGET_LINKED)
    if not target.exists():
        return finish(CODE_TARGET_MISSING)
    if not target.is_dir():
        return finish(CODE_TARGET_NOT_DIRECTORY)
    resolved_target = _resolved(target)
    resolved_canonical = _resolved(canonical_root)
    resolved_git = _resolved(canonical_root / ".git")
    if resolved_target is None or resolved_canonical is None or resolved_git is None:
        return finish(CODE_TARGET_RELATION_UNSAFE)
    if (
        os.path.normcase(str(resolved_target)) in {os.path.normcase(str(resolved_canonical)), os.path.normcase(str(resolved_git))}
        or _path_is_inside(resolved_target, resolved_canonical)
        or _path_is_inside(resolved_canonical, resolved_target)
        or _path_is_inside(resolved_target, resolved_git)
        or _path_is_inside(resolved_git, resolved_target)
    ):
        return finish(CODE_TARGET_RELATION_UNSAFE)
    if _has_link_or_reparse_component(lease.common_git_dir) or not lease.common_git_dir.is_dir():
        return finish(CODE_TARGET_COMMON_DIR_MISMATCH)
    marker = target / ".git"
    if _is_link_or_reparse(marker) or not marker.is_file():
        return finish(CODE_TARGET_GITLINK_INVALID)
    try:
        marker_text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return finish(CODE_TARGET_GITLINK_INVALID)
    if len(marker_text) > 1024 or not marker_text.startswith("gitdir: ") or marker_text.count("\n") > 1:
        return finish(CODE_TARGET_GITLINK_INVALID)
    git_link = marker_text.strip()[8:].strip()
    linked_path = Path(git_link)
    if not linked_path.is_absolute():
        linked_path = marker.parent / linked_path
    linked_path = _resolved(linked_path)
    expected_common = _resolved(lease.common_git_dir)
    if linked_path is None or expected_common is None or _has_link_or_reparse_component(linked_path):
        return finish(CODE_TARGET_GITLINK_INVALID)
    try:
        linked_path.relative_to(expected_common / "worktrees")
    except ValueError:
        return finish(CODE_TARGET_COMMON_DIR_MISMATCH)
    top_result = _run_git(target, ("rev-parse", "--show-toplevel"))
    top_text = _text(top_result)
    top_path = _resolve_git_output(target, top_text or "") if top_text is not None else None
    if top_path is None or not _same_path(top_path, target):
        return finish(CODE_TARGET_TOP_LEVEL_MISMATCH)
    common_result = _run_git(target, ("rev-parse", "--git-common-dir"))
    common_text = _text(common_result)
    common_path = _resolve_git_output(target, common_text or "") if common_text is not None else None
    if common_path is None or not _same_path(common_path, lease.common_git_dir):
        return finish(CODE_TARGET_COMMON_DIR_MISMATCH)
    git_dir_result = _run_git(target, ("rev-parse", "--git-dir"))
    git_dir_text = _text(git_dir_result)
    git_dir_path = _resolve_git_output(target, git_dir_text or "") if git_dir_text is not None else None
    if git_dir_path is None or not _same_path(git_dir_path, linked_path):
        return finish(CODE_TARGET_GITLINK_INVALID)
    branch_result = _run_git(target, ("symbolic-ref", "--quiet", "--short", "HEAD"))
    branch_text = _text(branch_result)
    if branch_text is None:
        return finish(_query_code(branch_result))
    if branch_text.strip() != lease.branch:
        return finish(CODE_TARGET_BRANCH_MISMATCH)
    head_result = _run_git(target, ("rev-parse", "--verify", "HEAD^{commit}"))
    head_text = _text(head_result)
    if head_text is None or not _HEX40.fullmatch(head_text.strip()):
        return finish(CODE_TARGET_HEAD_INVALID)
    base_result = _run_git(target, ("merge-base", "--is-ancestor", lease.base, "HEAD"))
    if base_result.code is not None:
        return finish(_query_code(base_result))
    if base_result.returncode != 0:
        return finish(CODE_TARGET_BASE_NOT_ANCESTOR)
    status_result = _run_git(target, ("status", "--porcelain=v1", "--untracked-files=all"))
    status_text = _text(status_result)
    if status_text is None:
        return finish(_query_code(status_result))
    if status_text:
        return finish(CODE_TARGET_DIRTY, dirty=True)
    try:
        probe = process_probe()
    except Exception:
        return finish(CODE_PROCESS_UNKNOWN)
    if not isinstance(probe, OwnedProcessProbe) or probe.scope != "owned-temporary-worktree" or probe.known is not True:
        return finish(CODE_PROCESS_UNKNOWN)
    if isinstance(probe.owned_count, bool) or not isinstance(probe.owned_count, int):
        return finish(CODE_PROCESS_UNKNOWN)
    if probe.owned_count != 0:
        return finish(CODE_PROCESS_PRESENT)
    return finish(CODE_OK, allowed=True)


__all__ = [
    "CANONICAL_ROOT",
    "CANONICAL_POLICY_PATH",
    "CANONICAL_DESIGNATION",
    "FORENSIC_SNAPSHOT_PATH",
    "SCHEMA_VERSION",
    "OwnedProcessProbe",
    "OwnedTemporaryWorktree",
    "CODE_OK",
    "CODE_WORKTREE_DIRTY",
    "CODE_SNAPSHOT_WRITE_FAILED",
    "CODE_LEASE_INVALID",
    "CODE_COMMON_DIR_PROTECTED",
    "CODE_ORIGIN_PUSH_MISMATCH",
    "CODE_TARGET_DIRTY",
    "CODE_PROCESS_PRESENT",
    "CODE_PROCESS_UNKNOWN",
    "decide_canonical_assignment",
    "decide_owned_temporary_worktree_cleanup",
    "inspect_canonical",
    "issue_owned_temporary_worktree",
    "preflight_canonical",
    "read_forensic_snapshot",
    "write_forensic_snapshot",
]
