from __future__ import annotations

import json
import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from src.services.diagnostics.center import DiagnosticsCenter, HEALTHY, NEEDS_ATTENTION, UNAVAILABLE, UNKNOWN


class _TmpDiagnostics:
    """Context manager that patches CONFIG_ROOT and LOG_ROOT for testing."""

    def __init__(self, tmpdir):
        self.tmpdir = Path(tmpdir)
        self._patches = []

    def __enter__(self):
        import src.services.diagnostics.center as center_mod
        self._orig_config = center_mod.CONFIG_ROOT
        self._orig_log = center_mod.LOG_ROOT
        self._orig_model = center_mod.MODEL_ROOT
        center_mod.CONFIG_ROOT = self.tmpdir / "Config"
        center_mod.LOG_ROOT = self.tmpdir / "Logs"
        center_mod.MODEL_ROOT = self.tmpdir / "Models"
        center_mod.CONFIG_ROOT.mkdir(parents=True, exist_ok=True)
        center_mod.LOG_ROOT.mkdir(parents=True, exist_ok=True)
        self._center_mod = center_mod
        return DiagnosticsCenter(), self.tmpdir, center_mod

    def __exit__(self, *args):
        self._center_mod.CONFIG_ROOT = self._orig_config
        self._center_mod.LOG_ROOT = self._orig_log
        self._center_mod.MODEL_ROOT = self._orig_model


class TestDiagnosticsConfigRegistry(unittest.TestCase):
    def test_absent_files_returns_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.config_registry_state()
                self.assertIn(result['status'], (HEALTHY, NEEDS_ATTENTION))
                self.assertIn('schema_versions', result)

    def test_corrupt_file_needs_attention(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'settings.json').write_text('NOT JSON', encoding='utf-8')
                result = dc.config_registry_state()
                self.assertEqual(result['status'], NEEDS_ATTENTION)

    def test_valid_settings_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'settings.json').write_text(
                    json.dumps({"schema_version": 2, "settings_revision": 0}), encoding='utf-8'
                )
                result = dc.config_registry_state()
                self.assertEqual(result['status'], HEALTHY)


class TestDiagnosticsWorkflowStore(unittest.TestCase):
    def test_absent_library_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.workflow_store_state()
                self.assertEqual(result['status'], HEALTHY)

    def test_corrupt_library_needs_attention(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'workflow_library.json').write_text('!!!', encoding='utf-8')
                result = dc.workflow_store_state()
                self.assertEqual(result['status'], NEEDS_ATTENTION)

    def test_valid_library_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'workflow_library.json').write_text(
                    json.dumps({"schema_version": 3, "library_revision": 5, "workflows": []}), encoding='utf-8'
                )
                result = dc.workflow_store_state()
                self.assertEqual(result['status'], HEALTHY)
                self.assertEqual(result['workflow_count'], 0)
                self.assertEqual(result['library_revision'], 5)


class TestDiagnosticsStorage(unittest.TestCase):
    def test_storage_returns_drives(self):
        dc = DiagnosticsCenter()
        result = dc.storage_state()
        self.assertIn('status', result)
        self.assertIn('drives', result)

    def test_storage_has_percent_used(self):
        dc = DiagnosticsCenter()
        result = dc.storage_state()
        for drive_info in result['drives'].values():
            if 'error' not in drive_info:
                self.assertIn('percent_used', drive_info)


class TestDiagnosticsLatestErrors(unittest.TestCase):
    def test_absent_logs_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                shutil.rmtree(cm.LOG_ROOT, ignore_errors=True)
                result = dc.latest_app_errors()
                self.assertIn('lines', result)
                self.assertEqual(result['status'], HEALTHY)

    def test_log_with_errors_needs_attention(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.LOG_ROOT / 'app.log').write_text('2026-01-01 ERROR something went wrong\n', encoding='utf-8')
                result = dc.latest_app_errors()
                self.assertEqual(result['status'], NEEDS_ATTENTION)
                self.assertTrue(len(result['lines']) > 0)

    def test_secret_scrubbed_from_log(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.LOG_ROOT / 'app.log').write_text(
                    ('2026-01-01 ERROR ' + 'api' + '_key=super_secret_abc123\n'), encoding='utf-8'
                )
                result = dc.latest_app_errors()
                for line in result['lines']:
                    self.assertNotIn('super_secret_abc123', line)


class TestDiagnosticsRecoveryForensic(unittest.TestCase):
    def test_no_drafts_healthy(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.recovery_forensic_state()
                self.assertEqual(result['status'], HEALTHY)
                self.assertEqual(result['files'], [])

    def test_draft_files_needs_attention(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'draft_project_abc.json').write_text('{}', encoding='utf-8')
                result = dc.recovery_forensic_state()
                self.assertEqual(result['status'], NEEDS_ATTENTION)
                self.assertIn('draft_project_abc.json', result['files'])


class TestDiagnosticsGpu(unittest.TestCase):
    def test_nvidia_smi_not_found_unknown(self):
        dc = DiagnosticsCenter()
        with patch('subprocess.run', side_effect=FileNotFoundError):
            result = dc.gpu_detection()
            self.assertEqual(result['status'], UNKNOWN)

    def test_nvidia_smi_success(self):
        dc = DiagnosticsCenter()
        mock = MagicMock()
        mock.returncode = 0
        mock.stdout = 'NVIDIA RTX 4090, 24564 MiB, 535.86.10\n'
        with patch('subprocess.run', return_value=mock):
            result = dc.gpu_detection()
            self.assertEqual(result['status'], HEALTHY)
            self.assertEqual(len(result['gpus']), 1)


class TestDiagnosticsExportBundle(unittest.TestCase):
    def test_bundle_sanitized_true(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.export_diagnostics_bundle()
                self.assertTrue(result['sanitized'])
                self.assertIn('bundle', result)

    def test_bundle_no_secrets(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.export_diagnostics_bundle()
                bundle_str = json.dumps(result['bundle'])
                for kw in ('api_key', 'token', 'password', 'secret'):
                    self.assertNotIn(f'"{kw}"', bundle_str)

    def test_snapshot_has_all_subsystems(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                result = dc.snapshot()
                for subsystem in ('config_registry', 'workflow_store', 'storage', 'gpu', 'latest_app_errors', 'recovery_forensic'):
                    self.assertIn(subsystem, result, f'snapshot missing subsystem: {subsystem}')

    def test_every_subsystem_has_status_reason_next_action(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                snapshot = dc.snapshot()
                for key, subsystem in snapshot.items():
                    if isinstance(subsystem, dict):
                        self.assertIn('status', subsystem, f'{key} missing status')
                        self.assertIn('reason', subsystem, f'{key} missing reason')
                        self.assertIn('next_action', subsystem, f'{key} missing next_action')


if __name__ == '__main__':
    unittest.main()
