from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

from src.app.desktop_lifecycle import (
    DesktopCloseController,
    GracefulShutdownCoordinator,
    StartupRaceGuard,
    startup_failure_payload,
)


class TestStartupFailurePayload(unittest.TestCase):
    def test_returns_required_fields(self):
        payload = startup_failure_payload('API port busy', 'Khởi động lại Hub')
        self.assertEqual(payload['status'], 'startup_failure')
        self.assertIn('reason', payload)
        self.assertIn('next_action', payload)

    def test_truncates_long_reason(self):
        payload = startup_failure_payload('x' * 1000, 'fix')
        self.assertLessEqual(len(payload['reason']), 512)

    def test_null_bytes_stripped(self):
        payload = startup_failure_payload('bad\x00value', 'ok\x00')
        self.assertNotIn('\x00', payload['reason'])
        self.assertNotIn('\x00', payload['next_action'])


class TestStartupRaceGuard(unittest.TestCase):
    def test_absent_pid_file_not_stale(self):
        with TemporaryDirectory() as tmpdir:
            guard = StartupRaceGuard(Path(tmpdir) / 'hub.pid')
            self.assertFalse(guard.is_stale())
            self.assertIsNone(guard.read_stale_pid())

    def test_write_then_stale(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'hub.pid'
            guard = StartupRaceGuard(path)
            guard.write_current_pid()
            self.assertTrue(guard.is_stale())
            pid = guard.read_stale_pid()
            self.assertIsNotNone(pid)
            self.assertGreater(pid, 0)

    def test_clear_removes_file(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'hub.pid'
            guard = StartupRaceGuard(path)
            guard.write_current_pid()
            guard.clear()
            self.assertFalse(guard.is_stale())

    def test_corrupt_pid_file_returns_none(self):
        with TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'hub.pid'
            path.write_text('not-a-number', encoding='utf-8')
            guard = StartupRaceGuard(path)
            self.assertIsNone(guard.read_stale_pid())
            # is_stale returns True because file exists even if corrupt
            self.assertTrue(guard.is_stale())


class TestGracefulShutdownCoordinator(unittest.TestCase):
    def test_empty_checkpoint_succeeds(self):
        coord = GracefulShutdownCoordinator()
        result = coord.save_checkpoint()
        self.assertEqual(result['flushed'], [])
        self.assertEqual(result['errors'], {})

    def test_all_flushers_called(self):
        coord = GracefulShutdownCoordinator()
        called = []
        coord.register('settings', lambda: called.append('settings'))
        coord.register('projects', lambda: called.append('projects'))
        result = coord.save_checkpoint()
        self.assertEqual(result['flushed'], ['settings', 'projects'])
        self.assertEqual(called, ['settings', 'projects'])

    def test_failing_flusher_doesnt_stop_rest(self):
        coord = GracefulShutdownCoordinator()
        called = []

        def bad_flusher():
            raise RuntimeError('disk full')

        coord.register('bad', bad_flusher)
        coord.register('good', lambda: called.append('good'))
        result = coord.save_checkpoint()
        self.assertIn('bad', result['errors'])
        self.assertIn('good', result['flushed'])
        self.assertEqual(called, ['good'])

    def test_concurrent_registration_safe(self):
        coord = GracefulShutdownCoordinator()
        called = []
        lock = threading.Lock()

        def make_flusher(name):
            def fn():
                with lock:
                    called.append(name)
            return fn

        threads = [threading.Thread(target=coord.register, args=(f'f{i}', make_flusher(f'f{i}'))) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = coord.save_checkpoint()
        self.assertEqual(len(result['flushed']), 10)


class TestDesktopCloseControllerExisting(unittest.TestCase):
    """Regression: existing DesktopCloseController behaviour is preserved."""

    def _make(self, active=0):
        prompt = MagicMock()
        cancel = MagicMock(return_value=(True, 'ok'))
        ctrl = DesktopCloseController(
            active_jobs=lambda: active,
            cancel_and_wait=cancel,
            prompt=prompt,
        )
        return ctrl, prompt, cancel

    def test_no_active_jobs_allows_close(self):
        ctrl, _, _ = self._make(active=0)
        self.assertTrue(ctrl.request_window_close())
        self.assertTrue(ctrl.cleanup_allowed)

    def test_active_jobs_blocks_close(self):
        ctrl, prompt, _ = self._make(active=2)
        self.assertFalse(ctrl.request_window_close())
        prompt.assert_called_once()

    def test_return_to_hub_resets_state(self):
        ctrl, _, _ = self._make(active=1)
        ctrl.request_window_close()  # prompts
        result = ctrl.return_to_hub()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(ctrl.state, 'interactive')

    def test_keep_running_in_background(self):
        ctrl, _, _ = self._make(active=0)
        background = MagicMock(return_value=(True, 'hidden'))
        result = ctrl.keep_running_in_background(background)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(ctrl.state, 'background')


if __name__ == '__main__':
    unittest.main()
