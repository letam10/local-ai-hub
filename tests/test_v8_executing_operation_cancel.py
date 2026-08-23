"""V8 executing component-operation cancellation bridge tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import threading
import unittest

from src.services.component_lifecycle_v8 import ComponentLifecycleCoordinator
from src.services.transaction_store import V8TransactionStore


class _CancellableInstaller:
    def __init__(self) -> None:
        self.plans: dict[str, dict[str, object]] = {}
        self.started = threading.Event()
        self.confirm_calls = 0

    def plan_install(self, component_id: str, *, component_type: str, variant: str | None, catalog_binding: object = None) -> dict[str, object]:
        del variant, catalog_binding
        plan_id = "install_plan_0123456789abcdef0123456789abcdef"
        plan = {
            "schema_version": "component-install-plan.v1",
            "plan_id": plan_id,
            "plan_fingerprint": hashlib.sha256(plan_id.encode("ascii")).hexdigest(),
            "component_id": component_id,
            "component_type": component_type,
            "expected_state_fingerprint": hashlib.sha256(component_id.encode("ascii")).hexdigest(),
            "status": "planned",
            "execution": "not_run",
            "dry_run": True,
        }
        self.plans[plan_id] = dict(plan)
        return plan

    def lookup_plan(self, plan_id: str) -> dict[str, object] | None:
        value = self.plans.get(plan_id)
        return dict(value) if value is not None else None

    def confirm_plan(
        self,
        plan_id: str,
        *,
        confirmed: bool,
        cancel_event: threading.Event | None = None,
        catalog_binding: object = None,
    ) -> dict[str, object]:
        del plan_id, catalog_binding
        self.confirm_calls += 1
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run"}
        self.started.set()
        if cancel_event is None or not cancel_event.wait(3):
            return {"status": "failed", "code": "cancel_event_missing", "execution": "not_run"}
        return {"status": "cancelled", "code": "cancelled", "execution": "not_run"}


class V8ExecutingOperationCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = V8TransactionStore(Path(self.temp.name) / "control.sqlite3")
        self.installer = _CancellableInstaller()
        self.lifecycle = ComponentLifecycleCoordinator(installer=self.installer, store=self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan(self) -> str:
        planned = self.lifecycle.plan_install("demo-model", component_type="model")
        return str(planned["operation_id"])

    def test_executing_install_cancel_uses_exact_owned_event_and_finishes_cancelled(self) -> None:
        operation_id = self._plan()
        completed: dict[str, object] = {}

        def confirm() -> None:
            completed.update(self.lifecycle.confirm_operation(operation_id, confirmed=True))

        worker = threading.Thread(target=confirm, name="v8-component-cancel-test")
        worker.start()
        self.assertTrue(self.installer.started.wait(2))
        requested = self.lifecycle.cancel_operation(operation_id)
        self.assertEqual(requested["status"], "cancelling")
        self.assertEqual(requested["code"], "component_operation_cancellation_requested")
        self.assertEqual(requested["execution"], "running")
        self.assertNotIn("job_id", requested)
        self.assertNotIn("path", str(requested).lower())

        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(completed["status"], "cancelled")
        operation = self.lifecycle.inspect_operation(operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["state"], "cancelled")
        self.assertEqual(self.installer.confirm_calls, 1)

    def test_executing_operation_without_owned_bridge_is_not_force_cancelled(self) -> None:
        operation_id = self._plan()
        self.assertTrue(
            self.store.transition_component_operation(
                operation_id,
                expected_state="planned",
                target_state="executing",
            )
        )
        result = self.lifecycle.cancel_operation(operation_id)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["code"], "component_operation_not_cancellable")
        self.assertEqual(self.lifecycle.inspect_operation(operation_id)["state"], "executing")
        self.assertEqual(self.installer.confirm_calls, 0)


if __name__ == "__main__":
    unittest.main()
