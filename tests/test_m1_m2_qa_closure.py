"""Regression coverage for the bounded M1-M2 post-merge QA closure."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.app import update_watchdog
from src.services import runtime_registry
from src.services.api.router_registry import build_router
from src.services.app_update import AppUpdateError, AppUpdateService, _read_update_state


ROOT = Path(__file__).resolve().parents[1]


class UpdaterQaClosureTests(unittest.TestCase):
    def test_staged_projection_is_watchdog_owned_and_not_manually_rollbackable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            value = AppUpdateService()._public_projection(
                {
                    "status": "staged",
                    "current_payload": "main-aaaaaaaaaaaa",
                    "latest_payload": "main-bbbbbbbbbbbb",
                },
                root=root,
            )
        self.assertFalse(value["can_rollback"])
        self.assertEqual(value["rollback_mode"], "automatic_watchdog")
        self.assertFalse(value["can_restart"] is False and value["requires_restart"] is False)

    def test_transient_prepare_failure_publishes_a_real_retry_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = AppUpdateService()
            with patch.object(
                service,
                "_prepare_locked_impl",
                side_effect=AppUpdateError("UPDATE_DOWNLOAD_FAILED"),
            ), patch.object(
                service,
                "_current_build",
                return_value={"payload_id": "main-aaaaaaaaaaaa", "commit": "a" * 40},
            ):
                with self.assertRaisesRegex(AppUpdateError, "UPDATE_DOWNLOAD_FAILED"):
                    service._prepare_locked(root)
            state = _read_update_state(root)
        self.assertEqual(state["phase"], "error")
        self.assertTrue(state["can_prepare"])

    def test_blocked_prepare_failure_does_not_publish_a_retry_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = AppUpdateService()
            with patch.object(
                service,
                "_prepare_locked_impl",
                side_effect=AppUpdateError("ACTIVE_JOBS_BLOCK_UPDATE"),
            ), patch.object(
                service,
                "_current_build",
                return_value={"payload_id": "main-aaaaaaaaaaaa", "commit": "a" * 40},
            ):
                with self.assertRaisesRegex(AppUpdateError, "ACTIVE_JOBS_BLOCK_UPDATE"):
                    service._prepare_locked(root)
            state = _read_update_state(root)
        self.assertEqual(state["phase"], "blocked")
        self.assertFalse(state["can_prepare"])


class WatchdogQaClosureTests(unittest.TestCase):
    def _run_failure_then_recovery(self, *, api_ready: bool) -> dict[str, object]:
        child = object()
        fallback = object()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            update_watchdog,
            "_target_identity",
            side_effect=[("main-bbbbbbbbbbbb", "b" * 40, {}), ("8.0.1", None, {})],
        ), patch.object(update_watchdog, "_launch_stable", side_effect=[child, fallback]), patch.object(
            update_watchdog,
            "_wait_for_target_health",
            side_effect=[False, True],
        ), patch.object(update_watchdog, "_health_matches", return_value=api_ready), patch.object(
            update_watchdog,
            "_rollback_previous",
            return_value=True,
        ) as rollback, patch.object(update_watchdog, "terminate_owned_process"), patch.object(
            update_watchdog,
            "_try_write_update_state",
        ) as write_state:
            result = update_watchdog.run(app_root=Path(temporary), wait_pid=0, timeout_seconds=5)
        self.assertEqual(result["status"], "rolled_back")
        rollback.assert_called_once_with(
            Path(temporary),
            reason="WATCHDOG_POST_RESTART_HEALTH_FAILED",
            reason_code="frontend_readiness_timeout" if api_ready else "api_readiness_timeout",
        )
        final_kwargs = write_state.call_args.kwargs
        self.assertEqual(final_kwargs["phase"], "rolled_back")
        self.assertEqual(
            final_kwargs["reason_code"],
            "frontend_readiness_timeout" if api_ready else "api_readiness_timeout",
        )
        self.assertEqual(final_kwargs["last_error_code"], "WATCHDOG_POST_RESTART_HEALTH_FAILED")
        return result

    def test_healthy_api_but_missing_frontend_handshake_records_frontend_timeout(self) -> None:
        self._run_failure_then_recovery(api_ready=True)

    def test_missing_candidate_api_records_api_timeout(self) -> None:
        self._run_failure_then_recovery(api_ready=False)

    def test_previous_payload_failure_is_the_only_previous_relaunch_failure_reason(self) -> None:
        child = object()
        fallback = object()
        with tempfile.TemporaryDirectory() as temporary, patch.object(
            update_watchdog,
            "_target_identity",
            side_effect=[("main-bbbbbbbbbbbb", "b" * 40, {}), ("8.0.1", None, {})],
        ), patch.object(update_watchdog, "_launch_stable", side_effect=[child, fallback]), patch.object(
            update_watchdog,
            "_wait_for_target_health",
            side_effect=[False, False],
        ), patch.object(update_watchdog, "_health_matches", return_value=False), patch.object(
            update_watchdog,
            "_rollback_previous",
            return_value=True,
        ), patch.object(update_watchdog, "terminate_owned_process"), patch.object(
            update_watchdog,
            "_try_write_update_state",
        ) as write_state:
            result = update_watchdog.run(app_root=Path(temporary), wait_pid=0, timeout_seconds=5)
        self.assertEqual(result["status"], "failed")
        final_kwargs = write_state.call_args.kwargs
        self.assertEqual(final_kwargs["phase"], "error")
        self.assertEqual(final_kwargs["reason_code"], "previous_payload_relaunch_failed")


class AiriProcessIdentityQaClosureTests(unittest.TestCase):
    def _candidate(self, directory: Path) -> runtime_registry._Candidate:
        executable = directory / "airi.exe"
        executable.write_bytes(b"verified-airi")
        fingerprint = hashlib.sha256(executable.read_bytes()).hexdigest()
        return runtime_registry._Candidate(
            executable=executable,
            working_directory=directory,
            arguments=(),
            fingerprint=fingerprint,
            source="local_registry",
            expected_product="AIRI",
            expected_publisher="Moeru AI",
        )

    def _state(self, candidate: runtime_registry._Candidate, snapshot: tuple[frozenset[str], frozenset[str], bool]) -> str:
        with patch.object(
            runtime_registry,
            "_read_file_identity",
            return_value={"product_name": "AIRI", "publisher": "Moeru AI"},
        ), patch.object(runtime_registry, "_file_fingerprint", return_value=candidate.fingerprint), patch.object(
            runtime_registry,
            "_running_process_snapshot",
            return_value=snapshot,
        ):
            return runtime_registry._candidate_running_state(candidate, force=True)

    def test_same_basename_at_wrong_path_is_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            candidate = self._candidate(directory)
            wrong = directory / "other" / candidate.executable.name
            wrong.parent.mkdir()
            wrong.write_bytes(b"other")
            state = self._state(
                candidate,
                (frozenset({runtime_registry._normalize_process_path(str(wrong))}), frozenset(), False),
            )
        self.assertEqual(state, "not_running")

    def test_exact_verified_path_is_running(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            state = self._state(
                candidate,
                (frozenset({runtime_registry._normalize_process_path(str(candidate.executable))}), frozenset(), False),
            )
        self.assertEqual(state, "running")

    def test_process_path_access_denied_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            state = self._state(candidate, (frozenset(), frozenset({"airi.exe"}), False))
        self.assertEqual(state, "unknown")

    def test_changed_candidate_fingerprint_is_not_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            candidate = self._candidate(Path(temporary))
            with patch.object(
                runtime_registry,
                "_read_file_identity",
                return_value={"product_name": "AIRI", "publisher": "Moeru AI"},
            ), patch.object(runtime_registry, "_file_fingerprint", return_value="b" * 64), patch.object(
                runtime_registry,
                "_running_process_snapshot",
                side_effect=AssertionError("changed candidate must fail before process enumeration"),
            ):
                state = runtime_registry._candidate_running_state(candidate, force=True)
        self.assertEqual(state, "not_running")


class AiriAndAcceptanceQaClosureTests(unittest.TestCase):
    def test_renderer_has_one_primary_launch_action_and_uses_running_state(self) -> None:
        renderer = (ROOT / "src" / "ui" / "features" / "airi" / "render.js").read_text(encoding="utf-8")
        self.assertEqual(renderer.count('data-launch="airi"'), 1)
        self.assertIn("running_state", renderer)
        self.assertNotIn("item.running ?", renderer)

    def test_normal_update_card_has_no_manual_rollback_action(self) -> None:
        module = (ROOT / "src" / "ui" / "app_update.js").read_text(encoding="utf-8")
        self.assertNotIn("data-app-update-rollback", module)
        self.assertNotIn("API.rollback", module)
        self.assertNotIn("rollback(card)", module)

    def test_application_launch_route_declares_actual_statuses(self) -> None:
        route = next(item for item in build_router().routes() if item.route_id == "applications.launch")
        self.assertEqual(route.status_codes, (202, 404, 409, 500, 503))
        inventory = json.loads((ROOT / "architecture" / "api_routes.yaml").read_text(encoding="utf-8"))
        row = next(item for item in inventory["routes"] if item["route_id"] == "applications.launch")
        self.assertEqual(row["status_codes"], [202, 404, 409, 500, 503])

    def test_airi_close_route_and_ui_are_ownership_gated(self) -> None:
        route = next(item for item in build_router().routes() if item.route_id == "applications.close")
        self.assertEqual(route.method, "POST")
        self.assertEqual(route.path, "/api/applications/{application_id}/instances/{instance_id}/close")
        self.assertEqual(route.status_codes, (200, 400, 404, 409, 500, 503))
        inventory = json.loads((ROOT / "architecture" / "api_routes.yaml").read_text(encoding="utf-8"))
        row = next(item for item in inventory["routes"] if item["route_id"] == "applications.close")
        self.assertEqual(row["path"], route.path)
        self.assertEqual(row["status_codes"], [200, 400, 404, 409, 500, 503])

        renderer = (ROOT / "src" / "ui" / "features" / "airi" / "render.js").read_text(encoding="utf-8")
        api = (ROOT / "src" / "ui" / "api.js").read_text(encoding="utf-8")
        app = (ROOT / "src" / "ui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("item.close_available === true && instanceId", renderer)
        self.assertIn('data-close-application="airi"', renderer)
        self.assertIn("AIRI đang chạy ngoài quyền quản lý của Hub", renderer)
        self.assertIn("Sẵn sàng để mở · AIRI chưa chạy.", renderer)
        self.assertIn("export const closeApplication", api)
        self.assertIn("closeApplicationButton", app)
        self.assertNotIn('data-close-airi="', renderer)

    def test_airi_launch_scope_is_required_by_webview_not_runtime_smoke(self) -> None:
        from scripts.v8_acceptance_gate import _GATE_IMPACT_SCOPES, _path_impact_scope, load_gate_contract

        self.assertEqual(_path_impact_scope("src/services/runtime_registry.py"), "application_launch")
        self.assertEqual(_path_impact_scope("src/ui/features/airi/render.js"), "application_launch")
        self.assertIn("application_launch", _GATE_IMPACT_SCOPES["webview2_product_ux"])
        self.assertNotIn("application_launch", _GATE_IMPACT_SCOPES["runtime_smoke"])
        contract = load_gate_contract(ROOT / "architecture" / "v8_acceptance_gates.json")
        webview = next(item for item in contract["gates"] if item["gate_id"] == "webview2_product_ux")
        self.assertIn("airi_verified_native_launch", webview["required_checks"])


if __name__ == "__main__":
    unittest.main()
