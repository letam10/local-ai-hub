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
EVIDENCE_SCHEMA_VERSION = "v8-local-acceptance-evidence.v2"
REPORT_SCHEMA_VERSION = "v8-local-gate-report.v1"
WEBVIEW_CAPABILITY_REPORT_SCHEMA_VERSION = "v8-local-gate-report.v2"
REPORTS_DIR_NAME = "reports"
MAX_JSON_BYTES = 256 * 1024
MAX_REPORT_BYTES = 512 * 1024
OID = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GATE_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
CHECK_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")
EVIDENCE_STATUSES = frozenset({"PASS", "FAIL", "BLOCKED", "NOT_RUN", "NOT_AVAILABLE_ON_TEST_HOST"})
PROVENANCE_SCHEMA_VERSION = "v8-local-gate-provenance.v1"
PROVENANCE_KINDS = frozenset({"RERUN_EXACT_HEAD", "REUSED_UNAFFECTED_EVIDENCE"})
_WEBVIEW_CAPABILITY_SCHEMA = "v8-webview-dpi-evidence.v1"
_WEBVIEW_NATIVE_STATUSES = frozenset({"PASS", "NOT_AVAILABLE_ON_TEST_HOST"})
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
    "scripts/v8_legacy_test_baseline.py",
    "scripts/v8_release_provenance.py",
    "src/services/api/routes/component_v8.py",
    "src/services/component_enablement_v8.py",
    "src/ui/features/components/index.js",
    "src/ui/features/components/v8_control_plane.js",
    "tests/test_v8_wave4_product_ux.py",
    "tests/test_v8_wave5_acceptance_gate.py",
    "tests/test_v8_wave6_release_provenance.py",
    "docs/operations/V8_LEGACY_TEST_BASELINE.json",
    "docs/operations/V8_SELF_UPDATE_TRANSACTION.md",
    "src/app/desktop_lifecycle.py",
    "src/app/main.py",
    "src/app/update_bridge.py",
    "src/app/update_watchdog.py",
    "src/services/app_update.py",
    "src/services/update_transport.py",
    "tests/test_v8_p0_startup_recovery.py",
    "tests/test_v8_p0_update_bridge.py",
    "tests/test_v8_update_transaction.py",
    "tests/test_v8_update_transport.py",
)

# These are bounded source domains, deliberately not filesystem paths in any
# evidence packet.  A reused report is valid only if the source diff from its
# originating commit does not touch the gate's relevant domain.
_GATE_IMPACT_SCOPES: dict[str, frozenset[str]] = {
    "windows_filesystem": frozenset({"filesystem_transaction"}),
    "artifact_callsite_inventory": frozenset({"artifact_callsite"}),
    "native_picker_restart": frozenset({"native_picker"}),
    "executing_operation_cancel": frozenset({"execution_cancel", "resource_scheduler"}),
    "bundle_atomic_rollback": frozenset({"bundle_atomic"}),
    "real_component_lifecycle": frozenset({"component_lifecycle"}),
    "loopback_api": frozenset({"loopback_api", "desktop_startup"}),
    "webview2_product_ux": frozenset({"desktop_startup", "product_experience", "diagnostics", "application_launch"}),
    "sqlite_backup_restore": frozenset({"sqlite_backup"}),
    "packaging_upgrade": frozenset({"desktop_startup", "updater"}),
    "crash_recovery": frozenset({"desktop_startup", "updater"}),
    "runtime_smoke": frozenset({"desktop_startup", "runtime"}),
}

# A high-risk source tree must never silently disappear from the impact audit
# merely because a new subsystem was added without a mapper entry.  This is a
# deliberately finite sentinel (not a wildcard gate scope): reused evidence
# is refused whenever the sentinel appears until the path is classified or the
# affected gate is rerun.  Documentation/tests outside these roots remain
# intentionally unclassified and do not block reuse.
_UNCLASSIFIED_ACCEPTANCE_RELEVANT = "unclassified_acceptance_relevant"
# These scopes are not gate-local.  A changed verifier/required-check
# contract can change the meaning of every reused report, while an unmapped
# high-risk source path has not earned any reuse permission yet.
_REUSE_ALWAYS_BLOCKING_SCOPES = frozenset({"acceptance_contract", _UNCLASSIFIED_ACCEPTANCE_RELEVANT})
_HIGH_RISK_UNCLASSIFIED_PREFIXES = (
    "src/app/",
    "src/modules/",
    "src/services/",
    "src/shared/",
    "src/ui/",
    "scripts/",
    "architecture/",
)


