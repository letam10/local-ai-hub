from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.api.context import ApiContext
from src.services.api.response import ApiResponse
from src.services.api.router import ApiRequest
from src.services.api.routes import node_studio as node_routes
from src.services.node_studio import state


class TestNodeDraftStorageSafety(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch_root = patch.object(state, "CONFIG_ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def graph() -> dict[str, object]:
        return {"nodes": [], "edges": []}

    def test_happy_path_is_opaque_and_truthful(self) -> None:
        saved = state.draft_persist("image", self.graph())
        self.assertEqual(saved, {"accepted": True, "status": "completed", "draft_id": "draft_image.json"})
        self.assertNotIn("path", json.dumps(saved))
        self.assertTrue((self.root / "node_studio_draft_image.json").is_file())

        loaded = state.draft_load("image")
        self.assertEqual(loaded, {"draft_id": "draft_image.json", "scope": "image", "graph": self.graph()})
        self.assertNotIn(str(self.root), json.dumps(loaded))

        cleared = state.draft_clear("image")
        self.assertTrue(cleared["accepted"])
        self.assertEqual(cleared["status"], "completed")
        self.assertFalse((self.root / "node_studio_draft_image.json").exists())
        self.assertEqual(state.draft_clear("image")["status"], "not_found")

    def test_scope_is_rejected_without_sanitizing_into_a_filename(self) -> None:
        for scope in ("", "Image", "image ", "../image", r"image\escape", r"C:\secret", "/tmp/image", "a" * 33, "image%2fsecret"):
            result = state.draft_persist(scope, self.graph())
            self.assertFalse(result["accepted"], scope)
            self.assertEqual(result["reason"], "scope_invalid")
            self.assertEqual(result["execution"], "not_run")
            if scope:
                self.assertNotIn(scope, json.dumps(result))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_graph_validation_is_bounded_and_path_free(self) -> None:
        hostile = {
            "nodes": [{"id": "n1", "type": "unknown", "data": {"value": r"C:\Users\secret"}}],
            "edges": [],
            "se" + "cret": "Bearer private token",
        }
        result = state.draft_persist("image", hostile)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "graph_invalid")
        self.assertNotIn("C:\\Users", json.dumps(result))
        self.assertNotIn("Bearer", json.dumps(result))

        too_large = {"nodes": [], "edges": [], "title": "x" * state._GRAPH_MAX_BYTES}
        oversized = state.draft_persist("image", too_large)
        self.assertFalse(oversized["accepted"])
        self.assertEqual(oversized["reason"], "graph_invalid")

    def test_duplicate_nonfinite_invalid_utf8_and_noncanonical_envelopes_fail_closed(self) -> None:
        path = self.root / "node_studio_draft_image.json"
        duplicate = b'{"draft_schema_version":1,"owner":"node_studio","owner":"node_studio","draft_id":"draft_image.json","scope":"image","graph":{"nodes":[],"edges":[]}}\n'
        path.write_bytes(duplicate)
        before = path.read_bytes()
        self.assertIsNone(state.draft_load("image"))
        refusal = state.draft_clear("image")
        self.assertFalse(refusal["accepted"])
        self.assertEqual(path.read_bytes(), before)

        path.write_bytes(b'{"draft_schema_version":1,"owner":"node_studio","draft_id":"draft_image.json","scope":"image","graph":{"nodes":[],"edges":[],"value":NaN}}\n')
        self.assertIsNone(state.draft_load("image"))
        path.write_bytes(b"\xff\xfe")
        self.assertIsNone(state.draft_load("image"))

        valid = {
            "draft_schema_version": 1,
            "owner": "node_studio",
            "draft_id": "draft_image.json",
            "scope": "image",
            "graph": self.graph(),
        }
        path.write_text(json.dumps(valid, indent=2), encoding="utf-8")
        self.assertIsNone(state.draft_load("image"))

    def test_foreign_directory_and_reparse_leaves_are_never_deleted(self) -> None:
        path = self.root / "node_studio_draft_image.json"
        foreign = {
            "draft_schema_version": 1,
            "owner": "foreign",
            "draft_id": "draft_image.json",
            "scope": "image",
            "graph": self.graph(),
        }
        path.write_text(json.dumps(foreign), encoding="utf-8")
        before = path.read_bytes()
        result = state.draft_clear("image")
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(path.read_bytes(), before)

        path.unlink()
        path.mkdir()
        result = state.draft_clear("image")
        self.assertFalse(result["accepted"])
        self.assertTrue(path.is_dir())
        path.rmdir()
        path.write_bytes(state._canonical_json({**foreign, "owner": "node_studio"}))
        real_lstat = state.os.lstat
        original = real_lstat(path)
        values = {
            name: getattr(original, name, 0)
            for name in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_file_attributes")
        }
        values["st_file_attributes"] = int(values.get("st_file_attributes", 0)) | state._REPARSE_POINT
        reparse_stat = type("ReparseStat", (), values)()

        def fake_lstat(candidate):
            return reparse_stat if Path(candidate) == path else real_lstat(candidate)

        with patch.object(state.os, "lstat", side_effect=fake_lstat):
            result = state.draft_clear("image")
        self.assertFalse(result["accepted"])
        self.assertTrue(path.exists())

    def test_write_failure_cleans_only_attested_task_temp_and_keeps_prior_bytes(self) -> None:
        first = state.draft_persist("image", self.graph())
        self.assertTrue(first["accepted"])
        path = self.root / "node_studio_draft_image.json"
        before = path.read_bytes()
        with patch.object(state.os, "fsync", side_effect=OSError("C:\\private marker")):
            result = state.draft_persist("image", {"nodes": [], "edges": [], "title": "new"})
        self.assertFalse(result["accepted"])
        self.assertIn(result["reason"], {"draft_write_failed", "draft_storage_unavailable"})
        self.assertNotIn("private marker", json.dumps(result))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob(".node-draft-*.tmp")), [])

    def test_target_replacement_before_commit_refuses_without_overwrite(self) -> None:
        self.assertTrue(state.draft_persist("image", self.graph())["accepted"])
        path = self.root / "node_studio_draft_image.json"
        before = path.read_bytes()
        original_replace = state.os.replace

        def replace_with_foreign_drift(source, target):
            if Path(target) == path:
                path.unlink()
                path.mkdir()
                raise OSError("target drift marker")
            return original_replace(source, target)

        with patch.object(state.os, "replace", side_effect=replace_with_foreign_drift):
            result = state.draft_persist("image", {"nodes": [], "edges": [], "title": "changed"})
        self.assertFalse(result["accepted"])
        self.assertIn(result["reason"], {"draft_write_failed", "draft_storage_conflict", "draft_manual_review"})
        self.assertNotIn(str(path), json.dumps(result))
        self.assertTrue(path.is_dir())

    def test_root_reparse_refuses_before_read_or_write(self) -> None:
        real_lstat = state.os.lstat
        original = real_lstat(self.root)
        values = {
            name: getattr(original, name, 0)
            for name in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_file_attributes")
        }
        values["st_file_attributes"] = int(values.get("st_file_attributes", 0)) | state._REPARSE_POINT
        reparse_stat = type("ReparseStat", (), values)()

        def fake_lstat(candidate):
            return reparse_stat if Path(candidate) == self.root else real_lstat(candidate)

        with patch.object(state.os, "lstat", side_effect=fake_lstat):
            result = state.draft_persist("image", self.graph())
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "draft_manual_review")
        self.assertEqual(list(self.root.iterdir()), [])


