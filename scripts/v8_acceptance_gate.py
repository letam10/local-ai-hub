"""Read-only V8 Acceptance & Release preflight.

The preflight separates tracked source validity from machine-local Windows
acceptance and release identity approval. It never upgrades a version,
creates/moves a tag, builds an installer, starts the desktop app, downloads a
component, or treats Linux CI as Windows evidence.

A PASS local gate is accepted only when its deterministic local report exists,
is bound to the same source commit/gate/platform, and its actual SHA-256 equals
the digest declared in the evidence manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.v8_release_provenance import ReleasePolicyError, release_policy_snapshot

GATES_PATH = ROOT / "architecture" / "v8_acceptance_gates.json"
EVIDENCE_SCHEMA_VERSION = "v8-local-acceptance-evidence.v1"
REPORT_SCHEMA_VERSION = "v8-local-gate-report.v1"
REPORTS_DIR_NAME = "reports"
MAX_JSON_BYTES = 256 * 1024
MAX_REPORT_BYTES = 512 * 1024
OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GATE_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
CHECK_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")
EVIDENCE_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_RUN"})
REQUIRED_SOURCE_FILES = (
    "Plan_Miss.md",
    "architecture/v8_foundation.yaml",
    "architecture/v8_acceptance_gates.json",
    "architecture/v8_release_policy.json",
    "docs/architecture/V8_MIGRATION_PLAN.md",
    "docs/V8_WAVE4_WINDOWS_LIFECYCLE_PRODUCT_UX.md",
    "docs/V8_WAVE5_ACCEPTANCE_RELEASE.md",
    "docs/V8_WAVE6_RELEASE_PROVENANCE_PREPARATION.md",
    "scripts/v8_acceptance_gate.py",
    "scripts/v8_release_provenance.py",
    "src/services/api/routes/component_v8.py",
    "src/services/component_enablement_v8.py",
    "src/ui/features/components/index.js",
    "src/ui/features/components/v8_control_plane.js",
    "tests/test_v8_wave4_product_ux.py",
    "tests/test_v8_wave5_acceptance_gate.py",
    "tests/test_v8_wave6_release_provenance.py",
)


class AcceptanceGateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _read_bytes(path: Path, *, max_bytes: int, unreadable_code: str, too_large_code: str) -> bytes:
    try:
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise AcceptanceGateError(unreadable_code) from None
    if len(raw) > max_bytes:
        raise AcceptanceGateError(too_large_code)
    return raw


def _parse_json(raw: bytes, *, invalid_code: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise AcceptanceGateError(invalid_code) from None


def _load_json(path: Path) -> Any:
    return _parse_json(
        _read_bytes(path, max_bytes=MAX_JSON_BYTES, unreadable_code="JSON_UNREADABLE", too_large_code="JSON_TOO_LARGE"),
        invalid_code="JSON_INVALID",
    )


def load_gate_contract(path: Path = GATES_PATH) -> dict[str, Any]:
    value = _load_json(path)
    expected_root = {
        "schema_version",
        "generation",
        "required_platform",
        "required_evidence_class",
        "release_provenance_generation",
        "gates",
        "release_policy",
    }
    if not isinstance(value, dict) or set(value) != expected_root:
        raise AcceptanceGateError("GATE_CONTRACT_ROOT_INVALID")
    if value.get("schema_version") != "v8-acceptance-gates.v1" or value.get("generation") != "V8":
        raise AcceptanceGateError("GATE_CONTRACT_VERSION_INVALID")
    if value.get("required_platform") != "windows-x64" or value.get("required_evidence_class") != "local_windows":
        raise AcceptanceGateError("GATE_CONTRACT_EVIDENCE_CLASS_INVALID")
    if value.get("release_provenance_generation") != "V8":
        raise AcceptanceGateError("GATE_CONTRACT_PROVENANCE_INVALID")

    gates = value.get("gates")
    if not isinstance(gates, list) or not 1 <= len(gates) <= 64:
        raise AcceptanceGateError("GATE_CONTRACT_GATES_INVALID")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for item in gates:
        if not isinstance(item, dict) or set(item) != {"gate_id", "required"}:
            raise AcceptanceGateError("GATE_CONTRACT_GATE_INVALID")
        gate_id = item.get("gate_id")
        required = item.get("required")
        if not isinstance(gate_id, str) or GATE_ID.fullmatch(gate_id) is None or gate_id in seen or type(required) is not bool:
            raise AcceptanceGateError("GATE_CONTRACT_GATE_INVALID")
        seen.add(gate_id)
        normalized.append({"gate_id": gate_id, "required": required})

    policy = value.get("release_policy")
    expected_policy = {
        "main_merge",
        "version_change",
        "tag_change",
        "release_artifact_build",
        "machine_local_evidence_committed_to_git",
    }
    if not isinstance(policy, dict) or set(policy) != expected_policy:
        raise AcceptanceGateError("GATE_CONTRACT_POLICY_INVALID")
    if policy.get("main_merge") != "user_approved_only" or policy.get("version_change") != "user_approved_only" or policy.get("tag_change") != "user_approved_only":
        raise AcceptanceGateError("GATE_CONTRACT_POLICY_INVALID")
    if policy.get("release_artifact_build") != "after_required_local_gates" or policy.get("machine_local_evidence_committed_to_git") is not False:
        raise AcceptanceGateError("GATE_CONTRACT_POLICY_INVALID")
    return {**value, "gates": normalized}


def current_head(repo_root: Path = ROOT) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        raise AcceptanceGateError("GIT_HEAD_UNAVAILABLE") from None
    try:
        value = result.stdout.decode("ascii").strip()
    except UnicodeDecodeError:
        raise AcceptanceGateError("GIT_HEAD_UNAVAILABLE") from None
    if result.returncode != 0 or OID.fullmatch(value) is None:
        raise AcceptanceGateError("GIT_HEAD_UNAVAILABLE")
    return value


def release_provenance_snapshot(repo_root: Path = ROOT, *, phase: str = "pre_tag", expected_commit: str | None = None) -> dict[str, Any]:
    """Compatibility name for the V8 release-policy snapshot."""
    try:
        return release_policy_snapshot(repo_root, phase=phase, expected_commit=expected_commit)
    except ReleasePolicyError as exc:
        raise AcceptanceGateError("V8_RELEASE_PROVENANCE_CONTRACT_INVALID") from exc


def source_preflight(repo_root: Path = ROOT, *, phase: str = "pre_tag", expected_commit: str | None = None) -> dict[str, Any]:
    contract = load_gate_contract(repo_root / "architecture" / "v8_acceptance_gates.json")
    missing = [name for name in REQUIRED_SOURCE_FILES if not (repo_root / name).is_file()]
    provenance = release_provenance_snapshot(repo_root, phase=phase, expected_commit=expected_commit)
    return {
        "status": "completed",
        "valid": not missing and provenance.get("contract_valid") is True,
        "required_source_files": len(REQUIRED_SOURCE_FILES),
        "missing_source_files": missing,
        "required_local_gates": sum(item["required"] is True for item in contract["gates"]),
        "release_provenance": provenance,
    }


def _validate_evidence(value: Any, contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "evidence_class", "platform", "source_commit", "gates"}:
        raise AcceptanceGateError("EVIDENCE_ROOT_INVALID")
    if value.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise AcceptanceGateError("EVIDENCE_SCHEMA_INVALID")
    if value.get("evidence_class") != contract.get("required_evidence_class") or value.get("platform") != contract.get("required_platform"):
        raise AcceptanceGateError("EVIDENCE_PLATFORM_INVALID")
    source_commit = value.get("source_commit")
    if not isinstance(source_commit, str) or OID.fullmatch(source_commit) is None:
        raise AcceptanceGateError("EVIDENCE_SOURCE_COMMIT_INVALID")

    required_ids = [item["gate_id"] for item in contract["gates"] if item["required"] is True]
    gates = value.get("gates")
    if not isinstance(gates, dict) or set(gates) != set(required_ids):
        raise AcceptanceGateError("EVIDENCE_GATE_SET_MISMATCH")
    normalized: dict[str, dict[str, Any]] = {}
    for gate_id in required_ids:
        item = gates.get(gate_id)
        if not isinstance(item, dict) or set(item) != {"status", "report_sha256"}:
            raise AcceptanceGateError("EVIDENCE_GATE_INVALID")
        status = item.get("status")
        report_sha256 = item.get("report_sha256")
        if status not in EVIDENCE_STATUSES:
            raise AcceptanceGateError("EVIDENCE_GATE_INVALID")
        if report_sha256 is not None and (not isinstance(report_sha256, str) or SHA256.fullmatch(report_sha256) is None):
            raise AcceptanceGateError("EVIDENCE_REPORT_DIGEST_INVALID")
        if status == "PASS" and not isinstance(report_sha256, str):
            raise AcceptanceGateError("EVIDENCE_PASS_WITHOUT_REPORT")
        normalized[gate_id] = {"status": status, "report_sha256": report_sha256}
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_class": value["evidence_class"],
        "platform": value["platform"],
        "source_commit": source_commit,
        "gates": normalized,
    }


def _validate_pass_report(path: Path, *, gate_id: str, source_commit: str, platform: str, expected_sha256: str) -> None:
    raw = _read_bytes(path, max_bytes=MAX_REPORT_BYTES, unreadable_code="EVIDENCE_REPORT_MISSING", too_large_code="EVIDENCE_REPORT_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise AcceptanceGateError("EVIDENCE_REPORT_DIGEST_MISMATCH")
    value = _parse_json(raw, invalid_code="EVIDENCE_REPORT_INVALID")
    if not isinstance(value, dict) or set(value) != {"schema_version", "gate_id", "status", "platform", "source_commit", "checks"}:
        raise AcceptanceGateError("EVIDENCE_REPORT_INVALID")
    if value.get("schema_version") != REPORT_SCHEMA_VERSION or value.get("gate_id") != gate_id or value.get("status") != "PASS":
        raise AcceptanceGateError("EVIDENCE_REPORT_BINDING_INVALID")
    if value.get("platform") != platform or value.get("source_commit") != source_commit:
        raise AcceptanceGateError("EVIDENCE_REPORT_BINDING_INVALID")
    checks = value.get("checks")
    if not isinstance(checks, dict) or not 1 <= len(checks) <= 128:
        raise AcceptanceGateError("EVIDENCE_REPORT_CHECKS_INVALID")
    for check_id, passed in checks.items():
        if not isinstance(check_id, str) or CHECK_ID.fullmatch(check_id) is None or passed is not True:
            raise AcceptanceGateError("EVIDENCE_REPORT_CHECKS_INVALID")


def _verify_pass_reports(evidence: Mapping[str, Any], evidence_path: Path) -> None:
    reports_dir = evidence_path.parent / REPORTS_DIR_NAME
    for gate_id, item in evidence["gates"].items():
        if item["status"] != "PASS":
            continue
        _validate_pass_report(
            reports_dir / f"{gate_id}.json",
            gate_id=gate_id,
            source_commit=str(evidence["source_commit"]),
            platform=str(evidence["platform"]),
            expected_sha256=str(item["report_sha256"]),
        )


def evaluate(*, evidence_path: Path | None = None, repo_root: Path = ROOT, phase: str = "pre_tag", expected_commit: str | None = None) -> dict[str, Any]:
    contract = load_gate_contract(repo_root / "architecture" / "v8_acceptance_gates.json")
    source = source_preflight(repo_root, phase=phase, expected_commit=expected_commit)
    head = current_head(repo_root)
    blockers: list[str] = []
    if source["valid"] is not True:
        blockers.append("SOURCE_PREFLIGHT_FAILED")
    provenance = source["release_provenance"]
    blockers.extend(str(item) for item in provenance.get("blockers", []) if isinstance(item, str))

    evidence_summary: dict[str, Any] = {
        "present": evidence_path is not None,
        "valid": False,
        "reports_verified": False,
        "source_commit_matches": None,
        "required": source["required_local_gates"],
        "passed": 0,
        "pending_gates": [item["gate_id"] for item in contract["gates"] if item["required"] is True],
    }
    if evidence_path is None:
        blockers.append("LOCAL_WINDOWS_EVIDENCE_REQUIRED")
    else:
        try:
            evidence = _validate_evidence(_load_json(evidence_path), contract)
            _verify_pass_reports(evidence, evidence_path)
            pending = [gate_id for gate_id, item in evidence["gates"].items() if item["status"] != "PASS"]
            source_matches = evidence["source_commit"] == head
            evidence_summary.update({
                "valid": True,
                "reports_verified": True,
                "source_commit_matches": source_matches,
                "passed": len(evidence["gates"]) - len(pending),
                "pending_gates": pending,
            })
            if not source_matches:
                blockers.append("EVIDENCE_SOURCE_COMMIT_MISMATCH")
            if pending:
                blockers.append("LOCAL_WINDOWS_GATES_INCOMPLETE")
        except AcceptanceGateError as exc:
            blockers.append(exc.code)

    blockers = list(dict.fromkeys(blockers))
    release_ready = not blockers
    return {
        "schema_version": "v8-acceptance-preflight.v2",
        "status": "release_ready" if release_ready else "blocked",
        "execution": "not_run",
        "dry_run": True,
        "source_commit": head,
        "source_preflight": source,
        "local_evidence": evidence_summary,
        "release_ready": release_ready,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the read-only V8 acceptance/release gate.")
    parser.add_argument("--evidence", type=Path, default=None, help="Local evidence.json; PASS reports are read from sibling reports/<gate_id>.json and no path is emitted.")
    parser.add_argument("--source-only", action="store_true", help="Validate tracked source contracts without claiming release readiness.")
    parser.add_argument("--strict-release", action="store_true", help="Return success only when every release gate is satisfied.")
    parser.add_argument("--phase", choices=("pre_tag", "post_tag"), default="pre_tag", help="Release identity phase.")
    parser.add_argument("--expected-commit", default=None, help="Expected commit for post-tag verification.")
    args = parser.parse_args()
    if args.source_only and args.strict_release:
        print(json.dumps({"status": "invalid", "code": "CLI_MODE_CONFLICT", "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 2
    try:
        result = evaluate(evidence_path=args.evidence, phase=args.phase, expected_commit=args.expected_commit)
    except AcceptanceGateError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    if args.strict_release:
        return 0 if result["release_ready"] is True else 1
    return 0 if result["source_preflight"]["valid"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcceptanceGateError",
    "EVIDENCE_SCHEMA_VERSION",
    "GATES_PATH",
    "REPORT_SCHEMA_VERSION",
    "ROOT",
    "current_head",
    "evaluate",
    "load_gate_contract",
    "release_provenance_snapshot",
    "source_preflight",
]
