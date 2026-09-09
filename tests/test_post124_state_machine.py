from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import src.app.launcher_migration as launcher_migration
from src.app.launcher_migration import launcher_projection, shell_identity
from src.app.update_watchdog import _activate_deferred_product
from src.services.app_update import (
    AppUpdateError,
    _complete_bootstrap_marker,
    _read_bootstrap_pending,
    _write_bootstrap_pending,
    mark_startup_health,
)
from src.services.shortcut_migration import (
    LEGACY_VBS_CONTENT_SHA256,
    LEGACY_VBS_MARKER,
    MIGRATION_ADMIN_REQUIRED,
    MIGRATION_AMBIGUOUS,
    plan_shortcut_migration,
)
from src.services.storage_manager import overview
from src.shared.runtime_identity import API_PROTOCOL_VERSION
from src.shared.version import PRODUCT_VERSION


ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


class Post124StateMachineTests(unittest.TestCase):
    def test_launcher_projection_is_fail_closed_and_invalidates_attestation(self) -> None:
        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-manifest-") as temporary:
            root = Path(temporary) / "install"
            (root / "_internal").mkdir(parents=True)
            (root / "LocalAIHub.exe").write_bytes(b"shell")
            (root / "_internal" / "support.dll").write_bytes(b"support")
            self.assertEqual(launcher_projection(root)["status"], "present_unverified")
            (root / "launcher-manifest.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(launcher_projection(root)["status"], "present_unverified")
            actual = launcher_migration._shell_tree_manifest(root)
            manifest = shell_identity(actual, "main-" + "a" * 12, "a" * 40, 123)
            _write_json(root / "launcher-manifest.json", manifest)
            self.assertEqual(launcher_projection(root)["status"], "verified")
            false_hash = dict(manifest, executable_sha256="0" * 64)
            _write_json(root / "launcher-manifest.json", false_hash)
            self.assertEqual(launcher_projection(root)["status"], "present_unverified")
            _write_json(root / "launcher-manifest.json", manifest)
            self.assertEqual(launcher_projection(root)["status"], "verified")
            (root / "_internal" / "support.dll").write_bytes(b"changed")
            self.assertEqual(launcher_projection(root)["status"], "present_unverified")
            (root / "_internal" / "support.dll").unlink()
            self.assertEqual(launcher_projection(root)["status"], "present_unverified")

    def test_bootstrap_marker_terminal_transition_is_bound_and_mismatch_stays_visible(self) -> None:
        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-bootstrap-state-") as temporary:
            root = Path(temporary)
            pending = _write_bootstrap_pending(
                root, source_commit="a" * 40, workflow_run_id=123, status="staged",
                transaction_id="txn-" + "a" * 32, payload_id="main-" + "a" * 12, restart_authorized=True,
                consent_transaction_id="txn-" + "a" * 32, consent_payload_id="main-" + "a" * 12,
            )
            completed = _complete_bootstrap_marker(root, pending, current={"commit": "a" * 40, "workflow_run_id": 123, "payload_id": "main-" + "a" * 12})
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(_read_bootstrap_pending(root)["status"], "completed")
            self.assertTrue((root / "update-state" / "bootstrap-completed.json").is_file())

            mismatch = _write_bootstrap_pending(root, source_commit="b" * 40, workflow_run_id=124, status="staged", payload_id="main-" + "b" * 12)
            blocked = _complete_bootstrap_marker(root, mismatch, current={"commit": "a" * 40, "workflow_run_id": 123, "payload_id": "main-" + "a" * 12})
            self.assertEqual(blocked["status"], "blocked")
            self.assertEqual(_read_bootstrap_pending(root)["status"], "staged")

    def test_bootstrap_consent_is_rejected_when_transaction_binding_changes(self) -> None:
        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-bootstrap-consent-") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(AppUpdateError, "UPDATE_STATE_INVALID"):
                _write_bootstrap_pending(
                    root,
                    source_commit="a" * 40,
                    workflow_run_id=123,
                    status="staged",
                    transaction_id="txn-" + "a" * 32,
                    payload_id="main-" + "a" * 12,
                    restart_authorized=True,
                    consent_transaction_id="txn-" + "b" * 32,
                    consent_payload_id="main-" + "a" * 12,
                )

    def test_frontend_health_admission_commits_terminal_restart_audit_before_cleanup(self) -> None:
        from tests.test_post123_manager_corrections import Post123ManagerCorrectionTests

        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-health-admission-") as temporary:
            fixture = Post123ManagerCorrectionTests._deferred_fixture(Path(temporary) / "install")
            root = Path(fixture["root"])
            previous = fixture["previous"]
            transaction = dict(fixture["transaction"])
            candidate_pointer = {
                "schema_version": previous["schema_version"], "version": fixture["payload_id"],
                "payload_relative": f"versions/{fixture['payload_id']}", "manifest_sha256": transaction["manifest_sha256"],
            }
            _write_json(root / "current.json", candidate_pointer)
            _write_json(root / "update-state" / "pending-health.json", {
                "schema_version": "local-ai-hub-pending-health.v1", "payload_id": fixture["payload_id"],
                "source_commit": fixture["source_commit"], "previous": previous,
                "transaction_id": transaction["transaction_id"],
            })
            health = {
                "product_id": "LocalAIHub", "product_version": PRODUCT_VERSION,
                "api_protocol_version": API_PROTOCOL_VERSION, "app_user_model_id": "LocalAIHub.Desktop",
                "installation_id": "a" * 32, "build_source_commit": fixture["source_commit"],
                "build_payload_id": fixture["payload_id"],
            }
            result = mark_startup_health(root, health=health, frontend_ready=True)
            self.assertEqual(result["status"], "healthy")
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            self.assertFalse((root / "update-state" / "restart-transaction.json").exists())
            self.assertEqual(json.loads((root / "update-state" / "restart-completed.json").read_text(encoding="utf-8"))["status"], "completed")

    def test_retained_shell_identity_is_required_for_health_admission(self) -> None:
        from tests.test_post123_manager_corrections import Post123ManagerCorrectionTests

        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-retained-health-") as temporary:
            fixture = Post123ManagerCorrectionTests._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = Path(fixture["root"])
            self.assertTrue(_activate_deferred_product(root, fixture["transaction"]))
            health = {
                "product_id": "LocalAIHub", "product_version": PRODUCT_VERSION,
                "api_protocol_version": API_PROTOCOL_VERSION, "app_user_model_id": "LocalAIHub.Desktop",
                "installation_id": "a" * 32,
                "build_source_commit": fixture["source_commit"], "build_payload_id": fixture["payload_id"],
            }
            result = mark_startup_health(root, health=health, frontend_ready=True)
            self.assertEqual(result["status"], "healthy")
            projection = launcher_projection(root)
            self.assertEqual(projection["payload_id"], fixture["previous"]["version"])
            self.assertNotEqual(projection["payload_id"], fixture["payload_id"])

    def test_storage_frontier_push_pop_commits_are_batched(self) -> None:
        with TemporaryDirectory(dir=ROOT / "Temp", prefix="post124-frontier-") as temporary:
            root = Path(temporary)
            ledger = overview._IdentityLedger(root, "scan-batch")
            try:
                for index in range(2048):
                    ledger.push_frontier(root / f"dir-{index}", 1, (1, index + 1, index + 2), True)
                while ledger.pop_frontier() is not None:
                    pass
                self.assertLess(ledger.commit_count, 100)
            finally:
                ledger.close(cleanup=True)

    def test_shortcut_python_classifier_requires_historical_provenance(self) -> None:
        root = r"D:\LocalAIHub\fixture-install"
        arbitrary = {"name": "Local AI Hub.lnk", "target_path": r"C:\Windows\System32\wscript.exe", "arguments": root + r"\LocalAIHub.vbs", "scope": "all_users", "location_class": "all_users_start_menu"}
        self.assertEqual(plan_shortcut_migration(arbitrary, root)["status"], MIGRATION_AMBIGUOUS)
        proven = {
            "name": "Local AI Hub.lnk", "target_path": r"C:\Windows\System32\wscript.exe",
            "arguments": r"D:\LocalAIHub\Temp\legacy\LocalAIHub.vbs", "scope": "all_users",
            "location_class": "all_users_start_menu", "legacy_vbs_path": r"D:\LocalAIHub\Temp\legacy\LocalAIHub.vbs",
            "legacy_script_marker": LEGACY_VBS_MARKER, "legacy_script_sha256": LEGACY_VBS_CONTENT_SHA256,
        }
        self.assertEqual(plan_shortcut_migration(proven, root)["status"], MIGRATION_ADMIN_REQUIRED)


if __name__ == "__main__":
    unittest.main()
