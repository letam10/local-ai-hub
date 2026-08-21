from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import src.services.project_manager.manager as project_manager_module
from src.services.project_manager.manager import CreativeProjectManager
from src.services.project_manager.schemas import (
    PROJECT_SCHEMA_VERSION,
    TRAVERSAL_RE,
    LOCAL_PATH_RE,
)
from src.services.node_studio.state import draft_persist, draft_load, draft_clear

PROJECT_ID = "project_" + "a" * 32


class TestProjectManagerAtomicSave(unittest.TestCase):
    def _make_manager(self, tmpdir):
        path = Path(tmpdir) / 'workspace.json'
        return CreativeProjectManager(path)

    def test_save_leaves_no_tmp_files(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.create_project({'title': 'Test Alpha', 'description': ''})
            tmps = list(Path(tmpdir).glob('.workspace-*.tmp'))
            self.assertEqual(tmps, [], msg='temp files should be cleaned up')

    def test_save_file_is_valid_json(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.create_project({'title': 'Beta', 'description': ''})
            path = Path(tmpdir) / 'workspace.json'
            raw = json.loads(path.read_bytes())
            self.assertIn('projects', raw)


class TestProjectManagerAutosaveDraft(unittest.TestCase):
    def _make_manager(self, tmpdir):
        return CreativeProjectManager(Path(tmpdir) / 'workspace.json')

    def test_autosave_creates_draft_file(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.autosave_draft(PROJECT_ID, {'nodes': [], 'edges': []})
            self.assertTrue(result['accepted'])
            self.assertNotIn('draft_path', result)
            self.assertEqual(result['draft_id'], f'draft_{PROJECT_ID}.json')
            draft_path = Path(tmpdir) / result['draft_id']
            self.assertTrue(draft_path.exists())

    def test_clear_draft_removes_file(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.autosave_draft(PROJECT_ID, {'nodes': []})
            # draft should exist
            drafts = list(Path(tmpdir).glob('draft_*.json'))
            self.assertTrue(len(drafts) > 0)
            result = mgr.clear_draft(PROJECT_ID)
            self.assertTrue(result['accepted'])
            # draft should be gone
            drafts_after = list(Path(tmpdir).glob('draft_*.json'))
            self.assertEqual(drafts_after, [])

    def test_invalid_project_id_rejected(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            for project_id in ('', '../secret', r'C:\private\workspace'):
                result = mgr.autosave_draft(project_id, {'nodes': []})
                self.assertFalse(result['accepted'])
                self.assertNotIn('draft_path', result)

    def test_persistence_failure_is_bounded_and_does_not_echo_path(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            with patch('src.services.project_manager.manager.os.fsync', side_effect=OSError('private path marker')):
                result = mgr.autosave_draft(PROJECT_ID, {'nodes': []})
            self.assertFalse(result['accepted'])
            self.assertEqual(result['status'], 'manual_review')
            self.assertNotIn('private path marker', json.dumps(result))
            self.assertNotIn('draft_path', result)
            self.assertEqual(list(Path(tmpdir).glob('.draft-*.tmp')), [])

    def test_clear_draft_refuses_foreign_record_without_delete(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.autosave_draft(PROJECT_ID, {'nodes': []})
            draft_path = Path(tmpdir) / result['draft_id']
            foreign = {'schema_version': 1, 'project_id': 'project_' + 'b' * 32, 'graph': {'nodes': []}}
            draft_path.write_text(json.dumps(foreign), encoding='utf-8')
            before = draft_path.read_bytes()
            cleared = mgr.clear_draft(PROJECT_ID)
            self.assertFalse(cleared['accepted'])
            self.assertEqual(cleared['status'], 'manual_review')
            self.assertEqual(draft_path.read_bytes(), before)
            self.assertNotIn(str(draft_path), json.dumps(cleared))

    def test_clear_draft_refuses_directory_and_reparse_without_delete(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.autosave_draft(PROJECT_ID, {'nodes': []})
            draft_path = Path(tmpdir) / result['draft_id']
            draft_path.unlink()
            draft_path.mkdir()
            directory_result = mgr.clear_draft(PROJECT_ID)
            self.assertFalse(directory_result['accepted'])
            self.assertEqual(directory_result['status'], 'manual_review')
            self.assertTrue(draft_path.is_dir())
            draft_path.rmdir()
            draft_path.write_text(json.dumps({'schema_version': 1, 'project_id': PROJECT_ID, 'graph': {}}), encoding='utf-8')
            real_lstat = project_manager_module.os.lstat
            original = real_lstat(draft_path)
            reparse_values = {
                name: getattr(original, name, 0)
                for name in ('st_mode', 'st_ino', 'st_dev', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_file_attributes')
            }
            reparse_values['st_file_attributes'] = int(reparse_values.get('st_file_attributes', 0)) | project_manager_module._REPARSE_POINT
            reparse_stat = type('ReparseStat', (), reparse_values)()

            def fake_lstat(candidate):
                return reparse_stat if Path(candidate) == draft_path else real_lstat(candidate)

            with patch.object(project_manager_module.os, 'lstat', side_effect=fake_lstat):
                reparse_result = mgr.clear_draft(PROJECT_ID)
            self.assertFalse(reparse_result['accepted'])
            self.assertEqual(reparse_result['status'], 'manual_review')
            self.assertTrue(draft_path.exists())


class TestProjectSchemas(unittest.TestCase):
    def test_traversal_re_catches_dotdot(self):
        self.assertTrue(TRAVERSAL_RE.search('../secret'))
        self.assertTrue(TRAVERSAL_RE.search('..\\\\secret'))
        self.assertTrue(TRAVERSAL_RE.search('%2F'))
        self.assertFalse(TRAVERSAL_RE.search('normal text'))

    def test_local_path_re_catches_windows_path(self):
        self.assertTrue(LOCAL_PATH_RE.search('C:\\\\Users'))
        self.assertFalse(LOCAL_PATH_RE.search('normal'))

    def test_project_schema_version_is_int(self):
        self.assertIsInstance(PROJECT_SCHEMA_VERSION, int)
        self.assertGreater(PROJECT_SCHEMA_VERSION, 0)


class TestNodeStudioDraftPersist(unittest.TestCase):
    def test_draft_persist_and_load(self):
        with TemporaryDirectory() as tmpdir:
            import src.shared.paths.registry as paths_mod
            original_config_root = paths_mod.CONFIG_ROOT
            try:
                paths_mod.CONFIG_ROOT = Path(tmpdir)
                # Re-import to pick up new CONFIG_ROOT
                from importlib import reload
                import src.services.node_studio.state as state_mod
                reload(state_mod)

                graph = {'nodes': [{'id': 'n1', 'type': 'image_input'}], 'edges': []}
                result = state_mod.draft_persist('image', graph)
                self.assertTrue(result['accepted'])
                loaded = state_mod.draft_load('image')
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded['scope'], 'image')
                self.assertEqual(loaded['graph'], graph)
            finally:
                paths_mod.CONFIG_ROOT = original_config_root

    def test_draft_load_absent_returns_none(self):
        with TemporaryDirectory() as tmpdir:
            import src.shared.paths.registry as paths_mod
            original = paths_mod.CONFIG_ROOT
            try:
                paths_mod.CONFIG_ROOT = Path(tmpdir)
                from importlib import reload
                import src.services.node_studio.state as state_mod
                reload(state_mod)
                result = state_mod.draft_load('nonexistent_scope')
                self.assertIsNone(result)
            finally:
                paths_mod.CONFIG_ROOT = original

    def test_draft_clear_removes_file(self):
        with TemporaryDirectory() as tmpdir:
            import src.shared.paths.registry as paths_mod
            original = paths_mod.CONFIG_ROOT
            try:
                paths_mod.CONFIG_ROOT = Path(tmpdir)
                from importlib import reload
                import src.services.node_studio.state as state_mod
                reload(state_mod)

                state_mod.draft_persist('video', {'nodes': [], 'edges': []})
                state_mod.draft_clear('video')
                result = state_mod.draft_load('video')
                self.assertIsNone(result)
            finally:
                paths_mod.CONFIG_ROOT = original

    def test_invalid_scope_rejected(self):
        from src.services.node_studio.state import draft_persist
        result = draft_persist('', {})
        self.assertFalse(result['accepted'])

    def test_invalid_graph_rejected(self):
        from src.services.node_studio.state import draft_persist
        result = draft_persist('image', 'not-a-dict')
        self.assertFalse(result['accepted'])

    def test_corrupt_draft_returns_none(self):
        with TemporaryDirectory() as tmpdir:
            import src.shared.paths.registry as paths_mod
            original = paths_mod.CONFIG_ROOT
            try:
                paths_mod.CONFIG_ROOT = Path(tmpdir)
                from importlib import reload
                import src.services.node_studio.state as state_mod
                reload(state_mod)

                # Write corrupt file
                draft_path = Path(tmpdir) / 'node_studio_draft_image.json'
                draft_path.write_text('NOT JSON', encoding='utf-8')
                result = state_mod.draft_load('image')
                self.assertIsNone(result)
            finally:
                paths_mod.CONFIG_ROOT = original


if __name__ == '__main__':
    unittest.main()
