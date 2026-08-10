from __future__ import annotations

import json
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.services.api import api_server
from src.services.image_mask_studio.config import CONFIG_CONTRACT, CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, normalize_config
from src.services.image_mask_studio.manager import ImageMaskStudioManager, StudioConflictError
from src.services.project_manager.manager import CreativeProjectManager


ASSET_A = "artifact_" + "a" * 32
ASSET_B = "artifact_" + "b" * 32
ASSET_C = "artifact_" + "c" * 32


def fake_artifact(artifact_id: str) -> dict | None:
    if artifact_id not in {ASSET_A, ASSET_B, ASSET_C}:
        return None
    return {
        "id": artifact_id,
        "name": {ASSET_A: "source.png", ASSET_B: "mask.png", ASSET_C: "derivative.png"}[artifact_id],
        "size_bytes": 240,
        "media_type": "image/png",
        "url": f"/api/artifacts/{artifact_id}",
        "sha256": "a" * 64,
        "created_at": "2026-08-10T00:00:00+00:00",
    }


def fake_artifacts(*, limit: int = 240) -> list[dict]:
    return [item for item in (fake_artifact(ASSET_A), fake_artifact(ASSET_B), fake_artifact(ASSET_C)) if item][:limit]


def bounded_config(*, sam2: bool = False) -> dict:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["sam2_assist"] = {"configured": sam2}
    config["limits"].update({"max_undo_entries": 4, "max_snapshots": 6, "max_layers_per_session": 12, "max_mask_operations": 8, "max_points_per_stroke": 12})
    return config


