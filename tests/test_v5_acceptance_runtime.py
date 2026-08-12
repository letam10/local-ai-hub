from __future__ import annotations

import json
import copy
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from unittest.mock import patch


class AcceptanceRuntimeTests(unittest.TestCase):
    @staticmethod
    def _approval(tool_smoke, *, source_head: str = "d" * 40, source_tree: str = "e" * 40) -> dict:
        return {
            "approval_id": tool_smoke.ACCEPTANCE_APPROVAL_ID,
            "approval_version": tool_smoke.ACCEPTANCE_APPROVAL_VERSION,
            "status": "approved",
            "authorized_by": tool_smoke.ACCEPTANCE_AUTHORIZED_BY,
            "release": {"branch": tool_smoke.ACCEPTANCE_RELEASE_BRANCH, "head": tool_smoke.ACCEPTANCE_RELEASE_HEAD},
            "source": {
                "branch": tool_smoke.ACCEPTANCE_BRANCH,
                "base": tool_smoke.ACCEPTANCE_RELEASE_HEAD,
                "head": source_head,
                "tree": source_tree,
            },
            "owner": "LAH 2",
            "objective": tool_smoke.ACCEPTANCE_OBJECTIVE,
            "allowed_input": {
                "kind": "synthetic task-owned video",
                "maximum_dimensions": "16x16",
                "maximum_duration_seconds": 1,
                "maximum_fps": 8,
                "maximum_bytes": tool_smoke.ACCEPTANCE_INPUT_MAX_BYTES,
            },
            "allowed_output": {
                "maximum_bytes": tool_smoke.ACCEPTANCE_OUTPUT_MAX_BYTES,
                "operations": list(tool_smoke.ACCEPTANCE_OPERATIONS),
                "maximum_pipeline_jobs": 1,
            },
            "limits": {
                "wall_seconds": 60,
                "no_retry": True,
                "temporary_root": "task-owned only",
                "stop_authority": "manager or user",
                "cleanup_owner": "LAH 2",
            },
            "preflight": {
                "require_existing_canonical_ffmpeg": True,
                "require_no_download_or_install": True,
                "require_free_space_check": True,
                "require_no_interference_with_user_owned_processes": True,
                "require_opaque_artifact_ids": True,
            },
            "prohibited": list(tool_smoke.ACCEPTANCE_PROHIBITED),
            "gpu": {
                "status": "not_authorized_by_this_approval",
                "next_action": "Read-only RTX 4060/runtime/checkpoint preflight may be reported for a separately bound approval.",
            },
        }

    @staticmethod
    def _git_results(tool_smoke, *, source_head: str = "d" * 40, source_tree: str = "e" * 40, branch: str | None = None, merge_base: str | None = None) -> list[subprocess.CompletedProcess[str]]:
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        return [
            subprocess.CompletedProcess([], 0, stdout=(branch or tool_smoke.ACCEPTANCE_BRANCH) + "\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=source_head + "\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=source_tree + "\n", stderr=""),
            completed,
            subprocess.CompletedProcess([], 0, stdout=(merge_base or tool_smoke.ACCEPTANCE_RELEASE_HEAD) + "\n", stderr=""),
            completed,
        ]

    def test_cpu_acceptance_requires_explicit_opt_in(self) -> None:
        from src.services import tool_smoke

        with TemporaryDirectory() as directory:
            root = Path(directory) / "acceptance-root"
            result = tool_smoke.run_cpu_media_acceptance(opt_in=False, task_root=root)
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["execution"], "not_run")
        self.assertFalse(root.exists())

    def test_approval_guard_requires_exact_scope_and_source_identity(self) -> None:
        from src.services import tool_smoke

        source_head = "d" * 40
        source_tree = "e" * 40
        approval = self._approval(tool_smoke, source_head=source_head, source_tree=source_tree)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            with patch.object(tool_smoke.subprocess, "run", side_effect=[
                *self._git_results(tool_smoke, source_head=source_head, source_tree=source_tree),
            ]):
                ok, code = tool_smoke._approval_guard(approval_path, root)
        self.assertTrue(ok)
        self.assertEqual(code, "ok")

    def test_old_approval_without_source_binding_is_blocked_before_git(self) -> None:
        from src.services import tool_smoke

        approval = self._approval(tool_smoke)
        approval.pop("source")
        approval["approval_version"] = 1
        with TemporaryDirectory() as directory:
            root = Path(directory)
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            with patch.object(tool_smoke.subprocess, "run") as git_run:
                ok, code = tool_smoke._approval_guard(approval_path, root)
        self.assertFalse(ok)
        self.assertEqual(code, "approval_contract_mismatch")
        git_run.assert_not_called()

    def test_approval_identity_and_scope_drift_blocks_before_runtime(self) -> None:
        from src.services import tool_smoke

        source_head = "d" * 40
        source_tree = "e" * 40
        cases: list[tuple[str, Callable[[dict], object], str, list[subprocess.CompletedProcess[str]] | None]] = []

        def contract_case(label: str, mutate: Callable[[dict], object]) -> None:
            cases.append((label, mutate, "approval_contract_mismatch", None))

        contract_case("approval version", lambda value: value.update(approval_version=1))
        contract_case("release branch", lambda value: value["release"].update(branch="feature/other"))
        contract_case("release head", lambda value: value["release"].update(head="a" * 40))
        contract_case("source branch", lambda value: value["source"].update(branch="feature/other"))
        contract_case("source base", lambda value: value["source"].update(base="a" * 40))
        contract_case("objective", lambda value: value.update(objective="different objective"))
        contract_case("scope", lambda value: value["allowed_output"].update(operations=["video_grade"]))
        contract_case("missing source", lambda value: value.pop("source"))
        contract_case("missing limits", lambda value: value.pop("limits"))
        contract_case("missing prohibition", lambda value: value.pop("prohibited"))
        cases.extend([
            (
                "current HEAD",
                lambda value: value,
                "git_guard_mismatch",
                self._git_results(tool_smoke, source_head="f" * 40, source_tree=source_tree),
            ),
            (
                "current tree",
                lambda value: value,
                "git_guard_mismatch",
                self._git_results(tool_smoke, source_head=source_head, source_tree="f" * 40),
            ),
            (
                "current branch",
                lambda value: value,
                "git_guard_mismatch",
                self._git_results(tool_smoke, source_head=source_head, source_tree=source_tree, branch="feature/other"),
            ),
            (
                "current base",
                lambda value: value,
                "git_guard_mismatch",
                self._git_results(tool_smoke, source_head=source_head, source_tree=source_tree, merge_base="a" * 40),
            ),
        ])

        for label, mutate, expected_code, git_results in cases:
            with self.subTest(label=label):
                approval = self._approval(tool_smoke, source_head=source_head, source_tree=source_tree)
                mutate(approval)
                with TemporaryDirectory() as directory:
                    root = Path(directory)
                    approval_path = root / "approval.json"
                    task_root = root / "task-root"
                    approval_path.write_text(json.dumps(approval), encoding="utf-8")
                    with (
                        patch.object(tool_smoke.subprocess, "run", side_effect=git_results) as git_run,
                        patch("src.modules.media_editor.backend.adapter._paths", side_effect=AssertionError("FFmpeg lookup must not run")) as paths,
                        patch.object(tool_smoke, "_run_cpu_pipeline", side_effect=AssertionError("pipeline must not run")),
                    ):
                        result = tool_smoke.run_cpu_media_acceptance(
                            opt_in=True,
                            approval_path=approval_path,
                            task_root=task_root,
                            repo_root=root,
                        )
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["failure_code"], expected_code)
                paths.assert_not_called()
                if git_results is None:
                    git_run.assert_not_called()

    def test_closed_public_payload_rejects_path_before_resolution(self) -> None:
        from src.modules.media_editor.backend import adapter

        sentinel = str(Path(__file__).resolve())
        for operation in ("video_grade", "logo_overlay", "audio_loudness"):
            with (
                patch.object(adapter, "resolve", side_effect=AssertionError("resolve must not run")),
                patch.object(adapter, "describe", side_effect=AssertionError("describe must not run")),
                patch.object(adapter, "_paths", side_effect=AssertionError("paths must not run")),
                patch.object(adapter, "run_command", side_effect=AssertionError("run_command must not run")),
            ):
                result = adapter.run_operation({"operation": operation, "path": sentinel})
            self.assertEqual(result["status"], "error")
            self.assertNotIn(sentinel, json.dumps(result, ensure_ascii=False))

    def test_acceptance_owner_detaches_success_failure_and_timeout_processes(self) -> None:
        from src.services import tool_smoke

        class FakeProcess:
            def __init__(self, pid: int, returncode: int | None) -> None:
                self.pid = pid
                self.returncode = returncode

            def poll(self) -> int | None:
                return self.returncode

        owner = tool_smoke._AcceptanceOwner(time.monotonic() + 10)
        success = FakeProcess(101, 0)
        failed = FakeProcess(102, 1)
        owner.note_command("success", ["ffmpeg.exe", "-version"])
        owner.attach_process(success, "success")
        owner.detach_process(success)
        owner.note_command("failed", ["ffmpeg.exe", "-version"])
        owner.attach_process(failed, "failed")
        owner.detach_process(failed)
        self.assertTrue(owner.clean())
        self.assertEqual([item["returncode"] for item in owner.lifecycle()], [0, 1])

        timed_out = tool_smoke._AcceptanceOwner(time.monotonic() - 1)
        process = FakeProcess(103, None)
        timed_out.attach_process(process, "timeout")
        with patch("src.services.process_manager.managed.terminate_owned_process") as terminate:
            timed_out.stop_all()
        terminate.assert_called_once_with(process)
        self.assertTrue(timed_out.clean())

    def test_acceptance_success_and_failure_cleanup_exact_task_root(self) -> None:
        from src.services import tool_smoke

        evidence = {
            "pipeline": ["video_grade", "logo_overlay", "encode"],
            "artifacts": {"encoded": {"id": "artifact_" + "a" * 32, "size_bytes": 10, "sha256": "a" * 64}},
            "source_overwritten": False,
        }
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            approval = parent / "approval.json"
            task_root = parent / "run-root"
            ffmpeg = parent / "ffmpeg.exe"
            ffprobe = parent / "ffprobe.exe"
            ffmpeg.write_bytes(b"fixture")
            ffprobe.write_bytes(b"fixture")
            evidence_path = parent / "runtime-evidence.json"
            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", return_value=evidence),
                patch.object(tool_smoke, "RUNTIME_EVIDENCE_STATE_PATH", evidence_path),
            ):
                result = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=approval, task_root=task_root, repo_root=parent)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["temp_cleaned"])
            self.assertFalse(task_root.exists())

            failure_root = parent / "failure-root"
            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", side_effect=tool_smoke.AcceptanceFailure("timeout", unavailable=True)),
                patch.object(tool_smoke, "RUNTIME_EVIDENCE_STATE_PATH", evidence_path),
            ):
                failure = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=approval, task_root=failure_root, repo_root=parent)
            self.assertEqual(failure["status"], "unavailable")
            self.assertTrue(failure["temp_cleaned"])
            self.assertFalse(failure_root.exists())

    def test_logo_overlay_diagnostic_classes_are_versioned_and_allowlisted(self) -> None:
        from src.services import tool_smoke

        fixtures = {
            "filter_graph": b"Error reinitializing filters!",
            "image_decode": b"Failed to decode image",
            "stream_mapping": b"Stream map '0:1' matches no streams.",
            "encoder_or_mux": b"Unknown encoder 'fixture'.",
            "filesystem": b"No such file or directory",
            "timeout": b"Operation timed out",
        }
        self.assertEqual(set(tool_smoke.ACCEPTANCE_DIAGNOSTIC_CLASSES), set(fixtures) | {"unknown"})
        with TemporaryDirectory() as directory:
            task_root = Path(directory)
            old_log_path = task_root / "logs" / "ffmpeg_logo_overlay.log"
            old_log_path.parent.mkdir()
            old_log_path.write_bytes(fixtures["filter_graph"])
            self.assertEqual(tool_smoke._classify_logo_overlay_failure(task_root)["class"], "unknown")
            log_path = task_root / "logs" / "workers" / "ffmpeg_logo_overlay_acceptance_cpu_media.log"
            log_path.parent.mkdir()
            for expected, content in fixtures.items():
                log_path.write_bytes(content)
                result = tool_smoke._classify_logo_overlay_failure(task_root)
                self.assertEqual(result, {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": expected})

    def test_logo_overlay_diagnostic_unknown_missing_unreadable_and_tail_bounded(self) -> None:
        from src.services import tool_smoke

        unknown = {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"}
        with TemporaryDirectory() as directory:
            task_root = Path(directory)
            self.assertEqual(tool_smoke._classify_logo_overlay_failure(task_root), unknown)
            log_path = task_root / "logs" / "workers" / "ffmpeg_logo_overlay_acceptance_cpu_media.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_bytes(b"safe fixture")
            with patch.object(Path, "open", side_effect=OSError("synthetic unreadable log")):
                self.assertEqual(tool_smoke._classify_logo_overlay_failure(task_root), unknown)
            log_path.write_bytes(b"Error reinitializing filters!" + b"x" * (tool_smoke.ACCEPTANCE_DIAGNOSTIC_TAIL_BYTES + 8))
            self.assertEqual(tool_smoke._classify_logo_overlay_failure(task_root), unknown)

    def test_logo_overlay_failure_projection_is_redacted_and_cleans_before_return(self) -> None:
        from src.services import tool_smoke

        sentinel = "".join(("C:", "/", "private", "/", "diagnostic-secret"))
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            ffmpeg = parent / "ffmpeg.exe"
            ffprobe = parent / "ffprobe.exe"
            ffmpeg.write_bytes(b"fixture")
            ffprobe.write_bytes(b"fixture")
            approval = parent / "approval.json"
            task_root = parent / "failure-root"

            def fail_logo_overlay(root: Path, _ffmpeg: Path, _owner: object) -> None:
                log_path = root / "logs" / "workers" / "ffmpeg_logo_overlay_acceptance_cpu_media.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(f"{sentinel}\nError reinitializing filters!\n", encoding="utf-8")
                raise tool_smoke.AcceptanceFailure("logo_overlay_failed")

            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", side_effect=fail_logo_overlay),
                patch.object(tool_smoke, "RUNTIME_EVIDENCE_STATE_PATH", parent / "runtime-evidence.json"),
            ):
                result = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=approval, task_root=task_root, repo_root=parent)
            self.assertEqual(result["failure_code"], "logo_overlay_failed")
            self.assertEqual(result["diagnostic"], {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "filter_graph"})
            self.assertTrue(result["temp_cleaned"])
            self.assertEqual(result["processes_remaining"], 0)
            self.assertFalse(task_root.exists())
            self.assertNotIn(sentinel, json.dumps(result, ensure_ascii=False))

            non_logo_root = parent / "non-logo-failure-root"
            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", side_effect=tool_smoke.AcceptanceFailure("video_grade_failed")),
                patch.object(tool_smoke, "RUNTIME_EVIDENCE_STATE_PATH", parent / "runtime-evidence.json"),
            ):
                non_logo = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=approval, task_root=non_logo_root, repo_root=parent)
            self.assertNotIn("diagnostic", non_logo)
            self.assertFalse(non_logo_root.exists())

    def test_runtime_evidence_writer_is_bounded_atomic_and_cleans_failed_temps(self) -> None:
        from src.services import tool_smoke

        result = {
            "status": "error",
            "execution": "attempted",
            "failure_code": "logo_overlay_failed",
            "diagnostic": {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        }
        record = tool_smoke._runtime_evidence_record(result)
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            state = parent / "runtime-evidence.json"
            self.assertTrue(tool_smoke._write_runtime_evidence(record, path=state))
            original = state.read_bytes()
            self.assertEqual(tool_smoke.read_runtime_evidence(state), record)
            projection = tool_smoke.runtime_evidence_projection(path=state)
            self.assertEqual(projection["status"], "unavailable")
            self.assertEqual(projection["outcome"], "error")
            self.assertEqual(projection["failure_class"], "unknown")
            for failure in ("fsync", "replace"):
                state.write_bytes(original)
                with patch.object(tool_smoke.os, failure, side_effect=OSError("synthetic persistence failure")):
                    self.assertFalse(tool_smoke._write_runtime_evidence(record, path=state))
                self.assertEqual(state.read_bytes(), original)
                self.assertEqual(list(parent.glob(".*.tmp")), [])

    def test_runtime_evidence_reader_fails_closed_and_preserves_invalid_bytes(self) -> None:
        from src.services import tool_smoke

        base = tool_smoke._runtime_evidence_record({
            "status": "error",
            "execution": "attempted",
            "diagnostic": {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        })
        marker = "client-private-marker"
        cases = [
            b"{}",
            b"x" * (tool_smoke.RUNTIME_EVIDENCE_MAX_BYTES + 1),
            json.dumps({**base, "unknown_control": marker}).encode("utf-8"),
            json.dumps({**base, "outcome": 7}).encode("utf-8"),
        ]
        with TemporaryDirectory() as directory:
            state = Path(directory) / "runtime-evidence.json"
            for payload in cases:
                state.write_bytes(payload)
                before = state.read_bytes()
                self.assertIsNone(tool_smoke.read_runtime_evidence(state))
                self.assertEqual(state.read_bytes(), before)
                self.assertNotIn(marker, json.dumps(tool_smoke.runtime_evidence_projection(path=state)))
            state.write_bytes(json.dumps(base).encode("utf-8"))
            with patch.object(Path, "is_symlink", return_value=True):
                self.assertIsNone(tool_smoke.read_runtime_evidence(state))
            with patch.object(Path, "open", side_effect=OSError("synthetic unreadable state")):
                self.assertIsNone(tool_smoke.read_runtime_evidence(state))

    def test_runtime_evidence_requires_exact_source_contract_and_outcome_rules(self) -> None:
        from src.services import tool_smoke

        completed = tool_smoke._runtime_evidence_record({
            "status": "completed",
            "execution": "completed",
            "artifacts": {"encoded": {"id": "artifact_" + "a" * 32, "size_bytes": 10, "sha256": "a" * 64}},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        })
        self.assertTrue(tool_smoke._runtime_evidence_record_valid(completed))
        self.assertTrue(tool_smoke.runtime_evidence_passed(completed))
        for field in ("branch", "base", "head", "tree"):
            stale = copy.deepcopy(completed)
            stale["source"][field] = "f" * 40
            self.assertFalse(tool_smoke._runtime_evidence_record_valid(stale))
            self.assertFalse(tool_smoke.runtime_evidence_passed(stale))
        stale_contract = copy.deepcopy(completed)
        stale_contract["contract_fingerprint"] = "b" * 64
        self.assertFalse(tool_smoke._runtime_evidence_record_valid(stale_contract))
        with TemporaryDirectory() as directory:
            stale_path = Path(directory) / "runtime-evidence.json"
            stale_path.write_text(json.dumps(stale_contract), encoding="utf-8")
            stale_projection = tool_smoke.runtime_evidence_projection(path=stale_path)
        self.assertEqual(stale_projection["outcome"], "not_run")
        self.assertEqual(stale_projection["reason"], "No bounded media acceptance evidence is available.")

        for failure_class in tool_smoke.ACCEPTANCE_DIAGNOSTIC_CLASSES:
            failed = tool_smoke._runtime_evidence_record({
                "status": "error",
                "execution": "attempted",
                "diagnostic": {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": failure_class},
                "processes_remaining": 0,
                "temp_cleaned": True,
                "source_overwritten": False,
            })
            self.assertTrue(tool_smoke._runtime_evidence_record_valid(failed))
            self.assertFalse(tool_smoke.runtime_evidence_passed(failed))
        for status in ("blocked", "not_run"):
            not_run = tool_smoke._runtime_evidence_record({"status": status, "execution": "not_run"})
            self.assertTrue(tool_smoke._runtime_evidence_record_valid(not_run))
            self.assertFalse(tool_smoke.runtime_evidence_passed(not_run))
        with TemporaryDirectory() as directory:
            legacy_state = Path(directory) / "tool_smoke_v3.local.json"
            with patch.object(tool_smoke, "STATE_PATH", legacy_state):
                tool_smoke.record_completed("legacy_tool")
                self.assertTrue(tool_smoke.passed("legacy_tool"))

    def test_runtime_evidence_source_overwrite_is_tri_state_and_never_promotes(self) -> None:
        from src.services import tool_smoke

        unchecked = tool_smoke._runtime_evidence_record({
            "status": "error",
            "execution": "attempted",
            "diagnostic": {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"},
            "processes_remaining": 0,
            "temp_cleaned": True,
        })
        self.assertFalse(unchecked["source_overwrite_checked"])
        self.assertIsNone(unchecked["source_overwritten"])
        self.assertTrue(tool_smoke._runtime_evidence_record_valid(unchecked))
        self.assertFalse(tool_smoke.runtime_evidence_passed(unchecked))
        invalid_unchecked = copy.deepcopy(unchecked)
        invalid_unchecked["source_overwritten"] = False
        self.assertFalse(tool_smoke._runtime_evidence_record_valid(invalid_unchecked))

        with TemporaryDirectory() as directory:
            state = Path(directory) / "runtime-evidence.json"
            for overwritten in (False, True):
                checked = copy.deepcopy(unchecked)
                checked["source_overwrite_checked"] = True
                checked["source_overwritten"] = overwritten
                self.assertTrue(tool_smoke._runtime_evidence_record_valid(checked))
                self.assertFalse(tool_smoke.runtime_evidence_passed(checked))
                self.assertTrue(tool_smoke._write_runtime_evidence(checked, path=state))
                projection = tool_smoke.runtime_evidence_projection(path=state)
                self.assertEqual(projection["source_overwrite_checked"], True)
                self.assertEqual(projection["source_overwritten"], overwritten)
                self.assertEqual(projection["status"], "unavailable")

            for outcome in ("blocked", "not_run"):
                record = tool_smoke._runtime_evidence_record({"status": outcome, "execution": "not_run"})
                self.assertTrue(tool_smoke._write_runtime_evidence(record, path=state))
                projection = tool_smoke.runtime_evidence_projection(path=state)
                self.assertEqual(projection["outcome"], outcome)
                self.assertEqual(projection["execution"], "not_run")
                self.assertEqual(projection["status"], "unavailable")
                self.assertNotEqual(projection["status"], "operational")
            missing = tool_smoke.runtime_evidence_projection(path=Path(directory) / "missing.json")
            self.assertEqual(missing["outcome"], "not_run")
            self.assertEqual(missing["reason"], "No bounded media acceptance evidence is available.")

    def test_runtime_evidence_operation_scope_is_exact_and_completed_only(self) -> None:
        from src.services import tool_smoke

        completed = tool_smoke._runtime_evidence_record({
            "status": "completed",
            "execution": "completed",
            "artifacts": {"encoded": {"id": "artifact_" + "a" * 32, "size_bytes": 10, "sha256": "a" * 64}},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        })
        scope = tool_smoke.runtime_evidence_operation_scope(completed)
        self.assertEqual(scope["schema_version"], "runtime-operation-scope.v1")
        self.assertTrue(scope["evidence_verified"])
        self.assertEqual(scope["operations"], ["video_grade", "logo_overlay", "encode"])
        self.assertEqual(scope["available_operations"], scope["operations"])
        self.assertEqual(set(scope["operation_status"].values()), {"operational"})

        failed = tool_smoke._runtime_evidence_record({
            "status": "error",
            "execution": "attempted",
            "diagnostic": {"version": tool_smoke.ACCEPTANCE_DIAGNOSTIC_VERSION, "class": "unknown"},
            "processes_remaining": 0,
            "temp_cleaned": True,
            "source_overwritten": False,
        })
        unavailable = tool_smoke.runtime_evidence_operation_scope(failed)
        self.assertFalse(unavailable["evidence_verified"])
        self.assertEqual(unavailable["available_operations"], [])
        self.assertEqual(set(unavailable["operation_status"].values()), {"partial"})

        stale = copy.deepcopy(completed)
        stale["operations"] = ["video_grade"]
        self.assertFalse(tool_smoke.runtime_evidence_passed(stale))
        self.assertFalse(tool_smoke.runtime_evidence_operation_scope(stale)["evidence_verified"])

    def test_runtime_evidence_finalizer_writes_only_after_cleanup_and_reader_is_read_only(self) -> None:
        from src.services import tool_smoke

        with TemporaryDirectory() as directory:
            parent = Path(directory)
            task_root = parent / "task-root"
            ffmpeg = parent / "ffmpeg.exe"
            ffprobe = parent / "ffprobe.exe"
            ffmpeg.write_bytes(b"fixture")
            ffprobe.write_bytes(b"fixture")
            observations: list[tuple[bool, dict]] = []

            def writer(record: dict) -> bool:
                observations.append((task_root.exists(), record))
                return True

            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", side_effect=tool_smoke.AcceptanceFailure("logo_overlay_failed")),
                patch.object(tool_smoke, "_write_runtime_evidence", side_effect=writer) as persist,
                patch.object(tool_smoke, "RUNTIME_EVIDENCE_STATE_PATH", parent / "runtime-evidence.json"),
            ):
                result = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=parent / "approval.json", task_root=task_root, repo_root=parent)
            self.assertEqual(result["failure_code"], "logo_overlay_failed")
            persist.assert_called_once()
            self.assertEqual(observations[0][0], False)
            with patch.object(tool_smoke, "_write_runtime_evidence", side_effect=AssertionError("reader must not write")) as writer_mock, patch.object(tool_smoke.subprocess, "run", side_effect=AssertionError("reader must not run git")) as run_mock:
                projection = tool_smoke.runtime_evidence_projection(path=parent / "missing.json")
            writer_mock.assert_not_called()
            run_mock.assert_not_called()
            self.assertEqual(projection["execution"], "not_run")

    def test_bounds_and_truthful_unavailable_result_never_echo_paths(self) -> None:
        from src.services import tool_smoke

        sentinel = "".join(("C:", "/", "private", "/", "acceptance-fixture.bin"))
        artifact = {
            "id": "artifact_" + "b" * 32,
            "media_type": "video/mp4",
            "size_bytes": 32,
            "sha256": "b" * 64,
            "path": sentinel,
        }
        evidence = tool_smoke._artifact_evidence(artifact, maximum_bytes=64)
        self.assertNotIn("path", evidence)
        self.assertEqual(evidence["size_bytes"], 32)
        with self.assertRaises(tool_smoke.AcceptanceFailure):
            tool_smoke._artifact_evidence({**artifact, "size_bytes": 65}, maximum_bytes=64)

        result = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=Path(sentinel), task_root=Path(sentinel + ".root"))
        self.assertIn(result["status"], {"blocked", "unavailable"})
        self.assertNotIn(sentinel, json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
