from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def graph() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "image-review",
        "title": "Image review",
        "scope": "image",
        "nodes": [
            {"id": "input", "type": "load_image", "data": {}, "position": {"x": 0, "y": 0}},
            {"id": "preview", "type": "preview_image", "data": {}, "position": {"x": 240, "y": 0}},
        ],
        "edges": [
            {
                "id": "edge-preview",
                "source": {"node": "input", "port": "image"},
                "target": {"node": "preview", "port": "image"},
            }
        ],
        "groups": [],
    }


def entry(workflow_id: str = "image-review") -> dict[str, object]:
    return {
        "schema_version": "workflow-entry.v1",
        "id": workflow_id,
        "title": "Image review",
        "description": "Declarative review graph.",
        "scope": "image",
        "graph": graph(),
        "revision": 1,
        "status": "draft",
        "source": "local",
        "tags": ["v5", "review"],
        "created_at": "",
        "updated_at": "",
    }


def library(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": "workflow-library.v1", "library_revision": 0, "workflows": list(items)}


class WorkflowLibrarySchemaTests(unittest.TestCase):
    def test_schema_parity_and_valid_detached_entry(self) -> None:
        from src.shared.schemas.workflow_library import validate_workflow_library, workflow_library_schema

        schema = workflow_library_schema()

        def parity(value: object) -> None:
            if isinstance(value, dict):
                if value.get("type") == "object":
                    self.assertTrue(set(value.get("required", [])) <= set(value.get("properties", {})))
                for child in value.values():
                    parity(child)
            elif isinstance(value, list):
                for child in value:
                    parity(child)

        parity(schema)
        result = validate_workflow_library(library(entry()))
        self.assertTrue(result["valid"])
        result["library"]["workflows"][0]["title"] = "detached"
        self.assertEqual(entry()["title"], "Image review")

    def test_rejects_unknown_path_secret_command_and_does_not_reflect_values(self) -> None:
        from src.shared.schemas.workflow_library import validate_workflow_library

        unsafe_value = "s" + "k-never-real-value"
        candidate = library(entry())
        candidate["workflows"][0]["graph"]["nodes"][0]["data"]["input_path"] = r"C:\private\input.png"
        candidate["unsafe_field"] = unsafe_value
        result = validate_workflow_library(candidate)
        self.assertFalse(result["valid"])
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(unsafe_value, rendered)
        self.assertNotIn(r"C:\private", rendered)

        command = library(entry())
        command["workflows"][0]["graph"]["nodes"][0]["data"]["notes"] = "run ffmpeg input output"
        self.assertFalse(validate_workflow_library(command)["valid"])

    def test_safe_import_is_bounded_duplicate_key_and_nonfinite_fail_closed(self) -> None:
        from src.shared.schemas.workflow_library import safe_import_workflow_library

        duplicate = '{"schema_version":"workflow-library.v1","schema_version":"workflow-library.v1","library_revision":0,"workflows":[]}'
        duplicate_result = safe_import_workflow_library(duplicate)
        self.assertFalse(duplicate_result["accepted"])
        self.assertEqual(duplicate_result["errors"][0]["code"], "duplicate_json_key")
        nonfinite_result = safe_import_workflow_library('{"schema_version":"workflow-library.v1","library_revision":NaN,"workflows":[]}')
        self.assertFalse(nonfinite_result["accepted"])
        self.assertEqual(nonfinite_result["errors"][0]["code"], "nonfinite_number")
        self.assertEqual(safe_import_workflow_library(b"\xff")["errors"][0]["code"], "invalid_utf8")

    def test_canonical_export_is_stable_for_reordered_entities(self) -> None:
        from src.shared.schemas.workflow_library import canonical_workflow_library, validate_workflow_library

        original = library(entry("b-workflow"), entry("a-workflow"))
        reordered = copy.deepcopy(original)
        reordered["workflows"].reverse()
        reordered["workflows"][0]["graph"]["nodes"].reverse()
        reordered["workflows"][0]["graph"]["edges"].reverse()
        self.assertEqual(canonical_workflow_library(original), canonical_workflow_library(reordered))
        self.assertEqual(validate_workflow_library(original)["fingerprint"], validate_workflow_library(reordered)["fingerprint"])


class WorkflowLibraryStoreTests(unittest.TestCase):
    def test_two_store_save_interleaving_conflicts_without_lost_update(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            first = WorkflowLibraryStore(path)
            second = WorkflowLibraryStore(path)
            concurrent_result: dict[str, object] = {}
            interleaved = False
            original_atomic = first._atomic_write

            def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_identity: object = None) -> str:
                nonlocal interleaved
                if not interleaved:
                    interleaved = True
                    concurrent_result["result"] = second.save_workflow(entry("concurrent"), expected_revision=0)
                return original_atomic(value, expected_bytes=expected_bytes, expected_identity=expected_identity)

            with patch.object(first, "_atomic_write", side_effect=interleave):
                stale = first.save_workflow(entry("stale"), expected_revision=0)

            self.assertTrue(concurrent_result["result"]["accepted"])
            self.assertFalse(stale["accepted"])
            self.assertEqual(stale["status"], "conflict")
            self.assertNotIn("stale", json.dumps(first.list_workflows(), ensure_ascii=False))
            self.assertEqual(first.list_workflows()["workflows"][0]["id"], "concurrent")
            self.assertNotIn(temporary, json.dumps(stale, ensure_ascii=False))

    def test_two_store_delete_and_import_interleavings_preserve_newer_bytes(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        for operation in ("delete", "import"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "workflow_library.json"
                first = WorkflowLibraryStore(path)
                second = WorkflowLibraryStore(path)
                self.assertTrue(first.save_workflow(entry(), expected_revision=0)["accepted"])
                original_atomic = first._atomic_write
                interleaved = False

                def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_identity: object = None) -> str:
                    nonlocal interleaved
                    if not interleaved:
                        interleaved = True
                        self.assertTrue(second.save_workflow(entry("concurrent"), expected_revision=1)["accepted"])
                    return original_atomic(value, expected_bytes=expected_bytes, expected_identity=expected_identity)

                with patch.object(first, "_atomic_write", side_effect=interleave):
                    if operation == "delete":
                        result = first.delete_workflow("image-review", expected_revision=1)
                    else:
                        imported = library(entry("imported"))
                        result = first.import_json(json.dumps(imported), expected_revision=1)

                self.assertFalse(result["accepted"])
                self.assertEqual(result["status"], "conflict")
                self.assertEqual(first.list_workflows()["workflows"][0]["id"], "concurrent")

    def test_confirmed_migration_uses_the_same_conflict_guard(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            first = WorkflowLibraryStore(path)
            second = WorkflowLibraryStore(path)
            self.assertTrue(first.save_workflow(entry(), expected_revision=0)["accepted"])
            original_atomic = first._atomic_write
            interleaved = False

            def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_identity: object = None) -> str:
                nonlocal interleaved
                if not interleaved:
                    interleaved = True
                    self.assertTrue(second.save_workflow(entry("concurrent"), expected_revision=1)["accepted"])
                return original_atomic(value, expected_bytes=expected_bytes, expected_identity=expected_identity)

            with patch.object(first, "_atomic_write", side_effect=interleave):
                result = first.confirm_migration([entry("migrated")], expected_revision=1)

            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(first.list_workflows()["workflows"][0]["id"], "concurrent")

    def test_atomic_revision_conflict_and_detached_export(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            store = WorkflowLibraryStore(Path(temporary) / "workflow_library.json")
            first = store.save_workflow(entry(), expected_revision=0)
            self.assertTrue(first["accepted"])
            self.assertEqual(first["library_revision"], 1)
            stale = store.save_workflow(entry("second"), expected_revision=0)
            self.assertFalse(stale["accepted"])
            self.assertEqual(stale["status"], "conflict")
            exported = store.export_json()
            self.assertTrue(exported["ready"])
            exported["content"] = "mutated"
            self.assertNotEqual(store.export_json()["content"], "mutated")

    def test_invalid_local_file_requires_recovery_without_overwrite(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            path.write_text("{not-json", encoding="utf-8")
            store = WorkflowLibraryStore(path)
            snapshot = store.snapshot()
            self.assertEqual(snapshot["recovery"]["status"], "recovery_required")
            result = store.save_workflow(entry(), expected_revision=0)
            self.assertFalse(result["accepted"])
            self.assertEqual(path.read_text(encoding="utf-8"), "{not-json")

    def test_migration_is_dry_run_nonmutating_and_requires_confirmation(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore
        from src.shared.schemas.workflow_library import plan_localstorage_migration

        candidate = entry("migrated")
        before = copy.deepcopy(candidate)
        plan = plan_localstorage_migration([candidate])
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["status"], "ready")
        plan["plans"][0]["id"] = "changed-only-in-plan"
        self.assertEqual(candidate, before)
        with tempfile.TemporaryDirectory() as temporary:
            store = WorkflowLibraryStore(Path(temporary) / "library.json")
            self.assertFalse(store.list_workflows()["workflows"])
            confirmed = store.confirm_migration([candidate], expected_revision=0)
            self.assertTrue(confirmed["accepted"])
            self.assertTrue(store.list_workflows()["workflows"])


class WorkflowLibraryLocationSafetyTests(unittest.TestCase):
    @staticmethod
    def _same_path(left: object, right: Path) -> bool:
        try:
            return Path(left).absolute() == right.absolute()
        except (TypeError, ValueError, OSError):
            return False

    def _assert_location_refusal(self, value: dict[str, object], path: Path) -> None:
        encoded = json.dumps(value, ensure_ascii=True)
        self.assertIn("recovery_required", encoded)
        self.assertNotIn(str(path), encoded)
        self.assertNotIn("https://", encoded)
        self.assertNotIn("secret", encoded.casefold())
        self.assertNotIn("[object Object]", encoded)

    def test_safe_first_use_regular_parent_and_full_metadata_flow(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            saved = store.save_workflow(entry(), expected_revision=0)
            self.assertTrue(saved["accepted"])
            self.assertTrue(store.list_workflows()["workflows"])
            self.assertTrue(store.get_workflow("image-review")["workflow"])
            self.assertTrue(store.export_json()["ready"])
            imported = store.import_json(json.dumps(library(entry("imported"))), expected_revision=1)
            self.assertTrue(imported["accepted"])
            confirmed = store.confirm_migration([entry("migrated")], expected_revision=2)
            self.assertTrue(confirmed["accepted"])
            deleted = store.delete_workflow("migrated", expected_revision=3)
            self.assertTrue(deleted["accepted"])

    def test_reparse_root_ancestor_parent_and_leaf_refuse_without_echo(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            config_root = Path(temporary) / "Config"
            parent = config_root / "nested"
            parent.mkdir(parents=True)
            path = parent / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            for label, reparse_target, create_leaf in (
                ("root", config_root, False),
                ("ancestor", config_root.parent, False),
                ("parent", parent, False),
                ("leaf", path, True),
            ):
                with self.subTest(label=label):
                    if create_leaf:
                        path.write_text("{}", encoding="utf-8")
                    with patch.object(
                        library_module,
                        "is_reparse_point",
                        side_effect=lambda value, target=reparse_target: self._same_path(value, target),
                    ):
                        result = store.list_workflows()
                    self._assert_location_refusal(result, path)
                    if create_leaf:
                        self.assertEqual(path.read_text(encoding="utf-8"), "{}")
                    if path.exists() and path.is_file():
                        path.unlink()

    def test_directory_lstat_failure_and_lexical_escape_refuse(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory_target = root / "workflow_library.json"
            directory_target.mkdir()
            self._assert_location_refusal(WorkflowLibraryStore(directory_target).list_workflows(), directory_target)

            lstat_target = root / "lstat-failure" / "workflow_library.json"
            lstat_target.parent.mkdir()
            store = WorkflowLibraryStore(lstat_target)
            original_identity = library_module._identity_signature

            def fail_lstat(value: Path, *, expected_kind: str | None = None):
                if self._same_path(value, lstat_target.parent):
                    return None, "workflow_library_lstat_failed"
                return original_identity(value, expected_kind=expected_kind)

            with patch.object(library_module, "_identity_signature", side_effect=fail_lstat):
                self._assert_location_refusal(store.list_workflows(), lstat_target)

            escaped = root / "Config" / ".." / "outside" / "workflow_library.json"
            self._assert_location_refusal(WorkflowLibraryStore(escaped).list_workflows(), escaped)

    def test_reparse_temp_parent_after_creation_refuses_without_target_write(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            original_named = library_module.tempfile.NamedTemporaryFile
            phase = {"after_creation": False}

            def create_then_reparse(*args: object, **kwargs: object):
                handle = original_named(*args, **kwargs)
                phase["after_creation"] = True
                return handle

            def fake_reparse(value: object) -> bool:
                return phase["after_creation"] and self._same_path(value, path.parent)

            with patch.object(library_module.tempfile, "NamedTemporaryFile", side_effect=create_then_reparse), patch.object(library_module, "is_reparse_point", side_effect=fake_reparse):
                result = store.save_workflow(entry(), expected_revision=0)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "recovery_required")
            self.assertFalse(path.exists())
            self._assert_location_refusal(result, path)

    def test_same_byte_target_replacement_is_conflict_and_preserves_bytes(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            before = path.read_bytes()
            original_named = library_module.tempfile.NamedTemporaryFile

            def replace_after_creation(*args: object, **kwargs: object):
                handle = original_named(*args, **kwargs)
                replacement = path.with_name("replacement.json")
                replacement.write_bytes(before)
                os.replace(replacement, path)
                return handle

            with patch.object(library_module.tempfile, "NamedTemporaryFile", side_effect=replace_after_creation):
                result = store.save_workflow(entry("changed"), expected_revision=1)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "conflict")
            self.assertEqual(path.read_bytes(), before)

    def test_parent_identity_drift_between_read_and_replace_refuses(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            before = path.read_bytes()
            original_named = library_module.tempfile.NamedTemporaryFile
            original_identity = library_module._identity_signature
            drift = {"enabled": False}

            def create_then_drift(*args: object, **kwargs: object):
                handle = original_named(*args, **kwargs)
                drift["enabled"] = True
                return handle

            def drift_parent(value: Path, *, expected_kind: str | None = None):
                identity, code = original_identity(value, expected_kind=expected_kind)
                if drift["enabled"] and self._same_path(value, path.parent) and identity is not None:
                    return (identity[0], identity[1] + 1, *identity[2:]), code
                return identity, code

            with patch.object(library_module.tempfile, "NamedTemporaryFile", side_effect=create_then_drift), patch.object(library_module, "_identity_signature", side_effect=drift_parent):
                result = store.save_workflow(entry("changed"), expected_revision=1)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "recovery_required")
            self.assertEqual(path.read_bytes(), before)

    def test_temp_identity_drift_before_replace_refuses_and_does_not_echo(self) -> None:
        import src.services.workflow_library.library as library_module
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path)
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            before = path.read_bytes()
            original_named = library_module.tempfile.NamedTemporaryFile
            original_identity = library_module._identity_signature
            temp_path: dict[str, Path | None] = {"value": None}
            temp_calls = {"count": 0}

            def capture_temp(*args: object, **kwargs: object):
                handle = original_named(*args, **kwargs)
                temp_path["value"] = Path(handle.name)
                return handle

            def drift_temp(value: Path, *, expected_kind: str | None = None):
                identity, code = original_identity(value, expected_kind=expected_kind)
                if temp_path["value"] is not None and self._same_path(value, temp_path["value"]):
                    temp_calls["count"] += 1
                    if temp_calls["count"] >= 2 and identity is not None:
                        return (identity[0], identity[1] + 1, *identity[2:]), code
                return identity, code

            with patch.object(library_module.tempfile, "NamedTemporaryFile", side_effect=capture_temp), patch.object(library_module, "_identity_signature", side_effect=drift_temp):
                result = store.save_workflow(entry("changed"), expected_revision=1)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "recovery_required")
            self.assertEqual(path.read_bytes(), before)
            self._assert_location_refusal(result, path)

    def test_location_contract_does_not_use_resolve_as_authority(self) -> None:
        source = (ROOT / "src" / "services" / "workflow_library" / "library.py").read_text(encoding="utf-8")
        self.assertNotIn(".resolve(", source)
        self.assertIn("_validate_location", source)
        self.assertIn("is_reparse_point", source)
        self.assertIn("expected_identity", source)

    def test_schema_module_has_no_runtime_execution_imports(self) -> None:
        source = (ROOT / "src" / "shared" / "schemas" / "workflow_library.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("importlib", source)
        self.assertNotIn("socket", source)
        self.assertIsNotNone(importlib.util.find_spec("src.services.workflow_library"))


if __name__ == "__main__":
    unittest.main()