def _path_impact_scope_single(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if normalized in {"src/services/runtime_registry.py", "src/services/local_registry_recovery.py"} or normalized.startswith("src/modules/airi/") or normalized.startswith("src/ui/features/airi/"):
        # AIRI's installer-managed application discovery/launch boundary is
        # distinct from AI runtime execution.  It must not invalidate a
        # reused runtime_smoke report, while remaining explicitly classified
        # instead of falling through to the unknown high-risk sentinel.
        return "application_launch"
    if normalized == "src/services/shortcut_migration.py":
        return "application_launch"
    if normalized.startswith("src/services/resource_scheduler/"):
        return "resource_scheduler"
    if normalized.startswith("src/services/workflow_runtime_v2/"):
        return "workflow_runtime"
    if normalized.startswith("src/services/node_studio/"):
        # Node Studio registry/state changes are rendered by the desktop
        # WebView.  Keep this scope on the required product-UX gate instead of
        # returning workflow_runtime, which has no required Windows consumer.
        return "product_experience"
    if normalized.startswith((
        "src/modules/sam2/",
        "src/modules/vision/",
        "src/modules/ocr/",
        "src/modules/whisper/",
    )):
        # These adapters/workers publish the M3/M4 opaque result contracts.
        # Keep their known callsites on the artifact gate; an unrelated new
        # module still falls through to the finite high-risk sentinel below.
        return "artifact_callsite"
    if normalized.startswith("src/services/provider_adapters_v2/"):
        return "provider_adapters"
    if normalized.startswith("src/services/project_manager/"):
        return "filesystem_transaction"
    if normalized.startswith("src/services/product_experience_v2/") or normalized.startswith("src/ui/"):
        return "product_experience"
    if normalized.startswith("src/services/diagnostics/"):
        return "diagnostics"
    if normalized.startswith(("src/app/main.py", "src/app/payload_bootstrap.py", "src/app/desktop_lifecycle.py")):
        return "desktop_startup"
    if normalized.startswith((
        "src/services/app_update.py",
        "src/services/update_transport.py",
        "src/app/update_",
        "src/app/stable_launcher.py",
        "src/app/stable_shell.py",
        "scripts/bootstrap_first_watchdog_payload.py",
    )):
        return "updater"
    if normalized == "scripts/generate_api_route_inventory.py":
        return "loopback_api"
    if normalized == "scripts/stage_stable_product.py":
        return "updater"
    if normalized in {"scripts/build_main_update.py", "scripts/assemble_product_update.py", "scripts/update_managed_shortcuts.ps1", ".github/workflows/ci.yml"}:
        return "updater"
    if normalized.startswith("src/services/api/"):
        return "loopback_api"
    if normalized.startswith("src/services/component_"):
        return "component_lifecycle"
    if normalized.startswith("src/services/backup") or normalized.startswith("src/services/restore"):
        return "sqlite_backup"
    if normalized.startswith("src/services/artifact"):
        return "artifact_callsite"
    if normalized.startswith("src/services/runtime"):
        return "runtime"
    if normalized in {
        "scripts/v8_acceptance_gate.py",
        "scripts/v8_legacy_test_baseline.py",
        "scripts/v8_release_provenance.py",
        "architecture/v8_acceptance_gates.json",
        "architecture/v8_release_policy.json",
    }:
        return "acceptance_contract"
    if normalized.startswith("src/services/capability_graph/"):
        return "loopback_api"
    if normalized.startswith("src/services/durable_job_engine_v2/"):
        return "execution_cancel"
    if normalized.startswith("src/services/feature_discovery_v2.py"):
        return "product_experience"
    if normalized.startswith("src/services/model_manager_v2/"):
        return "component_lifecycle"
    if normalized.startswith("src/services/platform_extensibility_v2.py"):
        return "product_experience"
    if normalized.startswith("src/services/platform_hardening_v2.py"):
        return "diagnostics"
    if normalized.startswith("src/services/product_experience_v2.py"):
        return "product_experience"
    if normalized.startswith("src/services/projection_cache.py"):
        return "product_experience"
    if normalized.startswith("src/services/workflow_library/"):
        return "workflow_runtime"
    if normalized.startswith("src/shared/schemas/workflow_library.py"):
        return "workflow_runtime"
    if normalized in {
        "src/shared/schemas/vision.py",
        "src/shared/schemas/ocr_whisper.py",
        "src/shared/utils/adapter_common.py",
    }:
        # Vision, OCR and Whisper result contracts plus the shared adapter
        # result normalizer govern opaque artifact/public projection shape;
        # they do not change the desktop startup or runtime-smoke protocol.
        return "artifact_callsite"
    if normalized.startswith(_HIGH_RISK_UNCLASSIFIED_PREFIXES):
        return _UNCLASSIFIED_ACCEPTANCE_RELEVANT
    return None


def _path_impact_scopes(path: str) -> frozenset[str]:
    """Return every conservative acceptance scope affected by one path.

    A path can be consumed by more than one physical gate.  The legacy helper
    below remains as a compatibility projection for older reports/tests, while
    source reuse decisions use this complete set so a primary mapping cannot
    silently hide a downstream consumer.
    """

    normalized = path.replace("\\", "/")
    primary = _path_impact_scope_single(normalized)
    scopes = {primary} if primary is not None else set()
    if normalized.startswith("src/ui/"):
        scopes.add("product_experience")
    if normalized.startswith("src/services/api/"):
        scopes.update({"loopback_api", "product_experience"})
    if normalized in {"src/app/main.py", "src/app/desktop_lifecycle.py", "src/app/payload_bootstrap.py"}:
        scopes.update({"desktop_startup", "product_experience"})
    if normalized.startswith("src/app/update_") or normalized in {"src/app/stable_launcher.py", "src/app/stable_shell.py"}:
        scopes.update({"updater", "desktop_startup"})
    if normalized in {"src/services/runtime_registry.py", "src/services/local_registry_recovery.py"} or normalized.startswith("src/modules/airi/") or normalized.startswith("src/ui/features/airi/"):
        scopes.update({"application_launch", "product_experience"})
    if normalized.startswith("src/services/component_"):
        scopes.add("product_experience")
    if normalized.startswith("src/services/storage"):
        scopes.add("product_experience")
    return frozenset(scopes)


def _path_impact_scope(path: str) -> str | None:
    """Compatibility projection retained for pre-set-based callers."""

    return _path_impact_scope_single(path)


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
        if not isinstance(item, dict) or not {"gate_id", "required", "required_checks"}.issubset(item) or set(item) - {"gate_id", "required", "required_checks", "capability_evidence"}:
            raise AcceptanceGateError("GATE_CONTRACT_GATE_INVALID")
        gate_id = item.get("gate_id")
        required = item.get("required")
        required_checks = item.get("required_checks")
        if not isinstance(gate_id, str) or GATE_ID.fullmatch(gate_id) is None or gate_id in seen or type(required) is not bool:
            raise AcceptanceGateError("GATE_CONTRACT_GATE_INVALID")
        if not isinstance(required_checks, list) or not 1 <= len(required_checks) <= 128:
            raise AcceptanceGateError("GATE_CONTRACT_REQUIRED_CHECKS_INVALID")
        check_ids: list[str] = []
        for check_id in required_checks:
            if not isinstance(check_id, str) or CHECK_ID.fullmatch(check_id) is None or check_id in check_ids:
                raise AcceptanceGateError("GATE_CONTRACT_REQUIRED_CHECKS_INVALID")
            check_ids.append(check_id)
        capability_evidence = item.get("capability_evidence")
        if gate_id == "webview2_product_ux" and capability_evidence is None:
            raise AcceptanceGateError("GATE_CONTRACT_CAPABILITY_INVALID")
        if capability_evidence is not None:
            expected_capability = {"schema_version", "native_dpi_values", "native_unavailable_status", "layout_scales"}
            if gate_id != "webview2_product_ux" or not isinstance(capability_evidence, dict) or set(capability_evidence) != expected_capability:
                raise AcceptanceGateError("GATE_CONTRACT_CAPABILITY_INVALID")
            native_values = capability_evidence.get("native_dpi_values")
            layout_scales = capability_evidence.get("layout_scales")
            if (
                capability_evidence.get("schema_version") != _WEBVIEW_CAPABILITY_SCHEMA
                or capability_evidence.get("native_unavailable_status") != "NOT_AVAILABLE_ON_TEST_HOST"
                or not isinstance(native_values, list)
                or not isinstance(layout_scales, list)
                or native_values != [100, 125, 150]
                or layout_scales != [100, 125, 150]
            ):
                raise AcceptanceGateError("GATE_CONTRACT_CAPABILITY_INVALID")
            capability_evidence = dict(capability_evidence)
        seen.add(gate_id)
        normalized.append({"gate_id": gate_id, "required": required, "required_checks": check_ids, "capability_evidence": capability_evidence})

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


def release_provenance_snapshot(
    repo_root: Path = ROOT,
    *,
    phase: str = "integration",
    expected_commit: str | None = None,
    requested_version: str | None = None,
    requested_tag: str | None = None,
) -> dict[str, Any]:
    """Compatibility name for the V8 release-policy snapshot."""
    try:
        return release_policy_snapshot(
            repo_root,
            phase=phase,
            expected_commit=expected_commit,
            requested_version=requested_version,
            requested_tag=requested_tag,
        )
    except ReleasePolicyError as exc:
        raise AcceptanceGateError("V8_RELEASE_PROVENANCE_CONTRACT_INVALID") from exc


def source_preflight(
    repo_root: Path = ROOT,
    *,
    phase: str = "integration",
    expected_commit: str | None = None,
    requested_version: str | None = None,
    requested_tag: str | None = None,
) -> dict[str, Any]:
    contract = load_gate_contract(repo_root / "architecture" / "v8_acceptance_gates.json")
    missing = [name for name in REQUIRED_SOURCE_FILES if not (repo_root / name).is_file()]
    provenance = release_provenance_snapshot(
        repo_root,
        phase=phase,
        expected_commit=expected_commit,
        requested_version=requested_version,
        requested_tag=requested_tag,
    )
    return {
        "status": "completed",
        "valid": not missing and provenance.get("contract_valid") is True,
        "required_source_files": len(REQUIRED_SOURCE_FILES),
        "missing_source_files": missing,
        "required_local_gates": sum(item["required"] is True for item in contract["gates"]),
        "release_provenance": provenance,
    }


def _validate_gate_provenance(value: Any, *, gate_id: str, evidence_source_commit: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_REQUIRED")
    kind = value.get("kind")
    expected_fields = {"schema_version", "kind", "origin_source_commit", "reason_code"}
    if kind == "REUSED_UNAFFECTED_EVIDENCE":
        expected_fields.add("unaffected_scopes")
    if set(value) != expected_fields or value.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_INVALID")
    origin = value.get("origin_source_commit")
    if kind not in PROVENANCE_KINDS or not isinstance(origin, str) or OID.fullmatch(origin) is None:
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_INVALID")
    if kind == "RERUN_EXACT_HEAD":
        if origin != evidence_source_commit or value.get("reason_code") != "rerun_exact_head":
            raise AcceptanceGateError("EVIDENCE_PROVENANCE_RERUN_BINDING_INVALID")
        return {"kind": kind, "origin_source_commit": origin, "reason_code": "rerun_exact_head"}
    scopes = value.get("unaffected_scopes")
    expected_scopes = sorted(_GATE_IMPACT_SCOPES.get(gate_id, frozenset()))
    if (
        value.get("reason_code") != "source_change_scope_unaffected"
        or origin == evidence_source_commit
        or not isinstance(scopes, list)
        or scopes != expected_scopes
    ):
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_REUSE_INVALID")
    return {
        "kind": kind,
        "origin_source_commit": origin,
        "reason_code": "source_change_scope_unaffected",
        "unaffected_scopes": list(scopes),
    }


def source_change_scopes(repo_root: Path, *, origin_source_commit: str, final_source_commit: str) -> set[str]:
    """Return bounded semantic source scopes changed between two commits."""

    try:
        ancestry = subprocess.run(
            ["git", "-C", str(repo_root), "merge-base", "--is-ancestor", origin_source_commit, final_source_commit],
            check=False,
            capture_output=True,
            timeout=10,
        )
        if ancestry.returncode != 0:
            raise AcceptanceGateError("EVIDENCE_PROVENANCE_ORIGIN_NOT_ANCESTOR")
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", f"{origin_source_commit}..{final_source_commit}"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_SCOPE_UNAVAILABLE") from None
    if result.returncode != 0:
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_SCOPE_UNAVAILABLE")
    try:
        paths = result.stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise AcceptanceGateError("EVIDENCE_PROVENANCE_SCOPE_UNAVAILABLE") from None
    changed: set[str] = set()
    for path in paths:
        changed.update(_path_impact_scopes(path))
    return changed


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
        if not isinstance(item, dict) or not {"status", "report_sha256", "provenance"}.issubset(item) or set(item) - {"status", "report_sha256", "provenance", "check_statuses"}:
            raise AcceptanceGateError("EVIDENCE_GATE_INVALID")
        status = item.get("status")
        report_sha256 = item.get("report_sha256")
        if status not in EVIDENCE_STATUSES:
            raise AcceptanceGateError("EVIDENCE_GATE_INVALID")
        if report_sha256 is not None and (not isinstance(report_sha256, str) or SHA256.fullmatch(report_sha256) is None):
            raise AcceptanceGateError("EVIDENCE_REPORT_DIGEST_INVALID")
        if status == "PASS" and not isinstance(report_sha256, str):
            raise AcceptanceGateError("EVIDENCE_PASS_WITHOUT_REPORT")
        check_statuses = item.get("check_statuses")
        if check_statuses is not None:
            declared_checks = next(item["required_checks"] for item in contract["gates"] if item["gate_id"] == gate_id)
            if not isinstance(check_statuses, dict) or set(check_statuses) != set(declared_checks) or any(value not in EVIDENCE_STATUSES for value in check_statuses.values()):
                raise AcceptanceGateError("EVIDENCE_CHECK_STATUS_INVALID")
            if status == "PASS" and any(value != "PASS" for value in check_statuses.values()):
                raise AcceptanceGateError("EVIDENCE_CHECK_STATUS_INVALID")
        normalized[gate_id] = {
            "status": status,
            "report_sha256": report_sha256,
            "provenance": _validate_gate_provenance(item.get("provenance"), gate_id=gate_id, evidence_source_commit=source_commit),
            "check_statuses": dict(check_statuses) if isinstance(check_statuses, dict) else None,
        }
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_class": value["evidence_class"],
        "platform": value["platform"],
        "source_commit": source_commit,
        "gates": normalized,
    }


