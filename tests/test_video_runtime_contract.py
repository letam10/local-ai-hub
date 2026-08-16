from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class VideoRuntimeContractTests(unittest.TestCase):
    def _owned_temp_root(self) -> Path:
        root = Path(__file__).resolve().parents[1] / "Temp" / f"test-video-runtime-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def _run_worker(self, module, request: dict, fake_run):
        stream = SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode("utf-8")))
        with patch.object(module.sys, "stdin", stream), patch.object(module, "run_hidden", side_effect=fake_run):
            with patch("builtins.print") as printed:
                exit_code = module.main()
        payload = json.loads(printed.call_args.args[0])
        return exit_code, payload

    def test_animesr_uses_checked_cli_and_explicit_ffmpeg(self) -> None:
        from src.modules.animesr.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            script = runtime / "scripts" / "inference_animesr_video.py"
            script.parent.mkdir(parents=True)
            script.write_text("# fixture", encoding="utf-8")
            source = root / "input.mp4"
            source.write_bytes(b"video")
            ffmpeg = runtime / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"tool")
            output_root = root / "Output" / "AnimeSR"
            seen: dict[str, object] = {}

            def fake_run(command, **kwargs):
                seen["command"] = command
                seen["environment"] = kwargs["env"]
                output = output_root.parent.parent / "Temp" / "jobs"
                task = next(output.glob("animesr_*"))
                artifact = task / "animesr_v2" / "videos" / "input" / "result.mp4"
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b"result")
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                code, payload = self._run_worker(worker, {"runtime": str(runtime), "path": str(source), "output_root": str(output_root), "ffmpeg": str(ffmpeg), "scale": 2}, fake_run)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "completed")
            command = seen["command"]
            self.assertIn("--netscale", command)
            self.assertNotIn("--low-memory-frame-write", command)
            self.assertEqual(seen["environment"]["ffmpeg_exe_path"], str(ffmpeg))

    def test_rife_worker_constrains_ffmpeg_path_and_uses_ffprobe(self) -> None:
        from src.modules.practical_rife.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            (runtime / "train_log" / "RIFEv4.26_0921").mkdir(parents=True)
            (runtime / "inference_video.py").write_text("# fixture", encoding="utf-8")
            (runtime / "train_log" / "RIFEv4.26_0921" / "flownet.pkl").write_bytes(b"model")
            source = root / "input.mp4"
            source.write_bytes(b"video")
            tools = runtime / "tools"
            tools.mkdir()
            ffmpeg, ffprobe = tools / "ffmpeg.exe", tools / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            output_root = root / "Output" / "Practical-RIFE"
            temp_root = root / "Temp" / "jobs" / "rife_fixture"
            calls: list[tuple[list[str], dict]] = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                if command[0] == str(ffmpeg) and "-show_entries" not in command:
                    (temp_root / "interpolated_with_audio.mp4").write_bytes(b"muxed")
                elif "inference_video.py" in command[1]:
                    (temp_root / "interpolated.mp4").write_bytes(b"video")
                return SimpleNamespace(returncode=0, stdout=b"{}", stderr=b"")

            old_system_root = os.environ.get("SystemRoot")
            os.environ["SystemRoot"] = str(root / "Windows")
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                code, payload = self._run_worker(worker, {"runtime": str(runtime), "path": str(source), "model_dir": str(runtime / "train_log" / "RIFEv4.26_0921"), "ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe), "output_root": str(output_root), "temp_root": str(temp_root), "target_fps": 48}, fake_run)
            finally:
                if old_system_root is None:
                    os.environ.pop("SystemRoot", None)
                else:
                    os.environ["SystemRoot"] = old_system_root
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 0)
            self.assertEqual(payload["backend"], "practical_rife")
            self.assertEqual(calls[0][1]["env"]["PATH"].split(os.pathsep)[0], str(tools))
            self.assertEqual(calls[-1][0][0], str(ffprobe))

    def test_media_dispatches_only_allowlisted_rife_and_realesrgan_backends(self) -> None:
        from src.modules.media_editor.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"video")
            artifact_id = "artifact_" + "a" * 32
            with patch.object(adapter, "resolve", return_value=source), patch.object(adapter, "describe", return_value={"media_type": "video/mp4"}), patch("src.modules.practical_rife.backend.adapter.run_practical_rife", return_value={"status": "unavailable", "component": "practical_rife"}) as rife:
                result = adapter.run_operation({"operation": "frame_interpolate", "backend": "practical_rife", "source_artifact_id": artifact_id})
            self.assertEqual(result["component"], "practical_rife")
            self.assertEqual(rife.call_args.args[0]["source_artifact_id"], artifact_id)
            image = Path(directory) / "input.png"
            image.write_bytes(b"png")
            image_id = "artifact_" + "b" * 32
            with patch.object(adapter, "resolve", return_value=image), patch.object(adapter, "describe", return_value={"media_type": "image/png"}), patch("src.modules.real_esrgan.backend.adapter.run_realesrgan", return_value={"status": "unavailable", "component": "real_esrgan"}) as esrgan:
                result = adapter.run_operation({"operation": "image_upscale", "backend": "real_esrgan", "source_artifact_id": image_id})
            self.assertEqual(result["component"], "real_esrgan")
            self.assertEqual(esrgan.call_args.args[0]["source_artifact_id"], image_id)

    def test_public_gpu_media_payloads_reject_raw_paths_before_worker_launch(self) -> None:
        from src.modules.animesr.backend import adapter as animesr
        from src.modules.media_editor.backend import adapter as media
        from src.services.api import core

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.mp4"
            source.write_bytes(b"video")
            raw = {"operation": "frame_interpolate", "backend": "practical_rife", "path": str(source)}
            with patch("src.modules.practical_rife.backend.adapter.run_practical_rife") as rife:
                result = media.run_operation(raw)
            self.assertEqual(result["status"], "error")
            rife.assert_not_called()
            with patch.object(animesr, "run_json_worker") as launch:
                result = animesr.run_animesr({"path": str(source), "executable": "not-allowed"})
            self.assertIn(result["status"], {"error", "unavailable"})
            self.assertIsNone(animesr._video_artifact({"path": str(source), "executable": "not-allowed"}))
            launch.assert_not_called()
            resolved, error = core._resolve_assets({"operation": "image_upscale", "backend": "real_esrgan", "asset_id": "artifact_" + "c" * 32}, tool="run_media_operation")
            self.assertIsNone(error)
            self.assertEqual(resolved["source_artifact_id"], "artifact_" + "c" * 32)
            self.assertNotIn("path", resolved)

    def test_rife_reparse_output_parent_refuses_before_worker_launch(self) -> None:
        from src.modules.practical_rife.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            model_dir = runtime / "train_log" / "RIFEv4.26_0921"
            model_dir.mkdir(parents=True)
            (runtime / "inference_video.py").write_text("# fixture", encoding="utf-8")
            (model_dir / "flownet.pkl").write_bytes(b"model")
            source = root / "input.mp4"
            source.write_bytes(b"video")
            tools = runtime / "tools"
            tools.mkdir()
            ffmpeg, ffprobe = tools / "ffmpeg.exe", tools / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            (root / "Output").mkdir()
            request = {"runtime": str(runtime), "path": str(source), "model_dir": str(model_dir), "ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe), "output_root": str(root / "Output" / "Practical-RIFE"), "temp_root": str(root / "Temp" / "jobs" / "rife_fixture")}
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "_is_reparse", side_effect=lambda path: path.name == "Output"), patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()
            self.assertFalse((root / "Output" / "Practical-RIFE").exists())

    def test_realesrgan_worker_uses_existing_model_without_download(self) -> None:
        from src.modules.real_esrgan.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "engines" / "video" / "Real-ESRGAN"
            runtime.mkdir(parents=True)
            (runtime / "inference_realesrgan.py").write_text("# fixture", encoding="utf-8")
            models = root / "Models" / "Video" / "Real-ESRGAN"
            models.mkdir(parents=True)
            model = models / "realesr-animevideov3.pth"
            model.write_bytes(b"model")
            source = root / "input.png"
            source.write_bytes(b"image")
            output_root = root / "Output" / "Real-ESRGAN"
            temp_root = root / "Temp" / "jobs" / "realesrgan_fixture"
            seen: dict[str, object] = {}

            def fake_run(command, **kwargs):
                seen["command"] = command
                (temp_root / "input_out.png").write_bytes(b"result")
                return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                code, payload = self._run_worker(worker, {"runtime": str(runtime), "path": str(source), "model_path": str(model), "output_root": str(output_root), "temp_root": str(temp_root), "scale": 2}, fake_run)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 0)
            self.assertEqual(payload["backend"], "real_esrgan")
            self.assertIn("--model_path", seen["command"])
            self.assertIn(str(model), seen["command"])

    def test_real_esrgan_model_selector_refuses_outside_models_root(self) -> None:
        from src.modules.real_esrgan.backend import adapter

        outside = Path(tempfile.gettempdir()) / "not-a-hub-model.pth"
        outside.write_bytes(b"model")
        try:
            with patch.object(adapter, "models", return_value=[{"id": "realesr-animevideov3", "engine": "Real-ESRGAN", "local_path": str(outside)}]):
                self.assertIsNone(adapter._selected_model())
        finally:
            outside.unlink(missing_ok=True)

    def test_video_worker_output_is_published_as_job_bound_artifact(self) -> None:
        """A completed video worker must survive the Hub job/artifact boundary."""

        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        output_root.mkdir()
        index_path = root / "Config" / "artifacts.json"
        target = output_root / "AnimeSR" / "clip_AnimeSR_x2.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"bounded video output")
        manager = HubJobManager()
        try:
            with (
                patch.object(api_jobs, "_jobs", {}),
                patch.object(api_jobs, "_save"),
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "INDEX_PATH", index_path),
            ):
                record = manager.submit(
                    "upscale_anime_video",
                    {"source_artifact_id": "artifact_" + "a" * 32},
                    lambda _payload, _context: {
                        "status": "completed",
                        "operation": "upscale_anime_video",
                        "backend": "animesr",
                        "output": str(target),
                    },
                )
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "completed")
                artifacts = public["result"]["artifacts"]
                self.assertEqual(len(artifacts), 1)
                artifact = artifacts[0]
                self.assertRegex(artifact["id"], r"^artifact_[a-f0-9]{32}$")
                self.assertEqual(artifact["media_type"], "video/mp4")
                self.assertEqual(artifact["provenance"]["job_id"], record["id"])
                self.assertEqual(artifact["provenance"]["status"], "completed")
                self.assertNotIn(str(target), json.dumps(public, ensure_ascii=False))
                self.assertNotIn("output", public["result"])
                self.assertEqual(artifact_store.describe(artifact["id"])["provenance"]["adapter_id"], "upscale_anime_video")
        finally:
            manager.cancel_all_and_wait(3)

    def test_video_output_outside_hub_fails_closed_without_artifact(self) -> None:
        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        output_root.mkdir()
        outside = root / "not-managed.mp4"
        outside.write_bytes(b"outside")
        manager = HubJobManager()
        try:
            with (
                patch.object(api_jobs, "_jobs", {}),
                patch.object(api_jobs, "_save"),
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "INDEX_PATH", root / "Config" / "artifacts.json"),
            ):
                record = manager.submit(
                    "run_media_operation",
                    {"source_artifact_id": "artifact_" + "b" * 32},
                    lambda _payload, _context: {"status": "completed", "output": str(outside)},
                )
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "failed")
                self.assertEqual(public["result"]["status"], "failed")
                self.assertNotIn(str(outside), json.dumps(public, ensure_ascii=False))
                self.assertFalse((root / "Config" / "artifacts.json").exists())
        finally:
            manager.cancel_all_and_wait(3)


if __name__ == "__main__":
    unittest.main()
