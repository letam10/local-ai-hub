from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import src.services.backup_manager as bm
from src.services.backup_manager import BACKUP_SCHEMA_VERSION, BackupManager


class V7BackupRestoreStorageSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = root / "Config"
        self.config.mkdir(parents=True)
        self.backups = self.config / "backups"
        self.backups.mkdir()
        self.settings = self.config / "settings.json"
        self.workspace = self.config / "creative_workspace.json"
        self.settings.write_text('{"schema_version":2,"settings_revision":1}', encoding="utf-8")
        self.workspace.write_text('{"schema_version":1,"projects":[]}', encoding="utf-8")
        self.root_patch = patch.object(bm, "CONFIG_ROOT", self.config)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        with bm._PLANS_LOCK:
            bm._RESTORE_PLANS.clear()
        self.addCleanup(self.temp.cleanup)

    def manager(self) -> BackupManager:
        return BackupManager(backup_dir=self.backups)

    @staticmethod
    def _manifest(files: dict[str, bytes], **extra: object) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": BACKUP_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "hub_backup_version": "1.0",
            "included_data_classes": ["settings", "creative_workspace", "workflow_library", "node_studio_drafts"],
            "files": {
                name: {"sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content)}
                for name, content in files.items()
            },
        }
        result.update(extra)
        return result

    def _archive(
        self,
        name: str,
        files: dict[str, bytes],
        *,
        manifest: object | None = None,
        raw_manifest: bytes | None = None,
        duplicate_manifest: bool = False,
    ) -> tuple[Path, str]:
        path = self.backups / name
        value = self._manifest(files) if manifest is None else manifest
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for member, content in files.items():
                archive.writestr(member, content)
            payload = raw_manifest if raw_manifest is not None else json.dumps(value, separators=(",", ":")).encode("utf-8")
            archive.writestr("manifest.json", payload)
            if duplicate_manifest:
                archive.writestr("manifest.json", payload)
        return path, "backup_" + path.stem

    def test_valid_lifecycle_keeps_private_state_out_of_public_plan(self) -> None:
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        inspected = manager.inspect_backup(created["backup_id"])
        self.assertTrue(inspected["valid"], inspected)
        self.settings.unlink()
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        encoded = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(str(self.config), encoded)
        self.assertNotIn("_entries", plan)
        self.assertNotIn("backup_path", plan)
        applied = manager.apply_restore(plan["plan_id"], confirmed=True)
        self.assertTrue(applied["accepted"], applied)
        self.assertTrue(applied["verified"])
        self.assertEqual(json.loads(self.settings.read_text(encoding="utf-8")), {"schema_version": 2, "settings_revision": 1})

    def test_root_level_draft_has_fixed_category_and_target_mapping(self) -> None:
        marker = "node_studio_draft_archive_marker.json"
        draft = self.config / marker
        draft.write_text('{"draft":true}', encoding="utf-8")
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        self.assertNotIn(marker, json.dumps(created, ensure_ascii=False))
        draft.unlink()
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        self.assertNotIn(marker, json.dumps(plan, ensure_ascii=False))
        self.assertEqual(plan["categories"].get("drafts"), 1)
        self.assertTrue(set(plan["categories"]).issubset({"settings", "creative_workspace", "workflow_library", "drafts"}))
        applied = manager.apply_restore(plan["plan_id"], confirmed=True)
        self.assertTrue(applied["accepted"], applied)
        self.assertEqual(json.loads(draft.read_text(encoding="utf-8")), {"draft": True})
        self.assertFalse((self.config / "drafts" / marker).exists())

    def test_manifest_schema_duplicate_and_wrong_types_fail_without_echo(self) -> None:
        marker = "archive_marker_sentinel"
        cases: list[tuple[str, dict[str, object] | None, bytes | None]] = [
            ("unknown", {**self._manifest({}), "unknown": marker}, None),
            ("wrong-files", {**self._manifest({}), "files": []}, None),
            ("bad-meta", {**self._manifest({"settings.json": b"{}"}), "files": {"settings.json": []}}, None),
            ("bad-time", {**self._manifest({}), "created_at": marker}, None),
            ("duplicate-json", None, b'{"schema_version":1,"schema_version":1,"created_at":"2026-01-01T00:00:00+00:00","hub_backup_version":"1.0","included_data_classes":["settings","creative_workspace","workflow_library","node_studio_drafts"],"files":{}}'),
        ]
        for name, manifest, raw in cases:
            with self.subTest(name=name):
                _path, backup_id = self._archive(f"hub-backup-{name}.zip", {}, manifest=manifest, raw_manifest=raw)
                result = self.manager().inspect_backup(backup_id)
                self.assertFalse(result["valid"])
                self.assertNotIn(marker, json.dumps(result, ensure_ascii=False))

    def test_unknown_traversal_and_symlink_members_fail_closed(self) -> None:
        hostile = "../outside_archive_marker.json"
        files = {hostile: b"{}"}
        _path, backup_id = self._archive("hub-backup-traversal.zip", files, manifest=self._manifest(files))
        result = self.manager().inspect_backup(backup_id)
        self.assertFalse(result["valid"])
        self.assertNotIn(hostile, json.dumps(result, ensure_ascii=False))

        symlink_path = self.backups / "hub-backup-symlink.zip"
        manifest = self._manifest({"settings.json": b"{}"})
        with zipfile.ZipFile(symlink_path, "w") as archive:
            info = zipfile.ZipInfo("settings.json")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"{}")
            archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
        result = self.manager().inspect_backup("backup_hub-backup-symlink")
        self.assertFalse(result["valid"])

    def test_config_root_reparse_refuses_create_without_outside_write(self) -> None:
        outside = Path(self.temp.name) / "outside-config"
        outside.mkdir()
        sentinel = outside / "sentinel.json"
        sentinel.write_text("outside", encoding="utf-8")
        preserved = Path(self.temp.name) / "Config-preserved"
        self.config.rename(preserved)
        os.symlink(outside, self.config, target_is_directory=True)
        try:
            result = self.manager().create_backup()
            self.assertFalse(result["accepted"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
            self.assertFalse(list(outside.glob("*.zip")))
        finally:
            if self.config.is_symlink():
                self.config.unlink()
            preserved.rename(self.config)

    def test_source_reparse_refuses_backup_read(self) -> None:
        outside = Path(self.temp.name) / "outside-source"
        outside.mkdir()
        sentinel = outside / "source.json"
        sentinel.write_text('{"marker":"outside"}', encoding="utf-8")
        original = self.config / "settings.original"
        self.settings.rename(original)
        os.symlink(sentinel, self.settings)
        try:
            result = self.manager().create_backup()
            self.assertFalse(result["accepted"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"marker":"outside"}')
            self.assertFalse(list(self.backups.glob("*.zip")))
        finally:
            if self.settings.is_symlink():
                self.settings.unlink()
            original.rename(self.settings)

    def test_same_byte_target_replacement_after_plan_refuses(self) -> None:
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        original = self.settings.read_bytes()
        self.settings.unlink()
        self.settings.write_bytes(original)
        result = manager.apply_restore(plan["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.settings.read_bytes(), original)

    def test_write_failure_after_first_candidate_rolls_back_all_prior_bytes(self) -> None:
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        self.settings.write_text('{"schema_version":2,"settings_revision":99}', encoding="utf-8")
        self.workspace.write_text('{"schema_version":1,"projects":["new"]}', encoding="utf-8")
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        settings_before = self.settings.read_bytes()
        workspace_before = self.workspace.read_bytes()
        original_replace = bm.os.replace
        calls = 0

        def fail_second(source: object, target: object) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected")
            original_replace(source, target)

        with patch.object(bm.os, "replace", side_effect=fail_second):
            result = manager.apply_restore(plan["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(self.workspace.read_bytes(), workspace_before)
        self.assertEqual(list(self.config.glob(".backup-restore-*")), [])

    def test_post_replace_guard_failure_rolls_back_already_replaced_file(self) -> None:
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        self.settings.write_text('{"schema_version":2,"settings_revision":99}', encoding="utf-8")
        self.workspace.write_text('{"schema_version":1,"projects":["new"]}', encoding="utf-8")
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        settings_before = self.settings.read_bytes()
        workspace_before = self.workspace.read_bytes()
        archive = manager._resolve_backup_target(created["backup_id"])
        self.assertIsNotNone(archive)
        archive = archive if archive is not None else self.backups / "missing.zip"
        original_replace = bm.os.replace
        calls = 0

        def replace_then_replace_archive(source: object, target: object) -> None:
            nonlocal calls
            calls += 1
            original_replace(source, target)
            if calls == 1:
                replacement = self.backups / ".same-byte-archive-replacement.zip"
                replacement.write_bytes(archive.read_bytes())
                original_replace(replacement, archive)

        with patch.object(bm.os, "replace", side_effect=replace_then_replace_archive):
            result = manager.apply_restore(plan["plan_id"], confirmed=True)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["verified"])
        self.assertEqual(self.settings.read_bytes(), settings_before)
        self.assertEqual(self.workspace.read_bytes(), workspace_before)
        self.assertEqual(list(self.config.glob(".backup-restore-*")), [])

    def test_parent_reparse_between_plan_and_apply_refuses_without_outside_write(self) -> None:
        manager = self.manager()
        created = manager.create_backup()
        self.assertTrue(created["accepted"], created)
        plan = manager.plan_restore(created["backup_id"])
        self.assertTrue(plan["accepted"], plan)
        outside = Path(self.temp.name) / "outside-parent"
        outside.mkdir()
        sentinel = outside / "sentinel.json"
        sentinel.write_text("outside", encoding="utf-8")
        preserved = Path(self.temp.name) / "Config-preserved"
        self.config.rename(preserved)
        os.symlink(outside, self.config, target_is_directory=True)
        try:
            result = manager.apply_restore(plan["plan_id"], confirmed=True)
            self.assertFalse(result["accepted"])
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside")
            self.assertFalse((outside / "settings.json").exists())
        finally:
            if self.config.is_symlink():
                self.config.unlink()
            preserved.rename(self.config)


if __name__ == "__main__":
    unittest.main()