def _validate_pass_report(
    path: Path,
    *,
    gate_id: str,
    source_commit: str,
    platform: str,
    expected_sha256: str,
    required_checks: list[str],
    capability_evidence: Mapping[str, Any] | None = None,
) -> None:
    raw = _read_bytes(path, max_bytes=MAX_REPORT_BYTES, unreadable_code="EVIDENCE_REPORT_MISSING", too_large_code="EVIDENCE_REPORT_TOO_LARGE")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise AcceptanceGateError("EVIDENCE_REPORT_DIGEST_MISMATCH")
    value = _parse_json(raw, invalid_code="EVIDENCE_REPORT_INVALID")
    expected_fields = {"schema_version", "gate_id", "status", "platform", "source_commit", "checks"}
    if capability_evidence is not None:
        expected_fields.add("capabilities")
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise AcceptanceGateError("EVIDENCE_REPORT_INVALID")
    report_schema = WEBVIEW_CAPABILITY_REPORT_SCHEMA_VERSION if capability_evidence is not None else REPORT_SCHEMA_VERSION
    if value.get("schema_version") != report_schema or value.get("gate_id") != gate_id or value.get("status") != "PASS":
        raise AcceptanceGateError("EVIDENCE_REPORT_BINDING_INVALID")
    if value.get("platform") != platform or value.get("source_commit") != source_commit:
        raise AcceptanceGateError("EVIDENCE_REPORT_BINDING_INVALID")
    checks = value.get("checks")
    if not isinstance(checks, dict) or not 1 <= len(checks) <= 128:
        raise AcceptanceGateError("EVIDENCE_REPORT_CHECKS_INVALID")
    check_keys = set(checks)
    required_keys = set(required_checks)
    if not required_keys.issubset(check_keys):
        raise AcceptanceGateError("EVIDENCE_REPORT_REQUIRED_CHECKS_MISSING")
    # A PASS report is a contract, not an arbitrary collection of true
    # booleans.  Requiring the exact declared check set prevents a synthetic
    # ``{"anything": true}`` report from satisfying a gate and keeps the
    # report/evidence binding reviewable.
    if check_keys != required_keys:
        raise AcceptanceGateError("EVIDENCE_REPORT_CHECKS_UNEXPECTED")
    for check_id, passed in checks.items():
        if not isinstance(check_id, str) or CHECK_ID.fullmatch(check_id) is None or passed is not True:
            raise AcceptanceGateError("EVIDENCE_REPORT_CHECKS_INVALID")
    if capability_evidence is not None:
        _validate_webview_capabilities(value.get("capabilities"), capability_evidence)


