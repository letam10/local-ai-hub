from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import threading
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
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            ffmpeg = runtime / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"tool")
            model_path = root / "Models" / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"model")
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
                code, payload = self._run_worker(worker, {"runtime": str(runtime), "path": str(source), "output_root": str(output_root), "ffmpeg": str(ffmpeg), "model_id": "animesr-v2", "model": "AnimeSR_v2", "model_path": str(model_path), "expname": "animesr_v2", "scale": 2}, fake_run)
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
            self.assertEqual(command[command.index("-n") + 1], "AnimeSR_v2")
            self.assertEqual(command[command.index("--expname") + 1], "animesr_v2")
            self.assertEqual(seen["environment"]["ffmpeg_exe_path"], str(ffmpeg))

    def test_animesr_worker_rejects_model_leaf_outside_models_root(self) -> None:
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
            (root / "Models").mkdir()
            forged_model = root / "runtime" / "forged-model"
            forged_model.write_bytes(b"not a Models leaf")
            output_root = root / "Output" / "AnimeSR"
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, {
                        "runtime": str(runtime), "path": str(source), "output_root": str(output_root),
                        "ffmpeg": str(ffmpeg), "model_id": "animesr-v2", "model": "AnimeSR_v2",
                        "model_path": str(forged_model), "expname": "animesr_v2", "scale": 2,
                    }, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()

    def test_animesr_worker_rejects_external_source_before_launch_or_write(self) -> None:
        from src.modules.animesr.backend import worker

        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external_directory:
            root = Path(directory)
            runtime = root / "runtime"
            script = runtime / "scripts" / "inference_animesr_video.py"
            script.parent.mkdir(parents=True)
            script.write_text("# fixture", encoding="utf-8")
            source = Path(external_directory) / "input.mp4"
            source.write_bytes(b"external video")
            ffmpeg = runtime / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"tool")
            model_path = root / "Models" / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"model")
            output_root = root / "Output" / "AnimeSR"
            request = {
                "runtime": str(runtime),
                "path": str(source),
                "output_root": str(output_root),
                "ffmpeg": str(ffmpeg),
                "model_id": "animesr-v2",
                "model": "AnimeSR_v2",
                "model_path": str(model_path),
                "expname": "animesr_v2",
                "scale": 2,
            }
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()
            self.assertFalse(output_root.exists())
            self.assertFalse((root / "Temp" / "jobs").exists())

    def test_animesr_worker_rejects_reparse_source_before_launch_or_write(self) -> None:
        from src.modules.animesr.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            script = runtime / "scripts" / "inference_animesr_video.py"
            script.parent.mkdir(parents=True)
            script.write_text("# fixture", encoding="utf-8")
            real_source = root / "real-input.mp4"
            real_source.write_bytes(b"video")
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            try:
                source.symlink_to(real_source)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            ffmpeg = runtime / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"tool")
            model_path = root / "Models" / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"model")
            output_root = root / "Output" / "AnimeSR"
            request = {
                "runtime": str(runtime),
                "path": str(source),
                "output_root": str(output_root),
                "ffmpeg": str(ffmpeg),
                "model_id": "animesr-v2",
                "model": "AnimeSR_v2",
                "model_path": str(model_path),
                "expname": "animesr_v2",
                "scale": 2,
            }
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()
            self.assertFalse(output_root.exists())
            self.assertFalse((root / "Temp" / "jobs").exists())

    def test_animesr_worker_rejects_reparse_script_before_launch_or_write(self) -> None:
        from src.modules.animesr.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            real_scripts = root / "outside-scripts"
            real_scripts.mkdir()
            real_script = real_scripts / "inference_animesr_video.py"
            real_script.write_text("# external fixture", encoding="utf-8")
            scripts = runtime / "scripts"
            scripts.mkdir(parents=True)
            script = scripts / "inference_animesr_video.py"
            try:
                script.symlink_to(real_script)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            ffmpeg = runtime / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"tool")
            model_path = root / "Models" / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"model")
            output_root = root / "Output" / "AnimeSR"
            request = {
                "runtime": str(runtime),
                "path": str(source),
                "output_root": str(output_root),
                "ffmpeg": str(ffmpeg),
                "model_id": "animesr-v2",
                "model": "AnimeSR_v2",
                "model_path": str(model_path),
                "expname": "animesr_v2",
                "scale": 2,
            }
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()
            self.assertFalse(output_root.exists())
            self.assertFalse((root / "Temp" / "jobs").exists())

    def test_animesr_adapter_rejects_models_reparse_alias(self) -> None:
        from src.modules.animesr.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_root = root / "Models"
            model_root.mkdir()
            real_model = root / "real-model"
            real_model.mkdir()
            alias = model_root / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            alias.parent.mkdir(parents=True)
            try:
                alias.symlink_to(real_model, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            with (
                patch.object(adapter, "MODEL_ROOT", model_root),
                patch.object(adapter, "models", return_value=[{"id": "animesr-v2", "engine": "AnimeSR", "local_path": str(alias)}]),
            ):
                self.assertIsNone(adapter._selected_model())

    def test_animesr_worker_rejects_models_reparse_alias_before_launch(self) -> None:
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
            models = root / "Models"
            models.mkdir()
            real_model = root / "real-model"
            real_model.mkdir()
            alias = models / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            alias.parent.mkdir(parents=True)
            try:
                alias.symlink_to(real_model, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            output_root = root / "Output" / "AnimeSR"
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, {
                        "runtime": str(runtime), "path": str(source), "output_root": str(output_root),
                        "ffmpeg": str(ffmpeg), "model_id": "animesr-v2", "model": "AnimeSR_v2",
                        "model_path": str(alias), "expname": "animesr_v2", "scale": 2,
                    }, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()

    def test_animesr_adapter_binds_registry_model_to_cli_contract(self) -> None:
        from src.modules.animesr.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_root = root / "Models"
            model_path = model_root / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"model")
            runtime = root / "runtime"
            runtime.mkdir()
            script = runtime / "scripts" / "inference_animesr_video.py"
            script.parent.mkdir(parents=True)
            script.write_text("# fixture", encoding="utf-8")
            environment = root / "Environments" / "animesr"
            python = environment / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            ffmpeg = root / "runtime" / "tools" / "ffmpeg" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg.write_bytes(b"ffmpeg")
            source = root / "Temp" / "uploads" / "source.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            artifact_id = "artifact_" + "1" * 32
            seen: dict[str, object] = {}

            def fake_component(component: str):
                if component == "animesr":
                    return {"id": "animesr", "path": str(runtime), "environment": str(environment)}
                if component == "ffmpeg":
                    return {"id": "ffmpeg", "path": str(ffmpeg.parent), "executable": str(ffmpeg)}
                return None

            def fake_worker(_command, request, **_kwargs):
                seen.update(request)
                return {"status": "completed", "operation": "upscale_anime_video"}

            with (
                patch.object(adapter, "MODEL_ROOT", model_root),
                patch.object(adapter, "models", return_value=[{"id": "animesr-v2", "engine": "AnimeSR", "local_path": str(model_path)}]),
                patch.object(adapter, "component", side_effect=fake_component),
                patch.object(adapter, "resolve", return_value=source),
                patch.object(adapter, "describe", return_value={"media_type": "video/mp4"}),
                patch.object(adapter, "run_json_worker", side_effect=fake_worker),
            ):
                result = adapter.run_animesr({"source_artifact_id": artifact_id, "model": "AnimeSR_v1-PaperModel", "scale": 2})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(seen["model_id"], "animesr-v2")
            self.assertEqual(seen["model"], "AnimeSR_v2")
            self.assertEqual(seen["expname"], "animesr_v2")
            self.assertEqual(seen["model_path"], str(model_path))

    def test_animesr_adapter_refuses_missing_registry_model_before_worker(self) -> None:
        from src.modules.animesr.backend import adapter

        with (
            patch.object(adapter, "models", return_value=[]),
            patch.object(adapter, "run_json_worker") as launch,
        ):
            result = adapter.run_animesr({"source_artifact_id": "artifact_" + "2" * 32})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["component"], "animesr")
        launch.assert_not_called()

    def test_animesr_capability_and_adapter_require_fixed_script_leaf(self) -> None:
        from src.modules.animesr.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            python = root / "Environments" / "animesr" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            model = root / "Models" / "Video" / "AnimeSR" / "AnimeSR_v2.pth"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model")
            ffmpeg = root / "runtime" / "tools" / "ffmpeg.exe"
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.write_bytes(b"ffmpeg")
            fixed = runtime / "scripts" / "inference_animesr_video.py"
            with (
                patch.object(adapter, "_runtime", return_value=(python, runtime)),
                patch.object(adapter, "_selected_model", return_value=model),
                patch.object(adapter, "_registry_path", return_value=ffmpeg),
            ):
                self.assertFalse(adapter.capability()["script_ready"])
                with patch.object(adapter, "run_json_worker") as launch:
                    result = adapter.run_animesr({"source_artifact_id": "artifact_" + "5" * 32})
                self.assertEqual(result["status"], "unavailable")
                launch.assert_not_called()

                fixed.mkdir(parents=True)
                self.assertFalse(adapter.capability()["script_ready"])
                fixed.rmdir()
                fixed.write_text("# fixture", encoding="utf-8")
                self.assertTrue(adapter.capability()["script_ready"])

                outside = root / "outside-animesr.py"
                outside.write_text("# outside", encoding="utf-8")
                fixed.unlink()
                try:
                    fixed.symlink_to(outside)
                except OSError as exc:
                    self.skipTest(f"symlink fixture unavailable: {exc}")
                self.assertFalse(adapter.capability()["script_ready"])

    def test_video_adapters_bind_runtime_from_registry_not_ambient_environment(self) -> None:
        from src.modules.practical_rife.backend import adapter as rife
        from src.modules.real_esrgan.backend import adapter as esrgan

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rife_runtime = root / "runtime" / "engines" / "video" / "Practical-RIFE"
            rife_env = root / "Environments" / "practical_rife"
            esrgan_runtime = root / "runtime" / "engines" / "video" / "Real-ESRGAN"
            esrgan_env = root / "Environments" / "real_esrgan"
            outside = root / "ambient-runtime"

            def registry(component_id: str):
                return {
                    "practical_rife": {"id": "practical_rife", "path": str(rife_runtime), "environment": str(rife_env)},
                    "real_esrgan": {"id": "real_esrgan", "path": str(esrgan_runtime), "environment": str(esrgan_env)},
                }.get(component_id)

            with patch.object(rife, "component", side_effect=registry), patch.object(esrgan, "component", side_effect=registry), patch.dict(
                os.environ,
                {"PRACTICAL_RIFE_HOME": str(outside), "PRACTICAL_RIFE_ENV": str(outside), "REAL_ESRGAN_HOME": str(outside), "REAL_ESRGAN_ENV": str(outside)},
            ):
                _rife_python, selected_rife, _rife_model = rife._runtime()
                _esrgan_python, selected_esrgan = esrgan._runtime()

            self.assertEqual(selected_rife, rife_runtime)
            self.assertEqual(selected_esrgan, esrgan_runtime)
            self.assertNotEqual(selected_rife, outside)
            self.assertNotEqual(selected_esrgan, outside)

    def test_rife_requires_one_registry_ffmpeg_pair(self) -> None:
        from src.modules.practical_rife.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "runtime" / "tools" / "ffmpeg"
            second = root / "other-tools"
            with patch.object(adapter, "component", side_effect=lambda component_id: {
                "ffmpeg": {"id": "ffmpeg", "path": str(second), "executable": str(first / "ffmpeg.exe")},
            }.get(component_id)):
                ffmpeg, ffprobe = adapter._configured_ffmpeg()
            self.assertIsNone(ffmpeg)
            self.assertIsNone(ffprobe)

    def test_real_esrgan_adapter_requires_fixed_model_leaf(self) -> None:
        from src.modules.real_esrgan.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_root = root / "Models"
            wrong = model_root / "Video" / "Real-ESRGAN" / "other.pth"
            wrong.parent.mkdir(parents=True)
            wrong.write_bytes(b"model")
            with patch.object(adapter, "MODEL_ROOT", model_root), patch.object(
                adapter, "models", return_value=[{"id": "realesr-animevideov3", "engine": "Real-ESRGAN", "local_path": str(wrong)}]
            ):
                self.assertIsNone(adapter._selected_model())

    def test_rife_worker_constrains_ffmpeg_path_and_uses_ffprobe(self) -> None:
        from src.modules.practical_rife.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            (runtime / "train_log" / "RIFEv4.26_0921").mkdir(parents=True)
            (runtime / "inference_video.py").write_text("# fixture", encoding="utf-8")
            (runtime / "train_log" / "RIFEv4.26_0921" / "flownet.pkl").write_bytes(b"model")
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            tools = runtime / "tools" / "ffmpeg"
            tools.mkdir(parents=True)
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

    def test_selected_missing_video_backend_refuses_before_job_queue(self) -> None:
        from src.services.api import core

        missing = {
            "backend": "practical_rife",
            "component": "practical_rife",
            "component_status": "missing",
            "tool_status": "unavailable",
            "status": "unavailable",
            "queue_allowed": False,
            "reason": "Practical-RIFE static prerequisites are unavailable.",
            "action": "Restore the bounded backend leaves.",
        }
        with (
            patch.object(core, "component_statuses", return_value=[{"id": "ffmpeg", "component_status": "installed"}]),
            patch.object(core, "_media_backend_readiness", return_value=missing),
            patch.object(core.job_manager, "submit") as submit,
        ):
            status, payload = core.submit_tool(
                "run_media_operation",
                {"operation": "frame_interpolate", "backend": "practical_rife", "source_artifact_id": "artifact_" + "3" * 32},
            )
        self.assertEqual(status, 503)
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["backend"], "practical_rife")
        self.assertNotIn("path", json.dumps(payload, ensure_ascii=False))
        submit.assert_not_called()

    def test_animesr_missing_model_refuses_before_job_queue(self) -> None:
        from src.services.api import core

        missing = {
            "backend": "animesr",
            "component": "animesr",
            "component_status": "missing",
            "tool_status": "unavailable",
            "status": "unavailable",
            "queue_allowed": False,
            "reason": "AnimeSR model registry record is unavailable.",
            "action": "Restore the fixed AnimeSR model record.",
        }
        with (
            patch.object(core, "component_statuses", return_value=[{"id": "animesr", "component_status": "installed"}]),
            patch.object(core, "_media_backend_readiness", return_value=missing),
            patch.object(core.job_manager, "submit") as submit,
        ):
            status, payload = core.submit_tool("upscale_anime_video", {"source_artifact_id": "artifact_" + "4" * 32})
        self.assertEqual(status, 503)
        self.assertEqual(payload["backend"], "animesr")
        submit.assert_not_called()

    def test_video_backend_readiness_is_static_partial_until_smoke(self) -> None:
        from src.services.api import core

        with (
            patch("src.modules.animesr.backend.adapter.capability", return_value={
                "runtime_ready": True, "environment_ready": True, "model_ready": True,
                "script_ready": True, "ffmpeg_ready": True, "worker_ready": True,
            }),
            patch("src.modules.practical_rife.backend.adapter.capability", return_value={
                "runtime_ready": True, "environment_ready": True, "script_ready": True,
                "ffmpeg_ready": True, "worker_ready": True,
            }),
            patch("src.modules.real_esrgan.backend.adapter.capability", return_value={
                "runtime_ready": True, "environment_ready": True, "model_ready": True, "script_ready": True, "worker_ready": True,
            }),
        ):
            readiness = core._media_backend_readiness()
        assert readiness is not None
        for backend in ("animesr", "practical_rife", "real_esrgan"):
            self.assertEqual(readiness[backend]["status"], "partial")
            self.assertTrue(readiness[backend]["queue_allowed"])
            self.assertNotEqual(readiness[backend]["status"], "operational")

    def test_practical_rife_capability_and_adapter_require_fixed_script_leaf(self) -> None:
        from src.modules.practical_rife.backend import adapter

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            model_dir = runtime / "train_log" / "RIFEv4.26_0921"
            model_dir.mkdir(parents=True)
            (model_dir / "flownet.pkl").write_bytes(b"model")
            python = root / "Environments" / "practical_rife" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_bytes(b"python")
            tools = root / "runtime" / "tools" / "ffmpeg"
            tools.mkdir(parents=True)
            ffmpeg, ffprobe = tools / "ffmpeg.exe", tools / "ffprobe.exe"
            ffmpeg.write_bytes(b"ffmpeg")
            ffprobe.write_bytes(b"ffprobe")
            fixed = runtime / "inference_video.py"
            with (
                patch.object(adapter, "_runtime", return_value=(python, runtime, model_dir)),
                patch.object(adapter, "_configured_ffmpeg", return_value=(ffmpeg, ffprobe)),
            ):
                self.assertFalse(adapter.capability()["script_ready"])
                with patch.object(adapter, "run_json_worker") as launch:
                    result = adapter.run_practical_rife({"source_artifact_id": "artifact_" + "6" * 32})
                self.assertEqual(result["status"], "unavailable")
                launch.assert_not_called()

                fixed.mkdir()
                self.assertFalse(adapter.capability()["script_ready"])
                fixed.rmdir()
                fixed.write_text("# fixture", encoding="utf-8")
                self.assertTrue(adapter.capability()["script_ready"])

                outside = root / "outside-rife.py"
                outside.write_text("# outside", encoding="utf-8")
                fixed.unlink()
                try:
                    fixed.symlink_to(outside)
                except OSError as exc:
                    self.skipTest(f"symlink fixture unavailable: {exc}")
                self.assertFalse(adapter.capability()["script_ready"])

    def test_missing_fixed_video_script_blocks_queue_before_job_manager(self) -> None:
        from src.services.api import core

        cases = (
            (
                "animesr",
                "upscale_anime_video",
                "src.modules.animesr.backend.adapter.capability",
                {"runtime_ready": True, "environment_ready": True, "model_ready": True, "script_ready": False, "ffmpeg_ready": True, "worker_ready": True},
            ),
            (
                "practical_rife",
                "frame_interpolate",
                "src.modules.practical_rife.backend.adapter.capability",
                {"runtime_ready": True, "environment_ready": True, "script_ready": False, "ffmpeg_ready": True, "worker_ready": True},
            ),
        )
        for backend, operation, capability_path, observed in cases:
            with self.subTest(backend=backend):
                with (
                    patch.object(core, "component_statuses", return_value=[{"id": "ffmpeg", "component_status": "installed"}]),
                    patch(capability_path, return_value=observed),
                    patch.object(core.job_manager, "submit") as submit,
                ):
                    readiness = core._media_backend_readiness(operation=operation, backend=backend)
                    assert readiness is not None
                    self.assertFalse(readiness["queue_allowed"])
                    status, payload = core.submit_tool(
                        "run_media_operation",
                        {"operation": operation, "backend": backend, "source_artifact_id": "artifact_" + "7" * 32},
                    )
                self.assertEqual(status, 503)
                self.assertEqual(payload["status"], "unavailable")
                self.assertNotIn("path", json.dumps(payload, ensure_ascii=False))
                submit.assert_not_called()

    def test_rife_reparse_output_parent_refuses_before_worker_launch(self) -> None:
        from src.modules.practical_rife.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            model_dir = runtime / "train_log" / "RIFEv4.26_0921"
            model_dir.mkdir(parents=True)
            (runtime / "inference_video.py").write_text("# fixture", encoding="utf-8")
            (model_dir / "flownet.pkl").write_bytes(b"model")
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            tools = runtime / "tools" / "ffmpeg"
            tools.mkdir(parents=True)
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

    def test_rife_reparse_ffmpeg_alias_refuses_before_worker_launch(self) -> None:
        from src.modules.practical_rife.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            model_dir = runtime / "train_log" / "RIFEv4.26_0921"
            model_dir.mkdir(parents=True)
            (runtime / "inference_video.py").write_text("# fixture", encoding="utf-8")
            (model_dir / "flownet.pkl").write_bytes(b"model")
            source = root / "Temp" / "uploads" / "input.mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            tools = runtime / "tools" / "ffmpeg"
            tools.mkdir(parents=True)
            real_ffmpeg = root / "real-ffmpeg.exe"
            real_ffmpeg.write_bytes(b"ffmpeg")
            ffmpeg = tools / "ffmpeg.exe"
            try:
                ffmpeg.symlink_to(real_ffmpeg)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            ffprobe = tools / "ffprobe.exe"
            ffprobe.write_bytes(b"ffprobe")
            request = {
                "runtime": str(runtime), "path": str(source), "model_dir": str(model_dir),
                "ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe),
                "output_root": str(root / "Output" / "Practical-RIFE"),
                "temp_root": str(root / "Temp" / "jobs" / "rife_fixture"),
            }
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()

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
            source = root / "Temp" / "uploads" / "input.png"
            source.parent.mkdir(parents=True)
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

    def test_realesrgan_worker_rejects_model_alias_before_worker_launch(self) -> None:
        from src.modules.real_esrgan.backend import worker

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime" / "engines" / "video" / "Real-ESRGAN"
            runtime.mkdir(parents=True)
            (runtime / "inference_realesrgan.py").write_text("# fixture", encoding="utf-8")
            models = root / "Models" / "Video" / "Real-ESRGAN"
            models.mkdir(parents=True)
            real_model = root / "real-model.pth"
            real_model.write_bytes(b"model")
            model = models / "realesr-animevideov3.pth"
            try:
                model.symlink_to(real_model)
            except OSError as exc:
                self.skipTest(f"symlink fixture unavailable: {exc}")
            source = root / "Temp" / "uploads" / "input.png"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"image")
            request = {
                "runtime": str(runtime), "path": str(source), "model_path": str(model),
                "output_root": str(root / "Output" / "Real-ESRGAN"),
                "temp_root": str(root / "Temp" / "jobs" / "realesrgan_fixture"),
            }
            old_hub_root = os.environ.get("LOCALAIHUB_ROOT")
            os.environ["LOCALAIHUB_ROOT"] = str(root)
            try:
                with patch.object(worker, "run_hidden") as launch:
                    code, payload = self._run_worker(worker, request, launch)
            finally:
                if old_hub_root is None:
                    os.environ.pop("LOCALAIHUB_ROOT", None)
                else:
                    os.environ["LOCALAIHUB_ROOT"] = old_hub_root
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")
            launch.assert_not_called()

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

    def test_video_worker_cannot_publish_input_upload_as_completed_output(self) -> None:
        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        upload_root = root / "Temp" / "uploads"
        output_root.mkdir()
        upload_root.mkdir(parents=True)
        upload = upload_root / "hub-upload-input.mp4"
        upload.write_bytes(b"input artifact")
        manager = HubJobManager()
        try:
            with (
                patch.object(api_jobs, "_jobs", {}),
                patch.object(api_jobs, "_save"),
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "UPLOAD_ROOT", upload_root),
                patch.object(artifact_store, "INDEX_PATH", root / "Config" / "artifacts.json"),
            ):
                record = manager.submit(
                    "upscale_anime_video",
                    {"source_artifact_id": "artifact_" + "c" * 32},
                    lambda _payload, _context: {"status": "completed", "output": str(upload)},
                )
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "failed")
                self.assertEqual(public["result"]["status"], "failed")
                self.assertNotIn(str(upload), json.dumps(public, ensure_ascii=False))
                self.assertFalse((root / "Config" / "artifacts.json").exists())
                self.assertTrue(upload.is_file())
        finally:
            manager.cancel_all_and_wait(3)

    def test_mixed_worker_outputs_publish_atomically(self) -> None:
        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        output_root.mkdir()
        good = output_root / "AnimeSR" / "good.mp4"
        good.parent.mkdir(parents=True)
        good.write_bytes(b"good output")
        unmanaged = root / "unmanaged.mp4"
        unmanaged.write_bytes(b"unmanaged output")
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
                    {"source_artifact_id": "artifact_" + "f" * 32},
                    lambda _payload, _context: {"status": "completed", "output": str(good), "files": [str(unmanaged)]},
                )
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "failed")
                self.assertEqual(public["result"]["status"], "failed")
                self.assertFalse((root / "Config" / "artifacts.json").exists())
                self.assertTrue(good.is_file())
                self.assertTrue(unmanaged.is_file())
        finally:
            manager.cancel_all_and_wait(3)

    def test_late_cancel_before_publication_leaves_no_artifact(self) -> None:
        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        output_root.mkdir()
        target = output_root / "AnimeSR" / "late-cancel.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"late output")
        runner_ready = threading.Event()
        release_runner = threading.Event()
        manager = HubJobManager()
        try:
            with (
                patch.object(api_jobs, "_jobs", {}),
                patch.object(api_jobs, "_save"),
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "INDEX_PATH", root / "Config" / "artifacts.json"),
            ):
                def runner(_payload, _context):
                    runner_ready.set()
                    self.assertTrue(release_runner.wait(3))
                    return {"status": "completed", "output": str(target)}

                record = manager.submit("upscale_anime_video", {"source_artifact_id": "artifact_" + "d" * 32}, runner)
                self.assertTrue(runner_ready.wait(3))
                cancelled, _message = manager.cancel(record["id"])
                self.assertTrue(cancelled)
                release_runner.set()
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "cancelled")
                self.assertEqual(public["result"]["status"], "cancelled")
                self.assertNotIn("artifacts", public["result"])
                self.assertFalse((root / "Config" / "artifacts.json").exists())
        finally:
            release_runner.set()
            manager.cancel_all_and_wait(3)

    def test_cancel_waits_for_publication_and_cannot_regress_completed_job(self) -> None:
        from src.services import artifact_store
        from src.services.api import jobs as api_jobs
        from src.services.job_manager.manager import HubJobManager

        root = self._owned_temp_root()
        output_root = root / "Output"
        output_root.mkdir()
        target = output_root / "RIFE" / "completed.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"completed output")
        publication_started = threading.Event()
        release_publication = threading.Event()
        cancel_result: list[tuple[bool, str]] = []
        manager = HubJobManager()
        try:
            with (
                patch.object(api_jobs, "_jobs", {}),
                patch.object(api_jobs, "_save"),
                patch.object(artifact_store, "OUTPUT_ROOT", output_root),
                patch.object(artifact_store, "INDEX_PATH", root / "Config" / "artifacts.json"),
            ):
                register = artifact_store.register_worker_outputs

                def blocking_register(*args, **kwargs):
                    publication_started.set()
                    self.assertTrue(release_publication.wait(3))
                    return register(*args, **kwargs)

                with patch.object(artifact_store, "register_worker_outputs", side_effect=blocking_register):
                    record = manager.submit(
                        "frame_interpolate",
                        {"source_artifact_id": "artifact_" + "e" * 32},
                        lambda _payload, _context: {"status": "completed", "output": str(target)},
                    )
                    self.assertTrue(publication_started.wait(3))
                    cancel_thread = threading.Thread(target=lambda: cancel_result.append(manager.cancel(record["id"])), daemon=True)
                    cancel_thread.start()
                    self.assertTrue(cancel_thread.is_alive())
                    release_publication.set()
                    cancel_thread.join(3)
                idle, remaining = manager.wait_for_idle(5)
                self.assertTrue(idle, remaining)
                self.assertEqual(len(cancel_result), 1)
                self.assertFalse(cancel_result[0][0])
                public = api_jobs.get_job(record["id"])
                self.assertIsNotNone(public)
                assert public is not None
                self.assertEqual(public["status"], "completed")
                self.assertEqual(len(public["result"]["artifacts"]), 1)
                self.assertTrue((root / "Config" / "artifacts.json").is_file())
        finally:
            release_publication.set()
            manager.cancel_all_and_wait(3)


if __name__ == "__main__":
    unittest.main()
