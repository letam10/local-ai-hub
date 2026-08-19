from __future__ import annotations

import json
import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.app_config.schema import (
    SETTINGS_SCHEMA_VERSION,
    SETTINGS_SECTION_DEFAULTS,
    migrate_settings,
    scrub_secrets,
    validate_settings,
)
from src.app_config.settings_service import SettingsPersistence


class TestValidateSettings(unittest.TestCase):
    def test_defaults_returned_for_empty(self):
        result = validate_settings({})
        self.assertEqual(result['schema_version'], SETTINGS_SCHEMA_VERSION)
        self.assertEqual(result['network']['bind_host'], '127.0.0.1')
        self.assertEqual(result['ui']['language'], 'vi')

    def test_bind_host_guard(self):
        with self.assertRaises(ValueError):
            validate_settings({'network': {'bind_host': '0.0.0.0'}})

    def test_valid_language_accepted(self):
        result = validate_settings({'ui': {'language': 'en'}})
        self.assertEqual(result['ui']['language'], 'en')

    def test_invalid_language_falls_back(self):
        result = validate_settings({'ui': {'language': 'xx_invalid'}})
        self.assertEqual(result['ui']['language'], 'vi')

    def test_invalid_port_falls_back(self):
        result = validate_settings({'network': {'api_port': 80}})
        self.assertEqual(result['network']['api_port'], 8765)

    def test_valid_port_accepted(self):
        result = validate_settings({'network': {'api_port': 9000}})
        self.assertEqual(result['network']['api_port'], 9000)

    def test_gpu_jobs_clamped(self):
        result = validate_settings({'jobs': {'max_heavy_gpu_jobs': 99}})
        self.assertEqual(result['jobs']['max_heavy_gpu_jobs'], 1)

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            validate_settings('not-a-dict')

    def test_revision_preserved(self):
        result = validate_settings({'settings_revision': 5})
        self.assertEqual(result['settings_revision'], 5)


class TestMigrateSettings(unittest.TestCase):
    def test_v1_flat_to_v2_sections(self):
        v1 = {
            'schema_version': 1,
            'start_maximized': False,
            'minimum_width': 1440,
            'max_heavy_gpu_jobs': 2,
            'model_load_policy': 'eager',
            'bind_host': '127.0.0.1',
        }
        result = migrate_settings(v1, from_version=1)
        self.assertEqual(result['schema_version'], SETTINGS_SCHEMA_VERSION)
        self.assertEqual(result['window']['start_maximized'], False)
        self.assertEqual(result['window']['minimum_width'], 1440)
        self.assertEqual(result['jobs']['max_heavy_gpu_jobs'], 2)
        self.assertEqual(result['jobs']['model_load_policy'], 'eager')
        self.assertNotIn('start_maximized', result)

    def test_v2_unchanged(self):
        v2 = validate_settings({'ui': {'language': 'zh'}})
        result = migrate_settings(v2, from_version=2)
        self.assertEqual(result['ui']['language'], 'zh')


class TestScrubSecrets(unittest.TestCase):
    def test_scrubs_known_keys(self):
        dirty = {'api_key': 'secret123', 'ui': {'theme': 'dark', 'token': 'xyz'}}
        clean = scrub_secrets(dirty)
        self.assertNotIn('api_key', clean)
        self.assertNotIn('token', clean.get('ui', {}))
        self.assertEqual(clean['ui']['theme'], 'dark')

    def test_nested_and_list(self):
        dirty = {'items': [{'password': 'pw', 'value': 1}]}
        clean = scrub_secrets(dirty)
        self.assertNotIn('password', clean['items'][0])
        self.assertEqual(clean['items'][0]['value'], 1)


