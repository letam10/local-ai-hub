"""Source regression for the payload-entrypoint rollback handoff.

This test is intentionally kept separate from native/candidate execution.  It
uses identity-bearing candidate/previous entrypoint doubles so the launcher
contract can prove that a rolled-back candidate never reaches its own desktop
``main`` and that the selected previous payload is the one relaunched.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.app import launcher


def _plan(version: str, source_commit: str) -> SimpleNamespace:
    payload_root = Path("fixture-install") / "versions" / version
    app_payload = payload_root / "app"
    runtime = payload_root / "runtime" / "Python312" / "pythonw.exe"
    return SimpleNamespace(
        version=version,
        payload_root=payload_root,
        app_payload=app_payload,
        runtime_pythonw=runtime,
        command=(str(runtime), "-m", "src.app.launcher"),
        environment={
            "LOCALAIHUB_BUILD_SHA": source_commit,
            "LOCALAIHUB_BUILD_PAYLOAD": version,
            "LOCALAIHUB_INSTALL_ROOT": str(Path("fixture-install")),
            "LOCALAIHUB_APP_ROOT": str(app_payload),
            "LOCALAIHUB_WATCHDOG_APP_ROOT": "stale-watchdog-candidate",
            "LOCALAIHUB_RESTART_SESSION_NONCE": "stale-session",
        },
    )


class Post124LauncherHandoffRegressionTests(unittest.TestCase):
    def test_rollback_handoff_skips_candidate_main_and_selects_previous_main(self) -> None:
        candidate = _plan("main-aaaaaaaaaaaa", "a" * 40)
        previous = _plan("main-bbbbbbbbbbbb", "b" * 40)
        events: list[tuple[str, str]] = []
        spawned: list[dict[str, object]] = []

        def candidate_bootstrap() -> None:
            events.append(("candidate-bootstrap", candidate.version))

        def candidate_main() -> int:
            events.append(("candidate-main", candidate.version))
            return 0

        def previous_main() -> int:
            events.append(("previous-main", previous.version))
            return 0

        def recovery() -> dict[str, object]:
            return {
                "status": "reconciled",
                "restart": {"status": "rolled_back"},
                "shell": {"status": "not_pending"},
            }

        def fake_popen(command: list[str], **kwargs: object) -> SimpleNamespace:
            environment = kwargs["env"]
            assert isinstance(environment, dict)
            spawned.append({"command": command, "environment": dict(environment), "cwd": kwargs["cwd"]})
            if environment.get("LOCALAIHUB_BUILD_PAYLOAD") != previous.version:
                raise AssertionError("rollback handoff selected the candidate payload")
            previous_main()
            return SimpleNamespace(pid=4242, poll=lambda: None)

        install_root = Path("fixture-install").absolute()
        with (
            patch.dict(
                os.environ,
                {
                    "LOCALAIHUB_INSTALL_ROOT": str(install_root),
                    "LOCALAIHUB_APP_ROOT": str(candidate.app_payload.absolute()),
                    "LOCALAIHUB_RESTART_WAIT_PID": "",
                },
                clear=False,
            ),
            patch.object(launcher, "_reconcile_startup_transactions", side_effect=recovery),
            patch.object(launcher, "resolve_verified_running_plan", side_effect=AssertionError("candidate resolver must not gate confirmed rollback")) as candidate_resolver,
            patch.object(launcher, "resolve_launch_plan", return_value=previous),
            patch.object(launcher.subprocess, "Popen", side_effect=fake_popen),
            patch.object(launcher, "bootstrap", side_effect=candidate_bootstrap),
            patch.object(launcher, "main", side_effect=candidate_main),
        ):
            result = launcher.launch()

        self.assertEqual(result, 0)
        candidate_resolver.assert_not_called()
        self.assertNotIn(("candidate-main", candidate.version), events)
        self.assertNotIn(("candidate-bootstrap", candidate.version), events)
        self.assertEqual(events, [("previous-main", previous.version)])
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0]["command"], list(previous.command))
        self.assertEqual(spawned[0]["cwd"], str(previous.app_payload))
        child_environment = spawned[0]["environment"]
        self.assertEqual(child_environment["LOCALAIHUB_BUILD_PAYLOAD"], previous.version)
        self.assertEqual(child_environment["LOCALAIHUB_BUILD_SHA"], "b" * 40)
        self.assertEqual(child_environment["LOCALAIHUB_RESTART_WAIT_PID"], str(os.getpid()))
        self.assertNotIn("LOCALAIHUB_WATCHDOG_APP_ROOT", child_environment)
        self.assertNotIn("LOCALAIHUB_RESTART_SESSION_NONCE", child_environment)

    def test_confirmed_rollback_with_invalid_previous_fails_closed(self) -> None:
        install_root = Path("fixture-install").absolute()
        recovery = {"restart": {"status": "rolled_back"}}
        with (
            patch.object(launcher, "resolve_verified_running_plan", side_effect=AssertionError("candidate must not be inspected")) as candidate_resolver,
            patch.object(launcher, "resolve_launch_plan", side_effect=ValueError("previous payload invalid")) as selected_resolver,
            patch.object(launcher.subprocess, "Popen") as popen,
        ):
            with self.assertRaisesRegex(ValueError, "previous payload invalid"):
                launcher._handoff_after_recovery(install_root, recovery)

        candidate_resolver.assert_not_called()
        selected_resolver.assert_called_once_with(install_root)
        popen.assert_not_called()

    def test_unconfirmed_recovery_does_not_swallow_candidate_resolver_error(self) -> None:
        candidate = _plan("main-aaaaaaaaaaaa", "a" * 40)
        install_root = Path("fixture-install").absolute()

        def recovery() -> dict[str, object]:
            return {"status": "reconciled", "restart": {"status": "candidate_pending_health"}}

        def candidate_bootstrap() -> None:
            raise AssertionError("candidate bootstrap must not run")

        def candidate_main() -> int:
            raise AssertionError("candidate main must not run")

        with (
            patch.dict(
                os.environ,
                {
                    "LOCALAIHUB_INSTALL_ROOT": str(install_root),
                    "LOCALAIHUB_APP_ROOT": str(candidate.app_payload.absolute()),
                    "LOCALAIHUB_RESTART_WAIT_PID": "",
                },
                clear=False,
            ),
            patch.object(launcher, "_reconcile_startup_transactions", side_effect=recovery),
            patch.object(launcher, "resolve_verified_running_plan", side_effect=ValueError("candidate payload invalid")) as candidate_resolver,
            patch.object(launcher, "resolve_launch_plan", side_effect=AssertionError("selected resolver must not hide candidate failure")) as selected_resolver,
            patch.object(launcher.subprocess, "Popen") as popen,
            patch.object(launcher, "bootstrap", side_effect=candidate_bootstrap) as bootstrap_call,
            patch.object(launcher, "main", side_effect=candidate_main) as main_call,
        ):
            result = launcher.launch()

        self.assertEqual(result, 81)
        candidate_resolver.assert_called_once()
        selected_resolver.assert_not_called()
        popen.assert_not_called()
        bootstrap_call.assert_not_called()
        main_call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
