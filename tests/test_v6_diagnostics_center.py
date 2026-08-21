from __future__ import annotations

import json
import os
import shutil
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from src.services.diagnostics.center import DiagnosticsCenter, HEALTHY, NEEDS_ATTENTION, UNAVAILABLE, UNKNOWN


_COLLECTOR_METHODS = {
    "git_integrity": "git_integrity_state",
    "config_registry": "config_registry_state",
    "jobs_store": "jobs_store_state",
    "artifact_store": "artifact_store_state",
    "workflow_store": "workflow_store_state",
    "models_inventory": "models_inventory",
    "environments_inventory": "environments_inventory",
    "runtime_inventory": "runtime_inventory",
    "storage": "storage_state",
    "gpu": "gpu_detection",
    "latest_app_errors": "latest_app_errors",
    "recovery_forensic": "recovery_forensic_state",
}
_PUBLIC_ROW_FIELDS = {
    "status", "reason", "next_action", "execution", "dry_run", "code",
    "root_verified", "inside_work_tree", "record_count", "status_bucket_count",
    "artifact_count", "workflow_count", "total_size_bytes", "environment_count",
    "drive_count", "gpu_count", "error_count", "has_errors", "file_count",
    "has_recovery_files", "category_flags",
}


def _safe_collector_rows():
    return {key: {"status": HEALTHY, "reason": "ignored", "next_action": "ignored"} for key in _COLLECTOR_METHODS}


