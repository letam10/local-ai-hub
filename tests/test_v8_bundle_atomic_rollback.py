"""V8 composite bundle compensation tests on a fake server-owned installer."""

from __future__ import annotations

from typing import Any
import unittest

from src.services.component_installer.bundle import ComponentBundleService


class _Installer:
    def __init__(self, *, existing_runtime: bool = False, rollback_fails: bool = False, model_raises: bool = False) -> None:
        self.states = {
            ("runtime", "demo-runtime"): "INSTALLED_UNVERIFIED" if existing_runtime else "UNAVAILABLE",
            ("model", "demo-model"): "UNAVAILABLE",
        }
        self.rollback_fails = rollback_fails
        self.model_raises = model_raises
        self.install_calls: list[tuple[str, str]] = []
        self.rollback_calls: list[tuple[str, str]] = []
        self._plans: dict[str, dict[str, str]] = {}

    def _catalog_record(self, component_id: str, component_type: str) -> dict[str, object]:
        if (component_type, component_id) not in self.states:
            raise ValueError("unknown component")
        if component_type == "model":
            return {
                "component_id": component_id,
                "runtime_id": "demo-runtime",
                "disposition": "AUTO_INSTALL_READY",
                "install_strategy": "fixture",
                "estimated_download_size": 1,
                "estimated_disk_size": 1,
            }
        return {
            "component_id": component_id,
            "disposition": "AUTO_INSTALL_READY",
            "install_strategy": "fixture",
            "estimated_download_size": 1,
            "estimated_disk_size": 1,
        }

    def _inspect(self, component_id: str, component_type: str) -> dict[str, str]:
        return {"status": self.states[(component_type, component_id)]}

    @staticmethod
    def _auto_install_ready(_record: object) -> bool:
        return True

    def plan_install(self, component_id: str, *, component_type: str, variant: str) -> dict[str, str]:
        del variant
        plan_id = f"install-{component_type}-{component_id}"
        self._plans[plan_id] = {"action": "install", "component_id": component_id, "component_type": component_type}
        return {"plan_id": plan_id}

    def confirm_plan(self, plan_id: str, *, confirmed: bool) -> dict[str, str]:
        self.assert_confirmed(confirmed)
        plan = self._plans[plan_id]
        node = (plan["component_type"], plan["component_id"])
        self.install_calls.append(node)
        if node == ("model", "demo-model"):
            if self.model_raises:
                raise RuntimeError("synthetic installer error")
            return {"status": "failed", "code": "injected_model_failure", "execution": "not_run"}
        self.states[node] = "INSTALLED_UNVERIFIED"
        return {"status": "completed", "state": "INSTALLED_UNVERIFIED", "execution": "completed"}

    def plan_maintenance(self, component_id: str, *, action: str) -> dict[str, str]:
        if action != "uninstall":
            raise AssertionError("rollback must use uninstall")
        plan_id = f"uninstall-runtime-{component_id}"
        self._plans[plan_id] = {"action": "uninstall", "component_id": component_id, "component_type": "runtime"}
        return {"plan_id": plan_id}

    def confirm_maintenance(self, plan_id: str, *, confirmed: bool) -> dict[str, str]:
        self.assert_confirmed(confirmed)
        plan = self._plans[plan_id]
        node = (plan["component_type"], plan["component_id"])
        self.rollback_calls.append(node)
        if self.rollback_fails:
            return {"status": "failed", "code": "injected_rollback_failure", "execution": "not_run"}
        self.states[node] = "UNAVAILABLE"
        return {"status": "completed", "execution": "completed"}

    @staticmethod
    def assert_confirmed(value: bool) -> None:
        if value is not True:
            raise AssertionError("expected explicit confirmation")


class V8BundleAtomicRollbackTests(unittest.TestCase):
    def test_new_dependency_is_rolled_back_when_later_component_fails(self) -> None:
        installer = _Installer()
        service = ComponentBundleService(installer)
        plan = service.plan("demo-model", component_type="model")
        result = service.confirm(str(plan["plan_id"]), confirmed=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "injected_model_failure")
        self.assertEqual(installer.install_calls, [("runtime", "demo-runtime"), ("model", "demo-model")])
        self.assertEqual(installer.rollback_calls, [("runtime", "demo-runtime")])
        self.assertEqual(installer.states[("runtime", "demo-runtime")], "UNAVAILABLE")
        self.assertEqual(result["rollback"], [{"component_id": "demo-runtime", "component_type": "runtime", "status": "rolled_back", "code": None, "execution": "completed"}])

    def test_preexisting_shared_dependency_is_not_rolled_back(self) -> None:
        installer = _Installer(existing_runtime=True)
        service = ComponentBundleService(installer)
        plan = service.plan("demo-model", component_type="model")
        result = service.confirm(str(plan["plan_id"]), confirmed=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(installer.install_calls, [("model", "demo-model")])
        self.assertEqual(installer.rollback_calls, [])
        self.assertEqual(installer.states[("runtime", "demo-runtime")], "INSTALLED_UNVERIFIED")
        self.assertEqual(result["rollback"], [])

    def test_rollback_failure_returns_manual_review_with_bounded_steps(self) -> None:
        installer = _Installer(rollback_fails=True)
        service = ComponentBundleService(installer)
        plan = service.plan("demo-model", component_type="model")
        result = service.confirm(str(plan["plan_id"]), confirmed=True)

        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(result["code"], "bundle_rollback_incomplete")
        self.assertEqual(installer.rollback_calls, [("runtime", "demo-runtime")])
        self.assertEqual(result["rollback"][0]["status"], "manual_review")

    def test_exception_in_later_step_still_rolls_back_new_dependency(self) -> None:
        installer = _Installer(model_raises=True)
        service = ComponentBundleService(installer)
        plan = service.plan("demo-model", component_type="model")
        result = service.confirm(str(plan["plan_id"]), confirmed=True)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "bundle_step_exception")
        self.assertEqual(installer.rollback_calls, [("runtime", "demo-runtime")])
        self.assertEqual(installer.states[("runtime", "demo-runtime")], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
