"""Allowlisted direct FFmpeg operations for the Hub Media workspace."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_command
from src.shared.paths.registry import OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import configured_path, unavailable


VIDEO_OPS = {
    "trim", "cut", "concat", "resize", "crop", "rotate", "fps", "transcode", "extract_audio", "replace_audio", "mux", "burn_subtitle", "extract_frames", "image_sequence_video",
}
IMAGE_OPS = {"image_resize", "image_crop", "image_rotate", "image_flip", "image_convert", "image_compress"}


def _paths() -> tuple[Path | None, Path | None]:
    ffmpeg = configured_path("ffmpeg", "executable", "FFMPEG_PATH")
    ffprobe = configured_path("ffmpeg", "path", "FFPROBE_PATH")
    if ffprobe and ffprobe.is_dir():
        ffprobe = ffprobe / "ffprobe.exe"
    if ffmpeg and ffmpeg.is_dir():
        ffmpeg = ffmpeg / "ffmpeg.exe"
    if ffprobe is None:
        home = configured_path("ffmpeg", "path", "FFMPEG_HOME")
        ffprobe = home / "ffprobe.exe" if home else None
    return ffmpeg, ffprobe


def _source(payload: dict[str, Any], field: str = "path") -> Path | None:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(os.path.expandvars(value)).expanduser()
    return candidate if candidate.is_file() else None


def _additional_sources(payload: dict[str, Any]) -> list[Path]:
    """Accept only paths resolved from opaque Hub artifact IDs by the API layer."""

    values = payload.get("input_paths")
    if not isinstance(values, list):
        return []
    result: list[Path] = []
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = Path(os.path.expandvars(value)).expanduser()
        if candidate.is_file():
            result.append(candidate)
    return result


def _concat_manifest(sources: list[Path], *, fps: float | None = None) -> Path:
    """Create a short-lived FFmpeg concat manifest inside Hub Temp only."""

    directory = TEMP_ROOT / "jobs"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / f"ffmpeg_concat_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.txt"
    lines: list[str] = []
    for source in sources:
        text = source.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{text}'")
        if fps:
            lines.append(f"duration {1 / fps:.8f}")
    # The concat demuxer needs the last frame listed once more when durations
    # are supplied, otherwise the final image can be dropped.
    if fps and sources:
        text = sources[-1].resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{text}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def _multi_input_command(operation: str, manifest: Path, target: Path, payload: dict[str, Any]) -> list[str] | None:
    ffmpeg, _ffprobe = _paths()
    if ffmpeg is None or not ffmpeg.is_file():
        return None
    prefix = [str(ffmpeg), "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest)]
    if operation == "concat":
        return [*prefix, "-c", "copy", str(target)]
    if operation == "image_sequence_video":
        fps = max(1, min(120, float(payload.get("fps", 24))))
        return [*prefix, "-r", str(fps), "-vf", "format=yuv420p", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(target)]
    return None


def _output(source: Path, operation: str, extension: str | None = None) -> Path:
    root = OUTPUT_ROOT / "Media"
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = extension or source.suffix or ".mp4"
    return root / f"{source.stem}_{operation}_{stamp}{suffix}"


def probe(path: str) -> dict[str, Any]:
    _ffmpeg, ffprobe = _paths()
    source = Path(os.path.expandvars(path)).expanduser()
    if not source.is_file():
        return {"status": "error", "error": "Không tìm thấy tệp media đầu vào."}
    if ffprobe is None or not ffprobe.is_file():
        return unavailable("ffmpeg", "Không tìm thấy ffprobe canonical của Hub.")
    try:
        result = subprocess.run(
            [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            capture_output=True,
            timeout=30,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "error": str(exc)}
    if result.returncode != 0:
        return {"status": "error", "error": result.stderr.decode("utf-8", errors="replace")[-2000:] or "ffprobe thất bại."}
    try:
        data = json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "error", "error": f"ffprobe trả JSON không hợp lệ: {exc}"}
    return {"status": "completed", "format": data.get("format", {}), "streams": data.get("streams", []), "stream_count": len(data.get("streams", []))}


def _subtitle_filter(path: Path) -> str:
    # FFmpeg filter syntax uses a backslash before the Windows drive colon.
    text = str(path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    return f"subtitles='{text}'"


def _command(payload: dict[str, Any], source: Path, target: Path) -> list[str] | None:
    operation = str(payload.get("operation") or "")
    ffmpeg, _ffprobe = _paths()
    if ffmpeg is None or not ffmpeg.is_file():
        return None
    prefix = [str(ffmpeg), "-hide_banner", "-y", "-i", str(source)]
    if operation in {"trim", "cut"}:
        start = max(0.0, float(payload.get("start", 0.0)))
        end = max(start + 0.05, float(payload.get("end", start + 5.0)))
        return [str(ffmpeg), "-hide_banner", "-y", "-ss", str(start), "-to", str(end), "-i", str(source), "-c", "copy", str(target)]
    if operation == "resize":
        width = max(2, int(payload.get("width", 1280)))
        height = max(2, int(payload.get("height", -2)))
        return [*prefix, "-vf", f"scale={width}:{height}", "-c:a", "copy", str(target)]
    if operation == "crop":
        width = max(2, int(payload.get("width", 720)))
        height = max(2, int(payload.get("height", 720)))
        x = max(0, int(payload.get("x", 0)))
        y = max(0, int(payload.get("y", 0)))
        return [*prefix, "-vf", f"crop={width}:{height}:{x}:{y}", "-c:a", "copy", str(target)]
    if operation == "rotate":
        degrees = int(payload.get("degrees", 90)) % 360
        filter_value = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}.get(degrees)
        return [*prefix, "-vf", filter_value or "null", "-c:a", "copy", str(target)]
    if operation == "fps":
        fps = max(1, min(120, float(payload.get("fps", 30))))
        return [*prefix, "-vf", f"fps={fps}", "-c:a", "copy", str(target)]
    if operation == "transcode":
        crf = max(14, min(32, int(payload.get("crf", 18))))
        return [*prefix, "-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-c:a", "aac", "-movflags", "+faststart", str(target)]
    if operation == "extract_audio":
        return [*prefix, "-vn", "-c:a", "aac", str(target)]
    if operation in {"replace_audio", "mux"}:
        audio = _source(payload, "secondary_path")
        if audio is None:
            return []
        return [str(ffmpeg), "-hide_banner", "-y", "-i", str(source), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", str(target)]
    if operation == "burn_subtitle":
        subtitle = _source(payload, "secondary_path")
        if subtitle is None:
            return []
        return [*prefix, "-vf", _subtitle_filter(subtitle), "-c:v", "libx264", "-c:a", "copy", str(target)]
    if operation == "extract_frames":
        return [*prefix, str(target)]
    if operation == "image_resize":
        return [*prefix, "-vf", f"scale={max(2, int(payload.get('width', 1920)))}:{max(2, int(payload.get('height', -2)))}", str(target)]
    if operation == "image_crop":
        return [*prefix, "-vf", f"crop={max(2, int(payload.get('width', 720)))}:{max(2, int(payload.get('height', 720)))}:{max(0, int(payload.get('x', 0)))}:{max(0, int(payload.get('y', 0)))}", str(target)]
    if operation == "image_rotate":
        angle = int(payload.get("degrees", 90)) % 360
        return [*prefix, "-vf", {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}.get(angle, "null"), str(target)]
    if operation == "image_flip":
        return [*prefix, "-vf", "hflip" if payload.get("axis", "horizontal") == "horizontal" else "vflip", str(target)]
    if operation == "image_convert":
        return [*prefix, str(target)]
    if operation == "image_compress":
        quality = max(2, min(31, int(payload.get("quality", 4))))
        return [*prefix, "-q:v", str(quality), str(target)]
    return None


def run_operation(payload: dict[str, Any], context: ProcessOwner | None = None) -> dict[str, Any]:
    operation = str(payload.get("operation") or "probe")
    source = _source(payload)
    if source is None:
        return {"status": "error", "error": "Chọn tệp đầu vào trước khi chạy media operation."}
    if operation == "probe":
        return probe(str(source))
    if operation not in VIDEO_OPS | IMAGE_OPS:
        return {"status": "error", "error": "Media operation không nằm trong allowlist."}
    if operation in {"concat", "image_sequence_video"}:
        sources = [source, *_additional_sources(payload)]
        unique_sources: list[Path] = []
        seen: set[str] = set()
        for candidate in sources:
            key = str(candidate.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                unique_sources.append(candidate)
        if len(unique_sources) < 2:
            return {"status": "error", "error": "Concat hoặc image sequence cần ít nhất hai artifact input trong Hub."}
        if operation == "image_sequence_video" and any(item.suffix.casefold() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} for item in unique_sources):
            return {"status": "error", "error": "Image sequence chỉ nhận ảnh PNG/JPG/WEBP/BMP đã tải lên Hub."}
        target = _output(source, operation, ".mp4" if operation == "image_sequence_video" else source.suffix or ".mp4")
        manifest = _concat_manifest(unique_sources, fps=max(1, min(120, float(payload.get("fps", 24)))) if operation == "image_sequence_video" else None)
        try:
            command = _multi_input_command(operation, manifest, target, payload)
            if command is None:
                return unavailable("ffmpeg", "Không tìm thấy FFmpeg canonical của Hub.")
            code, output = run_command(command, label=f"ffmpeg_{operation}", owner=context, timeout_seconds=float(payload.get("timeout_seconds", 1800)))
        finally:
            try:
                manifest.unlink(missing_ok=True)
            except OSError:
                pass
        if code == -2:
            return {"status": "cancelled", "reason": output}
        if code != 0:
            return {"status": "error", "error": output or f"FFmpeg kết thúc với mã {code}."}
        if not target.is_file():
            return {"status": "error", "error": "FFmpeg không tạo output mong đợi."}
        return {"status": "completed", "operation": operation, "output": str(target), "input_count": len(unique_sources)}
    requested_format = str(payload.get("format") or "png").strip().lower().lstrip(".")
    image_extension = "." + ({"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp", "bmp": "bmp"}.get(requested_format, "png"))
    extension = ".m4a" if operation == "extract_audio" else (image_extension if operation.startswith("image_") else source.suffix or ".mp4")
    if operation == "extract_frames":
        output_dir = OUTPUT_ROOT / "Media" / f"frames_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        output_dir.mkdir(parents=True, exist_ok=False)
        target = output_dir / "frame_%06d.png"
    else:
        target = _output(source, operation, extension)
    command = _command(payload, source, target)
    if command is None:
        return unavailable("ffmpeg", "Không tìm thấy FFmpeg canonical của Hub.")
    if not command:
        return {"status": "error", "error": "Operation này cần tệp audio/subtitle thứ hai đã tải lên Hub."}
    code, output = run_command(command, label=f"ffmpeg_{operation}", owner=context, timeout_seconds=float(payload.get("timeout_seconds", 1800)))
    if code == -2:
        return {"status": "cancelled", "reason": output}
    if code != 0:
        return {"status": "error", "error": output or f"FFmpeg kết thúc với mã {code}."}
    if operation == "extract_frames":
        files = sorted(target.parent.glob("*.png"))
        return {"status": "completed", "operation": operation, "frame_count": len(files), "files": [str(item) for item in files[:100]]}
    if not target.is_file():
        return {"status": "error", "error": "FFmpeg không tạo output mong đợi."}
    return {"status": "completed", "operation": operation, "output": str(target)}