def _patch_collector_rows(center, rows):
    stack = ExitStack()
    for key, method in _COLLECTOR_METHODS.items():
        stack.enter_context(patch.object(center, method, return_value=rows[key]))
    return stack


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

    def test_hostile_schema_version_is_fixed_and_not_echoed(self):
        marker = 'C:' + r'\Users\Alice Smith\private.json'
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                (cm.CONFIG_ROOT / 'settings.json').write_text(
                    json.dumps({"schema_version": {"path": marker}, "status": {"reason": marker}}), encoding='utf-8'
                )
                result = dc.config_registry_state()
        self.assertEqual(result['status'], NEEDS_ATTENTION)
        self.assertEqual(result['schema_versions']['settings.json'], 'invalid')
        self.assertNotIn(marker, json.dumps(result, ensure_ascii=True))


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
        with patch.object(shutil, 'disk_usage', return_value=SimpleNamespace(total=100, used=25, free=75)):
            result = dc.storage_state()
        self.assertIn('status', result)
        self.assertIn('drives', result)

    def test_storage_has_percent_used(self):
        dc = DiagnosticsCenter()
        with patch.object(shutil, 'disk_usage', return_value=SimpleNamespace(total=100, used=25, free=75)):
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
                with _patch_collector_rows(dc, _safe_collector_rows()):
                    result = dc.export_diagnostics_bundle()
                self.assertTrue(result['sanitized'])
                self.assertIn('bundle', result)

    def test_bundle_no_secrets(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                with _patch_collector_rows(dc, _safe_collector_rows()):
                    result = dc.export_diagnostics_bundle()
                bundle_str = json.dumps(result['bundle'])
                for kw in ('api_key', 'token', 'password', 'secret'):
                    self.assertNotIn(f'"{kw}"', bundle_str)

    def test_snapshot_has_all_subsystems(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                with _patch_collector_rows(dc, _safe_collector_rows()):
                    result = dc.snapshot()
                for subsystem in ('config_registry', 'workflow_store', 'storage', 'gpu', 'latest_app_errors', 'recovery_forensic'):
                    self.assertIn(subsystem, result, f'snapshot missing subsystem: {subsystem}')

    def test_every_subsystem_has_status_reason_next_action(self):
        with TemporaryDirectory() as tmpdir:
            with _TmpDiagnostics(tmpdir) as (dc, td, cm):
                with _patch_collector_rows(dc, _safe_collector_rows()):
                    snapshot = dc.snapshot()
                for key, subsystem in snapshot.items():
                    if isinstance(subsystem, dict):
                        self.assertIn('status', subsystem, f'{key} missing status')
                        self.assertIn('reason', subsystem, f'{key} missing reason')
                        self.assertIn('next_action', subsystem, f'{key} missing next_action')


class TestDiagnosticsPublicProjection(unittest.TestCase):
    def test_hostile_collector_values_are_omitted_without_echo(self):
        marker_path = 'C:' + r'\Users\Alice Smith\private.txt'
        marker_unc = r'\\server\share\private.txt'
        marker_url = 'https://user:pass@example.invalid/private'
        marker_bearer = 'Authorization: Bearer ' + 'opaque-marker'
        marker_command = 'python ' + marker_path
        hostile = {
            'git_integrity': {'status': HEALTHY, 'root_verified': True, 'inside_work_tree': True, 'origin': marker_url, 'head_sha': marker_path, 'branch': marker_command},
            'config_registry': {'status': NEEDS_ATTENTION, 'schema_versions': {'settings.json': '2'}, 'reason': marker_path},
            'jobs_store': {'status': UNAVAILABLE, 'counts': {'failed': 1}, 'error': marker_unc},
            'artifact_store': {'status': HEALTHY, 'artifact_count': 2, 'path': marker_path},
            'workflow_store': {'status': HEALTHY, 'workflow_count': 1, 'exception': marker_url},
            'models_inventory': {'status': HEALTHY, 'models': {marker_path: {}}, 'total_size_bytes': 12},
            'environments_inventory': {'status': HEALTHY, 'environments': [marker_path]},
            'runtime_inventory': {'status': HEALTHY, 'runtimes': {marker_path: True}},
            'storage': {'status': HEALTHY, 'drives': {marker_path: {}}},
            'gpu': {'status': HEALTHY, 'gpus': [marker_command]},
            'latest_app_errors': {'status': NEEDS_ATTENTION, 'lines': [marker_path, marker_unc, marker_url, marker_bearer, marker_command]},
            'recovery_forensic': {'status': NEEDS_ATTENTION, 'files': ['node_studio_draft_' + marker_path + '.json', 'draft_' + marker_unc + '.json', '.recovery.tmp']},
        }
        dc = DiagnosticsCenter()
        with _patch_collector_rows(dc, hostile):
            result = dc.snapshot()
        serialized = json.dumps(result, ensure_ascii=True)
        for marker in (marker_path, marker_unc, marker_url, marker_bearer, marker_command):
            self.assertNotIn(marker, serialized)
        self.assertNotIn('"head_sha"', serialized)
        self.assertNotIn('"origin"', serialized)
        self.assertNotIn('"branch"', serialized)
        self.assertNotIn('"lines"', serialized)
        self.assertNotIn('"files"', serialized)
        for row in result.values():
            self.assertTrue(set(row).issubset(_PUBLIC_ROW_FIELDS))
            self.assertEqual(row['execution'], 'not_run')
            self.assertTrue(row['dry_run'])

    def test_unexpected_collector_shape_fails_closed_without_dereference(self):
        class Explosive:
            def __getattribute__(self, name):
                raise AssertionError('unexpected collector dereference')

        dc = DiagnosticsCenter()
        rows = _safe_collector_rows()
        rows['jobs_store'] = Explosive()
        with _patch_collector_rows(dc, rows):
            result = dc.snapshot()
        self.assertEqual(result['jobs_store']['status'], UNKNOWN)
        self.assertEqual(result['jobs_store']['code'], 'diagnostic_projection_unavailable')
        self.assertNotIn('unexpected collector dereference', json.dumps(result))

    def test_projection_is_deterministic_and_recovery_is_category_only(self):
        rows = _safe_collector_rows()
        rows['recovery_forensic'] = {
            'status': NEEDS_ATTENTION,
            'files': ['node_studio_draft_image.json', 'draft_project_abc.json', '.node.tmp'],
        }
        dc = DiagnosticsCenter()
        with _patch_collector_rows(dc, rows):
            first = dc.snapshot()
            second = dc.snapshot()
        self.assertEqual(first, second)
        recovery = first['recovery_forensic']
        self.assertEqual(recovery['file_count'], 3)
        self.assertEqual(recovery['category_flags'], {'node_studio': True, 'project': True, 'temporary': True})
        self.assertNotIn('files', recovery)


if __name__ == '__main__':
    unittest.main()
