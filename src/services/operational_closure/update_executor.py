"""Candidate staging/activation and rollback for catalog-owned model updates.

Only server-owned catalog metadata can name a candidate.  Client payloads never
contain paths, URLs or files.  This executor supports a bounded single-model
leaf candidate and refuses when the catalog has not supplied an immutable
candidate graph; runtimes/dependency bundles remain explicit future plans.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any

from src.platform.paths import ComponentPathError, HubPaths, resolve_component_leaf, resolve_component_root
from src.services.component_installer.archive import ArchiveSafetyError, safe_extract_archive
from src.services.component_installer.downloader import DownloadError, TrustedDownloader
from src.services.component_installer.receipts import CatalogBindingContext, ReceiptError, read_receipts, source_identity, write_component_receipt


_V2_CATALOG_SCHEMA = "v7-production-catalog.v2"


def _fingerprint(value: object) -> str | None:
    try:
        payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload).hexdigest()


def binding_refusal(code: str, *, component_id: object = None) -> dict[str, Any]:
    """Return one fixed, path-free refusal for every binding boundary."""

    status = "unavailable" if code == "catalog_binding_unavailable" else "conflict"
    value: dict[str, Any] = {
        "status": status,
        "code": code if code in {"catalog_binding_stale", "catalog_schema_unsupported", "catalog_binding_unavailable"} else "catalog_binding_stale",
        "execution": "not_run",
        "dry_run": True,
        "next_action": "Refresh the server-owned catalog context and create a new update plan.",
    }
    return value


def _coerce_binding(value: object) -> CatalogBindingContext | None:
    try:
        if isinstance(value, CatalogBindingContext):
            return value
        if isinstance(value, Mapping):
            return CatalogBindingContext.from_mapping(value)
    except (ReceiptError, TypeError, ValueError):
        return None
    return None


def _binding_from_provider(value: object, fallback_record: Mapping[str, Any] | None) -> tuple[CatalogBindingContext | None, Mapping[str, Any] | None]:
    if isinstance(value, tuple) and len(value) == 2:
        binding_value, record_value = value
        binding = _coerce_binding(binding_value)
        record = record_value if isinstance(record_value, Mapping) else fallback_record
        return binding, record
    if isinstance(value, Mapping) and ("binding" in value or "catalog_binding" in value):
        binding = _coerce_binding(value.get("binding", value.get("catalog_binding")))
        record_value = value.get("record")
        record = record_value if isinstance(record_value, Mapping) else fallback_record
        return binding, record
    return _coerce_binding(value), fallback_record


def _expected_plan_payload(plan: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = plan.get("_plan_fingerprint_payload")
    return value if isinstance(value, Mapping) else None


def resolve_execution_binding(
    plan: Mapping[str, Any],
    *,
    catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None,
    current_record: Mapping[str, Any] | None = None,
    binding_provider: Any | None = None,
) -> tuple[CatalogBindingContext | None, Mapping[str, Any] | None, str | None]:
    """Resolve a current binding without accepting a plan or path as authority."""

    planned_record = plan.get("_record")
    record = current_record if isinstance(current_record, Mapping) else planned_record if isinstance(planned_record, Mapping) else None
    binding = _coerce_binding(catalog_binding)
    if callable(binding_provider):
        try:
            provided = binding_provider(plan.get("component_id"), plan.get("component_type"))
        except Exception:
            return None, None, "catalog_binding_unavailable"
        binding, provided_record = _binding_from_provider(provided, record)
        record = provided_record
    if binding is None:
        return None, record, "catalog_binding_unavailable"
    code = validate_execution_binding(plan, binding, record)
    if code is not None:
        return None, record, code
    return binding, record, None


def validate_execution_binding(
    plan: Mapping[str, Any],
    binding: CatalogBindingContext,
    record: Mapping[str, Any] | None,
) -> str | None:
    """Validate typed catalog, component and candidate identity before mutation."""

    if not isinstance(plan, Mapping) or not isinstance(binding, CatalogBindingContext):
        return "catalog_binding_unavailable"
    planned_value = plan.get("_catalog_binding", plan.get("catalog_binding"))
    planned = _coerce_binding(planned_value)
    if planned is None:
        return "catalog_binding_unavailable"
    if planned.catalog_schema != binding.catalog_schema:
        return "catalog_schema_unsupported" if _V2_CATALOG_SCHEMA in {planned.catalog_schema, binding.catalog_schema} else "catalog_binding_stale"
    if planned.as_record_fields() != binding.as_record_fields():
        return "catalog_binding_stale"
    if plan.get("catalog_fingerprint") is not None and plan.get("catalog_fingerprint") != binding.catalog_fingerprint:
        return "catalog_binding_stale"
    component_id = plan.get("component_id")
    component_type = plan.get("component_type")
    if not isinstance(component_id, str) or component_type not in {"model", "runtime"}:
        return "catalog_binding_stale"
    if not isinstance(record, Mapping):
        return "catalog_binding_unavailable"
    record_key = "model_id" if component_type == "model" else "runtime_id"
    if record.get(record_key) != component_id:
        return "catalog_binding_stale"
    try:
        binding.validate_record(component_type=str(component_type), record=record)
    except (ReceiptError, TypeError, ValueError):
        return "catalog_binding_stale"
    for plan_key, record_key in (("_record_revision", "revision"), ("_install_strategy", "install_strategy")):
        if plan_key not in plan:
            return "catalog_binding_stale"
        if plan.get(plan_key) != record.get(record_key):
            return "catalog_binding_stale"
    if "_latest_supported_revision" not in plan:
        return "catalog_binding_stale"
    current_supported = record.get("latest_supported_revision", record.get("revision"))
    if plan.get("_latest_supported_revision") != current_supported:
        return "catalog_binding_stale"
    expected_candidate_fingerprint = plan.get("_candidate_fingerprint")
    current_candidate = record.get("update_candidate")
    current_candidate_fingerprint = _fingerprint(current_candidate) if isinstance(current_candidate, Mapping) else None
    if expected_candidate_fingerprint != current_candidate_fingerprint:
        return "catalog_binding_stale"
    payload = _expected_plan_payload(plan)
    expected_plan_fingerprint = plan.get("plan_fingerprint")
    if payload is not None and (not isinstance(expected_plan_fingerprint, str) or _fingerprint(payload) != expected_plan_fingerprint):
        return "catalog_binding_stale"
    return None


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
        if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or ":" in relative:
            return None
        relative_parts = relative.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in relative_parts):
            return None
        root = Path(os.path.normpath(os.path.abspath(os.fspath(root))))
        candidate = Path(os.path.normpath(os.path.join(os.fspath(root), *relative_parts)))
        candidate.relative_to(root)
    except (OSError, ValueError):
        return None
    # Validate every existing original ancestor, not only the supplied
    # component root.  This catches a reparse/junction at Models or any data
    # ancestor before resolved containment is considered.
    current = candidate
    while True:
        if _is_reparse(current):
            return None
        if current.parent == current:
            break
        current = current.parent
    try:
        resolved_root = os.path.realpath(os.fspath(root))
        resolved_candidate = os.path.realpath(os.fspath(candidate))
        if os.path.commonpath((os.path.normcase(resolved_root), os.path.normcase(resolved_candidate))) != os.path.normcase(resolved_root):
            return None
    except (OSError, ValueError):
        return None
    return candidate


def _safe_model_root(paths: HubPaths, component_id: str) -> Path | None:
    """Resolve the complete data-root -> Models -> component chain."""

    try:
        return resolve_component_root(paths, component_id, "model", root_class="models_root", require_exists=False)
    except (ComponentPathError, OSError, ValueError):
        return None


def _runtime_slot(root: Path, required_leaves: object) -> tuple[Path, str] | None:
    """Return one shared runtime slot without touching unrelated legacy leaves."""

    if not isinstance(required_leaves, list) or not required_leaves:
        return None
    try:
        leaves = [Path(value) for value in required_leaves if isinstance(value, str)]
        if len(leaves) != len(required_leaves):
            return None
        common = Path(os.path.commonpath([str(value.parent) for value in leaves])).as_posix()
        if not common or common == ".":
            return None
        slot = resolve_component_leaf(root, common, require_exists=False)
        return slot, common
    except (ComponentPathError, OSError, ValueError):
        return None


def _copy_tree(source_root: Path, destination_root: Path) -> bool:
    """Copy an archive-derived tree while refusing reparses and non-files."""

    if not source_root.is_dir() or _is_reparse(source_root):
        return False
    try:
        destination_root.mkdir(parents=True, exist_ok=False)
        for item in source_root.rglob("*"):
            if _is_reparse(item):
                return False
            relative = item.relative_to(source_root)
            target = destination_root / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
            else:
                return False
        return True
    except (OSError, ValueError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clone_tree_preserving_files(source_root: Path, destination_root: Path) -> bool:
    """Clone an active component slot without redownloading unchanged bytes.

    Ordinary files are hard-linked where the filesystem permits it; a copy is
    the bounded fallback.  Any reparse entry fails closed instead of following
    an external target.  The helper is only used after an explicit update plan
    and never scans the drive or a user-selected path.
    """

    if not source_root.is_dir() or _is_reparse(source_root):
        return False
    try:
        for item in source_root.rglob("*"):
            relative = item.relative_to(source_root)
            target = destination_root / relative
            if _is_reparse(item):
                return False
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not item.is_file():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(item, target)
            except OSError:
                shutil.copy2(item, target)
        return True
    except (OSError, ValueError):
        return False


class RuntimeUpdateExecutor:
    """Activate a catalog-pinned runtime candidate in an isolated slot."""

    def __init__(self, *, paths: HubPaths) -> None:
        self.paths = paths

    @staticmethod
    def _refusal(code: str) -> dict[str, Any]:
        return {"status": "unavailable", "code": code, "execution": "not_run", "dry_run": True}

    @staticmethod
    def _candidate_integrity(candidate: Mapping[str, Any]) -> tuple[int | None, str | None]:
        integrity = candidate.get("integrity")
        if not isinstance(integrity, Mapping) or integrity.get("verification") != "verified":
            return None, None
        size = integrity.get("size_bytes")
        digest = integrity.get("sha256")
        return (size if isinstance(size, int) and size > 0 else None, digest if isinstance(digest, str) and len(digest) == 64 else None)

    @staticmethod
    def _archive_source(plan: Mapping[str, Any]) -> Path | None:
        value = plan.get("_candidate_archive")
        return value if isinstance(value, Path) else None

    def _slot_and_versions(self, record: Mapping[str, Any], runtime_id: str) -> tuple[Path, Path, Path] | None:
        try:
            root = resolve_component_root(self.paths, runtime_id, "runtime", record.get("root_class"), require_exists=False)
        except (ComponentPathError, OSError, ValueError):
            return None
        slot_info = _runtime_slot(root, record.get("required_leaves"))
        if slot_info is None:
            return None
        slot, _common = slot_info
        versions = root / ".v8-versions" / runtime_id
        if _safe_leaf(root, Path(".v8-versions") .as_posix()) is None or _is_reparse(versions):
            return None
        return root, slot, versions

    def apply(
        self,
        plan: Mapping[str, Any],
        *,
        confirmed: bool,
        catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None,
        current_record: Mapping[str, Any] | None = None,
        binding_provider: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        if plan.get("component_type") != "runtime":
            return self._refusal("runtime_update_component_mismatch")
        candidate = plan.get("update_candidate")
        runtime_id = plan.get("component_id")
        if not isinstance(candidate, Mapping) or not isinstance(runtime_id, str):
            return self._refusal("update_candidate_unavailable")
        binding, record, binding_error = resolve_execution_binding(
            plan,
            catalog_binding=catalog_binding,
            current_record=current_record,
            binding_provider=binding_provider,
        )
        if binding is None or not isinstance(record, Mapping):
            return binding_refusal(binding_error or "catalog_binding_stale", component_id=runtime_id)
        archive = self._archive_source(plan)
        expected_size, expected_hash = self._candidate_integrity(candidate)
        if archive is None or expected_size is None or expected_hash is None or not archive.is_file() or _is_reparse(archive):
            return self._refusal("update_candidate_unavailable")
        try:
            if archive.stat().st_size != expected_size or _sha256(archive) != expected_hash.casefold():
                return {"status": "failed", "code": "update_candidate_checksum_mismatch", "execution": "not_run"}
        except OSError:
            return self._refusal("update_candidate_unavailable")
        slots = self._slot_and_versions(record, runtime_id)
        if slots is None:
            return self._refusal("unsafe_runtime_update_root")
        root, slot, versions = slots
        if not slot.is_dir() or _is_reparse(slot):
            return self._refusal("runtime_not_installed")
        stage = self.paths.temp_root / "v8-runtime-update" / runtime_id
        extract_root = stage / "extract"
        candidate_slot = stage / "candidate-slot"
        required = [str(value) for value in record.get("required_leaves", []) if isinstance(value, str)]
        archive_prefix = candidate.get("archive_prefix")
        archive_leaves = candidate.get("archive_leaves")
        if not isinstance(archive_prefix, str) or not isinstance(archive_leaves, Mapping) or len(required) != len(record.get("required_leaves", [])):
            return self._refusal("runtime_update_mapping_invalid")
        previous = versions / str(plan.get("installed_revision") or record.get("revision") or "previous")
        try:
            if _safe_leaf(self.paths.temp_root, stage.relative_to(self.paths.temp_root).as_posix()) is None:
                return self._refusal("unsafe_runtime_update_stage")
            if _safe_leaf(root, Path(".v8-versions").as_posix()) is None or _safe_leaf(versions.parent, previous.name) is None:
                return self._refusal("unsafe_runtime_update_versions")
            if previous.exists() or _is_reparse(previous):
                return self._refusal("runtime_rollback_slot_exists")
            stage.mkdir(parents=True, exist_ok=False)
            safe_extract_archive(archive, extract_root, max_bytes=max(int(candidate.get("estimated_disk_size") or 1), 1))
            if not _copy_tree(slot, candidate_slot):
                return self._refusal("runtime_update_preserve_failed")
            slot_root = _runtime_slot(root, record.get("required_leaves"))
            if slot_root is None:
                return self._refusal("unsafe_runtime_update_root")
            _slot, common = slot_root
            for target_relative in required:
                archive_leaf = archive_leaves.get(target_relative)
                if not isinstance(archive_leaf, str):
                    return self._refusal("runtime_update_mapping_invalid")
                source = _safe_leaf(extract_root, f"{archive_prefix}/{archive_leaf}")
                target_relative_to_slot = Path(target_relative).relative_to(Path(common)).as_posix()
                target = _safe_leaf(candidate_slot, target_relative_to_slot)
                if source is None or target is None or not source.is_file() or _is_reparse(source):
                    return self._refusal("runtime_update_archive_leaf_invalid")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if not target.is_file() or _is_reparse(target) or target.stat().st_size != source.stat().st_size or _sha256(target) != _sha256(source):
                    return self._refusal("runtime_update_stage_mismatch")
            binding, record, binding_error = resolve_execution_binding(
                plan,
                catalog_binding=binding,
                current_record=record,
                binding_provider=binding_provider,
            )
            if binding is None:
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=runtime_id)
            versions.mkdir(parents=True, exist_ok=True)
            if _is_reparse(versions):
                return self._refusal("unsafe_runtime_update_versions")
            os.rename(slot, previous)
            try:
                os.rename(candidate_slot, slot)
            except OSError:
                os.rename(previous, slot)
                return {"status": "failed", "code": "runtime_update_activation_failed", "execution": "not_run"}
            candidate_revision = candidate.get("revision")
            receipt = {
                "component_id": runtime_id,
                "component_type": "runtime",
                "catalog_schema": binding.catalog_schema,
                "catalog_revision": binding.catalog_revision,
                "catalog_fingerprint": binding.catalog_fingerprint,
                "source_identity": binding.source_identity,
                "root_class": record.get("root_class", "runtime_root"),
                "location_class": record.get("root_class", "runtime_root"),
                "leaves": [{"relative_path": path, "observed_size_bytes": int((_safe_leaf(root, path) or Path()).stat().st_size), "observed_mtime_ns": int((_safe_leaf(root, path) or Path()).stat().st_mtime_ns), "verification_level": "unverified"} for path in required],
                "recorded_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "source": "catalog_candidate",
                "operational": False,
                "bundle_revision": str(candidate_revision or "candidate"),
                "previous_version": str(plan.get("installed_revision") or record.get("revision") or "previous"),
                "rollback_candidate": True,
            }
            write_component_receipt(self.paths.config_root, runtime_id, receipt, catalog_binding=binding)
            return {"status": "completed", "execution": "completed", "dry_run": False, "component_id": runtime_id, "state": "INSTALLED_UNVERIFIED", "rollback_available": True, "next_action": "Run the bounded runtime verification before operational promotion."}
        except (OSError, ValueError, ArchiveSafetyError, ReceiptError):
            try:
                if slot.exists() and previous.exists() and not _is_reparse(slot) and not _is_reparse(previous):
                    quarantine = versions / f"failed-{int(time.time() * 1000)}"
                    os.rename(slot, quarantine)
                    os.rename(previous, slot)
            except OSError:
                pass
            return {"status": "failed", "code": "runtime_update_activation_failed", "execution": "not_run"}
        finally:
            if stage.exists() and not _is_reparse(stage):
                shutil.rmtree(stage, ignore_errors=True)

    def rollback(
        self,
        component_id: str,
        *,
        catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None,
        current_record: Mapping[str, Any] | None = None,
        binding_provider: Any | None = None,
        plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            receipts = read_receipts(self.paths.config_root)
            receipt = receipts.get("records", {}).get(component_id)
            if not isinstance(receipt, Mapping) or not receipt.get("rollback_candidate"):
                return self._refusal("rollback_unavailable")
            binding, record, binding_error = resolve_execution_binding(
                plan or {"component_id": component_id, "component_type": "runtime", "_record": current_record, "_catalog_binding": catalog_binding, "_record_revision": (current_record or {}).get("revision"), "_install_strategy": (current_record or {}).get("install_strategy"), "_latest_supported_revision": (current_record or {}).get("latest_supported_revision", (current_record or {}).get("revision")), "_candidate_fingerprint": _fingerprint((current_record or {}).get("update_candidate"))},
                catalog_binding=catalog_binding,
                current_record=current_record,
                binding_provider=binding_provider,
            )
            if binding is None or not isinstance(record, Mapping):
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            slots = self._slot_and_versions(record, component_id)
            previous_name = receipt.get("previous_version")
            if slots is None or not isinstance(previous_name, str):
                return self._refusal("rollback_candidate_missing")
            root, slot, versions = slots
            previous = versions / previous_name
            backup = versions / f"active-{int(time.time() * 1000)}"
            if not previous.is_dir() or _is_reparse(previous) or _is_reparse(slot) or backup.exists():
                return self._refusal("rollback_candidate_missing")
            os.rename(slot, backup)
            try:
                os.rename(previous, slot)
            except OSError:
                os.rename(backup, slot)
                return {"status": "failed", "code": "runtime_rollback_activation_failed", "execution": "not_run"}
            restored = dict(receipt)
            restored["bundle_revision"] = previous_name
            restored["rollback_candidate"] = False
            restored["previous_version"] = None
            write_component_receipt(self.paths.config_root, component_id, restored, catalog_binding=binding)
            return {"status": "completed", "execution": "completed", "dry_run": False, "component_id": component_id, "state": "INSTALLED_UNVERIFIED"}
        except (OSError, ReceiptError, ValueError):
            return {"status": "failed", "code": "runtime_rollback_activation_failed", "execution": "not_run"}


class ComponentUpdateExecutor:
    """Apply one immutable model candidate and preserve a rollback version."""

    def __init__(self, *, paths: HubPaths) -> None:
        self.paths = paths

    def _candidate_file(self, candidate: Mapping[str, Any]) -> tuple[Path | None, str | None, int | None, str | None]:
        relative = candidate.get("relative_path")
        if not isinstance(relative, str):
            return None, None, None, None
        expected_size = candidate.get("size_bytes") if isinstance(candidate.get("size_bytes"), int) else None
        expected_hash = candidate.get("sha256") if isinstance(candidate.get("sha256"), str) else None
        staged_relative = candidate.get("staged_relative_path")
        if isinstance(staged_relative, str):
            source = _safe_leaf(self.paths.temp_root, staged_relative)
            return source, relative, expected_size, expected_hash
        return None, relative, expected_size, expected_hash

    def _restore_activation(self, root: Path, candidate_root: Path, previous_root: Path | None) -> None:
        """Restore the pre-activation roots after a binding/receipt refusal."""

        try:
            if _safe_model_root(self.paths, root.name) is None:
                return
            if root.exists() and not _is_reparse(root):
                os.rename(root, candidate_root)
            if previous_root is not None and previous_root.exists() and not _is_reparse(previous_root) and not root.exists():
                os.rename(previous_root, root)
        except OSError:
            # The caller still returns a fixed refusal.  The ordinary cleanup
            # path never follows a reparse point or exposes this error.
            return

    def _restore_rollback(self, root: Path, previous: Path, current_backup: Path) -> None:
        """Restore both roots after a rollback receipt failure or drift."""

        try:
            if _safe_model_root(self.paths, root.name) is None:
                return
            if root.exists() and not _is_reparse(root) and not previous.exists():
                os.rename(root, previous)
            if current_backup.exists() and not _is_reparse(current_backup) and not root.exists():
                os.rename(current_backup, root)
        except OSError:
            # Never follow or remove an unexpected/reparse path while trying
            # to compensate. The caller returns a fixed failed projection.
            return

    def apply(
        self,
        plan: Mapping[str, Any],
        *,
        confirmed: bool,
        catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None,
        current_record: Mapping[str, Any] | None = None,
        binding_provider: Any | None = None,
    ) -> dict[str, Any]:
        if not confirmed:
            return {"status": "waiting_confirmation", "execution": "not_run", "dry_run": True}
        if plan.get("component_type") == "runtime":
            return RuntimeUpdateExecutor(paths=self.paths).apply(
                plan,
                confirmed=confirmed,
                catalog_binding=catalog_binding,
                current_record=current_record,
                binding_provider=binding_provider,
            )
        if plan.get("component_type") != "model":
            return {"status": "unavailable", "code": "runtime_candidate_executor_required", "execution": "not_run", "dry_run": True}
        candidate = plan.get("update_candidate")
        if not isinstance(candidate, Mapping):
            return {"status": "unavailable", "code": "update_candidate_unavailable", "execution": "not_run", "dry_run": True, "next_action": "Use Manual Import or wait for a catalog-pinned candidate."}
        component_id = plan.get("component_id")
        if not isinstance(component_id, str):
            return {"status": "error", "code": "update_plan_invalid", "execution": "not_run", "dry_run": True}
        binding, bound_record, binding_error = resolve_execution_binding(
            plan,
            catalog_binding=catalog_binding,
            current_record=current_record,
            binding_provider=binding_provider,
        )
        if binding is None:
            return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
        source, relative, expected_size, expected_hash = self._candidate_file(candidate)
        if source is None or relative is None or not source.is_file() or _is_reparse(source):
            return {"status": "unavailable", "code": "update_candidate_unavailable", "execution": "not_run"}
        if expected_size is None or expected_size <= 0 or source.stat().st_size != expected_size:
            return {"status": "failed", "code": "update_candidate_size_mismatch", "execution": "not_run"}
        actual_hash = _sha256(source)
        if not isinstance(expected_hash, str) or len(expected_hash) != 64 or actual_hash != expected_hash.lower():
            return {"status": "failed", "code": "update_candidate_checksum_mismatch", "execution": "not_run"}
        root = _safe_model_root(self.paths, component_id)
        if root is None:
            return {"status": "unavailable", "code": "unsafe_update_root", "execution": "not_run", "dry_run": True}
        target = _safe_leaf(root, relative)
        if target is None:
            return {"status": "failed", "code": "unsafe_update_target", "execution": "not_run"}
        candidate_root = self.paths.temp_root / "component-update" / f"{component_id}-{int(time.time() * 1000)}"
        if not _safe_leaf(self.paths.temp_root, candidate_root.relative_to(self.paths.temp_root).as_posix()):
            return {"status": "failed", "code": "unsafe_update_stage", "execution": "not_run"}
        candidate_root.mkdir(parents=True, exist_ok=True)
        previous_root: Path | None = None
        try:
            if root.exists() and not _clone_tree_preserving_files(root, candidate_root):
                return {"status": "failed", "code": "update_preserve_existing_failed", "execution": "not_run"}
            staged_target = candidate_root / Path(relative)
            staged_target.parent.mkdir(parents=True, exist_ok=True)
            if staged_target.exists() or staged_target.is_symlink():
                if _is_reparse(staged_target):
                    return {"status": "failed", "code": "update_target_reparse", "execution": "not_run"}
                staged_target.unlink()
            shutil.copy2(source, staged_target)
            if _sha256(staged_target) != actual_hash:
                return {"status": "failed", "code": "update_stage_checksum_mismatch", "execution": "not_run"}
            # Revalidate the full data-root -> Models -> component chain
            # before creating or renaming any managed model directory.
            if _safe_model_root(self.paths, component_id) is None:
                return {"status": "failed", "code": "update_root_reparse", "execution": "not_run"}
            root.parent.mkdir(parents=True, exist_ok=True)
            root = _safe_model_root(self.paths, component_id)
            if root is None or (root.exists() and _is_reparse(root)):
                return {"status": "failed", "code": "update_root_reparse", "execution": "not_run"}
            if root.exists():
                previous_root = self.paths.models_root / ".versions" / component_id / str(plan.get("installed_revision") or "previous")
                previous_relative = Path(".versions") / component_id / str(plan.get("installed_revision") or "previous")
                if _safe_leaf(self.paths.models_root, previous_relative.as_posix()) is None:
                    return {"status": "failed", "code": "update_root_reparse", "execution": "not_run"}
                if previous_root.exists() or _is_reparse(previous_root):
                    return {"status": "conflict", "code": "rollback_slot_exists", "execution": "not_run"}
                previous_root.parent.mkdir(parents=True, exist_ok=True)
                if _safe_model_root(self.paths, component_id) is None or _safe_leaf(self.paths.models_root, previous_relative.as_posix()) is None:
                    return {"status": "failed", "code": "update_root_reparse", "execution": "not_run"}
                os.rename(root, previous_root)
            binding, bound_record, binding_error = resolve_execution_binding(
                plan,
                catalog_binding=binding,
                current_record=bound_record,
                binding_provider=binding_provider,
            )
            if binding is None:
                self._restore_activation(root, candidate_root, previous_root)
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            staged_mtime_ns = int(staged_target.stat().st_mtime_ns)
            try:
                if _safe_model_root(self.paths, component_id) is None or _is_reparse(root) or _safe_leaf(self.paths.temp_root, candidate_root.relative_to(self.paths.temp_root).as_posix()) is None:
                    raise OSError("update_root_reparse")
                os.rename(candidate_root, root)
            except OSError:
                if previous_root is not None and previous_root.exists() and not root.exists():
                    os.rename(previous_root, root)
                return {"status": "failed", "code": "update_activation_failed", "execution": "not_run"}
            receipt = {
                "component_id": component_id,
                "component_type": "model",
                "catalog_schema": binding.catalog_schema,
                "catalog_revision": binding.catalog_revision,
                "catalog_fingerprint": binding.catalog_fingerprint,
                "source": "catalog_candidate",
                "source_identity": binding.source_identity,
                "root_class": "models_root",
                "location_class": "models_root",
                "leaves": [{"relative_path": relative, "observed_size_bytes": expected_size, "observed_mtime_ns": staged_mtime_ns, "verification_level": "unverified"}],
                "recorded_at": int(time.time()),
                "verified_at": None,
                "state": "INSTALLED_UNVERIFIED",
                "operational": False,
                "previous_version": str(plan.get("installed_revision") or "unknown"),
                "rollback_candidate": True,
            }
            binding, bound_record, binding_error = resolve_execution_binding(
                plan,
                catalog_binding=binding,
                current_record=bound_record,
                binding_provider=binding_provider,
            )
            if binding is None:
                self._restore_activation(root, candidate_root, previous_root)
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            write_component_receipt(self.paths.config_root, component_id, receipt, catalog_binding=binding)
            return {"status": "completed", "execution": "completed", "dry_run": False, "component_id": component_id, "state": "INSTALLED_UNVERIFIED", "rollback_available": previous_root is not None, "next_action": "Run bounded verification before operational promotion."}
        except (OSError, ValueError, DownloadError):
            self._restore_activation(root, candidate_root, previous_root)
            return {"status": "failed", "code": "update_activation_failed", "execution": "not_run"}
        finally:
            if candidate_root.exists():
                shutil.rmtree(candidate_root, ignore_errors=True)

    def rollback(
        self,
        component_id: str,
        *,
        catalog_binding: CatalogBindingContext | Mapping[str, Any] | None = None,
        current_record: Mapping[str, Any] | None = None,
        binding_provider: Any | None = None,
        plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if current_record is not None and current_record.get("runtime_id") == component_id:
            return RuntimeUpdateExecutor(paths=self.paths).rollback(
                component_id,
                catalog_binding=catalog_binding,
                current_record=current_record,
                binding_provider=binding_provider,
                plan=plan,
            )
        receipts = read_receipts(self.paths.config_root)
        receipt = receipts["records"].get(component_id)
        if not isinstance(receipt, Mapping) or not receipt.get("rollback_candidate"):
            return {"status": "unavailable", "code": "rollback_unavailable", "execution": "not_run"}
        expected_plan = plan if isinstance(plan, Mapping) else {
            "component_id": component_id,
            "component_type": receipt.get("component_type"),
            "catalog_fingerprint": receipt.get("catalog_fingerprint"),
            "_catalog_binding": {
                "catalog_schema": receipt.get("catalog_schema"),
                "catalog_revision": receipt.get("catalog_revision"),
                "catalog_fingerprint": receipt.get("catalog_fingerprint"),
                "source_identity": receipt.get("source_identity"),
            },
            "_record_revision": (current_record or {}).get("revision"),
            "_install_strategy": (current_record or {}).get("install_strategy"),
            "_latest_supported_revision": (current_record or {}).get("latest_supported_revision", (current_record or {}).get("revision")),
            "_candidate_fingerprint": _fingerprint((current_record or {}).get("update_candidate")) if isinstance((current_record or {}).get("update_candidate"), Mapping) else None,
        }
        binding, bound_record, binding_error = resolve_execution_binding(
            expected_plan,
            catalog_binding=catalog_binding,
            current_record=current_record,
            binding_provider=binding_provider,
        )
        if binding is None:
            return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
        receipt_binding = _coerce_binding({key: receipt.get(key) for key in ("catalog_schema", "catalog_revision", "catalog_fingerprint", "source_identity")})
        if receipt_binding is None or receipt_binding.as_record_fields() != binding.as_record_fields():
            return binding_refusal("catalog_binding_stale", component_id=component_id)
        previous_relative = Path(".versions") / component_id / str(receipt.get("previous_version") or "previous")
        previous = self.paths.models_root / previous_relative
        root = _safe_model_root(self.paths, component_id)
        if root is None or _safe_leaf(self.paths.models_root, previous_relative.as_posix()) is None:
            return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
        if not previous.is_dir() or _is_reparse(previous) or root.exists() and _is_reparse(root):
            return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
        current_backup = self.paths.models_root / ".versions" / component_id / f"active-{int(time.time())}"
        backup_relative = Path(".versions") / component_id / current_backup.name
        if _safe_leaf(self.paths.models_root, backup_relative.as_posix()) is None:
            return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
        try:
            binding, bound_record, binding_error = resolve_execution_binding(
                expected_plan,
                catalog_binding=binding,
                current_record=bound_record,
                binding_provider=binding_provider,
            )
            if binding is None:
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            if root.exists():
                if _safe_model_root(self.paths, component_id) is None or _safe_leaf(self.paths.models_root, backup_relative.as_posix()) is None:
                    return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
                os.rename(root, current_backup)
            if _safe_model_root(self.paths, component_id) is None or _safe_leaf(self.paths.models_root, previous_relative.as_posix()) is None:
                self._restore_rollback(root, previous, current_backup)
                return {"status": "unavailable", "code": "rollback_candidate_missing", "execution": "not_run"}
            os.rename(previous, root)
            receipt = dict(receipt)
            receipt["rollback_candidate"] = False
            receipt["state"] = "INSTALLED_UNVERIFIED"
            receipt["operational"] = False
            binding, bound_record, binding_error = resolve_execution_binding(
                expected_plan,
                catalog_binding=binding,
                current_record=bound_record,
                binding_provider=binding_provider,
            )
            if binding is None:
                self._restore_rollback(root, previous, current_backup)
                return binding_refusal(binding_error or "catalog_binding_stale", component_id=component_id)
            write_component_receipt(self.paths.config_root, component_id, receipt, catalog_binding=binding)
            return {"status": "completed", "execution": "completed", "component_id": component_id, "state": "INSTALLED_UNVERIFIED"}
        except (OSError, ReceiptError, ValueError):
            self._restore_rollback(root, previous, current_backup)
            return {"status": "failed", "code": "rollback_activation_failed", "execution": "not_run"}


__all__ = ["ComponentUpdateExecutor", "RuntimeUpdateExecutor"]