class TestSettingsPersistenceLoad(unittest.TestCase):
    def test_load_absent_file_returns_clean_defaults(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            result = svc.load()
            self.assertEqual(result['recovery']['status'], 'clean')
            self.assertEqual(result['settings']['schema_version'], SETTINGS_SCHEMA_VERSION)
            self.assertFalse(path.exists())

    def test_load_malformed_json_blocked(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            path.write_text('{bad json', encoding='utf-8')
            svc = SettingsPersistence(path)
            result = svc.load()
            self.assertEqual(result['recovery']['status'], 'recovery_required')
            # File must NOT be modified
            self.assertEqual(path.read_text(encoding='utf-8'), '{bad json')

    def test_load_v1_migrates(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            v1 = {'schema_version': 1, 'start_maximized': False, 'bind_host': '127.0.0.1'}
            path.write_text(json.dumps(v1), encoding='utf-8')
            svc = SettingsPersistence(path)
            result = svc.load()
            # Migration does not persist; it returns migrated view
            self.assertEqual(result['recovery']['status'], 'clean')
            self.assertEqual(result['settings']['window']['start_maximized'], False)


class TestSettingsPersistenceSave(unittest.TestCase):
    def test_atomic_write_creates_file(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            result = svc.save({'ui': {'language': 'en'}})
            self.assertTrue(result['accepted'])
            self.assertTrue(path.exists())
            on_disk = json.loads(path.read_bytes())
            self.assertEqual(on_disk['ui']['language'], 'en')
            # No .tmp file left
            tmps = list(Path(tmpdir).glob('.settings-*.tmp'))
            self.assertEqual(tmps, [])

    def test_revision_increments(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            r1 = svc.save({'ui': {'language': 'ja'}})
            self.assertEqual(r1['settings_revision'], 1)
            r2 = svc.save({'ui': {'language': 'ko'}})
            self.assertEqual(r2['settings_revision'], 2)

    def test_conflict_detection(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            svc.save({'ui': {'language': 'en'}})  # revision becomes 1
            result = svc.save({'ui': {'language': 'zh'}}, expected_revision=0)
            self.assertFalse(result['accepted'])
            self.assertEqual(result['status'], 'conflict')

    def test_blocked_recovery_prevents_save(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            path.write_text('NOT JSON', encoding='utf-8')
            svc = SettingsPersistence(path)
            result = svc.save({'ui': {'language': 'en'}})
            self.assertFalse(result['accepted'])
            self.assertEqual(result['status'], 'recovery_required')
            # Corrupt file preserved
            self.assertEqual(path.read_text(encoding='utf-8'), 'NOT JSON')

    def test_secret_scrubbed_on_write(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            svc.save({'ui': {'language': 'vi'}})
            raw = json.loads(path.read_bytes())
            # No secret keys in persisted file
            def has_secret(obj):
                if isinstance(obj, dict):
                    for k in obj:
                        if k.lower() in {'api_key', 'token', 'password', 'secret'}:
                            return True
                        if has_secret(obj[k]):
                            return True
                return False
            self.assertFalse(has_secret(raw))

    def test_invalid_patch_rejected(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            result = svc.save('not-a-dict')
            self.assertFalse(result['accepted'])


class TestSettingsPersistenceResetSection(unittest.TestCase):
    def test_reset_ui_section(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            svc.save({'ui': {'language': 'ko', 'theme': 'light'}})
            result = svc.reset_section('ui')
            self.assertTrue(result['accepted'])
            self.assertEqual(result['settings']['ui']['language'], SETTINGS_SECTION_DEFAULTS['ui']['language'])

    def test_reset_invalid_section(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            result = svc.reset_section('nonexistent')
            self.assertFalse(result['accepted'])


class TestSettingsPersistenceExportSanitized(unittest.TestCase):
    def test_export_sanitized_no_secrets(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            svc.save({'ui': {'language': 'vi'}})
            export = svc.export_sanitized()
            self.assertIn('settings', export)
            self.assertIn('recovery', export)


class TestSettingsPersistenceConcurrency(unittest.TestCase):
    def test_concurrent_saves_are_safe(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'settings.json'
            svc = SettingsPersistence(path)
            errors = []

            def writer(lang):
                try:
                    svc.save({'ui': {'language': lang}})
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=writer, args=(lang,)) for lang in ['vi', 'en', 'zh', 'ja', 'ko'] * 4]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            # File must be valid JSON after concurrent writes
            on_disk = json.loads(path.read_bytes())
            self.assertIn('ui', on_disk)


if __name__ == '__main__':
    unittest.main()
