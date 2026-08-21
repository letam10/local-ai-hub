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
    def _symlink(self, link: Path, target: Path, *, directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink fixture unavailable: {type(exc).__name__}")

    @staticmethod
    def _rendered(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, default=repr)

    def test_first_use_missing_leaf_requires_safe_existing_parent(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "safe-root"
            root.mkdir()
            path = root / "workflow_library.json"
            store = WorkflowLibraryStore(path, root=root)
            self.assertEqual(store.snapshot()["recovery"]["status"], "clean")
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            self.assertTrue(path.is_file())

            missing_parent = root / "missing" / "workflow_library.json"
            refused = WorkflowLibraryStore(missing_parent, root=root).snapshot()
            self.assertEqual(refused["recovery"]["status"], "recovery_required")
            self.assertFalse((root / "missing").exists())

    def test_root_ancestor_parent_and_leaf_reparse_refuse_without_echo(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            outside = Path(temporary) / "outside"
            outside.mkdir()

            root_link = Path(temporary) / "root-link"
            self._symlink(root_link, root, directory=True)
            root_result = WorkflowLibraryStore(root_link / "workflow_library.json", root=root_link).snapshot()
            self.assertEqual(root_result["recovery"]["status"], "recovery_required")

            ancestor_link = Path(temporary) / "ancestor-link"
            self._symlink(ancestor_link, Path(temporary), directory=True)
            real_ancestor_root = Path(temporary) / "ancestor-root"
            real_ancestor_root.mkdir()
            ancestor_root = ancestor_link / "ancestor-root"
            ancestor_result = WorkflowLibraryStore(ancestor_root / "workflow_library.json", root=ancestor_root).snapshot()
            self.assertEqual(ancestor_result["recovery"]["status"], "recovery_required")

            parent_link = root / "nested"
            self._symlink(parent_link, outside, directory=True)
            parent_result = WorkflowLibraryStore(parent_link / "workflow_library.json", root=root).snapshot()
            self.assertEqual(parent_result["recovery"]["status"], "recovery_required")

            outside_file = outside / "outside.json"
            outside_file.write_bytes(b"outside-bytes")
            leaf_link = root / "workflow_library.json"
            self._symlink(leaf_link, outside_file)
            leaf_result = WorkflowLibraryStore(leaf_link, root=root).snapshot()
            self.assertEqual(leaf_result["recovery"]["status"], "recovery_required")
            self.assertEqual(outside_file.read_bytes(), b"outside-bytes")
            rendered = self._rendered((root_result, ancestor_result, parent_result, leaf_result))
            self.assertNotIn(str(outside), rendered)

    def test_lexical_escape_directory_and_lstat_fail_closed_without_echo(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore
        import src.services.workflow_library.library as library_module

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            escaped = root / ".." / "outside" / "workflow_library.json"
            result = WorkflowLibraryStore(escaped, root=root).snapshot()
            self.assertEqual(result["recovery"]["status"], "recovery_required")
            self.assertFalse((Path(temporary) / "outside").exists())

            directory_target = root / "workflow_library.json"
            directory_target.mkdir()
            directory_result = WorkflowLibraryStore(directory_target, root=root).snapshot()
            self.assertEqual(directory_result["recovery"]["status"], "recovery_required")

            marker = "C:\\private\\workflow-secret-marker"
            with patch.object(library_module.os, "lstat", side_effect=OSError(marker)):
                lstat_result = WorkflowLibraryStore(root / "other.json", root=root).snapshot()
            self.assertEqual(lstat_result["recovery"]["status"], "recovery_required")
            self.assertNotIn(marker, self._rendered(lstat_result))

    def test_invalid_utf8_and_read_failure_preserve_existing_bytes(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            path.write_bytes(b"\xff\xfe")
            store = WorkflowLibraryStore(path, root=path.parent)
            before = path.read_bytes()
            snapshot = store.snapshot()
            self.assertEqual(snapshot["recovery"]["status"], "recovery_required")
            self.assertFalse(store.save_workflow(entry(), expected_revision=0)["accepted"])
            self.assertEqual(path.read_bytes(), before)

            with patch.object(Path, "read_bytes", side_effect=OSError("raw-read-marker")):
                failed = store.snapshot()
            self.assertEqual(failed["recovery"]["status"], "recovery_required")
            self.assertNotIn("raw-read-marker", self._rendered(failed))

    def test_same_byte_and_changed_target_replacement_after_read_refuse(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path, root=path.parent)
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            before = path.read_bytes()
            original_read = Path.read_bytes

            for replacement_bytes in (before, b"replacement-bytes"):
                with self.subTest(replacement_bytes=replacement_bytes), patch.object(Path, "read_bytes") as read_bytes:
                    calls = 0

                    def swap_after_initial() -> bytes:
                        nonlocal calls
                        calls += 1
                        if calls == 2:
                            replacement = path.with_name(".replacement.json")
                            replacement.write_bytes(replacement_bytes)
                            os.replace(replacement, path)
                        return original_read(path)

                    read_bytes.side_effect = swap_after_initial
                    result = store.save_workflow(entry("changed"), expected_revision=1)
                    self.assertFalse(result["accepted"])
                    self.assertEqual(result["status"], "recovery_required")
                    self.assertEqual(path.read_bytes(), replacement_bytes)

    def test_temp_identity_drift_refuses_before_replace(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore
        import src.services.workflow_library.library as library_module

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            store = WorkflowLibraryStore(path, root=path.parent)
            self.assertTrue(store.save_workflow(entry(), expected_revision=0)["accepted"])
            before = path.read_bytes()
            original_temp_guard = library_module._temp_guard
            calls = 0

            def drift_after_create(temp: Path, location: object) -> object:
                nonlocal calls
                calls += 1
                if calls >= 2:
                    return None
                return original_temp_guard(temp, location)

            with patch.object(library_module, "_temp_guard", side_effect=drift_after_create), patch.object(
                library_module.os, "replace", side_effect=AssertionError("replace must not run after temp drift")
            ):
                result = store.save_workflow(entry("temp-drift"), expected_revision=1)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "recovery_required")
            self.assertEqual(path.read_bytes(), before)

    def test_storage_authority_does_not_use_path_resolve(self) -> None:
        from src.services.workflow_library import library as library_module

        source = Path(library_module.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".resolve(", source)
        self.assertIn("os.lstat", source)
        self.assertIn("os.replace", source)
        self.assertIn("os.fsync", source)

    def test_two_store_save_interleaving_conflicts_without_lost_update(self) -> None:
        from src.services.workflow_library import WorkflowLibraryStore

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "workflow_library.json"
            first = WorkflowLibraryStore(path)
            second = WorkflowLibraryStore(path)
            concurrent_result: dict[str, object] = {}
            interleaved = False
            original_atomic = first._atomic_write

            def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_guard: object | None = None) -> str:
                nonlocal interleaved
                if not interleaved:
                    interleaved = True
                    concurrent_result["result"] = second.save_workflow(entry("concurrent"), expected_revision=0)
                return original_atomic(value, expected_bytes=expected_bytes, expected_guard=expected_guard)

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

                def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_guard: object | None = None) -> str:
                    nonlocal interleaved
                    if not interleaved:
                        interleaved = True
                        self.assertTrue(second.save_workflow(entry("concurrent"), expected_revision=1)["accepted"])
                    return original_atomic(value, expected_bytes=expected_bytes, expected_guard=expected_guard)

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

            def interleave(value: dict[str, object], *, expected_bytes: bytes | None = None, expected_guard: object | None = None) -> str:
                nonlocal interleaved
                if not interleaved:
                    interleaved = True
                    self.assertTrue(second.save_workflow(entry("concurrent"), expected_revision=1)["accepted"])
                return original_atomic(value, expected_bytes=expected_bytes, expected_guard=expected_guard)

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

    def test_schema_module_has_no_runtime_execution_imports(self) -> None:
        source = (ROOT / "src" / "shared" / "schemas" / "workflow_library.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", source)
        self.assertNotIn("importlib", source)
        self.assertNotIn("socket", source)
        self.assertIsNotNone(importlib.util.find_spec("src.services.workflow_library"))


if __name__ == "__main__":
    unittest.main()