def _validate_webview_capabilities(value: Any, contract: Mapping[str, Any]) -> None:
    """Validate bounded native-host availability and real WebView layout QA.

    Native monitor DPI is a capability of the Windows test host, not a
    requirement that every host can force into existence.  A PASS therefore
    needs one actual native DPI and a real layout result for every requested
    WebView scale.  Unavailable native levels stay explicit evidence, never
    synthetic PASS booleans and never a product failure by themselves.
    """

    expected = {"schema_version", "native_host_dpi_current", "native_host_dpi", "webview_layout"}
    if not isinstance(value, dict) or set(value) != expected:
        raise AcceptanceGateError("EVIDENCE_WEBVIEW_CAPABILITY_INVALID")
    values = contract.get("native_dpi_values")
    scales = contract.get("layout_scales")
    unavailable = contract.get("native_unavailable_status")
    current = value.get("native_host_dpi_current")
    native = value.get("native_host_dpi")
    layout = value.get("webview_layout")
    if (
        value.get("schema_version") != contract.get("schema_version")
        or not isinstance(values, list)
        or not isinstance(scales, list)
        or not isinstance(current, int)
        or current not in values
        or not isinstance(native, dict)
        or not isinstance(layout, dict)
        or set(native) != {str(item) for item in values}
        or set(layout) != {str(item) for item in scales}
    ):
        raise AcceptanceGateError("EVIDENCE_WEBVIEW_CAPABILITY_INVALID")
    if native.get(str(current)) != "PASS":
        raise AcceptanceGateError("EVIDENCE_WEBVIEW_NATIVE_CURRENT_MISSING")
    if not any(status == "PASS" for status in native.values()):
        raise AcceptanceGateError("EVIDENCE_WEBVIEW_NATIVE_CURRENT_MISSING")
    for scale in values:
        status = native.get(str(scale))
        if status not in _WEBVIEW_NATIVE_STATUSES or (status == "NOT_AVAILABLE_ON_TEST_HOST" and status != unavailable):
            raise AcceptanceGateError("EVIDENCE_WEBVIEW_NATIVE_STATUS_INVALID")
    for scale in scales:
        item = layout.get(str(scale))
        if not isinstance(item, dict) or set(item) != {"status", "no_clipping", "no_overlap", "usable_controls"}:
            raise AcceptanceGateError("EVIDENCE_WEBVIEW_LAYOUT_INVALID")
        if item.get("status") != "PASS" or item.get("no_clipping") is not True or item.get("no_overlap") is not True or item.get("usable_controls") is not True:
            raise AcceptanceGateError("EVIDENCE_WEBVIEW_LAYOUT_FAILED")


