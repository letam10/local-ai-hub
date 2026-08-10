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
from src.services.image_mask_studio.manager import STATE_PATH, ImageMaskStudioManager, StudioConflictError
from src.services.project_manager.manager import CreativeProjectManager


ASSET_A = "artifact_" + "a" * 32
ASSET_B = "artifact_" + "b" * 32
ASSET_C = "artifact_" + "c" * 32
PROJECT_A = "project_" + "d" * 32


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
            Path(temporary) / "image_mask_studio_state.json",
            artifact_describer=fake_artifact,
            config_provider=lambda: bounded_config(sam2=sam2),
        )

    def test_policy_config_and_autosave_state_use_distinct_local_files(self) -> None:
        self.assertEqual(STATE_PATH.name, "image_mask_studio_state.json")
        self.assertNotEqual(STATE_PATH.name, "image_mask_studio.json")

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

            adjustment = manager.add_layer(session["id"], {
                "kind": "adjustment",
                "name": "Exposure",
                "adjustment": {"kind": "exposure", "settings": {"amount": 0.2}},
                "base_revision": session["revision"],
            })
            session = adjustment["session"]
            moved = manager.move_layer(session["id"], adjustment["layer"]["id"], {"direction": "down", "base_revision": session["revision"]})["session"]
            self.assertEqual(moved["layers"][0]["kind"], "source")
            self.assertEqual(moved["layers"][1]["kind"], "adjustment")
            with self.assertRaisesRegex(ValueError, "nguồn"):
                manager.remove_layer(moved["id"], moved["layers"][0]["id"], {"base_revision": moved["revision"]})

            saved = manager.save(moved["id"], {"base_revision": moved["revision"]})["session"]
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
            overlong = json.loads(json.dumps(exported))
            overlong["mask"]["operations"] = [json.loads(json.dumps(exported["mask"]["operations"][0])) for _ in range(9)]
            third = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            with self.assertRaisesRegex(ValueError, "giới hạn"):
                manager.import_mask(third["id"], {"mask": overlong, "base_revision": third["revision"]})

    def test_stale_revision_recovery_and_bounded_history(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
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

    def test_persisted_timestamps_never_project_paths_and_bad_unicode_is_read_only(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            mask = manager.add_layer(session["id"], {"kind": "mask", "artifact_id": ASSET_B, "base_revision": session["revision"]})
            session = manager.apply_mask_operation(mask["session"]["id"], mask["layer"]["id"], {
                "base_revision": mask["session"]["revision"], "operation": "brush", "points": [{"x": 0.2, "y": 0.2}],
            })["session"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw["sessions"][session["id"]]
            stored["created_at"] = r"C:\private\created"
            stored["updated_at"] = r"\\server\private\updated"
            stored["layers"][1]["operations"][0]["created_at"] = r"C:\private\stroke"
            stored["snapshots"][0]["created_at"] = r"C:\private\snapshot"
            path.write_text(json.dumps(raw), encoding="utf-8")

            public = self.make_studio(temporary).get_session(session["id"])["session"]
            serialized = json.dumps(public, ensure_ascii=False)
            self.assertNotIn(r"C:\private", serialized)
            self.assertNotIn(r"\\server", serialized)
            self.assertIn("T", public["created_at"])
            self.assertIn("T", public["layers"][1]["operations"][0]["created_at"])

            path.write_bytes(b"\xff\xfe\x00")
            recovery = self.make_studio(temporary).overview()["recovery"]
            self.assertEqual(recovery["status"], "recovery_required")
            with self.assertRaises(ValueError):
                self.make_studio(temporary).create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_bytes(), b"\xff\xfe\x00")

    def test_poisoned_history_cannot_swap_source_or_be_overwritten(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw["sessions"][session["id"]]
            poisoned = json.loads(json.dumps(stored["snapshots"][0]["document"]))
            poisoned["source_artifact_id"] = ASSET_C
            for layer in poisoned["layers"]:
                if layer["kind"] == "source":
                    layer["artifact_id"] = ASSET_C
            stored["undo"] = [poisoned]
            path.write_text(json.dumps(raw), encoding="utf-8")

            reloaded = self.make_studio(temporary)
            overview = reloaded.overview()
            self.assertEqual(overview["recovery"]["status"], "recovered_partial")
            self.assertEqual(overview["sessions"], [])
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(ValueError):
                reloaded.create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_persisted_documents_keep_bounds_and_server_owned_lineage(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            manager = self.make_studio(temporary)
            source = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            mask = manager.add_layer(source["id"], {
                "kind": "mask", "artifact_id": ASSET_B, "base_revision": source["revision"],
            })
            painted = manager.apply_mask_operation(mask["session"]["id"], mask["layer"]["id"], {
                "base_revision": mask["session"]["revision"], "operation": "brush",
                "points": [{"x": 0.2, "y": 0.2}],
            })
            generated = manager.add_layer(painted["session"]["id"], {
                "kind": "generated", "artifact_id": ASSET_C,
                "parent_layer_id": mask["layer"]["id"], "base_revision": painted["session"]["revision"],
                "provenance": {"note": "safe"},
            })["session"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw["sessions"][generated["id"]]
            source_layer = next(item for item in stored["layers"] if item["kind"] == "source")
            mask_layer = next(item for item in stored["layers"] if item["kind"] == "mask")
            generated_layer = next(item for item in stored["layers"] if item["kind"] == "generated")
            poisoned_document = {
                "title": stored["title"], "project_id": stored["project_id"],
                "source_artifact_id": ASSET_A,
                "layers": [
                    json.loads(json.dumps(source_layer)), json.loads(json.dumps(mask_layer)),
                    json.loads(json.dumps(generated_layer)),
                ],
                "active_layer_id": generated_layer["id"],
            }
            poisoned_generated = next(item for item in poisoned_document["layers"] if item["kind"] == "generated")
            poisoned_generated["provenance"] = {
                "studio_id": "studio_" + "f" * 32,
                "source_artifact_id": ASSET_C,
                "metadata": {"claimed": "wrong"},
            }
            stored["undo"] = [poisoned_document]
            path.write_text(json.dumps(raw), encoding="utf-8")

            reloaded = self.make_studio(temporary)
            current = reloaded.get_session(generated["id"])["session"]
            undone = reloaded.undo(generated["id"], {"base_revision": current["revision"]})["session"]
            restored_generated = next(item for item in undone["layers"] if item["kind"] == "generated")
            self.assertEqual(restored_generated["provenance"]["studio_id"], generated["id"])
            self.assertEqual(restored_generated["provenance"]["source_artifact_id"], ASSET_A)
            self.assertEqual(restored_generated["provenance"]["metadata"], {"claimed": "wrong"})

            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw["sessions"][generated["id"]]
            stored_mask = next(item for item in stored["layers"] if item["kind"] == "mask")
            stored_mask["operations"] = [json.loads(json.dumps(stored_mask["operations"][0])) for _ in range(9)]
            before = json.dumps(raw)
            path.write_text(before, encoding="utf-8")
            bounded = self.make_studio(temporary)
            self.assertEqual(bounded.overview()["recovery"]["status"], "recovered_partial")
            with self.assertRaises(ValueError):
                bounded.create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_limits_and_revisioned_project_intent_are_bounded_and_linearizable(self) -> None:
        with TemporaryDirectory() as temporary:
            config = bounded_config()
            config["limits"].update({"max_sessions": 2, "max_presets": 2})
            manager = ImageMaskStudioManager(
                Path(temporary) / "image_mask_studio_state.json",
                artifact_describer=fake_artifact,
                config_provider=lambda: config,
            )
            first = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            manager.create_session({"source_artifact_id": ASSET_A})
            with self.assertRaisesRegex(ValueError, "giới hạn phiên"):
                manager.create_session({"source_artifact_id": ASSET_A})
            manager.capture_preset(first["id"], {"title": "One", "base_revision": first["revision"]})
            manager.capture_preset(first["id"], {"title": "Two", "base_revision": first["revision"]})
            with self.assertRaisesRegex(ValueError, "giới hạn preset"):
                manager.capture_preset(first["id"], {"title": "Three", "base_revision": first["revision"]})
            limits = manager.overview()["limits"]
            self.assertEqual((limits["session_count"], limits["preset_count"]), (2, 2))

            intent = manager.prepare_project_attachment(first["id"], {"project_id": PROJECT_A, "base_revision": first["revision"]})
            revision = intent["session"]["revision"]
            self.assertEqual(revision, first["revision"] + 1)
            self.assertNotIn("intent_id", intent["session"]["pending_project_attach"])
            with self.assertRaisesRegex(ValueError, "đang chờ"):
                manager.add_layer(first["id"], {"kind": "mask", "artifact_id": ASSET_B, "base_revision": revision})
            retry = manager.prepare_project_attachment(first["id"], {"project_id": PROJECT_A, "base_revision": revision})
            self.assertEqual(retry["attachment"]["intent_id"], intent["attachment"]["intent_id"])
            with self.assertRaises(StudioConflictError):
                manager.complete_project_attachment(
                    first["id"], project_id=PROJECT_A, artifact_ids=intent["attachment"]["artifacts"],
                    intent_id=intent["attachment"]["intent_id"], expected_revision=first["revision"],
                )
            completed = manager.complete_project_attachment(
                first["id"], project_id=PROJECT_A, artifact_ids=intent["attachment"]["artifacts"],
                intent_id=intent["attachment"]["intent_id"], expected_revision=revision,
            )["session"]
            self.assertIsNone(completed["pending_project_attach"])
            self.assertEqual(completed["revision"], revision)
            duplicate = manager.prepare_project_attachment(first["id"], {
                "project_id": PROJECT_A,
                "base_revision": completed["revision"],
            })
            self.assertEqual(duplicate["status"], "completed")
            self.assertEqual(duplicate["session"]["revision"], completed["revision"])
            self.assertNotIn("intent_id", duplicate["attachment"])
            reloaded = ImageMaskStudioManager(
                Path(temporary) / "image_mask_studio_state.json",
                artifact_describer=fake_artifact,
                config_provider=lambda: config,
            )
            after_restart = reloaded.prepare_project_attachment(first["id"], {
                "project_id": PROJECT_A,
                "base_revision": completed["revision"],
            })
            self.assertEqual(after_restart["status"], "completed")

    def test_loaded_state_over_limits_stays_read_only_without_pruning_user_records(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            relaxed = bounded_config()
            relaxed["limits"].update({"max_sessions": 4, "max_presets": 4, "max_layers_per_session": 8})
            writer = ImageMaskStudioManager(path, artifact_describer=fake_artifact, config_provider=lambda: relaxed)
            for _ in range(3):
                writer.create_session({"source_artifact_id": ASSET_A})
            before = path.read_text(encoding="utf-8")
            strict = bounded_config()
            strict["limits"].update({"max_sessions": 2, "max_presets": 2, "max_layers_per_session": 2})
            reader = ImageMaskStudioManager(path, artifact_describer=fake_artifact, config_provider=lambda: strict)
            overview = reader.overview()
            self.assertEqual(overview["recovery"]["status"], "recovered_partial")
            with self.assertRaises(ValueError):
                reader.create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_pending_project_intent_is_durable_and_uses_the_configured_layer_bound(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            manager.prepare_project_attachment(session["id"], {"project_id": PROJECT_A, "base_revision": session["revision"]})
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["sessions"][session["id"]]["pending_project_attach"]["intent_id"] = "not-an-opaque-intent"
            before = json.dumps(raw)
            path.write_text(before, encoding="utf-8")
            reloaded = self.make_studio(temporary)
            self.assertEqual(reloaded.overview()["recovery"]["status"], "recovered_partial")
            with self.assertRaises(ValueError):
                reloaded.create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            config = bounded_config()
            config["limits"]["max_layers_per_session"] = 64
            manager = ImageMaskStudioManager(
                path,
                artifact_describer=fake_artifact,
                config_provider=lambda: config,
            )
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored = raw["sessions"][session["id"]]
            extra_artifact_ids = [f"artifact_{number:032x}" for number in range(1, 49)]
            stored["layers"] = [stored["layers"][0], *[
                {
                    "id": f"layer_{number:032x}", "kind": "mask", "name": f"Mask {number}",
                    "artifact_id": artifact_id, "visible": True, "opacity": 1.0, "operations": [],
                }
                for number, artifact_id in enumerate(extra_artifact_ids, start=1)
            ]]
            stored["project_id"] = PROJECT_A
            stored["revision"] = 2
            stored["pending_project_attach"] = {
                "intent_id": "attach_" + "f" * 32,
                "project_id": PROJECT_A,
                "artifacts": [ASSET_A, *extra_artifact_ids],
                "revision": 2,
                "created_at": "2026-08-10T00:00:00+00:00",
            }
            path.write_text(json.dumps(raw), encoding="utf-8")
            reloaded = ImageMaskStudioManager(path, artifact_describer=fake_artifact, config_provider=lambda: config)
            self.assertEqual(reloaded.overview()["recovery"]["status"], "clean")
            pending = reloaded.get_session(session["id"])["session"]["pending_project_attach"]
            self.assertEqual(pending["project_id"], PROJECT_A)
            self.assertNotIn("intent_id", pending)
            with self.assertRaisesRegex(ValueError, "đang chờ"):
                reloaded.update_session(session["id"], {"title": "must remain locked", "base_revision": 2})

    def test_cyclic_preset_state_becomes_read_only_recovery(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "image_mask_studio_state.json"
            manager = self.make_studio(temporary)
            session = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            first_id = "layer_" + "b" * 32
            second_id = "layer_" + "c" * 32
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["presets"]["maskpreset_" + "d" * 32] = {
                "contract_version": "image-mask-preset.v1",
                "title": "Cyclic preset",
                "layers": [
                    {"id": first_id, "kind": "generated", "name": "First", "artifact_id": ASSET_C, "parent_layer_id": second_id, "visible": True, "opacity": 1.0, "provenance": {}},
                    {"id": second_id, "kind": "generated", "name": "Second", "artifact_id": ASSET_C, "parent_layer_id": first_id, "visible": True, "opacity": 1.0, "provenance": {}},
                ],
                "created_at": "2026-08-10T00:00:00+00:00",
                "updated_at": "2026-08-10T00:00:00+00:00",
                "source_studio_id": session["id"],
            }
            before = json.dumps(raw)
            path.write_text(before, encoding="utf-8")
            reloaded = self.make_studio(temporary)
            self.assertEqual(reloaded.overview()["recovery"]["status"], "recovered_partial")
            with self.assertRaises(ValueError):
                reloaded.create_session({"source_artifact_id": ASSET_A})
            self.assertEqual(path.read_text(encoding="utf-8"), before)

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
            string_false = normalize_config({"sam2_assist": {"configured": "false"}})
            self.assertFalse(string_false["sam2_assist"]["configured"])

    def test_generated_provenance_and_preset_reuse_keep_server_owned_lineage(self) -> None:
        with TemporaryDirectory() as temporary:
            availability = {ASSET_C: True}

            def describe(artifact_id: str) -> dict | None:
                if artifact_id == ASSET_C and not availability[ASSET_C]:
                    return None
                return fake_artifact(artifact_id)

            manager = ImageMaskStudioManager(
                Path(temporary) / "image_mask_studio_state.json",
                artifact_describer=describe,
                config_provider=bounded_config,
            )
            source = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            with self.assertRaisesRegex(ValueError, "không được ghi đè"):
                manager.add_layer(source["id"], {
                    "kind": "generated", "artifact_id": ASSET_C, "base_revision": source["revision"],
                    "provenance": {"studio_id": "studio_" + "e" * 32},
                })
            mask = manager.add_layer(source["id"], {"kind": "mask", "artifact_id": ASSET_B, "name": "Subject", "base_revision": source["revision"]})
            generated = manager.add_layer(mask["session"]["id"], {
                "kind": "generated", "artifact_id": ASSET_C, "name": "Variant", "parent_layer_id": mask["layer"]["id"],
                "base_revision": mask["session"]["revision"], "provenance": {"note": "safe metadata"},
            })
            generated_layer = generated["layer"]
            self.assertEqual(generated_layer["provenance"]["studio_id"], source["id"])
            self.assertEqual(generated_layer["provenance"]["source_artifact_id"], ASSET_A)
            self.assertEqual(generated_layer["provenance"]["metadata"]["note"], "safe metadata")
            with self.assertRaisesRegex(ValueError, "parent"):
                manager.remove_layer(generated["session"]["id"], mask["layer"]["id"], {
                    "base_revision": generated["session"]["revision"],
                })
            with self.assertRaises(StudioConflictError):
                manager.capture_preset(source["id"], {"title": "stale", "base_revision": source["revision"]})
            current = generated["session"]
            preset = manager.capture_preset(current["id"], {"title": "Keep hierarchy", "base_revision": current["revision"]})["preset"]
            target = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            applied = manager.apply_preset(target["id"], preset["id"], {"base_revision": target["revision"]})["session"]
            copied_mask = next(layer for layer in applied["layers"] if layer["kind"] == "mask")
            copied_generated = next(layer for layer in applied["layers"] if layer["kind"] == "generated")
            self.assertEqual(copied_generated["parent_layer_id"], copied_mask["id"])
            self.assertEqual(copied_generated["provenance"]["studio_id"], target["id"])
            self.assertEqual(copied_generated["provenance"]["source_artifact_id"], ASSET_A)
            availability[ASSET_C] = False
            unavailable_target = manager.create_session({"source_artifact_id": ASSET_A})["session"]
            with self.assertRaisesRegex(ValueError, "Artifact của preset"):
                manager.apply_preset(unavailable_target["id"], preset["id"], {"base_revision": unavailable_target["revision"]})

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
            self.assertEqual(linked["project"]["image_mask_studio_link_count"], 1)
            project_detail = projects.get_project(project["id"])
            self.assertIsNotNone(project_detail)
            self.assertEqual(len(project_detail["image_mask_studio_links"]), 1)
            assets = {item["id"]: item for item in projects.list_assets(project_id=project["id"])["assets"]}
            self.assertEqual(assets[ASSET_C]["lineage"]["parent_artifact_id"], ASSET_A)
            self.assertEqual(assets[ASSET_C]["provenance"]["image_mask_studio_links"], [{
                "image_mask_studio_id": "studio_" + "d" * 32,
                "image_mask_revision": 3,
                "source_artifact_id": ASSET_A,
                "mask_artifact_ids": [ASSET_B],
            }])
            other_project = projects.create_project({"title": "Mask campaign reuse"})["project"]
            projects.attach_image_mask_studio_revision(other_project["id"], {
                "studio_id": "studio_" + "e" * 32,
                "revision": 4,
                "source_artifact_id": ASSET_A,
                "artifact_ids": [ASSET_A, ASSET_C],
                "mask_artifact_ids": [],
            })
            shared = {item["id"]: item for item in projects.list_assets(project_id=other_project["id"])["assets"]}[ASSET_C]
            self.assertEqual(shared["lineage"]["parent_artifact_id"], ASSET_A)
            self.assertEqual(len(shared["provenance"]["image_mask_studio_links"]), 2)
            self.assertEqual(projects.get_project(other_project["id"])["project"]["image_mask_studio_link_count"], 1)
            source_only = projects.create_project({"title": "Source-only studio link"})["project"]
            projects.attach_image_mask_studio_revision(source_only["id"], {
                "studio_id": "studio_" + "1" * 32,
                "revision": 1,
                "source_artifact_id": ASSET_A,
                "artifact_ids": [ASSET_A],
                "mask_artifact_ids": [],
            })
            source_only_detail = projects.get_project(source_only["id"])
            self.assertEqual(source_only_detail["image_mask_studio_links"][0]["artifact_ids"], [ASSET_A])
            source_only_manifest = projects.export_project(source_only["id"])["manifest"]
            imported_source_only = projects.import_project({"manifest": source_only_manifest, "conflict": "copy"})["project"]
            imported_detail = projects.get_project(imported_source_only["id"])
            self.assertEqual(imported_detail["image_mask_studio_links"][0]["source_artifact_id"], ASSET_A)
            workspace_path = Path(temporary) / "creative_workspace.json"
            workspace = json.loads(workspace_path.read_text(encoding="utf-8"))
            workspace["projects"][source_only["id"]]["image_mask_studio_links"][0]["created_at"] = r"C:\private\project-link"
            workspace_path.write_text(json.dumps(workspace), encoding="utf-8")
            reloaded_projects = CreativeProjectManager(workspace_path, artifact_describer=fake_artifact, artifact_lister=fake_artifacts)
            self.assertNotIn(r"C:\private", json.dumps(reloaded_projects.get_project(source_only["id"]), ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, "nguồn khác"):
                projects.attach_image_mask_studio_revision(other_project["id"], {
                    "studio_id": "studio_" + "f" * 32,
                    "revision": 5,
                    "source_artifact_id": ASSET_B,
                    "artifact_ids": [ASSET_B, ASSET_C],
                    "mask_artifact_ids": [],
                })
            with self.assertRaisesRegex(ValueError, "phải thuộc đúng tập"):
                projects.attach_image_mask_studio_revision(project["id"], {
                    "studio_id": "studio_" + "d" * 32,
                    "revision": 4,
                    "source_artifact_id": ASSET_A,
                    "artifact_ids": [ASSET_A],
                    "mask_artifact_ids": [ASSET_B],
                })

    def test_canvas_module_and_rendered_studio_are_keyboard_ready(self) -> None:
        script = """
 import { HubApiError } from './src/ui/api.js';
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
 const recoveryHtml = renderPage('image', {...state, imageMaskStudio:{...state.imageMaskStudio, recovery:{status:'recovery_required', reason:'Poisoned local state', action:'Recover safely'}}});
 for (const needle of ['chế độ chỉ đọc', 'Poisoned local state', 'Recover safely']) if (!recoveryHtml.includes(needle)) throw new Error(`missing recovery ${needle}`);
 const pending = new HubApiError('pending', {status:'pending_project_attach'}, 409);
 if (pending.payload.status !== 'pending_project_attach' || pending.status !== 409) throw new Error('API error payload failed');
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
            studio = ImageMaskStudioManager(Path(temporary) / "image_mask_studio_state.json", artifact_describer=fake_artifact, config_provider=bounded_config)
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
                status, overridden = request(f"/api/image-mask-studio/sessions/{session['id']}/layers", "POST", {
                    "kind": "generated", "artifact_id": ASSET_C, "base_revision": current["revision"],
                    "provenance": {"studio_id": "studio_" + "e" * 32},
                })
                self.assertEqual(status, 400)
                self.assertIn("không được ghi đè", overridden["error"])
                status, project = request("/api/projects", "POST", {"title": "Loopback project"})
                self.assertEqual(status, 200)
                status, linked = request(f"/api/image-mask-studio/sessions/{session['id']}/link-project", "POST", {
                    "project_id": project["project"]["id"], "artifact_ids": [ASSET_A], "base_revision": current["revision"],
                })
                self.assertEqual(status, 200)
                self.assertEqual(linked["status"], "completed")
                self.assertEqual(linked["project"]["asset_count"], 1)
                self.assertNotIn("intent_id", json.dumps(linked))
                self.assertEqual([asset["id"] for asset in projects.list_assets(project_id=project["project"]["id"])["assets"]], [ASSET_A])
                linked_session = linked["session"]
                status, duplicate_link = request(f"/api/image-mask-studio/sessions/{session['id']}/link-project", "POST", {
                    "project_id": project["project"]["id"], "artifact_ids": [ASSET_A], "base_revision": linked_session["revision"],
                })
                self.assertEqual(status, 200)
                self.assertTrue(duplicate_link["idempotent"])
                self.assertIsNone(duplicate_link["project"])
                self.assertEqual(duplicate_link["session"]["revision"], linked_session["revision"])
                pending_session = request("/api/image-mask-studio/sessions", "POST", {"title": "Pending", "source_artifact_id": ASSET_A})[1]["session"]
                original_attach = projects.attach_image_mask_studio_revision
                try:
                    def reject_attach(*_args: object, **_kwargs: object) -> dict:
                        raise ValueError("Project target unavailable")

                    projects.attach_image_mask_studio_revision = reject_attach  # type: ignore[method-assign]
                    status, pending = request(f"/api/image-mask-studio/sessions/{pending_session['id']}/link-project", "POST", {
                        "project_id": project["project"]["id"], "base_revision": pending_session["revision"],
                    })
                finally:
                    projects.attach_image_mask_studio_revision = original_attach  # type: ignore[method-assign]
                self.assertEqual(status, 409)
                self.assertEqual(pending["status"], "pending_project_attach")
                self.assertNotIn("intent_id", json.dumps(pending))
                self.assertIsNotNone(studio.get_session(pending_session["id"])["session"]["pending_project_attach"])
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
