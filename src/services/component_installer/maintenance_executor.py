"""Bounded production maintenance executor for managed components.

Repair and uninstall are deliberately conservative.  Repair never fabricates
missing bytes: it can re-verify and refresh a receipt when the owned leaves are
still present, otherwise it asks for a trusted source/manual import.  Uninstall
deletes only catalog-owned ordinary files after a stale-state and shared
dependency check.  Update activation remains owned by the update candidate
executor and refuses when no immutable candidate is available.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
from typing import Any

from src.platform.paths import HubPaths


def _is_reparse(path: Path) -> bool:
    try:
        if stat.S_ISLNK(path.lstat().st_mode):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if os.name == "nt":
        try:
            import ctypes
            attrs = int(ctypes.windll.kernel32.GetFileAttributesW(str(path))) & 0xFFFFFFFF
            return attrs != 0xFFFFFFFF and bool(attrs & 0x400)
        except (AttributeError, OSError):
            return True
    return False


def _safe_leaf(root: Path, relative: str) -> Path | None:
    try:
        root = root.absolute()
        candidate = (root / Path(relative)).absolute()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    current = candidate
    while True:
        if _is_reparse(current):
            return None
        if current == root:
            break
        if current.parent == current:
            return None
        current = current.parent
    try:
        candidate.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".component-maintenance-", suffix=".tmp", dir=path.parent)
        os.close(handle)
        temporary = Path(name)
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


class MaintenanceExecutor:
    """Execute only catalog-bound repair/update/uninstall plans."""

    def __init__(self, *, paths: HubPaths, catalog: Any) -> None:
        self.paths = paths
        self.catalog = catalog

    def _record(self, component_id: str, component_type: str) -> Mapping[str, Any] | None:
        records = self.catalog.models if component_type == "model" else self.catalog.runtimes if component_type == "runtime" else {}
        return records.get(component_id) if isinstance(records, Mapping) else None

    def _root(self, record: Mapping[str, Any], component_id: str, component_type: str) -> Path:
        if component_type == "model":
            return self.paths.models_root / component_id
        if record.get("root_class") == "environments_root":
            return self.paths.environments_root
        return self.paths.runtime_root

    def _leaves(self, record: Mapping[str, Any], component_type: str) -> list[str]:
        if component_type == "model":
            return [str(item.get("relative_path")) for item in record.get("files", []) if isinstance(item, Mapping)]
        return [str(item) for item in record.get("required_leaves", [])]

    def _receipt_path(self) -> Path:
        return self.paths.config_root / "component_install_receipts.json"

    def _receipts(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._receipt_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raw = {}
        records = raw.get("records") if isinstance(raw, Mapping) else None
        return {"schema_version": "component-install-receipts.v2", "records": dict(records) if isinstance(records, Mapping) else {}}

    def _shared_reference(self, component_id: str, record: Mapping[str, Any], receipts: Mapping[str, Any]) -> bool:
        shared = record.get("shared_dependency_id")
        if not shared:
            return False
        for other_id, value in receipts.items():
            if other_id == component_id or not isinstance(value, Mapping):
                continue
            if value.get("shared_dependency_id") == shared and value.get("state") not in {"REMOVED", "UNINSTALLED"}:
                return True
        return False

    def _verify_leaves(self, root: Path, record: Mapping[str, Any], component_type: str) -> tuple[bool, list[dict[str, Any]]]:
        leaves: list[dict[str, Any]] = []
        for relative in self._leaves(record, component_type):
            target = _safe_leaf(root, relative)
            if target is None:
                return False, leaves
            present = target.is_file() and not _is_reparse(target)
            item: dict[str, Any] = {"relative_leaf": relative, "present": present}
            if present:
                item["size_bytes"] = target.stat().st_size
                item["sha256"] = _sha256(target) if target.stat().st_size <= 64 * 1024 * 1024 else None
            leaves.append(item)
        return True, leaves

    def apply(
        self,
        plan: Mapping[str, Any],
        *,
        confirmed: bool,
        catalog_binding: Any | None = None,
        current_record: Mapping[str, Any] | None = None,
        binding_provider: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        component_id = plan.get("component_id")
        component_type = plan.get("component_type")
        action = plan.get("action")
        if not isinstance(component_id, str) or component_type not in {"model", "runtime"} or action not in {"repair", "update", "uninstall"}:
            return {"status": "error", "code": "maintenance_plan_invalid", "execution": "not_run"}
        from src.services.operational_closure.update_executor import binding_refusal, resolve_execution_binding

        binding, bound_record, binding_error = resolve_execution_binding(
            plan,
            catalog_binding=catalog_binding,
            current_record=current_record,
            binding_provider=binding_provider,
        )
        if binding is None:
            return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
        record = bound_record if isinstance(bound_record, Mapping) else self._record(component_id, str(component_type))
        if not isinstance(record, Mapping):
            return {"status": "error", "code": "unknown_component", "execution": "not_run"}
        root = self._root(record, component_id, str(component_type))
        valid_root, leaves = self._verify_leaves(root, record, str(component_type))
        if not valid_root:
            return {"status": "failed", "code": "unsafe_or_reparse_component_leaf", "execution": "not_run"}
        receipts = self._receipts()
        if action == "repair":
            if not leaves or not all(item.get("present") for item in leaves):
                return {"status": "unavailable", "code": "repair_source_required", "execution": "not_run", "next_action": "Create a trusted source or manual-import plan for the missing catalog leaves."}
            binding, bound_record, binding_error = resolve_execution_binding(
                plan,
                catalog_binding=binding,
                current_record=record,
                binding_provider=binding_provider,
            )
            if binding is None:
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            record = bound_record if isinstance(bound_record, Mapping) else record
            previous_receipt = receipts["records"].get(component_id)
            runtime_root_class = record.get("root_class") if record.get("root_class") in {"runtime_root", "environments_root", "external_managed"} else "runtime_root"
            receipt: dict[str, Any] = {
                "component_id": component_id,
                "component_type": component_type,
                "catalog_schema": binding.catalog_schema,
                "catalog_revision": binding.catalog_revision,
                "catalog_fingerprint": binding.catalog_fingerprint,
                "source_identity": binding.source_identity,
                "root_class": "models_root" if component_type == "model" else runtime_root_class,
                "location_class": "models_root" if component_type == "model" else runtime_root_class,
                "leaves": [
                    {
                        "relative_path": item.get("relative_leaf"),
                        "observed_size_bytes": int(item.get("size_bytes", 0)),
                        "observed_mtime_ns": 0,
                        "verification_level": "unverified",
                    }
                    for item in leaves
                ],
                "recorded_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "source": "existing_install_reuse",
                "operational": False,
            }
            if isinstance(previous_receipt, Mapping):
                for key in ("previous_version", "rollback_candidate"):
                    if key in previous_receipt:
                        receipt[key] = previous_receipt[key]
            try:
                from src.services.component_installer.receipts import ReceiptError, write_component_receipt

                write_component_receipt(self.paths.config_root, component_id, receipt, catalog_binding=binding)
            except (OSError, ReceiptError, KeyError, TypeError):
                return {"status": "failed", "code": "receipt_write_failed", "execution": "not_run"}
            return {"status": "completed", "action": "repair", "component_id": component_id, "state": "INSTALLED_UNVERIFIED", "execution": "completed", "next_action": "Run the component-specific bounded verification before operational promotion."}
        if action == "update":
            candidate = record.get("update_candidate")
            if not isinstance(candidate, Mapping):
                return {"status": "unavailable", "code": "update_candidate_required", "execution": "not_run", "next_action": "Run Check Update and confirm an immutable candidate plan before activation."}
            # The maintenance route is still plan-first/stale-state checked;
            # the actual candidate activation stays in the dedicated update
            # executor so rollback and source identity remain centralized.
            from src.services.operational_closure.update_executor import ComponentUpdateExecutor
            update_plan = {
                "component_id": component_id,
                "component_type": component_type,
                "plan_fingerprint": plan.get("plan_fingerprint"),
                "installed_revision": str(receipts["records"].get(component_id, {}).get("bundle_revision") or "previous"),
                "latest_supported_revision": str(record.get("latest_supported_revision") or record.get("revision") or "unknown"),
                "catalog_fingerprint": binding.catalog_fingerprint,
                "update_candidate": dict(candidate),
                "_catalog_binding": binding,
                "_record": record,
                "_record_revision": plan.get("_record_revision"),
                "_install_strategy": plan.get("_install_strategy"),
                "_latest_supported_revision": plan.get("_latest_supported_revision"),
                "_candidate_fingerprint": plan.get("_candidate_fingerprint"),
                "_plan_fingerprint_payload": plan.get("_plan_fingerprint_payload"),
            }
            return ComponentUpdateExecutor(paths=self.paths).apply(
                update_plan,
                confirmed=True,
                catalog_binding=binding,
                current_record=record,
                binding_provider=binding_provider,
            )
        binding, bound_record, binding_error = resolve_execution_binding(
            plan,
            catalog_binding=binding,
            current_record=record,
            binding_provider=binding_provider,
        )
        if binding is None:
            return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
        if self._shared_reference(component_id, record, receipts["records"]):
            return {"status": "conflict", "code": "shared_dependency_in_use", "execution": "not_run", "next_action": "Remove dependent components first; shared runtime/model leaves are preserved."}
        # Validate all leaves before the first delete.  Unknown files are never
        # removed; only the catalog list is eligible.
        for item, relative in zip(leaves, self._leaves(record, str(component_type))):
            target = _safe_leaf(root, relative)
            if target is None or not item.get("present"):
                continue
            if target.is_file() and not _is_reparse(target):
                continue
            return {"status": "failed", "code": "uninstall_target_changed", "execution": "not_run"}
        removed = 0
        for relative in self._leaves(record, str(component_type)):
            target = _safe_leaf(root, relative)
            if target is not None and target.is_file() and not _is_reparse(target):
                try:
                    target.unlink()
                    removed += 1
                except OSError:
                    return {"status": "failed", "code": "uninstall_delete_failed", "execution": "not_run"}
        receipts["records"].pop(component_id, None)
        try:
            _atomic_json(self._receipt_path(), receipts)
        except OSError:
            return {"status": "failed", "code": "receipt_write_failed", "execution": "not_run"}
        return {"status": "completed", "action": "uninstall", "component_id": component_id, "removed_known_leaves": removed, "state": "NOT_INSTALLED", "execution": "completed", "next_action": "Refresh the component catalog."}


__all__ = ["MaintenanceExecutor"]