def _verify_pass_reports(evidence: Mapping[str, Any], evidence_path: Path, contract: Mapping[str, Any], *, repo_root: Path) -> None:
    reports_dir = evidence_path.parent / REPORTS_DIR_NAME
    required_by_gate = {
        str(item["gate_id"]): item
        for item in contract["gates"]
        if item["required"] is True
    }
    for gate_id, item in evidence["gates"].items():
        if item["status"] != "PASS":
            continue
        provenance = item["provenance"]
        report_source_commit = str(provenance["origin_source_commit"])
        if provenance["kind"] == "REUSED_UNAFFECTED_EVIDENCE":
            changed_scopes = source_change_scopes(
                repo_root,
                origin_source_commit=report_source_commit,
                final_source_commit=str(evidence["source_commit"]),
            )
            if changed_scopes & _REUSE_ALWAYS_BLOCKING_SCOPES or changed_scopes & _GATE_IMPACT_SCOPES[gate_id]:
                raise AcceptanceGateError("EVIDENCE_REUSED_GATE_AFFECTED_BY_SOURCE_CHANGE")
        _validate_pass_report(
            reports_dir / f"{gate_id}.json",
            gate_id=gate_id,
            source_commit=report_source_commit,
            platform=str(evidence["platform"]),
            expected_sha256=str(item["report_sha256"]),
            required_checks=[str(check) for check in required_by_gate[gate_id]["required_checks"]],
            capability_evidence=required_by_gate[gate_id].get("capability_evidence"),
        )


