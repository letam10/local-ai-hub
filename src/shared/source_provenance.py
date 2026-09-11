"""Resolve one source identity for build and validation entrypoints."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re
import subprocess


SOURCE_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
EXPECTED_SOURCE_SHA_ENV = "LOCALAIHUB_EXPECTED_SOURCE_SHA"


class SourceIdentityError(ValueError):
    """Raised when a build identity cannot be bound to this checkout."""


def git_head(repo_root: Path) -> str:
    """Return the exact commit checked out at ``repo_root``."""

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    value = result.stdout.strip()
    if result.returncode != 0 or SOURCE_COMMIT_RE.fullmatch(value) is None:
        raise SourceIdentityError("SOURCE_HEAD_UNAVAILABLE")
    return value


def resolve_source_commit(
    repo_root: Path,
    expected_source_sha: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return checkout identity only when it matches the requested identity.

    ``GITHUB_SHA`` is a PR merge ref for ``pull_request`` workflows.  Callers
    building an exact PR-head candidate therefore pass the immutable head via
    ``expected_source_sha`` or ``LOCALAIHUB_EXPECTED_SOURCE_SHA``.  The Git
    checkout remains authoritative and every mismatch fails closed.
    """

    actual = git_head(repo_root)
    environment = os.environ if environ is None else environ
    expected = expected_source_sha
    if expected is None:
        expected = environment.get(EXPECTED_SOURCE_SHA_ENV)
    if expected is None:
        expected = environment.get("GITHUB_SHA")
    if expected is None:
        expected = actual
    if not isinstance(expected, str) or SOURCE_COMMIT_RE.fullmatch(expected) is None:
        raise SourceIdentityError("SOURCE_COMMIT_INVALID")
    if actual != expected:
        raise SourceIdentityError("SOURCE_COMMIT_MISMATCH")
    return actual
