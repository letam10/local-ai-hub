from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from scripts.build_main_update import build
import src.app.launcher_migration as launcher_migration
import src.app.update_watchdog as update_watchdog
from src.app.launcher_migration import LauncherMigrationError, activate_launcher_bundle, launcher_tree_manifest, launcher_projection, restore_launcher_bundle
from src.app.stable_shell import POINTER_SCHEMA, VERSION_MANIFEST_SCHEMA
from src.app.update_watchdog import _activate_deferred_product
import src.services.app_update as app_update_module
from src.services.app_update import (
    COMPOSITE_UPDATE_ARTIFACT_NAME,
    LEGACY_UPDATE_ARTIFACT_NAME,
    UPDATE_KIND_APP_AND_LAUNCHER,
    AppUpdateError,
    AppUpdateService,
    UpdateCandidate,
    _read_bootstrap_pending,
    _write_bootstrap_pending,
)
from src.services.shortcut_migration import (
    LEGACY_VBS_CONTENT_SHA256,
    LEGACY_VBS_MARKER,
    MIGRATION_ADMIN_REQUIRED,
    MIGRATION_AMBIGUOUS,
    MIGRATION_ALREADY_CORRECT,
    MIGRATION_UNOWNED,
    MIGRATION_UPDATE_REQUIRED,
    migration_complete,
    normal_launch_command,
    plan_shortcut_migration,
)


REPO = Path(__file__).resolve().parents[1]


class _ArtifactTransport:
    def __init__(self, artifacts: list[dict[str, object]]) -> None:
        self.artifacts = artifacts

    def auth_state(self):
        return SimpleNamespace(status="ready", code=None, transport="fixture")

    def api_json(self, endpoint: str, _fields=None):
        if endpoint.endswith("/runs"):
            return {"workflow_runs": [{"id": 123, "head_sha": "a" * 40, "head_branch": "main", "conclusion": "success"}]}
        return {"artifacts": self.artifacts}