class TestNodeDraftDirectRoutes(unittest.TestCase):
    def _request(self, method: str, body: dict[str, object] | None = None) -> ApiRequest:
        return ApiRequest(method=method, path="/api/node-studio/drafts/image", query={}, headers={}, _body_reader=lambda strict: body or {})

    def test_routes_use_safe_state_contract_without_loopback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(state, "CONFIG_ROOT", root):
                context = ApiContext({
                    "node_draft_persist": state.draft_persist,
                    "node_draft_load": state.draft_load,
                    "node_draft_clear": state.draft_clear,
                })
                saved = node_routes.draft_save(self._request("POST", {"graph": {"nodes": [], "edges": []}}), context, {"scope": "image"})
                self.assertEqual(saved.status, 200)
                self.assertNotIn("draft_path", json.dumps(saved.payload))
                loaded = node_routes.draft_get(self._request("GET"), context, {"scope": "image"})
                self.assertEqual(loaded.status, 200)
                self.assertEqual(loaded.payload["draft"]["draft_id"], "draft_image.json")
                cleared = node_routes.draft_delete(self._request("DELETE"), context, {"scope": "image"})
                self.assertEqual(cleared.status, 200)
                missing = node_routes.draft_get(self._request("GET"), context, {"scope": "image"})
                self.assertEqual(missing.status, 404)

    def test_routes_strip_legacy_paths_and_raw_failures(self) -> None:
        marker = r"C:\Users\secret https://example.invalid Bearer token"
        context = ApiContext({
            "node_draft_load": lambda scope: {"draft_path": marker, "scope": "image", "graph": {"nodes": [], "edges": []}},
            "node_draft_persist": lambda scope, graph: {"accepted": False, "reason": marker, "draft_path": marker},
            "node_draft_clear": lambda scope: {"accepted": False, "status": "manual_review", "reason": marker, "draft_path": marker},
        })
        responses: list[ApiResponse] = [
            node_routes.draft_get(self._request("GET"), context, {"scope": "image"}),
            node_routes.draft_save(self._request("POST", {"graph": {}}), context, {"scope": "image"}),
            node_routes.draft_delete(self._request("DELETE"), context, {"scope": "image"}),
        ]
        rendered = json.dumps([item.payload for item in responses], ensure_ascii=False)
        self.assertNotIn(marker, rendered)
        self.assertNotIn("Bearer", rendered)
        self.assertEqual(responses[0].status, 404)
        self.assertEqual(responses[1].status, 400)
        self.assertEqual(responses[2].status, 409)
        self.assertNotIn("execution", json.dumps(responses[0].payload))
        self.assertEqual(responses[1].payload["execution"], "not_run")


if __name__ == "__main__":
    unittest.main()
