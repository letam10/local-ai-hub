"""Synthetic V8 Wave 2 production-callsite migration tests.

No model/runtime download, network service, GPU inference, browser or external
application is started.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.platform.paths import HubPaths
from src.services import artifact_store
from src.services.artifact_access_v8 import (
    install_v8_artifact_compatibility,
    uninstall_v8_artifact_compatibility_for_tests,
)
from src.services.component_lifecycle_v8 import ComponentLifecycleCoordinator
from src.services.output_authority_v8 import ProductionOutputAuthority
from src.services.v8_output_bridge import (
    V8OutputBridge,
    reset_default_bridge_for_tests,
    set_default_bridge_for_tests,
)
from src.services.v8_transaction_extensions import V8ProductionTransactionStore


def _job(char: str) -> str:
    return "jobv5_" + char * 32


def _provenance(job_id: str) -> dict[str, object]:
    return {
        "job_id": job_id,
        "job_spec_fingerprint": hashlib.sha256(job_id.encode("utf-8")).hexdigest(),
        "adapter_id": "test.synthetic.v8",
        "attempt": 1,
        "status": "completed",
    }


class _FakeInstaller:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.confirm_calls = 0

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def plan_install(self, component_id: str, *, component_type: str, variant: str | None, catalog_binding: object = None) -> dict[str, object]:
        plan_id = "install_plan_0123456789abcdef0123456789abcdef"
        value = {
            "schema_version": "component-install-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": self._fingerprint(plan_id),
            "component_id": component_id,
            "component_type": component_type,
            "expected_state_fingerprint": self._fingerprint(component_id),
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
            "auto_install_supported": False,
        }
        self.plans[plan_id] = dict(value)
        return value

    def lookup_plan(self, plan_id: str) -> dict[str, object] | None:
        value = self.plans.get(plan_id)
        return dict(value) if value is not None else None

    def confirm_plan(self, plan_id: str, *, confirmed: bool, catalog_binding: object = None) -> dict[str, object]:
        self.confirm_calls += 1
        return {
            "status": "completed",
            "state": "INSTALLED_UNVERIFIED",
            "execution": "completed",
            "operational": False,
        }


class V8Wave2MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.paths = HubPaths(app_root=root / "app", data_root=root / "data")
        self.paths.app_root.mkdir()
        self.paths.data_root.mkdir()
        self.store = V8ProductionTransactionStore(root / "control.sqlite3")
        self.authority = ProductionOutputAuthority(paths=self.paths, store=self.store)
        self.bridge = V8OutputBridge(self.authority)

    def tearDown(self) -> None:
        uninstall_v8_artifact_compatibility_for_tests()
        reset_default_bridge_for_tests()
        self.temp.cleanup()

    def test_aborted_missing_object_row_is_dropped_once(self) -> None:
        job_id = _job("a")
        reservation = self.store.create_output_reservation(job_id)
        transaction = self.store.create_output_transaction(reservation, job_id)
        lease = self.authority.storage.lease("output", create=True)
        object_id = "obj_" + "a" * 32
        object_key = f".hub-v8/objects/aa/{object_id}"
        path = self.authority.storage.resolve_relative(lease, object_key, require_exists=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"staged")
        identity = self.authority.storage.file_identity(lease, object_key)
        artifact_id = self.store.stage_artifact(
            transaction_id=transaction,
            reservation_id=reservation,
            job_id=job_id,
            object_id=object_id,
            object_key=object_key,
            name="result.bin",
            media_type="application/octet-stream",
            size_bytes=6,
            sha256=hashlib.sha256(b"staged").hexdigest(),
            file_device=identity.device,
            file_inode=identity.inode,
            file_mtime_ns=identity.mtime_ns,
            provenance=_provenance(job_id),
        )
        self.assertTrue(self.store.abort_output_transaction(transaction, reservation, job_id))
        self.assertTrue(self.authority.storage.unlink_if_identity(lease, object_key, identity))

        first = self.authority.reconcile_incomplete()
        self.assertEqual(first["manual_review"], 0)
        self.assertEqual(first["dropped_rows"], 1)
        self.assertEqual(self.store.incomplete_artifacts(), [])
        self.assertIsNone(self.store.internal_artifact(artifact_id))

        second = self.authority.reconcile_incomplete()
        self.assertEqual(second["manual_review"], 0)
        self.assertEqual(second["dropped_rows"], 0)

    def test_legacy_worker_publish_signature_requires_v8_reservation(self) -> None:
        output = self.paths.output_root
        output.mkdir(parents=True, exist_ok=True)
        legacy_index = self.paths.config_root / "artifacts.json"
        legacy_index.parent.mkdir(parents=True, exist_ok=True)
        set_default_bridge_for_tests(self.bridge)
        install_v8_artifact_compatibility()

        job_id = _job("b")
        producer = output / "producer.bin"
        producer.write_bytes(b"owned")
        with patch.object(artifact_store, "OUTPUT_ROOT", output), patch.object(artifact_store, "INDEX_PATH", legacy_index):
            self.assertIsNone(
                artifact_store.register_worker_outputs([producer], provenance=_provenance(job_id))
            )
            self.assertIsNotNone(artifact_store.begin_job_output_scope(job_id))
            prepared = artifact_store.prepare_job_output_scope(
                job_id, {"status": "completed", "output": str(producer)}
            )
            self.assertEqual(prepared["status"], "owned")
            published = artifact_store.register_worker_outputs(
                [producer], provenance=_provenance(job_id)
            )
            self.assertIsInstance(published, list)
            assert published is not None
            self.assertEqual(len(published), 1)
            artifact_id = str(published[0]["id"])
            self.assertIsNotNone(artifact_store.resolve(artifact_id))
            self.assertIsNotNone(artifact_store.describe(artifact_id))
            self.assertFalse(legacy_index.exists())
            artifact_store.finalize_job_output_scope(
                job_id,
                {"status": "completed", "output": str(producer)},
                terminal_state="completed",
                published=True,
            )

    def test_component_api_plan_id_confirms_v8_operation(self) -> None:
        from src.services.api import components as component_api

        fake = _FakeInstaller()
        lifecycle = ComponentLifecycleCoordinator(installer=fake, store=self.store)
        with patch.object(component_api, "_manager", fake), patch.object(component_api, "_lifecycle", lifecycle):
            plan = component_api.plan_install("sam2.1-hiera-small", component_type="model")
            operation_id = str(plan["operation_id"])
            looked_up = component_api.lookup_plan(str(plan["plan_id"]))
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertEqual(looked_up["operation_id"], operation_id)
            result = component_api.confirm_install(str(plan["plan_id"]), confirmed=True)
            self.assertEqual(result["status"], "completed")
            operation = lifecycle.inspect_operation(operation_id)
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual(operation["state"], "committed")
            self.assertEqual(fake.confirm_calls, 1)

    def test_production_package_exports_v8_durable_engine(self) -> None:
        from src.services.job_manager import DurableWorkEngine, LegacyDurableWorkEngine, V8DurableWorkEngine

        self.assertIs(DurableWorkEngine, V8DurableWorkEngine)
        self.assertIsNot(DurableWorkEngine, LegacyDurableWorkEngine)


if __name__ == "__main__":
    unittest.main()
