"""Run Practical-RIFE with Hub-owned inputs, outputs, and FFmpeg selection."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from src.services.process_manager.windows import run_hidden


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
    """Return a contained non-reparse Hub path without following a junction."""

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
    for relative in (Path("Temp") / "uploads", Path("Output"), Path("Archive")):
        root = _safe_tree(hub_root, relative)
        if root is not None and _safe_existing_under(root, candidate) and candidate.is_file():
            return True
    return False


def _discard_task(hub_root: Path, path: Path) -> None:
    try:
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            expected_jobs is not None
            and path.name.startswith("rife_")
            and path.parent.resolve(strict=False) == expected_jobs.resolve(strict=False)
            and not _is_reparse(path)
        ):
            shutil.rmtree(path)
    except OSError:
        pass


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        runtime = Path(str(request.get("runtime") or ""))
        source = Path(str(request.get("path") or ""))
        model_dir = Path(str(request.get("model_dir") or ""))
        ffmpeg = Path(str(request.get("ffmpeg") or ""))
        ffprobe = Path(str(request.get("ffprobe") or ""))
        output_root = Path(str(request.get("output_root") or ""))
        output_reserved = request.get("output_reserved") is True
        temp_root = Path(str(request.get("temp_root") or ""))
        hub_root = Path(os.environ.get("LOCALAIHUB_ROOT", ""))
        script = runtime / "inference_video.py"
        expected_runtime = _safe_tree(hub_root, Path("runtime"))
        expected_tools = _safe_tree(hub_root, Path("runtime") / "tools" / "ffmpeg")
        expected_output = _safe_tree(hub_root, Path("Output") / "Practical-RIFE")
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if (
            not hub_root.is_dir()
            or expected_runtime is None
            or expected_tools is None
            or expected_output is None
            or expected_jobs is None
            or not runtime.is_dir()
            or not script.is_file()
            or not _safe_artifact_input(hub_root, source)
            or not (model_dir / "flownet.pkl").is_file()
            or not ffmpeg.is_file()
            or not ffprobe.is_file()
            or not _safe_existing_under(hub_root, runtime)
            or not _safe_existing_under(runtime, script)
            or not _safe_existing_under(runtime, model_dir)
            or not _safe_existing_under(expected_tools, ffmpeg)
            or not _safe_existing_under(expected_tools, ffprobe)
            or ffmpeg.parent.resolve(strict=False) != ffprobe.parent.resolve(strict=False)
            or (
                not _safe_existing_under(_safe_tree(hub_root, Path("Output")), output_root)
                if output_reserved
                else output_root.resolve(strict=False) != expected_output.resolve(strict=False)
            )
            or temp_root.parent.resolve(strict=False) != expected_jobs.resolve(strict=False)
            or not temp_root.name.startswith("rife_")
            or temp_root.parent.name != "jobs"
        ):
            return _emit({"status": "error", "error": "Practical-RIFE runtime contract không hợp lệ."})
        output_root.mkdir(parents=True, exist_ok=True)
        expected_output = _safe_tree(hub_root, Path("Output") / "Practical-RIFE")
        if expected_output is None or output_root.is_symlink() or (
            not _safe_existing_under(_safe_tree(hub_root, Path("Output")), output_root)
            if output_reserved
            else output_root.resolve(strict=False) != expected_output.resolve(strict=False)
        ):
            return _emit({"status": "error", "error": "Output Practical-RIFE không khả dụng."})
        temp_root.parent.mkdir(parents=True, exist_ok=True)
        expected_jobs = _safe_tree(hub_root, Path("Temp") / "jobs")
        if expected_jobs is None or temp_root.parent.resolve(strict=False) != expected_jobs.resolve(strict=False):
            return _emit({"status": "error", "error": "Temp Practical-RIFE không khả dụng."})
        temp_root.mkdir(parents=True, exist_ok=False)
        intermediate = temp_root / "interpolated.mp4"
        target_fps = max(2, min(120, int(request.get("target_fps", 48))))
        system_root = Path(os.environ.get("SystemRoot", r"C:\\Windows"))
        environment = dict(os.environ)
        # The upstream script still shells out by basename.  Its entire lookup
        # path is fixed here to Hub's canonical pair plus Windows command host;
        # the interactive PATH is intentionally not inherited.
        environment["PATH"] = os.pathsep.join((str(ffmpeg.parent), str(system_root / "System32")))
        environment["FFMPEG_BINARY"] = str(ffmpeg)
        environment["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)
        environment["ComSpec"] = str(system_root / "System32" / "cmd.exe")
        command = [
            sys.executable,
            str(script),
            "--video", str(source),
            "--output", str(intermediate),
            "--model", str(model_dir),
            "--fps", str(target_fps),
        ]
        if bool(request.get("half", True)):
            command.append("--fp16")
        result = run_hidden(command, cwd=str(temp_root), capture_output=True, timeout=int(request.get("inner_timeout_seconds", 1500)), check=False, env=environment)
        if result.returncode != 0 or not intermediate.is_file() or not intermediate.stat().st_size:
            return _emit({"status": "error", "error": "Practical-RIFE không tạo được video nội suy."})
        muxed = temp_root / "interpolated_with_audio.mp4"
        mux = run_hidden(
            [str(ffmpeg), "-hide_banner", "-y", "-i", str(intermediate), "-i", str(source), "-map", "0:v:0", "-map", "1:a?", "-c:v", "copy", "-c:a", "copy", "-shortest", str(muxed)],
            cwd=str(temp_root), capture_output=True, timeout=180, check=False, env=environment,
        )
        produced = muxed if mux.returncode == 0 and muxed.is_file() and muxed.stat().st_size else intermediate
        probe = run_hidden([str(ffprobe), "-v", "error", "-show_entries", "format=duration", "-of", "json", str(produced)], capture_output=True, timeout=30, check=False, env=environment)
        if probe.returncode != 0:
            return _emit({"status": "error", "error": "FFprobe canonical không xác minh được output Practical-RIFE."})
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = output_root / f"{source.stem}_RIFE_{target_fps}fps_{stamp}.mp4"
        shutil.move(str(produced), str(target))
        _discard_task(hub_root, temp_root)
        return _emit({"status": "completed", "operation": "frame_interpolate", "backend": "practical_rife", "output": str(target), "target_fps": target_fps, "audio": "preserved" if produced == muxed else "not_present"})
    except subprocess.TimeoutExpired:
        return _emit({"status": "error", "error": "Practical-RIFE vượt quá thời gian bounded của Hub."})
    except Exception:
        return _emit({"status": "error", "error": "Practical-RIFE worker không thể hoàn tất job."})


if __name__ == "__main__":
    raise SystemExit(main())
