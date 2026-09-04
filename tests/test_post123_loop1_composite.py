from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from scripts.build_main_update import build
from src.app.launcher_migration import (
    activate_launcher_bundle,
    launcher_projection,
    restore_launcher_bundle,
)
from src.services.app_update import (
    PRODUCT_UPDATE_CONTRACT_SCHEMA,
    UPDATE_KIND_APP_AND_LAUNCHER,
    _safe_update_contract,
)


REPO = Path(__file__).resolve().parents[1]


class CompositeProductContractTests(unittest.TestCase):
    def test_app_only_remains_separate_and_composite_binds_launcher_and_run(self) -> None:
        temp_root = REPO / "Temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root, prefix="post123-loop1-") as temporary:
            root = Path(temporary)
            bundle = root / "LocalAIHub"
            (bundle / "_internal").mkdir(parents=True)
            (bundle / "LocalAIHub.exe").write_bytes(b"launcher")
            (bundle / "_internal" / "support.dll").write_bytes(b"support")
            with patch.dict("os.environ", {"GITHUB_SHA": "a" * 40}, clear=False):
                manifest = build(root / "composite", update_kind=UPDATE_KIND_APP_AND_LAUNCHER, launcher_bundle=bundle, workflow_run_id=123)
            self.assertEqual(manifest["schema_version"], "local-ai-hub-composite-update.v1")
            self.assertEqual(manifest["workflow_run_id"], 123)
            contract = json.loads((root / "composite" / "update-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(contract["schema_version"], PRODUCT_UPDATE_CONTRACT_SCHEMA)
            self.assertEqual(contract["update_kind"], UPDATE_KIND_APP_AND_LAUNCHER)
            self.assertEqual(contract["workflow_run_id"], 123)
            self.assertEqual(contract["launcher_format"], "onedir")
            self.assertEqual(_safe_update_contract(contract)["launcher_tree_manifest_sha256"], contract["launcher_tree_manifest_sha256"])
            with zipfile.ZipFile(root / "composite" / "LocalAIHub-main-update.zip") as archive:
                names = archive.namelist()
            self.assertIn("launcher/LocalAIHub/LocalAIHub.exe", names)
            self.assertIn("launcher/LocalAIHub/_internal/support.dll", names)


class LauncherMigrationTests(unittest.TestCase):
    def test_activation_switches_complete_onedir_and_restores_legacy_shell(self) -> None:
        temp_root = REPO / "Temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root, prefix="post123-shell-") as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            (root / "LocalAIHub.exe").write_bytes(b"legacy")
            candidate = root / "versions" / "main-aaaaaaaaaaaa" / "launcher" / "LocalAIHub"
            (candidate / "_internal").mkdir(parents=True)
            (candidate / "LocalAIHub.exe").write_bytes(b"new")
            (candidate / "_internal" / "support.dll").write_bytes(b"support")
            result = activate_launcher_bundle(
                root, candidate, transaction_id="txn-" + "a" * 32,
                payload_id="main-aaaaaaaaaaaa", source_commit="a" * 40,
            )
            self.assertEqual(result["status"], "shell_switched")
            self.assertEqual(launcher_projection(root)["format"], "onedir")
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"new")
            restored = restore_launcher_bundle(root, transaction_id="txn-" + "a" * 32)
            self.assertEqual(restored["status"], "restored")
            self.assertEqual(launcher_projection(root)["format"], "single_file")
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy")


class JobExceptionBoundaryTests(unittest.TestCase):
    def test_cleanup_failure_does_not_mask_original_worker_exception(self) -> None:
        from src.services.job_manager import manager as manager_module

        manager = manager_module.HubJobManager()
        context = manager_module.JobContext("jobv5_" + "a" * 32)
        updates: list[dict[str, object]] = []

        def worker(_payload, _context):
            raise RuntimeError("original-worker-failure")

        with (
            patch.object(manager_module, "update_job", side_effect=lambda _job_id, **values: updates.append(values) or {"id": _job_id, **values}),
            patch.object(manager_module.artifact_store, "reconcile_job_output_scopes"),
            patch.object(manager_module.artifact_store, "finalize_job_output_scope", side_effect=OSError("cleanup-failure")),
            patch.object(manager_module, "record_failed", side_effect=OSError("evidence-failure")),
        ):
            manager._run(context.job_id, "probe_media", {}, worker, context, False)

        failures = [item for item in updates if item.get("status") == "failed"]
        self.assertTrue(failures)
        self.assertIn("original-worker-failure", str(failures[-1].get("error")))
        self.assertEqual(failures[-1]["result"]["failure_code"], "WORKER_EXCEPTION")
        self.assertEqual(failures[-1]["result"]["cleanup_status"], "failed")


if __name__ == "__main__":
    unittest.main()
