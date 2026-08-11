from __future__ import annotations

import json
import os
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class AcceptanceRuntimeTests(unittest.TestCase):
    def test_cpu_acceptance_requires_explicit_opt_in(self) -> None:
        from src.services import tool_smoke

        with TemporaryDirectory() as directory:
            root = Path(directory) / "acceptance-root"
            result = tool_smoke.run_cpu_media_acceptance(opt_in=False, task_root=root)
        self.assertEqual(result["status"], "not_run")
        self.assertEqual(result["execution"], "not_run")
        self.assertFalse(root.exists())

    def test_approval_guard_requires_exact_scope_and_base_ancestry(self) -> None:
        from src.services import tool_smoke

        approval = {
            "approval_id": tool_smoke.ACCEPTANCE_APPROVAL_ID,
            "status": "approved",
            "owner": "LAH 2",
            "release": {"branch": "feature/local-ai-hub-v5", "head": tool_smoke.ACCEPTANCE_RELEASE_HEAD},
            "limits": {"wall_seconds": 60, "no_retry": True},
            "allowed_output": {"maximum_pipeline_jobs": 1, "operations": ["video_grade", "logo_overlay", "encode"]},
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            approval_path = root / "approval.json"
            approval_path.write_text(json.dumps(approval), encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
            with patch.object(tool_smoke.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, stdout=tool_smoke.ACCEPTANCE_BRANCH + "\n", stderr=""),
                subprocess.CompletedProcess([], 0, stdout=tool_smoke.ACCEPTANCE_RELEASE_HEAD + "\n", stderr=""),
                completed,
                completed,
            ]):
                ok, code = tool_smoke._approval_guard(approval_path, root)
        self.assertTrue(ok)
        self.assertEqual(code, "ok")

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
            with (
                patch.object(tool_smoke, "_approval_guard", return_value=(True, "ok")),
                patch("src.modules.media_editor.backend.adapter._paths", return_value=(ffmpeg, ffprobe)),
                patch.object(tool_smoke, "_run_cpu_pipeline", return_value=evidence),
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
            ):
                failure = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=approval, task_root=failure_root, repo_root=parent)
            self.assertEqual(failure["status"], "unavailable")
            self.assertTrue(failure["temp_cleaned"])
            self.assertFalse(failure_root.exists())

    def test_bounds_and_truthful_unavailable_result_never_echo_paths(self) -> None:
        from src.services import tool_smoke

        path_secret = str(Path(__file__).resolve())
        artifact = {
            "id": "artifact_" + "b" * 32,
            "media_type": "video/mp4",
            "size_bytes": 32,
            "sha256": "b" * 64,
            "path": path_secret,
        }
        evidence = tool_smoke._artifact_evidence(artifact, maximum_bytes=64)
        self.assertNotIn("path", evidence)
        self.assertEqual(evidence["size_bytes"], 32)
        with self.assertRaises(tool_smoke.AcceptanceFailure):
            tool_smoke._artifact_evidence({**artifact, "size_bytes": 65}, maximum_bytes=64)

        result = tool_smoke.run_cpu_media_acceptance(opt_in=True, approval_path=Path(path_secret), task_root=Path(path_secret + ".root"))
        self.assertIn(result["status"], {"blocked", "unavailable"})
        self.assertNotIn(path_secret, json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
