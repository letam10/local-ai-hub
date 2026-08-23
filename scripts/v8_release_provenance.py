"""Read-only V8 release identity/provenance preparation.

This helper validates the tracked V8 release policy and can dry-run a proposed
V8 version/tag pair. It never changes PRODUCT_VERSION, creates/moves a tag,
merges main, builds an installer, publishes a release, or mutates historical V7
release evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.shared.version import PRODUCT_VERSION

POLICY_PATH = ROOT / "architecture" / "v8_release_policy.json"
MAX_POLICY_BYTES = 64 * 1024
SEMVER_V8 = re.compile(r"^8\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
TAG_V8 = re.compile(r"^v8\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


class ReleasePolicyError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise ReleasePolicyError("RELEASE_POLICY_UNREADABLE") from None
    if len(raw) > MAX_POLICY_BYTES:
        raise ReleasePolicyError("RELEASE_POLICY_TOO_LARGE")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ReleasePolicyError("RELEASE_POLICY_INVALID_JSON") from None


def load_release_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = _load_json(path)
    root_keys = {"schema_version", "generation", "release_branch", "version_policy", "tag_policy", "approval", "activation"}
    if not isinstance(value, dict) or set(value) != root_keys:
        raise ReleasePolicyError("RELEASE_POLICY_ROOT_INVALID")
    if value.get("schema_version") != "v8-release-policy.v1" or value.get("generation") != "V8":
        raise ReleasePolicyError("RELEASE_POLICY_VERSION_INVALID")
    if value.get("release_branch") != "feature/local-ai-hub-v8":
        raise ReleasePolicyError("RELEASE_POLICY_BRANCH_INVALID")

    version = value.get("version_policy")
    if not isinstance(version, dict) or set(version) != {"major", "format", "candidate_version"}:
        raise ReleasePolicyError("RELEASE_VERSION_POLICY_INVALID")
    if version.get("major") != 8 or version.get("format") != "semver-stable":
        raise ReleasePolicyError("RELEASE_VERSION_POLICY_INVALID")
    candidate_version = version.get("candidate_version")
    if candidate_version is not None and (not isinstance(candidate_version, str) or SEMVER_V8.fullmatch(candidate_version) is None):
        raise ReleasePolicyError("RELEASE_CANDIDATE_VERSION_INVALID")

    tag = value.get("tag_policy")
    if not isinstance(tag, dict) or set(tag) != {"prefix", "immutable", "candidate_tag"}:
        raise ReleasePolicyError("RELEASE_TAG_POLICY_INVALID")
    if tag.get("prefix") != "v8." or tag.get("immutable") is not True:
        raise ReleasePolicyError("RELEASE_TAG_POLICY_INVALID")
    candidate_tag = tag.get("candidate_tag")
    if candidate_tag is not None and (not isinstance(candidate_tag, str) or TAG_V8.fullmatch(candidate_tag) is None):
        raise ReleasePolicyError("RELEASE_CANDIDATE_TAG_INVALID")
    if candidate_version is None and candidate_tag is not None or candidate_version is not None and candidate_tag is None:
        raise ReleasePolicyError("RELEASE_IDENTITY_PARTIAL")
    if candidate_version is not None and candidate_tag != f"v{candidate_version}":
        raise ReleasePolicyError("RELEASE_IDENTITY_MISMATCH")

    approval = value.get("approval")
    if not isinstance(approval, dict) or set(approval) != {"identity", "version_change", "tag_creation", "main_merge"}:
        raise ReleasePolicyError("RELEASE_APPROVAL_POLICY_INVALID")
    if approval.get("identity") not in {"required", "approved"}:
        raise ReleasePolicyError("RELEASE_APPROVAL_POLICY_INVALID")
    if any(approval.get(key) != "user_approved_only" for key in ("version_change", "tag_creation", "main_merge")):
        raise ReleasePolicyError("RELEASE_APPROVAL_POLICY_INVALID")

    activation = value.get("activation")
    expected_activation = {
        "requires_all_windows_gates",
        "requires_exact_source_commit",
        "requires_product_version_match",
        "requires_release_identity_approval",
        "requires_unoccupied_tag",
        "historical_v7_release_evidence_immutable",
    }
    if not isinstance(activation, dict) or set(activation) != expected_activation or any(activation.get(key) is not True for key in expected_activation):
        raise ReleasePolicyError("RELEASE_ACTIVATION_POLICY_INVALID")
    return value


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(["git", "-C", str(repo_root), *args], check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise ReleasePolicyError("RELEASE_GIT_QUERY_FAILED") from None
    try:
        output = result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise ReleasePolicyError("RELEASE_GIT_QUERY_FAILED") from None
    return result.returncode, output


def tag_available(tag: str, repo_root: Path = ROOT) -> bool:
    if TAG_V8.fullmatch(tag) is None:
        raise ReleasePolicyError("RELEASE_CANDIDATE_TAG_INVALID")
    code, _output = _git(repo_root, "show-ref", "--verify", "--quiet", f"refs/tags/{tag}")
    if code == 0:
        return False
    if code == 1:
        return True
    raise ReleasePolicyError("RELEASE_GIT_QUERY_FAILED")


def validate_candidate(version: str, tag: str, repo_root: Path = ROOT) -> dict[str, Any]:
    load_release_policy(repo_root / "architecture" / "v8_release_policy.json")
    if not isinstance(version, str) or SEMVER_V8.fullmatch(version) is None:
        raise ReleasePolicyError("RELEASE_CANDIDATE_VERSION_INVALID")
    if not isinstance(tag, str) or TAG_V8.fullmatch(tag) is None:
        raise ReleasePolicyError("RELEASE_CANDIDATE_TAG_INVALID")
    if tag != f"v{version}":
        raise ReleasePolicyError("RELEASE_IDENTITY_MISMATCH")
    available = tag_available(tag, repo_root)
    return {
        "schema_version": "v8-release-candidate-check.v1",
        "status": "candidate_available" if available else "blocked",
        "execution": "not_run",
        "dry_run": True,
        "candidate_version": version,
        "candidate_tag": tag,
        "tag_available": available,
        "writes_performed": False,
        "blockers": [] if available else ["RELEASE_TAG_ALREADY_EXISTS"],
    }


def _commit_at_head(repo_root: Path) -> str | None:
    code, output = _git(repo_root, "rev-parse", "HEAD")
    return output if code == 0 and re.fullmatch(r"[0-9a-f]{40,64}", output) else None


def _commit_at_tag(repo_root: Path, tag: str) -> str | None:
    code, output = _git(repo_root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    return output if code == 0 and re.fullmatch(r"[0-9a-f]{40,64}", output) else None


def release_policy_snapshot(repo_root: Path = ROOT, *, phase: str = "pre_tag", expected_commit: str | None = None) -> dict[str, Any]:
    if phase not in {"pre_tag", "post_tag"}:
        raise ReleasePolicyError("RELEASE_PHASE_INVALID")
    policy = load_release_policy(repo_root / "architecture" / "v8_release_policy.json")
    version = policy["version_policy"]["candidate_version"]
    tag = policy["tag_policy"]["candidate_tag"]
    identity_approved = bool(policy["approval"]["identity"] == "approved" and isinstance(version, str) and isinstance(tag, str))
    tag_is_available: bool | None = None
    tag_verified = False
    release_commit = expected_commit if isinstance(expected_commit, str) else _commit_at_head(repo_root)
    if isinstance(tag, str):
        if phase == "pre_tag":
            tag_is_available = tag_available(tag, repo_root)
        else:
            target = _commit_at_tag(repo_root, tag)
            tag_is_available = bool(target and release_commit and target == release_commit)
            tag_verified = tag_is_available
    product_matches = bool(isinstance(version, str) and PRODUCT_VERSION == version)
    activation_ready = bool(identity_approved and product_matches and tag_is_available is True)
    blockers: list[str] = []
    if not identity_approved:
        blockers.append("V8_RELEASE_IDENTITY_APPROVAL_REQUIRED")
    if identity_approved and not product_matches:
        blockers.append("V8_PRODUCT_VERSION_ACTIVATION_REQUIRED")
    if identity_approved and tag_is_available is not True:
        blockers.append("V8_RELEASE_TAG_UNAVAILABLE" if phase == "pre_tag" else "V8_RELEASE_TAG_MISMATCH")
    return {
        "schema_version": "v8-release-policy-snapshot.v1",
        "contract_valid": True,
        "generation": "V8",
        "release_branch": policy["release_branch"],
        "candidate_version": version,
        "candidate_tag": tag,
        "phase": phase,
        "identity_approved": identity_approved,
        "product_version": PRODUCT_VERSION,
        "product_version_matches": product_matches,
        "tag_available": tag_is_available,
        "tag_verified": tag_verified,
        "release_commit": release_commit,
        "activation_ready": activation_ready,
        "blockers": blockers,
        "execution": "not_run",
        "dry_run": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only V8 release identity/provenance preparation.")
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--phase", choices=("pre_tag", "post_tag"), default="pre_tag")
    parser.add_argument("--expected-commit", default=None)
    parser.add_argument("--candidate-version", default=None)
    parser.add_argument("--candidate-tag", default=None)
    args = parser.parse_args()
    try:
        if (args.candidate_version is None) != (args.candidate_tag is None):
            raise ReleasePolicyError("RELEASE_IDENTITY_PARTIAL")
        if args.candidate_version is not None:
            result = validate_candidate(args.candidate_version, args.candidate_tag)
            print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
            return 0 if result["tag_available"] is True else 1
        result = release_policy_snapshot(phase=args.phase, expected_commit=args.expected_commit)
        print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
        return 0 if result["contract_valid"] is True else 1
    except ReleasePolicyError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "POLICY_PATH",
    "ReleasePolicyError",
    "load_release_policy",
    "release_policy_snapshot",
    "tag_available",
    "validate_candidate",
]
