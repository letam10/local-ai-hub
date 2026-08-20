from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.services.project_manager.manager import CreativeProjectManager
from src.services.project_manager.schemas import (
    PROJECT_SCHEMA_VERSION,
    TRAVERSAL_RE,
    LOCAL_PATH_RE,
)
from src.services.node_studio.state import draft_persist, draft_load, draft_clear


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
            result = mgr.autosave_draft('project_abc', {'nodes': [], 'edges': []})
            self.assertTrue(result['accepted'])
            draft_path = Path(result['draft_path'])
            self.assertTrue(draft_path.exists())

    def test_clear_draft_removes_file(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            mgr.autosave_draft('project_def', {'nodes': []})
            # draft should exist
            drafts = list(Path(tmpdir).glob('draft_*.json'))
            self.assertTrue(len(drafts) > 0)
            mgr.clear_draft('project_def')
            # draft should be gone
            drafts_after = list(Path(tmpdir).glob('draft_*.json'))
            self.assertEqual(drafts_after, [])

    def test_invalid_project_id_rejected(self):
        with TemporaryDirectory() as tmpdir:
            mgr = self._make_manager(tmpdir)
            result = mgr.autosave_draft('', {'nodes': []})
            self.assertFalse(result['accepted'])


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
