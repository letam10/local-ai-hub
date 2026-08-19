from __future__ import annotations

import json
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.project_manager.manager import CreativeProjectManager
from src.services.backup_manager import BackupManager, BACKUP_SCHEMA_VERSION, _scrub


class TestSearchAssets(unittest.TestCase):
    def _make_manager(self, tmpdir):
        return CreativeProjectManager(Path(tmpdir) / 'workspace.json')

    def test_search_empty_workspace(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.search_assets(query='anything')
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['assets'], [])

    def test_sort_by_invalid_falls_back(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            # Should not raise even with unknown sort key
            result = mgr.search_assets(sort_by='malicious_key')
            self.assertEqual(result['status'], 'completed')


class TestExportManifest(unittest.TestCase):
    def _make_manager(self, tmpdir):
        return CreativeProjectManager(Path(tmpdir) / 'workspace.json')

    def test_invalid_project_id_rejected(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.export_manifest('not_a_valid_id')
            self.assertFalse(result['accepted'])

    def test_nonexistent_project_rejected(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.export_manifest('project_' + 'a' * 32)
            self.assertFalse(result['accepted'])

    def test_manifest_no_secrets(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            create_result = mgr.create_project({'title': 'Secret Project'})
            project_id = create_result['project']['id']
            manifest_result = mgr.export_manifest(project_id)
            self.assertTrue(manifest_result['accepted'])
            manifest_str = json.dumps(manifest_result['manifest'])
            for secret_key in ('api_key', 'token', 'password', 'secret'):
                self.assertNotIn(f'"{secret_key}"', manifest_str, f'Manifest must not contain {secret_key}')

    def test_manifest_has_required_fields(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            create_result = mgr.create_project({'title': 'Manifest Test'})
            project_id = create_result['project']['id']
            result = mgr.export_manifest(project_id)
            manifest = result['manifest']
            for field in ('manifest_schema_version', 'created_at', 'project_id', 'artifact_count', 'checksum'):
                self.assertIn(field, manifest, f'Manifest missing field: {field}')


class TestGetArtifactStatus(unittest.TestCase):
    def test_invalid_artifact_id(self):
        with TemporaryDirectory() as tmpdir:
            mgr = CreativeProjectManager(Path(tmpdir) / 'workspace.json')
            result = mgr.get_artifact_status('invalid')
            self.assertFalse(result['found'])

    def test_unknown_artifact_returns_not_found(self):
        with TemporaryDirectory() as tmpdir:
            mgr = CreativeProjectManager(Path(tmpdir) / 'workspace.json')
            result = mgr.get_artifact_status('artifact_' + 'b' * 32)
            self.assertFalse(result['found'])


class TestMissingArtifactState(unittest.TestCase):
    def test_invalid_project_id_rejected(self):
        with TemporaryDirectory() as tmpdir:
            mgr = CreativeProjectManager(Path(tmpdir) / 'workspace.json')
            result = mgr.missing_artifact_state('bad_id')
            self.assertFalse(result['accepted'])

    def test_empty_project_no_missing(self):
        with TemporaryDirectory() as tmpdir:
            mgr = CreativeProjectManager(Path(tmpdir) / 'workspace.json')
            create_result = mgr.create_project({'title': 'Empty Proj'})
            project_id = create_result['project']['id']
            result = mgr.missing_artifact_state(project_id)
            self.assertTrue(result['accepted'])
            self.assertEqual(result['missing_count'], 0)


class TestBackupManagerScrubSecrets(unittest.TestCase):
    def test_scrub_removes_api_key(self):
        data = {'api_key': 'secret123', 'ui': {'theme': 'dark', 'token': 'xyz'}}
        clean = _scrub(data)
        self.assertEqual(clean['api_key'], '[REDACTED]')
        self.assertEqual(clean['ui']['token'], '[REDACTED]')
        self.assertEqual(clean['ui']['theme'], 'dark')

    def test_scrub_nested_list(self):
        data = {'items': [{'password': 'pw', 'value': 1}]}
        clean = _scrub(data)
        self.assertEqual(clean['items'][0]['password'], '[REDACTED]')
        self.assertEqual(clean['items'][0]['value'], 1)


class TestBackupManagerCreate(unittest.TestCase):
    def test_create_backup_produces_valid_zip(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            result = mgr.create_backup()
            self.assertTrue(result['accepted'], result.get('reason', ''))
            backup_path = Path(result['backup_path'])
            self.assertTrue(backup_path.exists())
            self.assertTrue(zipfile.is_zipfile(backup_path))

    def test_backup_manifest_in_zip(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            result = mgr.create_backup()
            with zipfile.ZipFile(result['backup_path'], 'r') as zf:
                self.assertIn('manifest.json', zf.namelist())
                manifest = json.loads(zf.read('manifest.json'))
                self.assertEqual(manifest['schema_version'], BACKUP_SCHEMA_VERSION)
                self.assertIn('created_at', manifest)
                self.assertIn('included_data_classes', manifest)

    def test_backup_no_tmp_files_left(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            mgr.create_backup()
            tmps = list(backup_dir.glob('*.tmp'))
            self.assertEqual(tmps, [])


class TestBackupManagerInspect(unittest.TestCase):
    def test_inspect_valid_backup(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            create_result = mgr.create_backup()
            inspect = mgr.inspect_backup(Path(create_result['backup_path']))
            self.assertTrue(inspect['valid'], inspect.get('errors'))
            self.assertEqual(inspect['errors'], [])

    def test_inspect_missing_file(self):
        with TemporaryDirectory() as tmpdir:
            mgr = BackupManager(backup_dir=Path(tmpdir))
            inspect = mgr.inspect_backup(Path(tmpdir) / 'nonexistent.zip')
            self.assertFalse(inspect['valid'])

    def test_inspect_corrupt_zip(self):
        with TemporaryDirectory() as tmpdir:
            corrupt = Path(tmpdir) / 'bad.zip'
            corrupt.write_bytes(b'not a zip')
            mgr = BackupManager(backup_dir=Path(tmpdir))
            inspect = mgr.inspect_backup(corrupt)
            self.assertFalse(inspect['valid'])


class TestBackupManagerPlanAndApply(unittest.TestCase):
    def test_plan_accepted_for_valid_backup(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            create_result = mgr.create_backup()
            plan = mgr.plan_restore(Path(create_result['backup_path']))
            self.assertTrue(plan['accepted'], plan.get('reason', ''))
            self.assertIn('changes', plan)
            self.assertIn('preview', plan)

    def test_apply_requires_confirmation(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            mgr = BackupManager(backup_dir=backup_dir)
            create_result = mgr.create_backup()
            plan = mgr.plan_restore(Path(create_result['backup_path']))
            result = mgr.apply_restore(Path(create_result['backup_path']), plan=plan, confirmed=False)
            self.assertFalse(result['accepted'])

    def test_apply_confirmed_restores_files(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'

            # Patch CONFIG_ROOT so backup/restore works in temp dir
            import src.services.backup_manager as bm_mod
            original_root = bm_mod.CONFIG_ROOT
            try:
                bm_mod.CONFIG_ROOT = Path(tmpdir)
                # Create a test file to include in backup
                (Path(tmpdir) / 'settings.json').write_text(
                    '{"schema_version": 2, "settings_revision": 1}', encoding='utf-8'
                )
                mgr = BackupManager(backup_dir=backup_dir)
                create_result = mgr.create_backup()

                # Remove the file
                (Path(tmpdir) / 'settings.json').unlink()

                plan = mgr.plan_restore(Path(create_result['backup_path']))
                result = mgr.apply_restore(Path(create_result['backup_path']), plan=plan, confirmed=True)
                self.assertTrue(result['accepted'])
                # Verify restored file is valid JSON
                verify = mgr.verify_restore(result)
                self.assertTrue(verify['valid'], verify.get('errors'))
            finally:
                bm_mod.CONFIG_ROOT = original_root

    def test_traversal_path_skipped(self):
        with TemporaryDirectory() as tmpdir:
            backup_dir = Path(tmpdir) / 'backups'
            import src.services.backup_manager as bm_mod
            original_root = bm_mod.CONFIG_ROOT
            try:
                bm_mod.CONFIG_ROOT = Path(tmpdir)
                mgr = BackupManager(backup_dir=backup_dir)
                # Manually craft a plan with traversal path
                plan = {
                    "accepted": True,
                    "backup_path": str(backup_dir / "fake.zip"),
                    "changes": [{"member": "../../../etc/passwd", "action": "create", "reason": ""}],
                    "newer_protected": [],
                    "preview": {},
                }
                # Create a fake zip with that member
                fake_zip = backup_dir / "fake.zip"
                backup_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(fake_zip, 'w') as zf:
                    zf.writestr("../../../etc/passwd", "root:x:0:0")
                result = mgr.apply_restore(fake_zip, plan=plan, confirmed=True)
                self.assertTrue(result['accepted'])
                # Should be in skipped, not applied
                self.assertFalse(any('../' in a for a in result.get('applied', [])))
            finally:
                bm_mod.CONFIG_ROOT = original_root


if __name__ == '__main__':
    unittest.main()