class ImageMaskStudioManagerTests(unittest.TestCase):
    def make_studio(self, temporary: str, *, sam2: bool = False) -> ImageMaskStudioManager:
        return ImageMaskStudioManager(
            Path(temporary) / "image_mask_studio.json",
            artifact_describer=fake_artifact,
            config_provider=lambda: bounded_config(sam2=sam2),
        )

    def test_non_destructive_layers_mask_history_snapshots_and_preset(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_studio(temporary)
            session = manager.create_session({"title": "Campaign edit", "source_artifact_id": ASSET_A})["session"]
            self.assertEqual(session["source_artifact_id"], ASSET_A)
            self.assertFalse(session["dirty"])

            mask = manager.add_layer(session["id"], {"kind": "mask", "name": "Subject", "artifact_id": ASSET_B, "base_revision": session["revision"]})
            session = mask["session"]
            operation = manager.apply_mask_operation(session["id"], mask["layer"]["id"], {
                "base_revision": session["revision"],
                "operation": "brush",
                "mode": "add",
                "size": 0.08,
                "points": [{"x": 0.1, "y": 0.2}, {"x": 0.4, "y": 0.5}],
            })
            session = operation["session"]
            self.assertTrue(session["dirty"])
            self.assertEqual(operation["operation"]["mode"], "add")
            self.assertEqual(session["layers"][-1]["operation_count"], 1)

            saved = manager.save(session["id"], {"base_revision": session["revision"]})["session"]
            self.assertFalse(saved["dirty"])
            before_undo_revision = saved["revision"]
            undone = manager.undo(saved["id"], {"base_revision": before_undo_revision})["session"]
            self.assertTrue(undone["history"]["can_redo"])
            redone = manager.redo(undone["id"], {"base_revision": undone["revision"]})["session"]
            self.assertEqual(redone["layers"][-1]["operation_count"], 1)

            preset = manager.capture_preset(redone["id"], {"title": "Subject mask"})["preset"]
            second = manager.create_session({"title": "Second", "source_artifact_id": ASSET_A})["session"]
            applied = manager.apply_preset(second["id"], preset["id"], {"base_revision": second["revision"]})["session"]
            self.assertGreater(applied["layer_count"], 1)
            self.assertEqual(applied["source_artifact_id"], ASSET_A)

            comparison = manager.compare(redone["id"])["compare"]
            self.assertIn("layers", comparison["differences"])
            serialized = json.dumps({"session": redone, "compare": comparison}, ensure_ascii=False)
            self.assertNotIn("C:\\", serialized)
            self.assertNotIn("data:image", serialized)

    def test_mask_import_export_and_geometry_reject_unsafe_values(self) -> None:
        with TemporaryDirectory() as temporary:
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            layer = manager.add_layer(session["id"], {"kind": "mask", "artifact_id": ASSET_B, "base_revision": session["revision"]})
            session = layer["session"]
            manager.apply_mask_operation(session["id"], layer["layer"]["id"], {
                "base_revision": session["revision"], "operation": "invert",
            })
            exported = manager.export_mask(session["id"], layer["layer"]["id"])["mask"]
            second = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            imported = manager.import_mask(second["id"], {"mask": exported, "base_revision": second["revision"]})
            self.assertEqual(imported["layer"]["kind"], "mask")
            with self.assertRaisesRegex(ValueError, "opaque"):
                manager.create_session({"source_artifact_id": r"C:\\private\\source.png"})
            with self.assertRaisesRegex(ValueError, "khoảng"):
                manager.apply_mask_operation(imported["session"]["id"], imported["layer"]["id"], {
                    "base_revision": imported["session"]["revision"],
                    "operation": "brush",
                    "points": [{"x": 2, "y": 0.2}],
                })
            with self.assertRaisesRegex(ValueError, "allowlist"):
                manager.apply_mask_operation(imported["session"]["id"], imported["layer"]["id"], {
                    "base_revision": imported["session"]["revision"], "operation": "shell", "points": [],
                })

    def test_stale_revision_recovery_and_bounded_history(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio.json"
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            for number in range(7):
                session = manager.update_session(session["id"], {"title": f"Draft {number}", "base_revision": session["revision"]})["session"]
            self.assertLessEqual(session["history"]["undo_count"], 4)
            with self.assertRaises(StudioConflictError):
                manager.update_session(session["id"], {"title": "stale", "base_revision": 1})
            reloaded = self.make_studio(temporary).get_session(session["id"])
            self.assertEqual(reloaded["session"]["title"], "Draft 6")

            path.write_text("{broken", encoding="utf-8")
            recovery = self.make_studio(temporary).overview()["recovery"]
            self.assertEqual(recovery["status"], "recovery_required")
            with self.assertRaisesRegex(ValueError, "không tự ghi đè"):
                self.make_studio(temporary).create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_preflight_is_capability_specific_and_never_promotes_inpaint(self) -> None:
        with TemporaryDirectory() as temporary:
            unavailable = {item["id"]: item for item in self.make_studio(temporary).preflight()["capabilities"]}
            partial = {item["id"]: item for item in self.make_studio(temporary, sam2=True).preflight()["capabilities"]}
            self.assertEqual(unavailable["sam2_assisted_mask"]["status"], "unavailable")
            self.assertEqual(partial["sam2_assisted_mask"]["status"], "partial")
            self.assertEqual(partial["image_inpaint"]["status"], "unavailable")
            self.assertEqual(partial["image_outpaint"]["status"], "unavailable")
            self.assertTrue(partial["image_inpaint"]["reason"])
            self.assertTrue(partial["image_outpaint"]["action"])
            with self.assertRaises(ValueError):
                normalize_config({"contract_version": CONFIG_CONTRACT, "schema_version": CONFIG_SCHEMA_VERSION, "limits": "not an object"})

    def test_project_attachment_is_idempotent_and_keeps_multi_input_provenance(self) -> None:
        with TemporaryDirectory() as temporary:
            projects = CreativeProjectManager(Path(temporary) / "creative_workspace.json", artifact_describer=fake_artifact, artifact_lister=fake_artifacts)
            project = projects.create_project({"title": "Mask campaign"})["project"]
            linked = projects.attach_image_mask_studio_revision(project["id"], {
                "studio_id": "studio_" + "d" * 32,
                "revision": 3,
                "source_artifact_id": ASSET_A,
                "artifact_ids": [ASSET_A, ASSET_B, ASSET_C],
                "mask_artifact_ids": [ASSET_B],
            })
            retried = projects.attach_image_mask_studio_revision(project["id"], {
                "studio_id": "studio_" + "d" * 32,
                "revision": 3,
                "source_artifact_id": ASSET_A,
                "artifact_ids": [ASSET_A, ASSET_B, ASSET_C],
                "mask_artifact_ids": [ASSET_B],
            })
            self.assertEqual(linked["project"]["asset_count"], 3)
            self.assertEqual(retried["project"]["asset_count"], 3)
            assets = {item["id"]: item for item in projects.list_assets(project_id=project["id"])["assets"]}
            self.assertEqual(assets[ASSET_C]["lineage"]["parent_artifact_id"], ASSET_A)
            self.assertEqual(assets[ASSET_C]["provenance"]["mask_artifact_ids"], [ASSET_B])

    def test_canvas_module_and_rendered_studio_are_keyboard_ready(self) -> None:
        script = """
import { compactMaskStroke, normalizedMaskPoint } from './src/ui/image_mask_studio.js';
import { renderPage } from './src/ui/pages.js';
const point = normalizedMaskPoint(50, 25, {left: 0, top: 0, width: 100, height: 50});
if (point.x !== .5 || point.y !== .5) throw new Error('normalization failed');
const stroke = compactMaskStroke([{x: .1, y: .2}, {x: .1001, y: .2001}, {x: 2, y: -1}], 2);
if (stroke.length !== 2 || stroke[1].x !== 1 || stroke[1].y !== 0) throw new Error('stroke bounds failed');
const asset = {id:'artifact_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', name:'source.png', media_type:'image/png', url:'/api/artifacts/artifact_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', available:true};
const session = {id:'studio_dddddddddddddddddddddddddddddddd', title:'Mask', source_artifact_id:asset.id, source_artifact:asset, revision:3, dirty:true, autosaved_at:'now', explicit_saved_at:'now', last_action:'brush', layer_count:2, mask_count:1, active_layer_id:'layer_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', history:{can_undo:true,can_redo:false}, snapshots:[{id:'snapshot_cccccccccccccccccccccccccccccccc', label:'Tạo', revision:1}], layers:[{id:'layer_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',kind:'source',name:'Ảnh nguồn',visible:true,opacity:1,artifact_id:asset.id,artifact:asset},{id:'layer_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',kind:'mask',name:'Subject',visible:true,opacity:1,operations:[],operation_count:0}], provenance:{}};
const state = {workspaceTabs:{image:'studio'}, imageMaskStudio:{sessions:[session], presets:[], preflight:{capabilities:[{id:'sam2_assisted_mask', title:'SAM2-assisted mask', status:'unavailable', reason:'No smoke', action:'Configure'}]}}, imageMaskSession:{session}, imageMaskCompare:null, selectedImageMaskLayerId:session.active_layer_id, creative:{assets:[asset], projects:[]}, pendingImageMaskSourceId:'', lifecycle:{}, tools:[], components:[]};
const html = renderPage('image', state);
for (const needle of ['Chỉnh sửa ảnh &amp; Mask Studio', 'data-mask-canvas', 'Hoàn tác', 'Escape hủy nét', 'SAM2-assisted mask']) if (!html.includes(needle)) throw new Error(`missing ${needle}`);
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


class ImageMaskStudioApiTests(unittest.TestCase):
    def test_loopback_routes_keep_contract_opaque_and_return_conflict(self) -> None:
        with TemporaryDirectory() as temporary:
            studio = ImageMaskStudioManager(Path(temporary) / "image_mask_studio.json", artifact_describer=fake_artifact, config_provider=bounded_config)
            projects = CreativeProjectManager(Path(temporary) / "creative_workspace.json", artifact_describer=fake_artifact, artifact_lister=fake_artifacts)
            original_studio = api_server.image_mask_studio
            original_projects = api_server.project_manager
            server = api_server.HubHTTPServer(("127.0.0.1", 0), api_server.HubHandler)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            api_server.image_mask_studio = studio
            api_server.project_manager = projects
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"

            def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
                data = json.dumps(payload).encode("utf-8") if payload is not None else None
                headers = {"Content-Type": "application/json"} if data else {}
                try:
                    with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=3) as response:
                        return response.status, json.loads(response.read().decode("utf-8"))
                except HTTPError as error:
                    return error.code, json.loads(error.read().decode("utf-8"))

            try:
                status, created = request("/api/image-mask-studio/sessions", "POST", {"title": "Loopback", "source_artifact_id": ASSET_A})
                self.assertEqual(status, 200)
                session = created["session"]
                status, layer_payload = request(f"/api/image-mask-studio/sessions/{session['id']}/layers", "POST", {"kind": "mask", "artifact_id": ASSET_B, "base_revision": session["revision"]})
                self.assertEqual(status, 200)
                session = layer_payload["session"]
                layer = layer_payload["layer"]
                status, operation = request(f"/api/image-mask-studio/sessions/{session['id']}/layers/{layer['id']}/operations", "POST", {"base_revision": session["revision"], "operation": "brush", "mode": "subtract", "points": [{"x": 0.2, "y": 0.3}]})
                self.assertEqual(status, 200)
                current = operation["session"]
                status, stale = request(f"/api/image-mask-studio/sessions/{session['id']}", "PUT", {"title": "stale", "base_revision": 1})
                self.assertEqual(status, 409)
                self.assertEqual(stale["status"], "conflict")
                self.assertEqual(stale["current_revision"], current["revision"])
                status, exported = request(f"/api/image-mask-studio/sessions/{session['id']}/layers/{layer['id']}/export")
                self.assertEqual(status, 200)
                self.assertEqual(exported["mask"]["contract_version"], "image-mask-export.v1")
                status, overview = request("/api/image-mask-studio/overview")
                self.assertEqual(status, 200)
                status, preflight = request("/api/image-mask-studio/preflight")
                self.assertEqual(status, 200)
                self.assertTrue(preflight["capabilities"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                api_server.image_mask_studio = original_studio
                api_server.project_manager = original_projects

            self.assertFalse(thread.is_alive())
            serialized = json.dumps({"overview": overview, "exported": exported, "operation": operation}, ensure_ascii=False)
            self.assertIn(ASSET_A, serialized)
            self.assertNotIn("C:\\", serialized)
            self.assertNotIn("input_path", serialized)
            self.assertNotIn("resume_data", serialized)


if __name__ == "__main__":
    unittest.main()
