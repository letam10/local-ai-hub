from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from unittest.mock import patch

from src.services.api import api_server
import src.services.project_manager.manager as project_manager_module
from src.services.project_manager.manager import CreativeProjectManager


ASSET_A = "artifact_" + "a" * 32
ASSET_B = "artifact_" + "b" * 32


def fake_artifact(artifact_id: str) -> dict | None:
    if artifact_id not in {ASSET_A, ASSET_B}:
        return None
    return {
        "id": artifact_id,
        "name": "before.png" if artifact_id == ASSET_A else "after.png",
        "size_bytes": 128 if artifact_id == ASSET_A else 256,
        "media_type": "image/png",
        "url": f"/api/artifacts/{artifact_id}",
        "created_at": "2026-08-10T00:00:00+00:00",
    }


def fake_artifacts(*, limit: int = 240) -> list[dict]:
    return [item for item in (fake_artifact(ASSET_A), fake_artifact(ASSET_B)) if item][:limit]


class CreativeProjectManagerTests(unittest.TestCase):
    def make_manager(self, temporary: str) -> CreativeProjectManager:
        return CreativeProjectManager(Path(temporary) / "creative_workspace.json", artifact_describer=fake_artifact, artifact_lister=fake_artifacts)

    def test_project_asset_recipe_compare_and_safe_round_trip(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_manager(temporary)
            project = manager.create_project({"title": "Campaign", "description": "Image exploration", "tags": ["Brand"]})["project"]
            recipe = manager.create_recipe({
                "project_id": project["id"],
                "title": "Hero portrait",
                "prompt_template": "{{subject}} in a studio",
                "variables": [{"name": "subject", "label": "Subject", "default": "astronaut", "required": True}],
                "style_block": "soft rim light",
                "negative_block": "blur",
                "seed": 77,
                "model": "flux",
                "settings": {"width": 1024, "height": 768, "steps": 24},
                "workflow_preset": "image_create_upscale",
            })["recipe"]
            applied = manager.apply_recipe(recipe["id"], {"project_id": project["id"], "values": {"subject": "architect"}})
            self.assertIn("architect", applied["quick"]["prompt"])
            self.assertEqual(applied["quick"]["seed"], 77)

            manager.add_project_asset(project["id"], {"artifact_id": ASSET_A, "recipe_id": recipe["id"], "tags": ["draft"], "provenance": {"node_type": "flux_generate"}})
            manager.add_project_asset(project["id"], {"artifact_id": ASSET_B, "parent_artifact_id": ASSET_A, "recipe_id": recipe["id"], "tags": ["selected"]})
            manager.update_asset(ASSET_B, {"favorite": True})
            collection = manager.create_collection({"title": "Keepers", "asset_ids": [ASSET_B]})["collection"]
            assets = manager.list_assets(favorite=True, collection_id=collection["id"])["assets"]
            self.assertEqual([item["id"] for item in assets], [ASSET_B])
            self.assertEqual(assets[0]["lineage"]["parent_artifact_id"], ASSET_A)

            manager.update_compare(project["id"], {"artifact_id": ASSET_A, "label": "Before"})
            board = manager.update_compare(project["id"], {"artifact_id": ASSET_B, "label": "After", "selected_artifact_id": ASSET_B, "favorite_selected": True})["compare"]
            self.assertEqual(board["selected_artifact_id"], ASSET_B)
            self.assertEqual(len(board["items"]), 2)
            self.assertIn("size_bytes", board["differences"])

            manifest = manager.export_project(project["id"])["manifest"]
            serialized = json.dumps(manifest, ensure_ascii=False)
            self.assertIn("creative-project-export.v1", serialized)
            self.assertNotIn("C:\\", serialized)
            self.assertNotIn("path", serialized.lower())

            with TemporaryDirectory() as imported_temporary:
                imported = self.make_manager(imported_temporary).import_project({"manifest": manifest, "conflict": "copy"})
                self.assertTrue(imported["imported"])
                self.assertEqual(imported["project"]["asset_count"], 2)

            skipped = manager.import_project({"manifest": manifest, "conflict": "skip"})
            self.assertFalse(skipped["imported"])
    def test_project_recent_archive_and_recipe_versions_are_bounded(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_manager(temporary)
            projects = [manager.create_project({"title": f"Project {index}"})["project"] for index in range(13)]
            listing = manager.list_projects()
            self.assertEqual(len(listing["recent_projects"]), 12)
            self.assertEqual(listing["recent_projects"][0]["id"], projects[-1]["id"])

            archived = manager.archive_project(projects[0]["id"])["project"]
            self.assertEqual(archived["status"], "archived")
            restored = manager.archive_project(projects[0]["id"], archived=False)["project"]
            self.assertEqual(restored["status"], "active")

            recipe = manager.create_recipe({"title": "Versioned", "prompt_template": "first"})["recipe"]
            updated = manager.update_recipe(recipe["id"], {"style_block": "second"})["recipe"]
            self.assertEqual(updated["version"], 2)
            self.assertEqual(updated["style_block"], "second")

    def test_recipe_pack_variables_and_conflicts_are_safe(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_manager(temporary)
            recipe = manager.create_recipe({"title": "Texture", "prompt_template": "{{material}}", "variables": [{"name": "material", "default": "paper", "required": True}]})["recipe"]
            pack = manager.export_recipe_pack([recipe["id"]])["pack"]
            imported = manager.import_recipe_pack({"pack": pack, "conflict": "copy"})["recipes"]
            self.assertEqual(len(imported), 1)
            self.assertNotEqual(imported[0]["id"], recipe["id"])
            with self.assertRaisesRegex(ValueError, "đường dẫn máy"):
                manager.create_recipe({"title": "Unsafe", "prompt_template": r"C:\private\prompt.txt"})
            with self.assertRaisesRegex(ValueError, "path hoặc secret"):
                manager.import_recipe_pack({"pack": {"contract_version": "creative-recipe-pack.v1", "recipes": [{"title": "Unsafe", "settings": {"path": "secret"}}]}})

    def test_recovery_does_not_overwrite_malformed_workspace(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            path.write_text("{broken", encoding="utf-8")
            manager = CreativeProjectManager(path, artifact_describer=fake_artifact, artifact_lister=fake_artifacts)
            overview = manager.overview()
            self.assertEqual(overview["recovery"]["status"], "recovery_required")
            with self.assertRaisesRegex(ValueError, "không tự ghi đè"):
                manager.create_project({"title": "Should not overwrite"})
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_recovery_blocks_unknown_workspace_contract_without_overwrite(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            payload = {"contract_version": "creative-workspace.v999", "schema_version": 999, "projects": {}}
            path.write_text(json.dumps(payload), encoding="utf-8")
            manager = CreativeProjectManager(path, artifact_describer=fake_artifact, artifact_lister=fake_artifacts)
            self.assertEqual(manager.overview()["recovery"]["status"], "recovery_required")
            with self.assertRaisesRegex(ValueError, "không tương thích"):
                manager.create_project({"title": "Blocked"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)

    def test_gallery_keeps_unavailable_workflow_truthful(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_manager(temporary)
            gallery = manager.workflow_gallery()["gallery"]
            video = next(item for item in gallery if item["id"] == "video_generation_unavailable")
            self.assertEqual(video["status"], "unavailable")
            self.assertTrue(video["availability"]["reason"])
            self.assertTrue(video["availability"]["action"])

    def test_recipe_application_updates_editable_graph_only(self) -> None:
        script = """
import { applyRecipeToGraph } from './src/ui/features/node_studio/studio.js';
const source = {nodes:[
  {id:'prompt',type:'prompt_text',data:{text:'old'}},
  {id:'generate',type:'flux_generate',data:{width:512,height:512,steps:4,seed:1}},
  {id:'save',type:'save_image',data:{}}
]};
const next = applyRecipeToGraph(source, {prompt:'new prompt', negative_prompt:'blur', seed:77, settings:{width:1024,height:768,steps:24}});
if (source.nodes[0].data.text !== 'old') throw new Error('source graph mutated');
if (next.nodes[0].data.text !== 'new prompt') throw new Error('prompt was not applied');
if (next.nodes[1].data.seed !== 77 || next.nodes[1].data.width !== 1024 || next.nodes[1].data.negative_prompt !== 'blur') throw new Error('settings were not applied');
console.log('ok');
"""
        completed = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "ok")

    def test_loopback_creative_routes_keep_public_contract_opaque(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_manager(temporary)
            original = api_server.project_manager
            server = api_server.HubHTTPServer(("127.0.0.1", 0), api_server.HubHandler)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            api_server.project_manager = manager
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def request(path: str, method: str = "GET", payload: dict | None = None) -> dict:
                data = json.dumps(payload).encode("utf-8") if payload is not None else None
                headers = {"Content-Type": "application/json"} if data else {}
                with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=3) as response:
                    return json.loads(response.read().decode("utf-8"))

            try:
                project = request("/api/projects", "POST", {"title": "Loopback Project"})["project"]
                recipe = request("/api/recipes", "POST", {"project_id": project["id"], "title": "Loopback Recipe", "prompt_template": "{{subject}}", "variables": [{"name": "subject", "default": "leaf"}]})["recipe"]
                request(f"/api/projects/{project['id']}/assets", "POST", {"artifact_id": ASSET_A, "recipe_id": recipe["id"]})
                request(f"/api/projects/{project['id']}/compare", "POST", {"artifact_id": ASSET_A, "label": "A", "selected_artifact_id": ASSET_A, "favorite_selected": True})
                overview = request("/api/creative/overview")
                detail = request(f"/api/projects/{project['id']}")
                exported = request(f"/api/projects/{project['id']}/export")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                api_server.project_manager = original

            self.assertFalse(thread.is_alive())
            self.assertEqual(overview["projects"][0]["id"], project["id"])
            self.assertEqual(detail["compare"]["selected_artifact_id"], ASSET_A)
            serialized = json.dumps({"overview": overview, "detail": detail, "exported": exported}, ensure_ascii=False)
            self.assertIn(ASSET_A, serialized)
            self.assertNotIn("C:\\", serialized)
            self.assertNotIn("input_path", serialized)
            self.assertNotIn("resume_data", serialized)


class ProjectWorkspaceStorageSafetyTests(unittest.TestCase):
    @staticmethod
    def _manager(path: Path) -> CreativeProjectManager:
        return CreativeProjectManager(path)

    @staticmethod
    def _reparse_stat(path: Path, lstat_fn=os.lstat):
        original = lstat_fn(path)
        values = {
            name: getattr(original, name, 0)
            for name in ("st_mode", "st_ino", "st_dev", "st_size", "st_mtime_ns", "st_ctime_ns", "st_file_attributes")
        }
        values["st_file_attributes"] = int(values.get("st_file_attributes", 0)) | project_manager_module._REPARSE_POINT
        return type("ReparseStat", (), values)()

    def test_reparse_target_refuses_before_read_and_preserves_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            original = b'{"contract_version":"creative-workspace.v1","schema_version":1,"projects":{}}\n'
            path.write_bytes(original)
            manager = self._manager(path)
            real_lstat = project_manager_module.os.lstat

            def fake_lstat(candidate):
                return self._reparse_stat(Path(candidate), real_lstat) if Path(candidate) == path else real_lstat(candidate)

            with patch.object(project_manager_module.os, "lstat", side_effect=fake_lstat), patch.object(Path, "read_bytes", side_effect=AssertionError("unsafe leaf was read")):
                overview = manager.overview()
            self.assertEqual(overview["recovery"]["status"], "recovery_required")
            self.assertEqual(path.read_bytes(), original)

    def test_reparse_parent_refuses_mutation_without_creating_state(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "creative_workspace.json"
            manager = self._manager(path)
            real_lstat = project_manager_module.os.lstat

            def fake_lstat(candidate):
                return self._reparse_stat(root, real_lstat) if Path(candidate) == root else real_lstat(candidate)

            with patch.object(project_manager_module.os, "lstat", side_effect=fake_lstat):
                with self.assertRaisesRegex(ValueError, "manual review"):
                    manager.create_project({"title": "Must not write"})
            self.assertFalse(path.exists())

    def test_duplicate_nonfinite_and_oversized_json_require_recovery_without_overwrite(self) -> None:
        cases = (
            b'{"contract_version":"creative-workspace.v1","schema_version":1,"projects":{},"projects":{}}',
            b'{"contract_version":"creative-workspace.v1","schema_version":1,"projects":{},"recent_project_ids":NaN}',
            b"{" + b"x" * (8 * 1024 * 1024 + 1) + b"}",
        )
        for original in cases:
            with self.subTest(size=len(original)):
                with TemporaryDirectory() as temporary:
                    path = Path(temporary) / "creative_workspace.json"
                    path.write_bytes(original)
                    manager = self._manager(path)
                    overview = manager.overview()
                    self.assertEqual(overview["recovery"]["status"], "recovery_required")
                    self.assertEqual(path.read_bytes(), original)
                    rendered = json.dumps(overview, ensure_ascii=False)
                    self.assertNotIn(str(path), rendered)

    def test_lstat_failure_is_fixed_recovery_without_write(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            manager = self._manager(path)
            real_lstat = project_manager_module.os.lstat

            def failing_lstat(candidate):
                if Path(candidate) == path.parent:
                    raise OSError("private path marker")
                return real_lstat(candidate)

            with patch.object(project_manager_module.os, "lstat", side_effect=failing_lstat):
                overview = manager.overview()
            self.assertEqual(overview["recovery"]["status"], "recovery_required")
            self.assertNotIn("private path marker", json.dumps(overview))
            self.assertFalse(path.exists())

    def test_parent_identity_drift_before_replace_preserves_existing_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            manager = self._manager(path)
            manager.create_project({"title": "before"})
            original = path.read_bytes()
            baseline = manager._context(path, allow_missing=True)
            drifted = project_manager_module._StorageContext(
                parent_chain=((999, 998, 997, 996),),
                target=baseline.target,
            )
            drift = False
            real_context = manager._context
            real_fsync = project_manager_module.os.fsync

            def fake_context(target, *, allow_missing):
                return drifted if drift else real_context(target, allow_missing=allow_missing)

            def fake_fsync(handle):
                nonlocal drift
                real_fsync(handle)
                drift = True

            with patch.object(manager, "_context", side_effect=fake_context), patch.object(project_manager_module.os, "fsync", side_effect=fake_fsync):
                with self.assertRaisesRegex(ValueError, "changed"):
                    manager._mutate(lambda state: state.update({"recent_project_ids": []}))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(temporary).glob(".workspace-*.tmp")), [])

    def test_replace_failure_preserves_target_and_cleans_owned_temp(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            manager = self._manager(path)
            manager.create_project({"title": "before"})
            original = path.read_bytes()
            with patch.object(project_manager_module.os, "replace", side_effect=OSError("private path marker")):
                with self.assertRaisesRegex(ValueError, "could not be written"):
                    manager._mutate(lambda state: state.update({"recent_project_ids": []}))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(temporary).glob(".workspace-*.tmp")), [])

    def test_two_manager_instances_refuse_stale_writer_and_preserve_newer_bytes(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            first = self._manager(path)
            first.create_project({"title": "first"})
            second = self._manager(path)

            def concurrent_write(state):
                first.create_project({"title": "newer"})
                state["recent_project_ids"] = list(state.get("recent_project_ids", []))

            with self.assertRaisesRegex(ValueError, "changed"):
                second._mutate(concurrent_write)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([item["title"] for item in payload["projects"].values()], ["first", "newer"])

    def test_same_byte_target_replacement_is_a_conflict(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "creative_workspace.json"
            manager = self._manager(path)
            manager.create_project({"title": "before"})
            replacement = path.with_name("replacement.json")

            def replace_with_same_bytes(state):
                replacement.write_bytes(path.read_bytes())
                os.replace(replacement, path)
                state["recent_project_ids"] = list(state.get("recent_project_ids", []))

            with self.assertRaisesRegex(ValueError, "changed"):
                manager._mutate(replace_with_same_bytes)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["projects"] != {}, True)


if __name__ == "__main__":
    unittest.main()