def evaluate(
    *,
    evidence_path: Path | None = None,
    repo_root: Path = ROOT,
    phase: str = "integration",
    expected_commit: str | None = None,
    requested_version: str | None = None,
    requested_tag: str | None = None,
) -> dict[str, Any]:
    contract = load_gate_contract(repo_root / "architecture" / "v8_acceptance_gates.json")
    source = source_preflight(
        repo_root,
        phase=phase,
        expected_commit=expected_commit,
        requested_version=requested_version,
        requested_tag=requested_tag,
    )
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
        "report_integrity_verified": False,
        "source_commit_matches": None,
        "required": source["required_local_gates"],
        "passed": 0,
        "pending_gates": [item["gate_id"] for item in contract["gates"] if item["required"] is True],
        "pending_checks": {
            item["gate_id"]: list(item["required_checks"])
            for item in contract["gates"] if item["required"] is True
        },
    }
    evidence_blockers: list[str] = []
    # Missing evidence is a missing execution, not proof that the host lacks
    # native WebView2 capability.  Only an exact-head Windows capability report
    # may produce NOT_AVAILABLE_ON_TEST_HOST.
    native_unavailable = False
    if evidence_path is None:
        evidence_blockers.append("LOCAL_WINDOWS_EVIDENCE_REQUIRED")
    else:
        try:
            evidence = _validate_evidence(_load_json(evidence_path), contract)
            _verify_pass_reports(evidence, evidence_path, contract, repo_root=repo_root)
            pending = [gate_id for gate_id, item in evidence["gates"].items() if item["status"] != "PASS"]
            webview_evidence = evidence["gates"].get("webview2_product_ux", {})
            native_unavailable = (
                webview_evidence.get("status") == "NOT_AVAILABLE_ON_TEST_HOST"
                or any(
                    status == "NOT_AVAILABLE_ON_TEST_HOST"
                    for status in (webview_evidence.get("check_statuses") or {}).values()
                )
            )
            capability = webview_evidence.get("capabilities") if isinstance(webview_evidence, dict) else None
            native_statuses = capability.get("native_host_dpi") if isinstance(capability, dict) else None
            if isinstance(native_statuses, dict) and any(status == "NOT_AVAILABLE_ON_TEST_HOST" for status in native_statuses.values()):
                native_unavailable = True
            required_check_map = {
                item["gate_id"]: list(item["required_checks"])
                for item in contract["gates"] if item["required"] is True
            }
            pending_checks = {
                gate_id: (
                    [check_id for check_id, check_status in item["check_statuses"].items() if check_status != "PASS"]
                    if isinstance(item.get("check_statuses"), dict)
                    else required_check_map[gate_id]
                )
                for gate_id, item in evidence["gates"].items()
                if item["status"] != "PASS"
            }
            source_matches = evidence["source_commit"] == head
            evidence_summary.update({
                "valid": True,
                "reports_verified": True,
                "report_integrity_verified": True,
                "source_commit_matches": source_matches,
                "passed": len(evidence["gates"]) - len(pending),
                "pending_gates": pending,
                "pending_checks": pending_checks,
            })
            if not source_matches:
                evidence_blockers.append("EVIDENCE_SOURCE_COMMIT_MISMATCH")
            if pending:
                evidence_blockers.append("LOCAL_WINDOWS_GATES_INCOMPLETE")
        except AcceptanceGateError as exc:
            evidence_blockers.append(exc.code)

    technical_blockers = list(dict.fromkeys([*source.get("release_provenance", {}).get("technical_blockers", []), "SOURCE_PREFLIGHT_FAILED"] if source["valid"] is not True else source.get("release_provenance", {}).get("technical_blockers", [])))
    technical_ready = not technical_blockers
    merge_blockers = list(dict.fromkeys([*technical_blockers, *evidence_blockers]))
    merge_ready = not merge_blockers
    release_blockers = list(dict.fromkeys([*merge_blockers, *[str(item) for item in provenance.get("blockers", []) if isinstance(item, str)]]))
    release_ready = bool(phase in {"pre_tag", "post_tag"} and not release_blockers)
    tagged_release_ready = bool(phase == "post_tag" and release_ready)
    blockers = release_blockers if phase in {"pre_tag", "post_tag"} else merge_blockers
    if phase == "integration":
        status = "merge_ready" if merge_ready else ("technical_ready" if technical_ready else "blocked")
    else:
        status = "release_ready" if release_ready else "blocked"
    report_integrity_verified = evidence_summary["report_integrity_verified"] is True
    strict_acceptance = "PASS" if merge_ready else "BLOCKED"
    return {
        "schema_version": "v8-acceptance-preflight.v2",
        "status": status,
        "execution": "not_run",
        "dry_run": True,
        "strict_acceptance": strict_acceptance,
        "native_webview_automation": ("NOT_AVAILABLE_ON_TEST_HOST" if native_unavailable else "PASS") if evidence_summary["valid"] is True else "NOT_RUN",
        "report_integrity_verified": report_integrity_verified,
        "technical_merge_ready": technical_ready,
        "full_acceptance_merge_ready": bool(merge_ready and not native_unavailable),
        "source_commit": head,
        "source_preflight": source,
        "local_evidence": evidence_summary,
        "phase": phase,
        "technical_ready": technical_ready,
        "merge_ready": merge_ready,
        "release_ready": release_ready,
        "tagged_release_ready": tagged_release_ready,
        "technical_blockers": technical_blockers,
        "merge_blockers": merge_blockers,
        "release_blockers": release_blockers,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the read-only V8 acceptance/release gate.")
    parser.add_argument("--evidence", type=Path, default=None, help="Local evidence.json; PASS reports are read from sibling reports/<gate_id>.json and no path is emitted.")
    parser.add_argument("--source-only", action="store_true", help="Validate tracked source contracts without claiming release readiness.")
    parser.add_argument("--strict-release", action="store_true", help="Return success only when every release gate is satisfied.")
    parser.add_argument("--phase", choices=("integration", "pre_tag", "post_tag"), default="integration", help="Release identity phase.")
    parser.add_argument("--expected-commit", default=None, help="Expected commit for post-tag verification.")
    parser.add_argument("--candidate-version", default=None, help="Explicit version for pre-tag/post-tag validation.")
    parser.add_argument("--candidate-tag", default=None, help="Explicit tag for pre-tag/post-tag validation.")
    args = parser.parse_args()
    if args.source_only and args.strict_release:
        print(json.dumps({"status": "invalid", "code": "CLI_MODE_CONFLICT", "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 2
    try:
        result = evaluate(
            evidence_path=args.evidence,
            phase=args.phase,
            expected_commit=args.expected_commit,
            requested_version=args.candidate_version,
            requested_tag=args.candidate_tag,
        )
    except AcceptanceGateError as exc:
        print(json.dumps({"status": "blocked", "code": exc.code, "execution": "not_run", "dry_run": True}, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    if args.strict_release:
        ready = result["merge_ready"] if args.phase == "integration" else result["release_ready"]
        return 0 if ready is True else 1
    return 0 if result["source_preflight"]["valid"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AcceptanceGateError",
    "EVIDENCE_SCHEMA_VERSION",
    "GATES_PATH",
    "REPORT_SCHEMA_VERSION",
    "WEBVIEW_CAPABILITY_REPORT_SCHEMA_VERSION",
    "ROOT",
    "current_head",
    "evaluate",
    "load_gate_contract",
    "release_provenance_snapshot",
    "source_preflight",
]
