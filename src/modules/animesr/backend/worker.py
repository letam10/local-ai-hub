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


def _emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload.get("status") == "completed" else 2


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        runtime = Path(str(request["runtime"])).expanduser()
        source = Path(os.path.expandvars(str(request.get("path", "")))).expanduser()
        if not runtime.is_dir() or not source.is_file():
            return _emit({"status": "error", "error": "AnimeSR runtime hoặc input video không tồn tại."})
        script = runtime / "scripts" / "inference_animesr_video.py"
        if not script.is_file():
            return _emit({"status": "error", "error": "Không tìm thấy AnimeSR video inference script."})
        scale = max(1, min(4, int(request.get("scale", 2))))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_root = Path(str(request["output_root"])).expanduser()
        output_root.mkdir(parents=True, exist_ok=True)
        temp_root = output_root.parent.parent / "Temp" / "jobs" / f"animesr_{stamp}"
        temp_root.mkdir(parents=True, exist_ok=False)
        command = [
            sys.executable,
            str(script),
            "-i", str(source),
            "-o", str(temp_root),
            "-n", str(request.get("model", "AnimeSR_v2")),
            "-s", str(scale),
            "--expname", "animesr_v2",
            "--num_process_per_gpu", "1",
            "--low-memory-frame-write",
            "--suffix", f"x{scale}",
        ]
        if bool(request.get("half", True)):
            command.append("--half")
        environment = dict(os.environ)
        ffmpeg_home = str(request.get("ffmpeg_home") or "")
        if ffmpeg_home:
            environment["PATH"] = ffmpeg_home + os.pathsep + environment.get("PATH", "")
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
                "returncode": result.returncode,
                "stderr": result.stderr.decode("utf-8", errors="replace")[-5000:],
            })
        target = output_root / f"{source.stem}_AnimeSR_x{scale}_{stamp}.mp4"
        shutil.move(str(videos[-1]), str(target))
        try:
            shutil.rmtree(temp_root)
        except OSError:
            pass
        return _emit({
            "status": "completed",
            "operation": "upscale_anime_video",
            "output": str(target),
            "scale": scale,
            "model": str(request.get("model", "AnimeSR_v2")),
            "rife": "partial" if request.get("use_rife") else "skipped",
            "realesrgan": "partial" if request.get("use_realesrgan") else "skipped",
        })
    except subprocess.TimeoutExpired:
        return _emit({"status": "error", "error": "AnimeSR worker vượt quá thời gian cho phép."})
    except Exception as exc:
        return _emit({"status": "error", "error": str(exc)})


if __name__ == "__main__":
    raise SystemExit(main())