class Post123ManagerCorrectionTests(unittest.TestCase):
    BASE_MAIN = "de616f4c4ad95dd74f1bdb22a68ea7be32efb9e7"

    @staticmethod
    def _shell_fixture(root: Path) -> tuple[Path, Path, str]:
        root.mkdir(parents=True)
        (root / "LocalAIHub.exe").write_bytes(b"legacy")
        candidate = root / "versions" / ("main-" + "a" * 12) / "launcher" / "LocalAIHub"
        (candidate / "_internal").mkdir(parents=True)
        (candidate / "LocalAIHub.exe").write_bytes(b"candidate")
        (candidate / "_internal" / "support.dll").write_bytes(b"support")
        return candidate, root / "LocalAIHub.exe", "txn-" + "a" * 32

    @staticmethod
    def _write_json(path: Path, value: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))

    @classmethod
    def _base_main_parser_functions(cls):
        """Load the exact BASE_MAIN parser functions, not the new parser."""

        result = subprocess.run(
            ["git", "show", f"{cls.BASE_MAIN}:src/services/app_update.py"],
            cwd=REPO,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError("BASE_MAIN parser source is unavailable")
        tree = ast.parse(result.stdout.decode("utf-8"), filename="BASE_MAIN:src/services/app_update.py")
        names = {"_safe_update_manifest", "_safe_update_contract", "_latest_candidate"}
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        if {node.name for node in functions} != names:
            raise AssertionError("BASE_MAIN parser functions are incomplete")
        namespace = {
            "Any": object,
            "AppUpdateError": AppUpdateError,
            "UpdateCandidate": UpdateCandidate,
            "re": re,
            "REPOSITORY": app_update_module.REPOSITORY,
            "WORKFLOW_FILE": app_update_module.WORKFLOW_FILE,
            "UPDATE_ARTIFACT_NAME": app_update_module.UPDATE_ARTIFACT_NAME,
            "UPDATE_SCHEMA": app_update_module.UPDATE_SCHEMA,
            "UPDATE_CONTRACT_SCHEMA": app_update_module.UPDATE_CONTRACT_SCHEMA,
            "PRODUCT_ID": app_update_module.PRODUCT_ID,
            "PRODUCT_VERSION": app_update_module.PRODUCT_VERSION,
            "RUNTIME_STRATEGY": app_update_module.RUNTIME_STRATEGY,
            "RUNTIME_STRATEGY_BUNDLED": app_update_module.RUNTIME_STRATEGY_BUNDLED,
            "MAX_ARCHIVE_FILES": app_update_module.MAX_ARCHIVE_FILES,
            "_SHA_RE": app_update_module._SHA_RE,
            "_PAYLOAD_RE": app_update_module._PAYLOAD_RE,
            "_SHA256_RE": app_update_module._SHA256_RE,
            "_RUNTIME_HASH_RE": app_update_module._RUNTIME_HASH_RE,
            "API_PROTOCOL_VERSION": app_update_module.API_PROTOCOL_VERSION,
            "MINIMUM_LAUNCHER_VERSION": app_update_module.MINIMUM_LAUNCHER_VERSION,
            "UPDATE_KIND_APP_ONLY": app_update_module.UPDATE_KIND_APP_ONLY,
            "UPDATE_KIND_FULL": app_update_module.UPDATE_KIND_FULL,
            "RUNTIME_CONTRACT_REUSE_CURRENT": app_update_module.RUNTIME_CONTRACT_REUSE_CURRENT,
            "RUNTIME_CONTRACT_BUNDLED": app_update_module.RUNTIME_CONTRACT_BUNDLED,
        }
        module = ast.Module(
            body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *functions],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        exec(compile(module, "BASE_MAIN:src/services/app_update.py", "exec"), namespace)
        return namespace["_safe_update_manifest"], namespace["_safe_update_contract"], namespace["_latest_candidate"]

    @staticmethod
    def _install_metadata(root: Path) -> None:
        data = root / "data"
        data.mkdir(parents=True, exist_ok=True)
        Post123ManagerCorrectionTests._write_json(root / "installation.json", {
            "schema_version": "v8.0.1-installation.v1", "product_id": "LocalAIHub", "app_root": str(root),
            "data_root": str(data), "app_user_model_id": "LocalAIHub.Desktop", "launcher": "LocalAIHub.exe",
        })
        Post123ManagerCorrectionTests._write_json(root / "product.json", {
            "schema_version": "v8.0.1-product.v1", "product_id": "LocalAIHub", "version": "8.0.1",
            "launcher": "LocalAIHub.exe", "icon": "local-ai-hub.ico", "current_pointer": "current.json",
        })
        (root / "local-ai-hub.ico").write_bytes(b"icon")

    @classmethod
    def _deferred_fixture(cls, root: Path, *, previous_format: str = "single_file") -> dict[str, object]:
        cls._install_metadata(root)
        (root / "LocalAIHub.exe").write_bytes(b"legacy-shell")
        if previous_format == "onedir":
            (root / "_internal").mkdir()
            (root / "_internal" / "legacy-support.dll").write_bytes(b"legacy-support")
            shell_manifest = launcher_migration._shell_tree_manifest(root)
            cls._write_json(root / "launcher-manifest.json", {
                "schema_version": "local-ai-hub-launcher-bundle.v1", "product_id": "LocalAIHub",
                "payload_id": "main-bbbbbbbbbbbb", "source_commit": "b" * 40, "format": "onedir",
                "executable": "LocalAIHub.exe", "workflow_run_id": 123,
                "executable_sha256": shell_manifest["executable_sha256"],
                "tree_manifest_sha256": shell_manifest["tree_manifest_sha256"],
                "file_count": shell_manifest["file_count"], "total_bytes": shell_manifest["total_bytes"],
            })
        previous_payload = root / "versions" / "main-bbbbbbbbbbbb"
        (previous_payload / "app").mkdir(parents=True)
        (previous_payload / "runtime" / "Python312").mkdir(parents=True)
        (previous_payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"runtime")
        (previous_payload / "app" / "previous-marker.txt").write_bytes(b"previous-payload")
        previous_manifest = {
            "schema_version": VERSION_MANIFEST_SCHEMA, "product_id": "LocalAIHub", "version": "main-bbbbbbbbbbbb",
            "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe", "entrypoint": "src.app.launcher",
        }
        cls._write_json(previous_payload / "manifest.json", previous_manifest)
        previous_pointer = {
            "schema_version": POINTER_SCHEMA, "version": "main-bbbbbbbbbbbb",
            "payload_relative": "versions/main-bbbbbbbbbbbb",
            "manifest_sha256": hashlib.sha256((previous_payload / "manifest.json").read_bytes()).hexdigest(),
        }
        cls._write_json(root / "current.json", previous_pointer)

        payload_id = "main-aaaaaaaaaaaa"
        source_commit = "a" * 40
        candidate_payload = root / "versions" / payload_id
        (candidate_payload / "app").mkdir(parents=True)
        (candidate_payload / "runtime" / "Python312").mkdir(parents=True)
        (candidate_payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"candidate-runtime")
        cls._write_json(candidate_payload / "manifest.json", {
            "schema_version": VERSION_MANIFEST_SCHEMA, "product_id": "LocalAIHub", "version": payload_id,
            "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe", "entrypoint": "src.app.launcher",
        })
        cls._write_json(candidate_payload / "build.json", {
            "schema_version": "local-ai-hub-build-info.v1", "source_commit": source_commit,
            "workflow_run_id": 123, "channel": "main", "product_version": "8.0.1",
        })
        candidate_pointer = {
            "schema_version": POINTER_SCHEMA, "version": payload_id,
            "payload_relative": f"versions/{payload_id}",
            "manifest_sha256": hashlib.sha256((candidate_payload / "manifest.json").read_bytes()).hexdigest(),
        }
        transaction_id = "txn-" + "a" * 32
        candidate = candidate_payload / "launcher" / "LocalAIHub"
        (candidate / "_internal").mkdir(parents=True)
        (candidate / "LocalAIHub.exe").write_bytes(b"candidate-shell")
        (candidate / "_internal" / "support.dll").write_bytes(b"candidate-support")
        launcher_manifest = launcher_tree_manifest(candidate)
        transaction = {
            "schema_version": update_watchdog.RESTART_TRANSACTION_SCHEMA,
            "transaction_id": transaction_id, "payload_id": payload_id, "source_commit": source_commit,
            "update_kind": "APP_AND_LAUNCHER", "previous": previous_pointer,
            "manifest_sha256": candidate_pointer["manifest_sha256"], "workflow_run_id": 123,
            "launcher_format": "onedir", "launcher_executable_sha256": launcher_manifest["executable_sha256"],
            "launcher_tree_manifest_sha256": launcher_manifest["tree_manifest_sha256"],
            "launcher_file_count": launcher_manifest["file_count"], "launcher_total_bytes": launcher_manifest["total_bytes"],
            "status": "awaiting_old_exit", "created_at": "2026-09-05T00:00:00+00:00",
        }
        cls._write_json(root / "update-state" / "restart-transaction.json", transaction)
        return {
            "root": root, "transaction": transaction, "previous": previous_pointer,
            "candidate": candidate, "payload_id": payload_id, "source_commit": source_commit,
            "previous_format": previous_format,
        }

    def test_exact_base_main_parser_discovers_only_legacy_artifact_and_rejects_composite(self) -> None:
        parse_manifest, parse_contract, select_legacy = self._base_main_parser_functions()
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-base-parser-") as temporary:
            root = Path(temporary)
            bundle = root / "launcher-source" / "LocalAIHub"
            (bundle / "_internal").mkdir(parents=True)
            (bundle / "LocalAIHub.exe").write_bytes(b"candidate")
            (bundle / "_internal" / "support.dll").write_bytes(b"support")
            with patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}, clear=False):
                legacy_dir = root / "legacy"
                composite_dir = root / "composite"
                build(legacy_dir, update_kind="APP_ONLY")
                build(composite_dir, update_kind=UPDATE_KIND_APP_AND_LAUNCHER, launcher_bundle=bundle, workflow_run_id=123)

            legacy_manifest = json.loads((legacy_dir / "update-manifest.json").read_text(encoding="utf-8"))
            legacy_contract = json.loads((legacy_dir / "update-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(parse_manifest(legacy_manifest, expected_commit="a" * 40)["payload_id"], "main-aaaaaaaaaaaa")
            self.assertEqual(parse_contract(legacy_contract, expected_commit="a" * 40)["update_kind"], "APP_ONLY")
            composite_manifest = json.loads((composite_dir / "update-manifest.json").read_text(encoding="utf-8"))
            with self.assertRaises(AppUpdateError):
                parse_manifest(composite_manifest, expected_commit="a" * 40)

            class LegacyClient:
                def _api_json(self, endpoint: str, _fields=None):
                    if endpoint.endswith("/runs"):
                        return {"workflow_runs": [{"id": 123, "head_sha": "a" * 40, "head_branch": "main", "conclusion": "success"}]}
                    return {"artifacts": [
                        {"id": 10, "name": LEGACY_UPDATE_ARTIFACT_NAME, "expired": False},
                        {"id": 11, "name": COMPOSITE_UPDATE_ARTIFACT_NAME, "expired": False},
                    ]}

            client = LegacyClient()
            client._latest_candidate = types.MethodType(select_legacy, client)
            selected = client._latest_candidate()
            self.assertEqual(selected.artifact_name, LEGACY_UPDATE_ARTIFACT_NAME)
            self.assertEqual(selected.artifact_id, 10)

    def test_legacy_app_only_restart_continues_same_run_composite_without_manual_artifact_selection(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-bootstrap-e2e-") as temporary:
            root = Path(temporary) / "install"
            self._install_metadata(root)
            (root / "LocalAIHub.exe").write_bytes(b"legacy-shell")
            old_payload = root / "versions" / "main-bbbbbbbbbbbb"
            (old_payload / "app").mkdir(parents=True)
            (old_payload / "runtime" / "Python312").mkdir(parents=True)
            (old_payload / "runtime" / "Python312" / "pythonw.exe").write_bytes(b"runtime")
            (old_payload / "app" / "previous-marker.txt").write_bytes(b"previous-payload")
            self._write_json(old_payload / "manifest.json", {
                "schema_version": VERSION_MANIFEST_SCHEMA, "product_id": "LocalAIHub", "version": "main-bbbbbbbbbbbb",
                "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe", "entrypoint": "src.app.launcher",
            })
            old_pointer = {
                "schema_version": POINTER_SCHEMA, "version": "main-bbbbbbbbbbbb",
                "payload_relative": "versions/main-bbbbbbbbbbbb",
                "manifest_sha256": hashlib.sha256((old_payload / "manifest.json").read_bytes()).hexdigest(),
            }
            self._write_json(root / "current.json", old_pointer)

            source_commit = "a" * 40
            payload_id = "main-aaaaaaaaaaaa"
            launcher_source = root / "launcher-source" / "LocalAIHub"
            (launcher_source / "_internal").mkdir(parents=True)
            (launcher_source / "LocalAIHub.exe").write_bytes(b"candidate-shell")
            (launcher_source / "_internal" / "support.dll").write_bytes(b"candidate-support")
            with patch.dict(os.environ, {"GITHUB_SHA": source_commit}, clear=False):
                legacy_dir = Path(temporary) / "legacy-artifact"
                composite_dir = Path(temporary) / "composite-artifact"
                build(legacy_dir, update_kind="APP_ONLY")
                build(composite_dir, update_kind=UPDATE_KIND_APP_AND_LAUNCHER, launcher_bundle=launcher_source, workflow_run_id=123)

            parse_manifest, parse_contract, _select_legacy = self._base_main_parser_functions()
            legacy_manifest = json.loads((legacy_dir / "update-manifest.json").read_text(encoding="utf-8"))
            legacy_contract = json.loads((legacy_dir / "update-contract.json").read_text(encoding="utf-8"))
            parse_manifest(legacy_manifest, expected_commit=source_commit)
            parse_contract(legacy_contract, expected_commit=source_commit)

            # This is the exact old-client stage boundary: it accepts only the
            # historical APP_ONLY contract, installs the new payload, writes
            # build/run identity, and atomically points current at it.
            new_payload = root / "versions" / payload_id
            with zipfile.ZipFile(legacy_dir / "LocalAIHub-main-update.zip") as archive:
                for info in archive.infolist():
                    name = info.filename.replace("\\", "/")
                    if info.is_dir() or not name.startswith("app/"):
                        continue
                    destination = new_payload / Path(name)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read(info))
            shutil.copytree(old_payload / "runtime", new_payload / "runtime")
            self._write_json(new_payload / "manifest.json", {
                "schema_version": VERSION_MANIFEST_SCHEMA, "product_id": "LocalAIHub", "version": payload_id,
                "app_relative": "app", "runtime_relative": "runtime/Python312/pythonw.exe", "entrypoint": "src.app.launcher",
            })
            self._write_json(new_payload / "build.json", {
                "schema_version": "local-ai-hub-build-info.v1", "source_commit": source_commit,
                "workflow_run_id": 123, "channel": "main", "product_version": "8.0.1",
            })
            new_pointer = {
                "schema_version": POINTER_SCHEMA, "version": payload_id,
                "payload_relative": f"versions/{payload_id}",
                "manifest_sha256": hashlib.sha256((new_payload / "manifest.json").read_bytes()).hexdigest(),
            }
            self._write_json(root / "update-state" / "previous-current.json", old_pointer)
            self._write_json(root / "current.json", new_pointer)
            self._write_json(root / "update-state" / "staged-update.json", {
                "schema_version": "local-ai-hub-staged-update.v1", "payload_id": payload_id,
                "source_commit": source_commit, "transaction_id": "txn-" + "b" * 32,
                "payload_relative": f"versions/{payload_id}", "manifest_sha256": new_pointer["manifest_sha256"],
                "previous": old_pointer, "staged_at": "2026-09-05T00:00:00+00:00", "update_kind": "APP_ONLY",
            })

            class ContinuationTransport:
                def auth_state(self):
                    return SimpleNamespace(status="ready", code=None, transport="fixture")

                def api_json(self, endpoint: str, _fields=None):
                    if f"/runs/123/artifacts" in endpoint:
                        return {"artifacts": [{"id": 22, "name": COMPOSITE_UPDATE_ARTIFACT_NAME, "expired": False}]}
                    if "/compare/" in endpoint:
                        return {"status": "ahead"}
                    return {"artifacts": []}

                def download_artifact(self, run_id: int, destination: Path, artifact_name: str):
                    self.assertion = (run_id, artifact_name)
                    destination.mkdir(parents=True, exist_ok=False)
                    for name in ("update-manifest.json", "update-contract.json", "SHA256SUMS.txt", "LocalAIHub-main-update.zip"):
                        shutil.copy2(composite_dir / name, destination / name)

            transport = ContinuationTransport()
            service = AppUpdateService(transport=transport, allow_test_root=True)
            with patch.dict(os.environ, {"LOCALAIHUB_INSTALL_ROOT": str(root)}, clear=False), patch.object(service, "_validate_staged_imports"), patch.object(service, "_preflight_candidate_api", return_value={"status": "passed"}):
                result = service.continue_bootstrap_after_restart()

            self.assertEqual(result["status"], "staged")
            self.assertEqual(transport.assertion, (123, COMPOSITE_UPDATE_ARTIFACT_NAME))
            pending = _read_bootstrap_pending(root)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["status"], "staged")
            self.assertFalse(pending["restart_authorized"])
            self.assertTrue(result["download_stage_automatic"])
            self.assertFalse(result["second_restart_automatic"])
            self.assertTrue(result["additional_confirmation_required"])
            self.assertEqual(result["user_action_count"], 2)
            staged = json.loads((root / "update-state" / "staged-update.json").read_text(encoding="utf-8"))
            self.assertEqual(staged["update_kind"], UPDATE_KIND_APP_AND_LAUNCHER)
            self.assertEqual(staged["workflow_run_id"], 123)
            self.assertTrue((new_payload / "launcher" / "LocalAIHub" / "LocalAIHub.exe").is_file())
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8")), new_pointer)
            self.assertEqual((old_payload / "app" / "previous-marker.txt").read_bytes(), b"previous-payload")
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")

    def test_deferred_activation_rejects_a_manifest_tampered_between_prepare_and_activation(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-deferred-tamper-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install")
            root = fixture["root"]
            candidate = fixture["candidate"]
            original = launcher_migration.launcher_tree_manifest
            calls = {"count": 0}

            def tamper_after_prepare(bundle: Path):
                value = original(bundle)
                calls["count"] += 1
                if calls["count"] == 1:
                    (bundle / "LocalAIHub.exe").write_bytes(b"tampered-after-prepare")
                return value

            with patch.object(update_watchdog, "launcher_tree_manifest", side_effect=tamper_after_prepare), patch.object(launcher_migration, "launcher_tree_manifest", side_effect=tamper_after_prepare):
                with self.assertRaisesRegex(LauncherMigrationError, "LAUNCHER_MANIFEST_MISMATCH"):
                    _activate_deferred_product(root, fixture["transaction"])
            self.assertGreaterEqual(calls["count"], 2)
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8")), fixture["previous"])
            self.assertFalse((root / "update-state" / "pending-health.json").exists())
            self.assertEqual(launcher_projection(root)["format"], "single_file")
            self.assertTrue((candidate / "LocalAIHub.exe").is_file())

    def test_deferred_activation_rejects_payload_source_or_run_tamper_before_shell_move(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-deferred-payload-tamper-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install")
            root = fixture["root"]
            build_path = root / "versions" / fixture["payload_id"] / "build.json"
            self._write_json(build_path, {
                "schema_version": "local-ai-hub-build-info.v1", "source_commit": fixture["source_commit"],
                "workflow_run_id": 999, "channel": "main", "product_version": "8.0.1",
            })
            with self.assertRaisesRegex(update_watchdog.StableShellError, "RESTART_PAYLOAD_IDENTITY_MISMATCH"):
                _activate_deferred_product(root, fixture["transaction"])
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8")), fixture["previous"])
            self.assertFalse((root / "update-state" / "pending-health.json").exists())

    def test_deferred_pointer_and_pending_health_failures_restore_pointer_then_shell(self) -> None:
        for failure in ("pointer", "pending-health"):
            with self.subTest(failure=failure), TemporaryDirectory(dir=REPO / "Temp", prefix=f"post123-deferred-{failure}-") as temporary:
                fixture = self._deferred_fixture(Path(temporary) / "install")
                root = fixture["root"]
                original_pointer = update_watchdog.atomic_activate_pointer
                original_atomic_json = update_watchdog._atomic_json
                injected = {"done": False}

                def pointer_failure(app_root: Path, *, version: str, manifest_sha256: str):
                    if failure == "pointer" and version == fixture["payload_id"] and not injected["done"]:
                        injected["done"] = True
                        raise update_watchdog.StableShellError("fixture_pointer_failure")
                    return original_pointer(app_root, version=version, manifest_sha256=manifest_sha256)

                def pending_failure(path: Path, value: dict[str, object]):
                    if failure == "pending-health" and path.name == "pending-health.json" and not injected["done"]:
                        injected["done"] = True
                        raise OSError("fixture_pending_health_failure")
                    return original_atomic_json(path, value)

                with patch.object(update_watchdog, "atomic_activate_pointer", side_effect=pointer_failure), patch.object(update_watchdog, "_atomic_json", side_effect=pending_failure):
                    with self.assertRaises(Exception):
                        _activate_deferred_product(root, fixture["transaction"])
                self.assertTrue(injected["done"])
                self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
                self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8")), fixture["previous"])
                self.assertFalse((root / "update-state" / "pending-health.json").exists())
                self.assertEqual(launcher_projection(root)["format"], "single_file")

    def test_launcher_fault_matrix_keeps_a_bootable_previous_shell_and_candidate_evidence(self) -> None:
        statuses = ("prepared", "activation_copy_complete", "old_exe_moved", "candidate_exe_moved", "candidate_internal_moved", "shell_manifest_written")
        for status in statuses:
            with self.subTest(status=status), TemporaryDirectory(dir=REPO / "Temp", prefix=f"post123-fault-{status}-") as temporary:
                fixture = self._deferred_fixture(Path(temporary) / "install")
                root = fixture["root"]
                candidate = fixture["candidate"]
                original_write = launcher_migration._write_json
                injected = {"done": False}

                def fail_once(path: Path, value: dict[str, object]):
                    if value.get("status") == status and not injected["done"]:
                        injected["done"] = True
                        raise LauncherMigrationError(f"fixture_{status}")
                    return original_write(path, value)

                with patch.object(launcher_migration, "_write_json", side_effect=fail_once):
                    with self.assertRaises(LauncherMigrationError):
                        activate_launcher_bundle(
                            root, candidate, transaction_id="txn-" + "c" * 32,
                            payload_id=fixture["payload_id"], source_commit=fixture["source_commit"], workflow_run_id=123,
                        )
                self.assertTrue(injected["done"])
                self.assertTrue((root / "LocalAIHub.exe").is_file())
                self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
                self.assertFalse((root / "_internal").exists())
                self.assertEqual(launcher_projection(root)["format"], "single_file")
                self.assertTrue((root / "versions" / fixture["payload_id"] / "launcher" / "LocalAIHub" / "LocalAIHub.exe").is_file(), status)

        with self.subTest(status="old_internal_moved"), TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-old-internal-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            candidate = fixture["candidate"]
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_internal(path: Path, value: dict[str, object]):
                if value.get("status") == "old_internal_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_old_internal_moved")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_internal):
                with self.assertRaises(LauncherMigrationError):
                    activate_launcher_bundle(
                        root, candidate, transaction_id="txn-" + "d" * 32,
                        payload_id=fixture["payload_id"], source_commit=fixture["source_commit"], workflow_run_id=123,
                    )
            self.assertTrue(injected["done"])
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertTrue((root / "_internal" / "legacy-support.dll").is_file())
            self.assertEqual(launcher_projection(root)["format"], "onedir")

        with self.subTest(status="old_exe_moved_onedir"), TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-old-exe-onedir-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            candidate = fixture["candidate"]
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_old_exe(path: Path, value: dict[str, object]):
                if value.get("status") == "old_exe_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_old_exe_moved_onedir")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_old_exe):
                with self.assertRaises(LauncherMigrationError):
                    activate_launcher_bundle(
                        root, candidate, transaction_id="txn-" + "b" * 32,
                        payload_id=fixture["payload_id"], source_commit=fixture["source_commit"], workflow_run_id=123,
                    )
            self.assertTrue(injected["done"])
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertTrue((root / "_internal" / "legacy-support.dll").is_file())
            self.assertEqual(launcher_projection(root)["format"], "onedir")

        with self.subTest(status="old_manifest_moved"), TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-old-manifest-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            candidate = fixture["candidate"]
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_old_manifest(path: Path, value: dict[str, object]):
                if value.get("status") == "old_manifest_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_old_manifest_moved")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_old_manifest):
                with self.assertRaises(LauncherMigrationError):
                    activate_launcher_bundle(
                        root, candidate, transaction_id="txn-" + "f" * 32,
                        payload_id=fixture["payload_id"], source_commit=fixture["source_commit"], workflow_run_id=123,
                    )
            self.assertTrue(injected["done"])
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertTrue((root / "_internal" / "legacy-support.dll").is_file())
            self.assertTrue((root / "launcher-manifest.json").is_file())
            self.assertEqual(launcher_projection(root)["format"], "onedir")

        with self.subTest(status="shell_manifest_write_failure"), TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-shell-manifest-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install")
            root = fixture["root"]
            candidate = fixture["candidate"]
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_shell_manifest(path: Path, value: dict[str, object]):
                if path.name == "launcher-manifest.json" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_shell_manifest_write")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_shell_manifest):
                with self.assertRaises(LauncherMigrationError):
                    activate_launcher_bundle(
                        root, candidate, transaction_id="txn-" + "e" * 32,
                        payload_id=fixture["payload_id"], source_commit=fixture["source_commit"], workflow_run_id=123,
                    )
            self.assertTrue(injected["done"])
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertFalse((root / "_internal").exists())
            self.assertEqual(launcher_projection(root)["format"], "single_file")

    def test_watchdog_deferred_api_frontend_and_process_failures_use_one_bounded_recovery(self) -> None:
        for failure in ("api", "frontend", "process"):
            with self.subTest(failure=failure), TemporaryDirectory(dir=REPO / "Temp", prefix=f"post123-watchdog-{failure}-") as temporary:
                fixture = self._deferred_fixture(Path(temporary) / "install")
                root = fixture["root"]
                child = SimpleNamespace(pid=40101, poll=lambda: None)
                fallback = SimpleNamespace(pid=40102, poll=lambda: None)
                if failure == "process":
                    identity_values = [(fixture["payload_id"], fixture["source_commit"], {})]
                else:
                    identity_values = [
                        (fixture["payload_id"], fixture["source_commit"], {}),
                        ("main-bbbbbbbbbbbb", "b" * 40, {}),
                    ]
                launch_values = [OSError("fixture_process_failure")] if failure == "process" else [child, fallback]
                wait_values = [] if failure == "process" else [False, True]
                with (
                    patch.object(update_watchdog, "_activate_deferred_product", return_value=True),
                    patch.object(update_watchdog, "_target_identity", side_effect=identity_values),
                    patch.object(update_watchdog, "_launch_stable", side_effect=launch_values),
                    patch.object(update_watchdog, "_wait_for_target_health", side_effect=wait_values),
                    patch.object(update_watchdog, "_health_matches", return_value=failure == "frontend"),
                    patch.object(update_watchdog, "_rollback_deferred_product", return_value=True) as rollback,
                    patch.object(update_watchdog, "terminate_owned_process"),
                    patch.object(update_watchdog, "_try_write_update_state"),
                    patch.object(update_watchdog, "_session_path", return_value=None),
                ):
                    result = update_watchdog.run(app_root=root, wait_pid=0, timeout_seconds=5)
                self.assertEqual(result["status"], "rolled_back")
                rollback.assert_called_once()
                if failure == "frontend":
                    self.assertEqual(rollback.call_args.kwargs["reason"], "WATCHDOG_POST_RESTART_HEALTH_FAILED")

    def test_repeated_restore_and_watchdog_retry_are_noops_after_previous_shell_is_restored(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-repeat-restore-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install")
            root = fixture["root"]
            _activate_deferred_product(root, fixture["transaction"])
            transaction_id = str(fixture["transaction"]["transaction_id"])
            first = restore_launcher_bundle(root, transaction_id=transaction_id)
            second = restore_launcher_bundle(root, transaction_id=transaction_id)
            third = restore_launcher_bundle(root, transaction_id=transaction_id)
            self.assertEqual(first["status"], "restored")
            self.assertEqual(second["status"], "already_restored")
            self.assertEqual(third["status"], "already_restored")
            self.assertTrue(update_watchdog._rollback_deferred_product(root, fixture["transaction"], reason="fixture-watchdog-retry"))
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertEqual(json.loads((root / "current.json").read_text(encoding="utf-8")), fixture["previous"])

    def test_restore_accepts_pre_correction_activation_state_without_new_fields(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-legacy-rollback-state-") as temporary:
            root = Path(temporary) / "install"
            root.mkdir(parents=True)
            (root / "LocalAIHub.exe").write_bytes(b"candidate-shell")
            (root / "_internal").mkdir()
            (root / "_internal" / "candidate.dll").write_bytes(b"candidate")
            transaction_id = "txn-" + "a" * 32
            backup = root / "update-state" / "launcher-rollback" / transaction_id
            backup.mkdir(parents=True)
            (backup / "LocalAIHub.exe").write_bytes(b"legacy-shell")
            self._write_json(root / "update-state" / "launcher-activation.json", {
                "schema_version": "local-ai-hub-launcher-activation.v1",
                "transaction_id": transaction_id,
                "payload_id": "main-aaaaaaaaaaaa",
                "source_commit": "a" * 40,
                "format": "onedir",
                "executable_sha256": hashlib.sha256(b"candidate-shell").hexdigest(),
                "tree_manifest_sha256": "1" * 64,
                "file_count": 2,
                "total_bytes": 1,
                "status": "switching",
            })
            result = restore_launcher_bundle(root, transaction_id=transaction_id)
            self.assertEqual(result["status"], "restored")
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertFalse((root / "_internal").exists())


    def test_artifact_selection_prefers_distinct_composite_and_legacy_fallback_is_explicit(self) -> None:
        artifacts = [
            {"id": 10, "name": LEGACY_UPDATE_ARTIFACT_NAME, "expired": False},
            {"id": 11, "name": COMPOSITE_UPDATE_ARTIFACT_NAME, "expired": False},
        ]
        service = AppUpdateService(transport=_ArtifactTransport(artifacts))
        selected = service._latest_candidate(require_composite=True)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.artifact_name, COMPOSITE_UPDATE_ARTIFACT_NAME)
        self.assertEqual(selected.update_kind, UPDATE_KIND_APP_AND_LAUNCHER)
        self.assertTrue(selected.is_composite)

        legacy_only = AppUpdateService(
            transport=_ArtifactTransport([{"id": 10, "name": LEGACY_UPDATE_ARTIFACT_NAME, "expired": False}])
        )._latest_candidate(require_composite=True)
        self.assertIsNotNone(legacy_only)
        self.assertEqual(legacy_only.artifact_name, LEGACY_UPDATE_ARTIFACT_NAME)
        self.assertFalse(legacy_only.is_composite)

    def test_expired_composite_is_not_silently_downgraded_when_shell_migration_is_required(self) -> None:
        service = AppUpdateService(transport=_ArtifactTransport([
            {"id": 10, "name": LEGACY_UPDATE_ARTIFACT_NAME, "expired": False},
            {"id": 11, "name": COMPOSITE_UPDATE_ARTIFACT_NAME, "expired": True},
        ]))
        with self.assertRaisesRegex(AppUpdateError, "UPDATE_ARTIFACT_EXPIRED"):
            service._latest_candidate(require_composite=True)

    def test_legacy_contract_and_composite_contract_have_separate_parseable_identities(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-contract-") as temporary:
            root = Path(temporary)
            bundle = root / "LocalAIHub"
            (bundle / "_internal").mkdir(parents=True)
            (bundle / "LocalAIHub.exe").write_bytes(b"candidate")
            (bundle / "_internal" / "support.dll").write_bytes(b"support")
            with patch.dict(os.environ, {"GITHUB_SHA": "b" * 40}, clear=False):
                legacy = build(root / "legacy", update_kind="APP_ONLY")
                composite = build(root / "composite", update_kind=UPDATE_KIND_APP_AND_LAUNCHER, launcher_bundle=bundle, workflow_run_id=123)
            self.assertEqual(set(legacy), {
                "schema_version", "product_id", "product_version", "channel", "source_commit",
                "payload_id", "runtime_strategy", "archive", "archive_sha256", "file_count",
            })
            legacy_contract = json.loads((root / "legacy" / "update-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(set(legacy_contract), {
                "schema_version", "update_kind", "app_protocol", "runtime_contract", "runtime_version",
                "runtime_hash", "minimum_launcher_version", "source_commit",
            })
            self.assertEqual(legacy_contract["update_kind"], "APP_ONLY")
            self.assertEqual(composite["schema_version"], "local-ai-hub-composite-update.v1")
            self.assertEqual(COMPOSITE_UPDATE_ARTIFACT_NAME, "local-ai-hub-composite-product-update")
            self.assertNotEqual(COMPOSITE_UPDATE_ARTIFACT_NAME, LEGACY_UPDATE_ARTIFACT_NAME)

    def test_bootstrap_marker_is_durable_and_path_free(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-bootstrap-") as temporary:
            root = Path(temporary)
            marker = _write_bootstrap_pending(root, source_commit="c" * 40, workflow_run_id=987, status="pending")
            self.assertEqual(_read_bootstrap_pending(root), marker)
            encoded = json.dumps(marker, ensure_ascii=True)
            self.assertNotIn(str(root), encoded)
            self.assertEqual(marker["legacy_artifact_name"], LEGACY_UPDATE_ARTIFACT_NAME)
            self.assertEqual(marker["composite_artifact_name"], COMPOSITE_UPDATE_ARTIFACT_NAME)

    def test_activation_rejects_post_prepare_candidate_tamper_before_moving_healthy_shell(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-tamper-") as temporary:
            root = Path(temporary) / "install"
            root.mkdir()
            (root / "LocalAIHub.exe").write_bytes(b"legacy")
            candidate = root / "versions" / ("main-" + "a" * 12) / "launcher" / "LocalAIHub"
            (candidate / "_internal").mkdir(parents=True)
            (candidate / "LocalAIHub.exe").write_bytes(b"candidate")
            (candidate / "_internal" / "support.dll").write_bytes(b"support")
            expected = launcher_tree_manifest(candidate)
            (candidate / "LocalAIHub.exe").write_bytes(b"tampered")
            with self.assertRaisesRegex(LauncherMigrationError, "LAUNCHER_MANIFEST_MISMATCH"):
                activate_launcher_bundle(
                    root,
                    candidate,
                    transaction_id="txn-" + "a" * 32,
                    payload_id="main-" + "a" * 12,
                    source_commit="a" * 40,
                    expected_manifest=expected,
                )
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy")

    def test_fault_after_old_shell_move_is_recovered_and_repeated_restore_is_safe(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-") as temporary:
            root = Path(temporary) / "install"
            candidate, legacy, transaction = self._shell_fixture(root)
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_after_old_exe(path, value):
                if value.get("status") == "old_exe_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_old_exe_state_write")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_after_old_exe):
                with self.assertRaises(LauncherMigrationError):
                    activate_launcher_bundle(
                        root,
                        candidate,
                        transaction_id=transaction,
                        payload_id="main-" + "a" * 12,
                        source_commit="a" * 40,
                    )
            self.assertTrue(legacy.is_file())
            self.assertEqual(legacy.read_bytes(), b"legacy")
            self.assertEqual(launcher_projection(root)["format"], "single_file")
            self.assertTrue((root / "update-state" / "launcher-rollback" / transaction / "candidate-shell").is_dir())
            self.assertEqual(restore_launcher_bundle(root, transaction_id=transaction)["status"], "already_restored")
            self.assertEqual(restore_launcher_bundle(root, transaction_id=transaction)["status"], "already_restored")
            self.assertEqual(legacy.read_bytes(), b"legacy")

    def test_restore_reconciles_previous_internal_tree_when_state_write_fails(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-previous-internal-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            _activate_deferred_product(root, fixture["transaction"])
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_previous_internal(path: Path, value: dict[str, object]):
                if value.get("status") == "previous__internal_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_previous_internal_state_write")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_previous_internal):
                result = restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertTrue(injected["done"])
            self.assertEqual(result["status"], "restored")
            self.assertEqual(launcher_projection(root)["format"], "onedir")
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertTrue((root / "_internal" / "legacy-support.dll").is_file())
            self.assertTrue((root / "launcher-manifest.json").is_file())
            self.assertEqual(
                restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))["status"],
                "already_restored",
            )

    def test_restore_does_not_move_complete_previous_shell_after_exe_state_write_failure(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-fault-previous-exe-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            _activate_deferred_product(root, fixture["transaction"])
            original_write = launcher_migration._write_json
            injected = {"done": False}

            def fail_previous_exe(path: Path, value: dict[str, object]):
                if value.get("status") == "previous_LocalAIHub_exe_moved" and not injected["done"]:
                    injected["done"] = True
                    raise LauncherMigrationError("fixture_previous_exe_state_write")
                return original_write(path, value)

            with patch.object(launcher_migration, "_write_json", side_effect=fail_previous_exe):
                result = restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertTrue(injected["done"])
            self.assertEqual(result["status"], "restored")
            self.assertEqual(launcher_projection(root)["format"], "onedir")
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertTrue((root / "_internal" / "legacy-support.dll").is_file())
            self.assertTrue((root / "launcher-manifest.json").is_file())
            self.assertEqual(
                restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))["status"],
                "already_restored",
            )

    def test_retained_onedir_identity_is_persisted_and_candidate_is_not_reported_as_installed(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-retained-identity-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            self.assertTrue(_activate_deferred_product(root, fixture["transaction"]))
            state = json.loads((root / "update-state" / "launcher-activation.json").read_text(encoding="utf-8"))
            pending = json.loads((root / "update-state" / "pending-health.json").read_text(encoding="utf-8"))
            retained = state["retained_shell_identity"]
            self.assertEqual(pending["retained_shell_identity"], retained)
            self.assertEqual(pending["retained_shell_payload_id"], retained["payload_id"])
            self.assertEqual(launcher_projection(root)["payload_id"], retained["payload_id"])
            self.assertNotEqual(launcher_projection(root)["payload_id"], fixture["payload_id"])
            recovery = launcher_migration.reconcile_launcher_transaction(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertEqual(recovery["status"], "shell_retained")
            self.assertEqual(recovery["retained_shell_identity"], retained)

    def test_retained_manifest_tamper_is_rejected_then_restored_from_exact_backup(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-retained-manifest-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            _activate_deferred_product(root, fixture["transaction"])
            manifest_path = root / "launcher-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["payload_id"] = fixture["payload_id"]
            manifest["source_commit"] = fixture["source_commit"]
            self._write_json(manifest_path, manifest)
            with self.assertRaisesRegex(LauncherMigrationError, "LAUNCHER_RETAINED_SHELL_MISMATCH"):
                launcher_migration.reconcile_launcher_transaction(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            repaired = restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertEqual(repaired["status"], "restored")
            self.assertEqual(launcher_projection(root)["payload_id"], "main-bbbbbbbbbbbb")
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8"))["source_commit"], "b" * 40)

    def test_retained_support_tree_tamper_fails_closed_without_moving_root_shell(self) -> None:
        with TemporaryDirectory(dir=REPO / "Temp", prefix="post123-retained-support-") as temporary:
            fixture = self._deferred_fixture(Path(temporary) / "install", previous_format="onedir")
            root = fixture["root"]
            _activate_deferred_product(root, fixture["transaction"])
            support = root / "_internal" / "legacy-support.dll"
            support.write_bytes(b"tampered-support")
            with self.assertRaisesRegex(LauncherMigrationError, "LAUNCHER_ROLLBACK_UNAVAILABLE"):
                restore_launcher_bundle(root, transaction_id=str(fixture["transaction"]["transaction_id"]))
            self.assertTrue((root / "LocalAIHub.exe").is_file())
            self.assertEqual((root / "LocalAIHub.exe").read_bytes(), b"legacy-shell")
            self.assertEqual(support.read_bytes(), b"tampered-support")

    def test_shortcut_fixture_contract_is_explicit_and_non_mutating(self) -> None:
        root = r"D:\LocalAIHub\fixture-install"
        correct = {"target_path": root + r"\LocalAIHub.exe", "arguments": "", "scope": "per_user"}
        stale = {"target_path": r"C:\Windows\System32\wscript.exe", "arguments": root + r"\LocalAIHub.vbs", "scope": "per_user"}
        stale_all_users = {
            "name": "Local AI Hub.lnk", "target_path": r"C:\Windows\System32\wscript.exe",
            "arguments": r"D:\LocalAIHub\Temp\legacy\LocalAIHub.vbs", "scope": "all_users",
            "location_class": "all_users_start_menu", "legacy_vbs_path": r"D:\LocalAIHub\Temp\legacy\LocalAIHub.vbs",
            "legacy_script_marker": LEGACY_VBS_MARKER, "legacy_script_sha256": LEGACY_VBS_CONTENT_SHA256,
        }
        ambiguous = {"name": "Local AI Hub.lnk", "target_path": r"C:\Windows\System32\wscript.exe", "arguments": "LocalAIHub.vbs --unknown"}
        unowned = {"name": "Other.lnk", "target_path": r"C:\Tools\other.exe", "arguments": ""}
        self.assertEqual(plan_shortcut_migration(correct, root)["status"], MIGRATION_ALREADY_CORRECT)
        self.assertEqual(plan_shortcut_migration(stale, root)["status"], MIGRATION_AMBIGUOUS)
        self.assertEqual(plan_shortcut_migration(stale_all_users, root)["status"], MIGRATION_ADMIN_REQUIRED)
        self.assertEqual(plan_shortcut_migration(ambiguous, root)["status"], MIGRATION_AMBIGUOUS)
        self.assertEqual(plan_shortcut_migration(unowned, root)["status"], MIGRATION_UNOWNED)
        self.assertFalse(migration_complete([stale, correct], root))
        self.assertIn("LocalAIHub.exe", normal_launch_command(root)[0])
        self.assertNotIn(".vbs", normal_launch_command(root)[0].casefold())

    def test_prompt_editor_static_contract_is_persistent_anchored_and_key_complete(self) -> None:
        studio = (REPO / "src" / "ui" / "features" / "node_studio" / "studio.js").read_text(encoding="utf-8")
        styles = (REPO / "src" / "ui" / "styles.css").read_text(encoding="utf-8")
        for marker in (
            'data-prompt-editor-mode', 'persistent-anchored', 'data-outside-click',
            'panel.addEventListener("pointerleave"', 'panel.addEventListener("pointerenter"',
            'event.button !== undefined && event.button !== 0',
            'event.key === "Escape"', 'event.ctrlKey || event.metaKey',
            'this.positionInlineEditor()', 'this.liteCanvas.ds.onredraw',
            'window.addEventListener("resize"',
        ):
            self.assertIn(marker, studio)
        self.assertIn("graph-node-prompt-editor", styles)
        self.assertIn("font-size: 13px", styles)
        self.assertIn("max-height: min(520px, 48vh", styles)
        self.assertNotIn("NODE_INLINE_EDITOR_MAX_HEIGHT = 240", studio)

    def test_windows_ci_launcher_archive_preserves_assembler_root_contract(self) -> None:
        workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("Compress-Archive -Path $bundle -DestinationPath", workflow)
        self.assertNotIn('Compress-Archive -Path "$bundle/*"', workflow)

    def test_composite_assembler_accepts_downloaded_launcher_sidecar_layout(self) -> None:
        from scripts.assemble_product_update import assemble

        source_commit = "a" * 40
        temp_root = REPO / "Temp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=temp_root, prefix="post123-assembler-layout-") as temporary:
            root = Path(temporary)
            app_root = root / "app-artifact"
            launcher_root = root / "launcher-artifact"
            app_root.mkdir()
            (launcher_root / "stable-launcher").mkdir(parents=True)
            with zipfile.ZipFile(app_root / "LocalAIHub-main-update.zip", "w") as archive:
                info = zipfile.ZipInfo("app/README.md", date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(info, b"app")
            (app_root / "update-manifest.json").write_text(json.dumps({"source_commit": source_commit}), encoding="utf-8")
            with zipfile.ZipFile(launcher_root / "LocalAIHub-stable-launcher-onedir.zip", "w") as archive:
                for name, data in (("LocalAIHub/LocalAIHub.exe", b"exe"), ("LocalAIHub/_internal/support.dll", b"support")):
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    archive.writestr(info, data)
            (launcher_root / "stable-launcher" / "launcher-build.json").write_text(
                json.dumps({"source_commit": source_commit, "workflow_run_id": 123}), encoding="utf-8"
            )
            result = assemble(app_root, launcher_root, root / "assembled", workflow_run_id=123)
            self.assertEqual(result["source_commit"], source_commit)
            self.assertEqual(result["workflow_run_id"], 123)
            self.assertEqual(result["launcher_format"], "onedir")


if __name__ == "__main__":
    unittest.main()
