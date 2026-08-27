from __future__ import annotations

import multiprocessing
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.app import main as desktop
from src.app.desktop_lifecycle import DesktopCloseController
from src.services.api import gpu
from src.services.api.core import health


ROOT = Path(__file__).resolve().parents[1]


def _run_concurrent_registration_close_probe() -> None:
    from src.app import main as desktop_module

    process = Mock()
    process.poll.return_value = None
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def invoke(callback: object) -> None:
        try:
            start.wait(timeout=2)
            callback()  # type: ignore[operator]
        except BaseException as exc:  # pragma: no cover - parent reports child failure
            errors.append(exc)

    desktop_module._api_process = None
    desktop_module._shutdown_started = False
    with patch.object(desktop_module, "terminate_owned_process") as terminate:
        threads = [
            threading.Thread(target=invoke, args=(lambda: desktop_module._remember_owned_api(process),), daemon=True),
            threading.Thread(target=invoke, args=(desktop_module.close_owned_api,), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("concurrent lifecycle worker exceeded its timeout")
    if errors:
        raise RuntimeError(f"concurrent lifecycle worker failed: {errors[0]!r}")
    terminate.assert_called_once_with(process)
    if desktop_module._api_process is not None:
        raise RuntimeError("concurrent lifecycle probe leaked the API process handle")


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
        context = multiprocessing.get_context("spawn")
        worker = context.Process(target=_run_concurrent_registration_close_probe)
        started = False

        def stop_worker() -> bool:
            if not worker.is_alive():
                return True
            worker.terminate()
            worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=2)
            return not worker.is_alive()

        try:
            worker.start()
            started = True
            worker.join(timeout=4)
            if worker.is_alive():
                if not stop_worker():
                    self.fail("concurrent lifecycle child could not be stopped after terminate/kill")
                self.fail("concurrent lifecycle child exceeded its timeout")
            self.assertEqual(worker.exitcode, 0, f"concurrent lifecycle child failed with exit code {worker.exitcode}")
        finally:
            if started:
                if worker.is_alive() and not stop_worker():
                    self.fail("concurrent lifecycle child remained alive during cleanup")
                if not worker.is_alive():
                    worker.close()

    def test_close_with_no_active_jobs_authorizes_owned_cleanup(self) -> None:
        prompts: list[dict] = []
        controller = DesktopCloseController(lambda: 0, lambda _timeout: (True, "ok"), prompts.append)
        self.assertTrue(controller.request_window_close())
        self.assertTrue(controller.cleanup_allowed)
        self.assertEqual(prompts, [])

    def test_close_rechecks_owned_api_admission_before_authorizing_cleanup(self) -> None:
        prompts: list[dict] = []
        controller = DesktopCloseController(
            lambda: 0,
            lambda _timeout: (True, "ok"),
            prompts.append,
            prepare_close=lambda: (False, 1, "Job mới vừa vào hàng đợi."),
        )
        self.assertFalse(controller.request_window_close())
        self.assertFalse(controller.cleanup_allowed)
        self.assertEqual(controller.state, "prompted")
        self.assertEqual(prompts[-1]["active_jobs"], 1)
        self.assertIn("Job mới", prompts[-1]["message"])

    def test_close_with_active_jobs_has_exact_return_cancel_background_decision_gate(self) -> None:
        prompts: list[dict] = []
        controller = DesktopCloseController(lambda: 2, lambda _timeout: (True, "ok"), prompts.append)
        self.assertFalse(controller.request_window_close())
        self.assertFalse(controller.cleanup_allowed)
        self.assertEqual(prompts[-1]["active_jobs"], 2)
        self.assertEqual(controller.return_to_hub()["status"], "completed")
        self.assertEqual(controller.state, "interactive")
        # A failed tray registration is a visible veto: the controller never
        # hides a window unless a Restore/Exit surface is already available.
        result = controller.keep_running_in_background(lambda: (False, "Không có khay"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(controller.state, "interactive")
        self.assertEqual(prompts[-1]["kind"], "error")

    def test_cancel_and_exit_waits_for_terminal_or_keeps_window_open_on_timeout(self) -> None:
        success_done = threading.Event()
        success = DesktopCloseController(lambda: 1, lambda _timeout: (True, "Đã dừng"), lambda _detail: None)
        result = success.cancel_jobs_and_exit(success_done.set)
        self.assertEqual(result["status"], "pending")
        self.assertTrue(success_done.wait(2))
        self.assertTrue(success.cleanup_allowed)

        timeout_prompt = threading.Event()
        timeout = DesktopCloseController(lambda: 1, lambda _timeout: (False, "Job vẫn đang chạy"), lambda _detail: timeout_prompt.set())
        destroyed = threading.Event()
        timeout.cancel_jobs_and_exit(destroyed.set)
        self.assertTrue(timeout_prompt.wait(2))
        self.assertFalse(destroyed.is_set())
        self.assertFalse(timeout.cleanup_allowed)
        self.assertEqual(timeout.state, "prompted")

    def test_cancel_choice_serializes_return_and_background_until_worker_is_terminal(self) -> None:
        cancellation_started = threading.Event()
        release_cancellation = threading.Event()
        destroyed = threading.Event()
        background_called = threading.Event()

        def cancel_and_wait(_timeout: float) -> tuple[bool, str]:
            cancellation_started.set()
            release_cancellation.wait(2)
            return True, "Đã dừng"

        controller = DesktopCloseController(lambda: 1, cancel_and_wait, lambda _detail: None)
        self.assertEqual(controller.cancel_jobs_and_exit(destroyed.set)["status"], "pending")
        self.assertTrue(cancellation_started.wait(1))
        self.assertEqual(controller.return_to_hub()["status"], "pending")
        result = controller.keep_running_in_background(lambda: background_called.set() or (True, "khay"))
        self.assertEqual(result["status"], "pending")
        self.assertFalse(background_called.is_set())
        release_cancellation.set()
        self.assertTrue(destroyed.wait(2))
        self.assertTrue(controller.cleanup_allowed)

    def test_background_exit_restores_then_reuses_close_gate(self) -> None:
        active = [1]
        prompts: list[dict] = []
        restored = threading.Event()
        destroyed = threading.Event()
        controller = DesktopCloseController(lambda: active[0], lambda _timeout: (True, "ok"), prompts.append)
        self.assertEqual(controller.keep_running_in_background(lambda: (True, "Khay sẵn sàng"))["status"], "completed")
        self.assertEqual(controller.state, "background")
        result = controller.request_exit_from_background(restored.set, destroyed.set)
        self.assertEqual(result["status"], "prompted")
        self.assertTrue(restored.is_set())
        self.assertFalse(destroyed.is_set())
        self.assertEqual(prompts[-1]["active_jobs"], 1)
        active[0] = 0
        result = controller.request_exit_from_background(lambda: None, destroyed.set)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(destroyed.wait(1))
        self.assertTrue(controller.cleanup_allowed)

    def test_externally_managed_api_never_receives_owned_backend_cleanup(self) -> None:
        desktop._api_process = None
        with patch.object(desktop.urllib.request, "urlopen") as urlopen:
            desktop.close_owned_idle_backends()
        urlopen.assert_not_called()

    def test_externally_managed_api_never_receives_global_job_cancellation(self) -> None:
        desktop._api_process = None
        with patch.object(desktop, "_cancel_api_jobs_and_wait") as cancel:
            ok, message = desktop._cancel_owned_api_jobs_and_wait(2)
        self.assertFalse(ok)
        self.assertIn("external owner", message)
        cancel.assert_not_called()

    def test_external_api_with_active_jobs_still_uses_the_safe_three_choice_close_gate(self) -> None:
        bridge = desktop.DesktopBridge()
        window = Mock()
        with patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_COMPATIBLE, {"active_jobs": 1})):
            bridge._bind(window)
            self.assertFalse(bridge._request_window_close())
        self.assertIsNotNone(bridge._controller)
        self.assertEqual(bridge._controller.state, "prompted")

    def test_external_zero_job_close_is_allowed_without_owned_cleanup(self) -> None:
        bridge = desktop.DesktopBridge()
        window = Mock()
        with patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_COMPATIBLE, {"active_jobs": 0})):
            bridge._bind(window)
            self.assertTrue(bridge._request_window_close())
        self.assertTrue(bridge._controller.cleanup_allowed)

    def test_external_active_job_never_offers_cancel(self) -> None:
        prompts: list[dict] = []
        controller = DesktopCloseController(
            lambda: {"verification": "verified", "owner": "external", "active_jobs": 2, "can_cancel": False, "message": "external"},
            lambda _timeout: (True, "ok"),
            prompts.append,
        )
        self.assertFalse(controller.request_window_close())
        self.assertEqual(prompts[-1]["active_jobs"], 2)
        self.assertFalse(prompts[-1]["can_cancel"])

    def test_unknown_close_probe_does_not_become_one_active_job(self) -> None:
        prompts: list[dict] = []
        controller = DesktopCloseController(lambda: (_ for _ in ()).throw(RuntimeError("unknown")), lambda _timeout: (True, "ok"), prompts.append)
        self.assertFalse(controller.request_window_close())
        self.assertIsNone(prompts[-1]["active_jobs"])
        self.assertFalse(prompts[-1]["can_cancel"])

    def test_same_installation_old_build_is_not_reused_by_candidate(self) -> None:
        expected = {"product_id": desktop.PRODUCT_ID, "product_version": desktop.PRODUCT_VERSION, "api_protocol_version": desktop.API_PROTOCOL_VERSION, "app_user_model_id": "LocalAIHub.Desktop", "process_owner": "local-ai-hub", "installation_id": "i" * 32}
        payload = {**expected, "build_source_commit": "a" * 40, "build_payload_id": "main-aaaaaaaaaaaa"}
        with patch.object(desktop, "_expected_api_identity", return_value=expected), patch.object(desktop, "_expected_build_identity", return_value=("b" * 40, "main-bbbbbbbbbbbb")):
            self.assertEqual(desktop._classify_api_identity(payload), desktop.API_PROBE_LOCAL_WRONG_BUILD)

    def test_old_build_listener_selects_fallback_port_without_scanning_or_killing_it(self) -> None:
        old = {"product_id": desktop.PRODUCT_ID, "product_version": desktop.PRODUCT_VERSION, "api_protocol_version": desktop.API_PROTOCOL_VERSION, "app_user_model_id": "LocalAIHub.Desktop", "process_owner": "local-ai-hub", "installation_id": "i" * 32, "build_source_commit": "a" * 40, "build_payload_id": "main-aaaaaaaaaaaa"}
        with patch.object(desktop, "_configured_port", return_value=8765), patch.object(desktop, "_probe_api", return_value=(desktop.API_PROBE_LOCAL_WRONG_BUILD, old)), patch.object(desktop, "_free_loopback_port", return_value=52943):
            self.assertEqual(desktop._select_api_port(), (52943, desktop.API_PROBE_LOCAL_WRONG_BUILD))

    def test_close_prompt_falls_back_to_a_visible_three_choice_bridge_page(self) -> None:
        bridge = desktop.DesktopBridge()
        window = Mock()
        fallback_loaded = threading.Event()
        window.evaluate_js.side_effect = RuntimeError("WebView is loading")
        window.load_html.side_effect = lambda _html: fallback_loaded.set()
        bridge._bind(window)
        with patch.object(desktop.time, "sleep", return_value=None):
            bridge._prompt_close({"active_jobs": 2, "verification": "verified", "can_cancel": True, "message": "Hãy chọn", "kind": "attention"})
            self.assertTrue(fallback_loaded.wait(1))
        page = window.load_html.call_args.args[0]
        self.assertIn("Quay lại Hub", page)
        self.assertIn("Hủy jobs và thoát", page)
        self.assertIn("Giữ chạy nền vào khay", page)

    def test_desktop_bridge_exposes_only_the_three_close_choices(self) -> None:
        bridge = desktop.DesktopBridge()
        methods = {
            name for name in dir(bridge)
            if not name.startswith("_") and callable(getattr(bridge, name))
        }
        self.assertEqual(methods, {"return_to_hub", "cancel_jobs_and_exit", "keep_running_in_background"})


if __name__ == "__main__":
    unittest.main()
