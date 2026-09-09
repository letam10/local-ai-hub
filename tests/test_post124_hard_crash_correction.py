"""Hard-termination verification for PR #124 shell and pointer journals.

The mutating child exits with ``os._exit`` at a named filesystem boundary.
The parent then starts a new Python process for reconciliation and checks the
root shell/pointer before and after two additional recovery passes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from src.app.launcher_migration import (
    LAUNCHER_EXECUTABLE_NAME,
    LAUNCHER_INTERNAL_NAME,
    activate_launcher_bundle,
    launcher_projection,
    launcher_tree_manifest,
    reconcile_launcher_transaction,
    restore_launcher_bundle,
)
from src.app.update_watchdog import _activate_deferred_product, reconcile_restart_transaction


REPO = Path(__file__).resolve().parents[1]
CRASH_BOUNDARIES = (
    "after_transaction_journal",
    "after_candidate_copy",
    "after_previous_exe_backup",
    "after_previous_internal_backup",
    "after_candidate_internal_preparation",
    "after_candidate_internal_install",
    "before_final_exe_replacement",
    "after_final_exe_replacement",
    "before_shell_manifest_publication",
    "after_shell_manifest_publication",
)
ROLLBACK_BOUNDARIES = (
    "rollback_before_exe_replacement",
    "rollback_after_exe_replacement",
    "rollback_after_previous_exe_restore",
    "rollback_after_previous_internal_restore",
)
POINTER_BOUNDARIES = (
    "during_pending_health_write",
    "after_health_intent_before_shell",
    "before_pointer_switch",
    "after_pointer_switch",
    "after_pointer_before_health_cleanup",
)


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _shell_fixture(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True)
    (root / LAUNCHER_EXECUTABLE_NAME).write_bytes(b"legacy-shell")
    candidate = root / "versions" / "main-aaaaaaaaaaaa" / "launcher" / "LocalAIHub"
    (candidate / LAUNCHER_INTERNAL_NAME).mkdir(parents=True)
    (candidate / LAUNCHER_EXECUTABLE_NAME).write_bytes(b"candidate-shell")
    (candidate / LAUNCHER_INTERNAL_NAME / "support.dll").write_bytes(b"candidate-support")
    return candidate, "txn-" + "a" * 32


def _run_child(mode: str, root: Path, boundary: str = "") -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    if boundary:
        env["LOCALAIHUB_TEST_HARD_CRASH_BOUNDARY"] = boundary
    else:
        env.pop("LOCALAIHUB_TEST_HARD_CRASH_BOUNDARY", None)
    return subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--child", mode, str(root)],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=30,
    )


def _child_main(mode: str, root: Path) -> int:
    if mode == "launcher-entrypoint":
        # Exercise the shipped payload entrypoint.  Only the normal bootstrap
        # and desktop main are replaced so this child performs real startup
        # reconciliation against the fixture journals.
        os.environ["LOCALAIHUB_INSTALL_ROOT"] = str(root)
        os.environ["LOCALAIHUB_APP_ROOT"] = str(root)
        os.environ["LOCALAIHUB_DATA_ROOT"] = str(root)
        from src.app import launcher

        launcher.bootstrap = lambda: None
        launcher.main = lambda: 0
        return launcher.launch()
    if mode == "activate":
        candidate = root / "versions" / "main-aaaaaaaaaaaa" / "launcher" / "LocalAIHub"
        activate_launcher_bundle(
            root, candidate, transaction_id="txn-" + "a" * 32,
            payload_id="main-aaaaaaaaaaaa", source_commit="a" * 40, workflow_run_id=123,
        )
        return 0
    if mode == "recover-launcher":
        result = reconcile_launcher_transaction(root, transaction_id="txn-" + "a" * 32)
        _write_json(root / "child-recovery.json", result)
        return 0
    if mode == "restore":
        restore_launcher_bundle(root, transaction_id="txn-" + "a" * 32)
        return 0
    if mode == "activate-pointer":
        from tests.test_post123_manager_corrections import Post123ManagerCorrectionTests

        transaction = json.loads((root / "update-state" / "restart-transaction.json").read_text(encoding="utf-8"))
        _activate_deferred_product(root, transaction)
        return 0
    if mode == "recover-pointer":
        result = reconcile_restart_transaction(root)
        transaction_path = root / "update-state" / "restart-transaction.json"
        if transaction_path.is_file():
            transaction = json.loads(transaction_path.read_text(encoding="utf-8"))
            if result.get("status") in {"prepared", "candidate_pending_health"}:
                _activate_deferred_product(root, transaction)
        _write_json(root / "child-recovery.json", result)
        return 0
    raise AssertionError(mode)


class Post124HardCrashTests(unittest.TestCase):
    def _assert_shell_recovery(self, root: Path, boundary: str) -> None:
        result = _run_child("activate", root, boundary)
        self.assertEqual(result.returncode, 197, (boundary, result.stdout, result.stderr))
        self.assertTrue((root / LAUNCHER_EXECUTABLE_NAME).is_file(), boundary)
        self.assertTrue((root / "versions" / "main-aaaaaaaaaaaa" / "launcher" / "LocalAIHub" / "LocalAIHub.exe").is_file(), boundary)
        recovery = _run_child("launcher-entrypoint", root)
        self.assertEqual(recovery.returncode, 0, (boundary, recovery.stdout, recovery.stderr))
        self.assertTrue((root / LAUNCHER_EXECUTABLE_NAME).is_file(), boundary)
        first_bytes = (root / LAUNCHER_EXECUTABLE_NAME).read_bytes()
        for _ in range(2):
            second = reconcile_launcher_transaction(root, transaction_id="txn-" + "a" * 32)
            self.assertIn(second["status"], {"not_pending", "already_reconciled", "already_restored", "restored", "shell_switched", "retry_activation"}, boundary)
            self.assertTrue((root / LAUNCHER_EXECUTABLE_NAME).is_file(), boundary)
            self.assertEqual((root / LAUNCHER_EXECUTABLE_NAME).read_bytes(), first_bytes, boundary)
        self.assertEqual((root / "user-marker.txt").read_bytes(), b"keep", boundary)

    def test_hard_crash_activation_boundaries_keep_root_route_and_converge(self) -> None:
        for boundary in CRASH_BOUNDARIES:
            with self.subTest(boundary=boundary), TemporaryDirectory(dir=REPO / "Temp", prefix="post124-crash-shell-") as temporary:
                root = Path(temporary) / "install"
                _shell_fixture(root)
                (root / "user-marker.txt").write_bytes(b"keep")
                self._assert_shell_recovery(root, boundary)

    def test_hard_crash_rollback_boundaries_keep_previous_shell(self) -> None:
        for boundary in ROLLBACK_BOUNDARIES:
            with self.subTest(boundary=boundary), TemporaryDirectory(dir=REPO / "Temp", prefix="post124-crash-rollback-") as temporary:
                root = Path(temporary) / "install"
                candidate, transaction = _shell_fixture(root)
                activate_launcher_bundle(root, candidate, transaction_id=transaction, payload_id="main-aaaaaaaaaaaa", source_commit="a" * 40, workflow_run_id=123)
                result = _run_child("restore", root, boundary)
                self.assertEqual(result.returncode, 197, (boundary, result.stdout, result.stderr))
                self.assertTrue((root / LAUNCHER_EXECUTABLE_NAME).is_file())
                recovery = _run_child("launcher-entrypoint", root)
                self.assertEqual(recovery.returncode, 0, (boundary, recovery.stdout, recovery.stderr))
                self.assertEqual((root / LAUNCHER_EXECUTABLE_NAME).read_bytes(), b"legacy-shell")
                self.assertEqual(launcher_projection(root)["format"], "single_file")
                self.assertEqual(reconcile_launcher_transaction(root, transaction_id=transaction)["status"], "already_reconciled")

    def test_candidate_pointer_without_health_intent_is_refused_by_new_recovery_process(self) -> None:
        from tests.test_post123_manager_corrections import Post123ManagerCorrectionTests

        with TemporaryDirectory(dir=REPO / "Temp", prefix="post124-pointer-no-intent-") as temporary:
            fixture = Post123ManagerCorrectionTests._deferred_fixture(Path(temporary) / "install")
            root = Path(fixture["root"])
            transaction = dict(fixture["transaction"])
            _write_json(root / "current.json", {
                "schema_version": transaction["previous"]["schema_version"],
                "version": transaction["payload_id"],
                "payload_relative": f"versions/{transaction['payload_id']}",
                "manifest_sha256": transaction["manifest_sha256"],
            })
            child = _run_child("launcher-entrypoint", root)
            self.assertEqual(child.returncode, 0, (child.stdout, child.stderr))
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8"))["version"], fixture["previous"]["version"])

    def test_hard_crash_pointer_boundaries_leave_intent_and_recover(self) -> None:
        from tests.test_post123_manager_corrections import Post123ManagerCorrectionTests

        for boundary in POINTER_BOUNDARIES:
            with self.subTest(boundary=boundary), TemporaryDirectory(dir=REPO / "Temp", prefix="post124-pointer-crash-") as temporary:
                fixture = Post123ManagerCorrectionTests._deferred_fixture(Path(temporary) / "install")
                root = Path(fixture["root"])
                child = _run_child("activate-pointer", root, boundary)
                self.assertEqual(child.returncode, 198, (boundary, child.stdout, child.stderr))
                self.assertTrue((root / "LocalAIHub.exe").is_file(), boundary)
                recovery = _run_child("launcher-entrypoint", root)
                self.assertEqual(recovery.returncode, 0, (boundary, recovery.stdout, recovery.stderr))
                self.assertTrue((root / "LocalAIHub.exe").is_file(), boundary)
                first = reconcile_restart_transaction(root)["status"]
                second = reconcile_restart_transaction(root)["status"]
                self.assertEqual(first, second, boundary)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--child":
        raise SystemExit(_child_main(sys.argv[2], Path(sys.argv[3])))
    unittest.main()
