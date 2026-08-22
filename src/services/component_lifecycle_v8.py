"""V8 durable component-lifecycle coordinator.

The coordinator keeps the existing V7 ComponentInstaller as the execution
implementation, but moves user-visible lifecycle authority behind an opaque,
path-free operation journal. API/UI wiring is intentionally a later wave.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from src.services.transaction_store import TransactionStoreError, V8TransactionStore


_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_TERMINAL = frozenset({"committed", "failed", "blocked", "cancelled"})
_ACTIVE = frozenset({"planned", "executing", "verifying"})
_PLAN_PUBLIC_KEYS = frozenset(
    {
        "schema_version",
        "plan_id",
        "plan_fingerprint",
        "status",
        "execution",
        "dry_run",
        "component_id",
        "component_type",
        "existing_status",
        "current_status",
        "auto_install_supported",
        "download_bytes",
        "estimated_disk_bytes",
        "dependencies",
        "warnings",
        "reason",
        "next_action",
        "action",
        "mode",
    }
)
_RESULT_PUBLIC_KEYS = frozenset(
    {
        "status",
        "code",
        "action",
        "component_id",
        "component_type",
        "state",
        "execution",
        "dry_run",
        "verified",
        "operational",
        "receipt",
        "next_action",
    }
)


class ComponentLifecycleError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _safe_code(value: object, fallback: str) -> str:
    if isinstance(value, str):
        candidate = re.sub(r"[^a-z0-9_-]+", "_", value.strip().lower()).strip("_")
        if candidate and not candidate[0].isalpha():
            candidate = f"result_{candidate}"
        candidate = candidate[:48]
        if _SAFE_CODE.fullmatch(candidate):
            return candidate
    return fallback


def _project(value: Mapping[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    """Keep a bounded path-free projection from server-owned lifecycle results."""

    result: dict[str, Any] = {}
    for key in keys:
        if key not in value:
            continue
        item = value.get(key)
        if isinstance(item, (bool, int, float)) or item is None:
            result[key] = item
        elif isinstance(item, str):
            if "\x00" not in item and len(item) <= 800:
                result[key] = item
        elif key in {"dependencies", "warnings"} and isinstance(item, list):
            clean: list[Any] = []
            for child in item[:64]:
                if isinstance(child, str) and len(child) <= 300:
                    clean.append(child)
                elif isinstance(child, Mapping):
                    projected: dict[str, Any] = {}
                    for child_key, child_value in child.items():
                        if (
                            isinstance(child_key, str)
                            and child_key not in {"path", "url", "command", "executable"}
                            and isinstance(child_value, (str, int, bool, type(None)))
                        ):
                            projected[child_key] = child_value
                    clean.append(projected)
            result[key] = clean
    return result


class ComponentLifecycleCoordinator:
    """Durably bind an opaque operation to one server-owned component plan."""

    def __init__(
        self,
        *,
        installer: Any | None = None,
        store: V8TransactionStore | None = None,
    ) -> None:
        if installer is None:
            from src.services.component_installer.manager import ComponentInstaller

            installer = ComponentInstaller()
        self.installer = installer
        self.store = store or V8TransactionStore.for_paths(getattr(installer, "paths", None))

    def _register(
        self,
        plan: Mapping[str, Any],
        *,
        component_id: str,
        component_type: str,
        action: str,
    ) -> dict[str, Any]:
        plan_id = plan.get("plan_id")
        expected = plan.get("expected_state_fingerprint")
        if not isinstance(plan_id, str) or not isinstance(expected, str):
            raise ComponentLifecycleError("plan_contract_incomplete")
        try:
            operation_id = self.store.create_component_operation(
                plan_id=plan_id,
                component_id=component_id,
                component_type=component_type,
                action=action,
                expected_state_fingerprint=expected,
            )
        except TransactionStoreError as exc:
            raise ComponentLifecycleError(exc.code) from exc
        public = _project(plan, _PLAN_PUBLIC_KEYS)
        public.update(
            {
                "operation_id": operation_id,
                "component_id": component_id,
                "component_type": component_type,
                "action": action,
                "operation_state": "planned",
            }
        )
        return public

    def plan_install(
        self,
        component_id: str,
        *,
        component_type: str = "model",
        variant: str | None = None,
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        plan = self.installer.plan_install(
            component_id,
            component_type=component_type,
            variant=variant,
            catalog_binding=catalog_binding,
        )
        return self._register(plan, component_id=component_id, component_type=component_type, action="install")

    def plan_verify(
        self,
        component_id: str,
        *,
        component_type: str,
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        plan = self.installer.plan_verify(
            component_id,
            component_type=component_type,
            catalog_binding=catalog_binding,
        )
        return self._register(plan, component_id=component_id, component_type=component_type, action="verify")

    def plan_reuse(
        self,
        component_id: str,
        *,
        component_type: str = "model",
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        plan = self.installer.plan_reuse(
            component_id,
            component_type=component_type,
            catalog_binding=catalog_binding,
        )
        return self._register(plan, component_id=component_id, component_type=component_type, action="reuse")

    def plan_maintenance(
        self,
        component_id: str,
        *,
        action: str,
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        if action not in {"repair", "update", "uninstall"}:
            raise ComponentLifecycleError("invalid_maintenance_action")
        plan = self.installer.plan_maintenance(
            component_id,
            action=action,
            catalog_binding=catalog_binding,
        )
        component_type = plan.get("component_type")
        if component_type not in {"model", "runtime"}:
            raise ComponentLifecycleError("plan_contract_incomplete")
        return self._register(
            plan,
            component_id=component_id,
            component_type=str(component_type),
            action=action,
        )

    def inspect_operation(self, operation_id: str) -> dict[str, Any] | None:
        return self.store.component_operation(operation_id)

    def list_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_component_operations(limit=limit)

    def _delegate(
        self,
        operation: Mapping[str, Any],
        *,
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        plan_id = str(operation["plan_id"])
        action = str(operation["action"])
        if action == "install":
            value = self.installer.confirm_plan(plan_id, confirmed=True, catalog_binding=catalog_binding)
        elif action == "verify":
            value = self.installer.confirm_verify(plan_id, confirmed=True, catalog_binding=catalog_binding)
        elif action == "reuse":
            value = self.installer.confirm_reuse(plan_id, confirmed=True, catalog_binding=catalog_binding)
        elif action in {"repair", "update", "uninstall"}:
            value = self.installer.confirm_maintenance(plan_id, confirmed=True, catalog_binding=catalog_binding)
        else:
            raise ComponentLifecycleError("unsupported_component_action")
        return dict(value) if isinstance(value, Mapping) else {"status": "error", "code": "invalid_executor_result"}

    @staticmethod
    def _terminal_target(result: Mapping[str, Any]) -> tuple[str, str]:
        status = str(result.get("status") or "").lower()
        if status == "completed":
            state = str(result.get("state") or "")
            return "committed", _safe_code(state, "completed")
        if status == "cancelled":
            return "cancelled", _safe_code(result.get("code"), "cancelled")
        if status in {"unavailable", "conflict", "waiting_confirmation"}:
            return "blocked", _safe_code(result.get("code"), status if status != "waiting_confirmation" else "blocked")
        return "failed", _safe_code(result.get("code"), "executor_failed")

    def confirm_operation(
        self,
        operation_id: str,
        *,
        confirmed: bool = False,
        catalog_binding: Any | None = None,
    ) -> dict[str, Any]:
        operation = self.store.component_operation(operation_id)
        if operation is None:
            return {"status": "error", "code": "unknown_component_operation", "execution": "not_run"}
        state = str(operation.get("state") or "")
        if state != "planned":
            return {
                "status": "conflict",
                "code": "component_operation_not_planned",
                "operation": operation,
                "execution": "not_run",
            }
        if not confirmed:
            return {
                "status": "waiting_confirmation",
                "operation": operation,
                "execution": "not_run",
                "dry_run": True,
            }

        lookup = getattr(self.installer, "lookup_plan", None)
        if not callable(lookup) or lookup(str(operation["plan_id"])) is None:
            self.store.transition_component_operation(
                operation_id,
                expected_state="planned",
                target_state="blocked",
                result_code="plan_session_lost",
            )
            return {
                "status": "unavailable",
                "code": "plan_session_lost",
                "operation": self.store.component_operation(operation_id),
                "execution": "not_run",
            }

        if not self.store.transition_component_operation(
            operation_id,
            expected_state="planned",
            target_state="executing",
        ):
            return {
                "status": "conflict",
                "code": "component_operation_changed",
                "operation": self.store.component_operation(operation_id),
                "execution": "not_run",
            }

        try:
            result = self._delegate(operation, catalog_binding=catalog_binding)
        except Exception:
            result = {"status": "error", "code": "component_executor_exception"}

        target, code = self._terminal_target(result)
        if target == "committed":
            if self.store.transition_component_operation(
                operation_id,
                expected_state="executing",
                target_state="verifying",
            ):
                self.store.transition_component_operation(
                    operation_id,
                    expected_state="verifying",
                    target_state="committed",
                    result_code=code,
                )
        else:
            self.store.transition_component_operation(
                operation_id,
                expected_state="executing",
                target_state=target,
                result_code=code,
            )

        public = _project(result, _RESULT_PUBLIC_KEYS)
        public["operation"] = self.store.component_operation(operation_id)
        public.setdefault("execution", "not_run" if target != "committed" else result.get("execution", "not_run"))
        return public

    def reconcile_startup(self) -> dict[str, int]:
        """Fail closed for operations whose in-memory V7 plan did not survive restart."""

        blocked = 0
        retained = 0
        for operation in self.store.list_component_operations(limit=500):
            state = str(operation.get("state") or "")
            if state in _TERMINAL:
                retained += 1
                continue
            if state not in _ACTIVE:
                continue
            operation_id = str(operation.get("operation_id") or "")
            if not operation_id:
                continue
            lookup = getattr(self.installer, "lookup_plan", None)
            plan_exists = callable(lookup) and lookup(str(operation.get("plan_id") or "")) is not None
            if state == "planned" and plan_exists:
                retained += 1
                continue
            if self.store.transition_component_operation(
                operation_id,
                expected_state=state,
                target_state="blocked",
                result_code="plan_session_lost",
            ):
                blocked += 1
        return {"blocked": blocked, "retained": retained}


__all__ = ["ComponentLifecycleCoordinator", "ComponentLifecycleError"]
