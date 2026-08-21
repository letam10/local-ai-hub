"""Run AnimeSR directly from the Hub without launching Anime Upscale Studio."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.services.process_manager.windows import run_hidden
from src.shared.utils.adapter_common import safe_output_namespace


_MODEL_ID = "animesr-v2"
_MODEL_NAME = "AnimeSR_v2"
_EXPNAME = "animesr_v2"
_MODEL_RELATIVE = Path("Video") / "AnimeSR" / "AnimeSR_v2.pth"


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") == "completed" else 2


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except (OSError, ValueError):
        return False


def _is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        return bool(getattr(os.lstat(path), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_tree(root: Path, relative: Path) -> Path | None:
    current = root
    if _is_reparse(current):
        return None
    for part in relative.parts:
        current = current / part
        if current.exists() or current.is_symlink():
            if _is_reparse(current):
                return None
        if not _inside(root, current):
            return None
    return current


def _safe_existing_under(root: Path, candidate: Path) -> bool:
    try:
        # Inspect the original lexical path first.  Resolving before lstat
        # would hide a symlink/junction alias under an otherwise safe root.
        lexical_root = Path(os.path.abspath(str(root)))
        lexical_candidate = Path(os.path.abspath(str(candidate)))
        relative = lexical_candidate.relative_to(lexical_root)
    except (OSError, ValueError):
        return False
    if _is_reparse(lexical_root):
        return False
    current = lexical_root
    for part in relative.parts:
        current = current / part
        if _is_reparse(current):
            return False
    try:
        resolved_root = lexical_root.resolve(strict=True)
        resolved = lexical_candidate.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return resolved == current.resolve(strict=True)


def _safe_artifact_input(hub_root: Path, candidate: Path) -> bool:
    """Accept only an existing file from a Hub-owned artifact input root."""

    for relative in (Path("Temp") / "uploads", Path("Output"), Path("Archive")):
        root = _safe_tree(hub_root, relative)
        if root is not None and _safe_existing_under(root, candidate) and candidate.is_file():
            return True
    return False


def _safe_model_leaf(root: Path, candidate: Path) -> bool:
    """Require a real model file/directory below the fixed Models root."""

    if not root.is_dir() or _is_reparse(root):
        return False
    expected = root / _MODEL_RELATIVE
    if os.path.normcase(os.path.abspath(str(candidate))) != os.path.normcase(os.path.abspath(str(expected))):
        return False
    if not candidate.is_file():
        return False
    return _safe_existing_under(root, candidate)


def _discard_task(hub_root: Path, path: Path) -> None:
    try:
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            expected_jobs is not None
            and path.name.startswith("animesr_")
            and path.parent.resolve(strict=False) == expected_jobs.resolve(strict=False)
            and not _is_reparse(path)
        ):
            shutil.rmtree(path)
    except OSError:
        pass


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        runtime = Path(str(request["runtime"])).expanduser()
        source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
        ffmpeg = Path(str(request.get("ffmpeg") or ""))
        model_path = Path(str(request.get("model_path") or ""))
        model_id = str(request.get("model_id") or "")
        model_name = str(request.get("model") or "")
        expname = str(request.get("expname") or "")
        output_root = Path(str(request["output_root"])).expanduser()
        hub_root = Path(os.environ.get("LOCALAIHUB_ROOT", ""))
        expected_runtime = _safe_tree(hub_root, Path("runtime"))
        expected_models = _safe_tree(hub_root, Path("Models"))
        namespace_value = request.get("output_namespace")
        expected_output = safe_output_namespace(hub_root, namespace_value) if namespace_value else _safe_tree(hub_root, Path("Output") / "AnimeSR")
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            not hub_root.is_dir()
            or expected_runtime is None
            or expected_models is None
            or expected_output is None
            or expected_jobs is None
            or not runtime.is_dir()
            or not expected_models.is_dir()
            or not _safe_artifact_input(hub_root, source)
            or not ffmpeg.is_file()
            or model_id != _MODEL_ID
            or model_name != _MODEL_NAME
            or expname != _EXPNAME
            or not (model_path.is_file() or model_path.is_dir())
            or not _safe_existing_under(hub_root, runtime)
            or not _safe_existing_under(hub_root, ffmpeg)
            or not _safe_model_leaf(expected_models, model_path)
            or output_root.resolve(strict=False) != expected_output.resolve(strict=False)
        ):
            return _emit({"status": "error", "error": "AnimeSR runtime hoặc input video không tồn tại."})
        script = runtime / "scripts" / "inference_animesr_video.py"
        if not script.is_file() or not _safe_existing_under(runtime, script):
            return _emit({"status": "error", "error": "Không tìm thấy AnimeSR video inference script."})
        scale = max(1, min(4, int(request.get("scale", 2))))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_root.mkdir(parents=True, exist_ok=True)
        expected_output = safe_output_namespace(hub_root, namespace_value) if namespace_value else _safe_tree(hub_root, Path("Output") / "AnimeSR")
        if expected_output is None or output_root.is_symlink() or output_root.resolve(strict=False) != expected_output.resolve(strict=False):
            return _emit({"status": "error", "error": "Output AnimeSR không khả dụng."})
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if expected_jobs is None:
            return _emit({"status": "error", "error": "Temp AnimeSR không khả dụng."})
        expected_jobs.mkdir(parents=True, exist_ok=True)
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if expected_jobs is None:
            return _emit({"status": "error", "error": "Temp AnimeSR không khả dụng."})
        temp_root = expected_jobs / f"animesr_{stamp}"
        temp_root.mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            str(script),
            "-i", str(source),
            "-o", str(temp_root),
            "-n", _MODEL_NAME,
            "-s", str(scale),
            "--expname", _EXPNAME,
            "--netscale", "4",
            "--num_process_per_gpu", "1",
            "--suffix", f"x{scale}",
        ]
        if bool(request.get("half", True)):
            command.append("--half")
        environment = dict(os.environ)
        # ``inference_animesr_video.py`` uses this explicit setting.  Do not
        # prepend or otherwise depend on the interactive process PATH.
        environment["ffmpeg_exe_path"] = str(ffmpeg)
        result = run_hidden(
            command,
            cwd=str(runtime),
            capture_output=True,
            timeout=int(request.get("inner_timeout_seconds", 3300)),
            check=False,
            env=environment,
        )
        videos = sorted(temp_root.rglob("*.mp4"), key=lambda item: item.stat().st_mtime)
        if result.returncode != 0 or not videos:
            return _emit({
                "status": "error",
                "error": "AnimeSR worker không tạo được video output.",
                "code": "animesr_output_missing",
            })
        target = output_root / f"{source.stem}_AnimeSR_x{scale}_{stamp}.mp4"
        shutil.move(str(videos[-1]), str(target))
        _discard_task(hub_root, temp_root)
        return _emit({
            "status": "completed",
            "operation": "upscale_anime_video",
            "output": str(target),
            "scale": scale,
            "model_id": _MODEL_ID,
            "model": _MODEL_NAME,
            "rife": "partial" if request.get("use_rife") else "skipped",
            "realesrgan": "partial" if request.get("use_realesrgan") else "skipped",
        })
    except subprocess.TimeoutExpired:
        return _emit({"status": "error", "error": "AnimeSR worker vượt quá thời gian cho phép.", "code": "animesr_timeout"})
    except Exception:
        return _emit({"status": "error", "error": "AnimeSR worker không thể hoàn tất job.", "code": "animesr_worker_failed"})


if __name__ == "__main__":
    raise SystemExit(main())
