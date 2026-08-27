"""Synthetic V8 Wave 1 component-lifecycle coordinator tests.

The fake installer performs no filesystem install, download, runtime or model
work. Tests cover only durable plan/operation state and fail-closed delegation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from src.services.component_lifecycle_v8 import ComponentLifecycleCoordinator
from src.services.transaction_store import V8TransactionStore


class FakeInstaller:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.confirm_calls = 0
        self.next_result: dict[str, object] = {
            "status": "completed",
            "state": "INSTALLED_UNVERIFIED",
            "execution": "completed",
            "operational": False,
        }

    @staticmethod
    def _fingerprint(component_id: str, action: str) -> str:
        return hashlib.sha256(f"{component_id}:{action}".encode("utf-8")).hexdigest()

    def _plan(self, component_id: str, component_type: str, action: str, prefix: str) -> dict[str, object]:
        plan_id = f"{prefix}_plan_0123456789abcdef0123456789abcdef"
        plan = {
            "schema_version": f"component-{action}-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": self._fingerprint(plan_id, action),
            "component_id": component_id,
            "component_type": component_type,
            "expected_state_fingerprint": self._fingerprint(component_id, action),
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
        }
        self.plans[plan_id] = dict(plan)
        return plan

    def lookup_plan(self, plan_id: str) -> dict[str, object] | None:
        value = self.plans.get(plan_id)
        return dict(value) if value is not None else None

    def plan_install(self, component_id: str, *, component_type: str, variant: str | None, catalog_binding: object = None) -> dict[str, object]:
        plan = self._plan(component_id, component_type, "install", "install")
        plan["auto_install_supported"] = False
        plan["download_bytes"] = 0
        plan["estimated_disk_bytes"] = 0
        return plan

    def plan_verify(self, component_id: str, *, component_type: str, catalog_binding: object = None) -> dict[str, object]:
        return self._plan(component_id, component_type, "verify", "verify")

    def plan_reuse(self, component_id: str, *, component_type: str, catalog_binding: object = None) -> dict[str, object]:
        return self._plan(component_id, component_type, "reuse", "reuse")

    def plan_maintenance(self, component_id: str, *, action: str, catalog_binding: object = None) -> dict[str, object]:
        return self._plan(component_id, "model", action, "maintenance")

    def _confirm(self, plan_id: str) -> dict[str, object]:
        self.confirm_calls += 1
        if plan_id not in self.plans:
            return {"status": "error", "code": "unknown_plan"}
        return dict(self.next_result)

    def confirm_plan(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        cancel_event: object = None,
        catalog_binding: object = None,
    ) -> dict[str, object]:
        del cancel_event
        return self._confirm(plan_id)

    def confirm_verify(self, plan_id: str, *, confirmed: bool, catalog_binding: object = None) -> dict[str, object]:
        return self._confirm(plan_id)

    def confirm_reuse(self, plan_id: str, *, confirmed: bool, catalog_binding: object = None) -> dict[str, object]:
        return self._confirm(plan_id)

    def confirm_maintenance(self, plan_id: str, *, confirmed: bool, catalog_binding: object = None) -> dict[str, object]:
        return self._confirm(plan_id)


class V8ComponentLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "control.sqlite3"
        self.store = V8TransactionStore(self.db)
        self.installer = FakeInstaller()
        self.lifecycle = ComponentLifecycleCoordinator(installer=self.installer, store=self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_install_plan_creates_path_free_durable_operation(self) -> None:
        plan = self.lifecycle.plan_install("sam2.1-hiera-small", component_type="model")
        self.assertRegex(str(plan["operation_id"]), r"^compop_[a-f0-9]{32}$")
        self.assertEqual(plan["action"], "install")
        operation = self.lifecycle.inspect_operation(str(plan["operation_id"]))
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["state"], "planned")
        serialized = repr(operation).lower()
        self.assertNotIn("path", serialized)
        self.assertNotIn(str(self.temp.name).lower(), serialized)

    def test_confirmation_is_required_before_executor_call(self) -> None:
        plan = self.lifecycle.plan_verify("sam2.1-hiera-small", component_type="model")
        operation_id = str(plan["operation_id"])
        result = self.lifecycle.confirm_operation(operation_id, confirmed=False)
        self.assertEqual(result["status"], "waiting_confirmation")
        self.assertEqual(self.installer.confirm_calls, 0)
        operation = self.lifecycle.inspect_operation(operation_id)
        self.assertEqual(operation["state"], "planned")

    def test_completed_executor_result_commits_operation_without_operational_promotion(self) -> None:
        plan = self.lifecycle.plan_reuse("sam2.1-hiera-small", component_type="model")
        operation_id = str(plan["operation_id"])
        result = self.lifecycle.confirm_operation(operation_id, confirmed=True)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["operational"])
        operation = self.lifecycle.inspect_operation(operation_id)
        self.assertEqual(operation["state"], "committed")
        self.assertEqual(operation["result_code"], "installed_unverified")
        self.assertEqual(self.installer.confirm_calls, 1)

    def test_unavailable_or_conflict_executor_result_becomes_blocked(self) -> None:
        self.installer.next_result = {
            "status": "unavailable",
            "code": "source_unavailable",
            "execution": "not_run",
        }
        plan = self.lifecycle.plan_maintenance("sam2.1-hiera-small", action="update")
        operation_id = str(plan["operation_id"])
        result = self.lifecycle.confirm_operation(operation_id, confirmed=True)
        self.assertEqual(result["status"], "unavailable")
        operation = self.lifecycle.inspect_operation(operation_id)
        self.assertEqual(operation["state"], "blocked")
        self.assertEqual(operation["result_code"], "source_unavailable")

    def test_terminal_operation_cannot_execute_twice(self) -> None:
        plan = self.lifecycle.plan_install("sam2.1-hiera-small", component_type="model")
        operation_id = str(plan["operation_id"])
        first = self.lifecycle.confirm_operation(operation_id, confirmed=True)
        self.assertEqual(first["status"], "completed")
        second = self.lifecycle.confirm_operation(operation_id, confirmed=True)
        self.assertEqual(second["status"], "conflict")
        self.assertEqual(self.installer.confirm_calls, 1)

    def test_operation_journal_survives_store_reopen(self) -> None:
        plan = self.lifecycle.plan_verify("sam2.1-hiera-small", component_type="model")
        operation_id = str(plan["operation_id"])
        reopened = V8TransactionStore(self.db)
        operation = reopened.component_operation(operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["plan_id"], plan["plan_id"])
        self.assertEqual(operation["state"], "planned")

    def test_restart_without_in_memory_plan_blocks_operation(self) -> None:
        plan = self.lifecycle.plan_install("sam2.1-hiera-small", component_type="model")
        operation_id = str(plan["operation_id"])
        fresh_installer = FakeInstaller()
        fresh = ComponentLifecycleCoordinator(installer=fresh_installer, store=V8TransactionStore(self.db))
        reconciliation = fresh.reconcile_startup()
        self.assertEqual(reconciliation["blocked"], 1)
        operation = fresh.inspect_operation(operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["state"], "blocked")
        self.assertEqual(operation["result_code"], "plan_session_lost")

    def test_real_lightweight_helper_lifecycle_is_actually_executed(self) -> None:
        """Exercise a safe installed-runtime-shaped helper, without models/GPU."""

        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; print('READY', flush=True); time.sleep(30)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(helper.stdout.readline().strip(), "READY")
            self.assertIsNone(helper.poll())
        finally:
            helper.terminate()
            try:
                helper.wait(timeout=3)
            except subprocess.TimeoutExpired:
                helper.kill()
                helper.wait(timeout=3)
            if helper.stdout is not None:
                helper.stdout.close()
            if helper.stderr is not None:
                helper.stderr.close()
        self.assertIsNotNone(helper.returncode)


if __name__ == "__main__":
    unittest.main()
