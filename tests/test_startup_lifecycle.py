from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.app import main as desktop
from src.services.api import gpu
from src.services.api.core import health


ROOT = Path(__file__).resolve().parents[1]


class StartupLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_gpu_cache = gpu._gpu_cache
        self._saved_api_process = desktop._api_process
        self._saved_shutdown_started = desktop._shutdown_started
        gpu._gpu_cache = None
        desktop._api_process = None
        desktop._shutdown_started = False

    def tearDown(self) -> None:
        gpu._gpu_cache = self._saved_gpu_cache
        desktop._api_process = self._saved_api_process
        desktop._shutdown_started = self._saved_shutdown_started

    def test_readiness_snapshot_does_not_spawn_nvidia_smi(self) -> None:
        with patch.object(gpu, "run_hidden") as run_hidden:
            snapshot = gpu.query_gpu(probe=False)
        run_hidden.assert_not_called()
        self.assertEqual(snapshot["reason"], "GPU snapshot pending")

    def test_health_uses_non_probing_gpu_snapshot_by_default(self) -> None:
        with patch("src.services.api.core.query_gpu", return_value={"available": False}) as query:
            payload = health()
        query.assert_called_once_with(probe=False)
        self.assertEqual(payload["status"], "healthy")

    def test_owned_process_termination_uses_native_tree_not_taskkill(self) -> None:
        managed = (ROOT / "src" / "services" / "process_manager" / "managed.py").read_text(encoding="utf-8")
        windows = (ROOT / "src" / "services" / "process_manager" / "windows.py").read_text(encoding="utf-8")
        self.assertIn("terminate_process_tree(process.pid)", managed)
        self.assertNotIn('"taskkill"', managed)
        self.assertIn("CreateToolhelp32Snapshot", windows)
        self.assertIn("TerminateProcess", windows)

    def test_late_owned_api_after_close_is_terminated_once(self) -> None:
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "terminate_owned_process") as terminate:
            desktop.close_owned_api()
            desktop._remember_owned_api(process)

        terminate.assert_called_once_with(process)
        self.assertIsNone(desktop._api_process)

    def test_owned_api_registered_before_close_is_terminated_once(self) -> None:
        process = Mock()
        process.poll.return_value = None
        with patch.object(desktop, "terminate_owned_process") as terminate:
            desktop._remember_owned_api(process)
            desktop.close_owned_api()

        terminate.assert_called_once_with(process)
        self.assertIsNone(desktop._api_process)

    def test_concurrent_registration_and_close_is_linearizable(self) -> None:
        process = Mock()
        process.poll.return_value = None
        start = threading.Barrier(2)
        errors: list[BaseException] = []

        def invoke(callback: object) -> None:
            try:
                start.wait(timeout=2)
                callback()  # type: ignore[operator]
            except BaseException as exc:  # pragma: no cover - assertion below reports it
                errors.append(exc)

        with patch.object(desktop, "terminate_owned_process") as terminate:
            threads = [
                threading.Thread(target=invoke, args=(lambda: desktop._remember_owned_api(process),)),
                threading.Thread(target=invoke, args=(desktop.close_owned_api,)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        terminate.assert_called_once_with(process)
        self.assertIsNone(desktop._api_process)


if __name__ == "__main__":
    unittest.main()
