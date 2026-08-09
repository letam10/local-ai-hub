"""Allowlisted direct FFmpeg operations for the Hub Media workspace."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.services.process_manager.managed import ProcessOwner, run_command
from src.services.process_manager.windows import run_hidden
from src.shared.paths.registry import OUTPUT_ROOT, TEMP_ROOT
from src.shared.utils.adapter_common import configured_path, unavailable


VIDEO_OPS = {
    "trim", "cut", "concat", "resize", "crop", "rotate", "fps", "transcode", "extract_audio", "replace_audio", "mux", "burn_subtitle", "extract_frames", "image_sequence_video", "frame_interpolate", "encode",
}
IMAGE_OPS = {"image_resize", "image_crop", "image_rotate", "image_flip", "image_convert", "image_compress", "image_levels"}
_ENCODER_CACHE_LOCK = threading.RLock()
_ENCODER_CACHE: dict[str, Any] | None = None


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


def _decoder_text(result: Any) -> str:
    stdout = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else str(result.stdout or "")
    stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr or "")
    return f"{stdout}\n{stderr}"


def _integer(value: Any, default: int) -> int:
    """Read a numeric UI property defensively without leaking a ValueError."""

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _encoder_names(output: str) -> set[str]:
    names: set[str] = set()
    for line in output.splitlines():
        match = re.match(r"^\s*[VAS\.]{6}\s+(\S+)", line)
        if match:
            names.add(match.group(1).strip())
    return names


def _encoder_help(ffmpeg: Path, encoder: str) -> dict[str, Any]:
    try:
        result = run_hidden([str(ffmpeg), "-hide_banner", "-h", f"encoder={encoder}"], capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"id": encoder, "available": False, "reason": str(exc), "pixel_formats": [], "rate_controls": []}
    text = _decoder_text(result)
    pixel_formats: list[str] = []
    match = re.search(r"Supported pixel formats:\s*([^\r\n]+)", text, re.IGNORECASE)
    if match:
        pixel_formats = [item.strip() for item in match.group(1).split() if item.strip()]
    quality = "-cq" in text or "-crf" in text
    rate_controls = ["quality"] if quality else []
    # Bitrate is an FFmpeg codec context option.  It is valid for detected
    # encoders and powers both CBR and VBR without inventing a codec flag.
    rate_controls.extend(["vbr", "cbr"])
    lowered = encoder.casefold()
    return {
        "id": encoder,
        "available": result.returncode == 0,
        "pixel_formats": pixel_formats,
        "rate_controls": rate_controls,
        "quality_option": "cq" if "-cq" in text else "crf" if "-crf" in text else None,
        "preset_supported": "-preset" in text,
        "multipass_supported": any(token in lowered for token in ("libx264", "libx265", "libvpx", "libaom")),
        "hardware": any(token in lowered for token in ("_nvenc", "_qsv", "_amf", "_vaapi")),
    }


def encoder_capabilities(*, force: bool = False) -> dict[str, Any]:
    """Detect FFmpeg encode options once and cache them for the Hub lifetime."""

    global _ENCODER_CACHE
    with _ENCODER_CACHE_LOCK:
        if _ENCODER_CACHE is not None and not force:
            return dict(_ENCODER_CACHE)
    ffmpeg, _ffprobe = _paths()
    if ffmpeg is None or not ffmpeg.is_file():
        value = {"available": False, "reason": "Không tìm thấy FFmpeg canonical của Hub.", "encoders": [], "containers": [], "audio_encoders": []}
        with _ENCODER_CACHE_LOCK:
            _ENCODER_CACHE = value
        return dict(value)
    try:
        listing = run_hidden([str(ffmpeg), "-hide_banner", "-encoders"], capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        value = {"available": False, "reason": str(exc), "encoders": [], "containers": [], "audio_encoders": []}
        with _ENCODER_CACHE_LOCK:
            _ENCODER_CACHE = value
        return dict(value)
    if listing.returncode != 0:
        value = {"available": False, "reason": _decoder_text(listing)[-2000:] or "FFmpeg -encoders thất bại.", "encoders": [], "containers": [], "audio_encoders": []}
        with _ENCODER_CACHE_LOCK:
            _ENCODER_CACHE = value
        return dict(value)
    names = _encoder_names(_decoder_text(listing))
    codec_candidates = {
        "H.264": ("h264_nvenc", "libx264", "h264_qsv", "h264_amf"),
        "H.265 / HEVC": ("hevc_nvenc", "libx265", "hevc_qsv", "hevc_amf"),
        "AV1": ("av1_nvenc", "libsvtav1", "libaom-av1", "av1_qsv", "av1_amf"),
    }
    encoders: list[dict[str, Any]] = []
    for codec, candidates in codec_candidates.items():
        for encoder in candidates:
            if encoder not in names:
                continue
            info = _encoder_help(ffmpeg, encoder)
            if info.get("available"):
                info["codec"] = codec
                encoders.append(info)
    audio_candidates = ("aac", "libopus", "libvorbis")
    value = {
        "available": bool(encoders),
        "reason": "" if encoders else "FFmpeg không báo encoder H.264/H.265/AV1 khả dụng.",
        "detected_at": int(time.time()),
        "encoders": encoders,
        "containers": ["mp4", "mkv", "webm"],
        "audio_encoders": [item for item in audio_candidates if item in names],
    }
    with _ENCODER_CACHE_LOCK:
        _ENCODER_CACHE = value
    return dict(value)


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
        result = run_hidden(
            [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)],
            capture_output=True,
            timeout=30,
            check=False,
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


def _source_fps(source: Path) -> float | None:
    details = probe(str(source))
    if details.get("status") != "completed":
        return None
    streams = details.get("streams")
    if not isinstance(streams, list):
        return None
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != "video":
            continue
        raw = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "")
        try:
            numerator, denominator = raw.split("/", 1)
            value = float(numerator) / float(denominator)
            return value if value > 0 else None
        except (ValueError, ZeroDivisionError):
            continue
    return None


def _interpolation_fps(payload: dict[str, Any], source: Path) -> float:
    mode = str(payload.get("mode") or "target_fps")
    target = max(1.0, min(120.0, float(payload.get("target_fps", payload.get("fps", 60)))))
    source_fps = _source_fps(source)
    if mode == "2x" and source_fps:
        return max(1.0, min(120.0, source_fps * 2))
    if mode == "4x" and source_fps:
        return max(1.0, min(120.0, source_fps * 4))
    return target


def _select_encoder(payload: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    encoders = [item for item in capabilities.get("encoders", []) if isinstance(item, dict) and item.get("available")]
    requested = str(payload.get("codec") or "auto").strip()
    prefer_gpu = bool(payload.get("prefer_gpu", True))
    aliases = {
        "h264": "H.264",
        "h.264": "H.264",
        "h265": "H.265 / HEVC",
        "h.265": "H.265 / HEVC",
        "hevc": "H.265 / HEVC",
        "av1": "AV1",
    }
    if requested != "auto":
        requested_codec = aliases.get(requested.casefold(), requested)
        matching = [item for item in encoders if item.get("id") == requested or item.get("codec") == requested_codec]
        if not matching:
            raise ValueError("Codec/encoder được chọn không có trong FFmpeg capability đã dò.")
        return next((item for item in matching if prefer_gpu and item.get("hardware")), matching[0])
    h264 = [item for item in encoders if item.get("codec") == "H.264"]
    if not h264:
        raise ValueError("FFmpeg không có H.264/H.265/AV1 encoder khả dụng.")
    return next((item for item in h264 if prefer_gpu and item.get("hardware")), h264[0])


def _audio_encoder(payload: dict[str, Any], capabilities: dict[str, Any], container: str) -> str | None:
    available = [str(item) for item in capabilities.get("audio_encoders", [])]
    requested = str(payload.get("audio_codec") or "").strip()
    if requested:
        if requested not in available:
            raise ValueError("Audio codec được chọn không có trong FFmpeg capability đã dò.")
        if container == "webm" and requested not in {"libopus", "libvorbis"}:
            raise ValueError("WebM chỉ hiển thị/nhận Opus hoặc Vorbis từ FFmpeg capability hiện tại.")
        return requested
    preferred = ("libopus", "libvorbis") if container == "webm" else ("aac", "libopus", "libvorbis")
    return next((item for item in preferred if item in available), None)


def _encode_base(
    source: Path,
    target: Path,
    payload: dict[str, Any],
    encoder: dict[str, Any],
    capabilities: dict[str, Any],
    *,
    include_audio: bool,
    extra: list[str] | None = None,
) -> list[str]:
    ffmpeg, _ffprobe = _paths()
    if ffmpeg is None or not ffmpeg.is_file():
        raise ValueError("Không tìm thấy FFmpeg canonical của Hub.")
    container = target.suffix.lower().lstrip(".")
    command = [str(ffmpeg), "-hide_banner", "-y", "-i", str(source)]
    secondary = _source(payload, "secondary_path")
    if secondary is not None:
        command.extend(["-i", str(secondary), "-map", "0:v:0", "-map", "1:a:0"])
    else:
        command.extend(["-map", "0:v:0", "-map", "0:a?"])
    command.extend(["-c:v", str(encoder["id"])])
    rate_control = str(payload.get("rate_control") or "quality").lower()
    if rate_control not in encoder.get("rate_controls", []):
        raise ValueError("Rate control được chọn không được encoder hiện tại hỗ trợ.")
    if rate_control == "quality":
        option = encoder.get("quality_option")
        if option not in {"crf", "cq"}:
            raise ValueError("Encoder hiện tại không công bố CRF/CQ quality option.")
        command.extend([f"-{option}", str(max(0, min(51, _integer(payload.get("quality"), 20))))])
    else:
        bitrate = max(100, _integer(payload.get("bitrate_kbps"), 6000))
        buffer = max(100, _integer(payload.get("buffer_kbps"), bitrate * 2))
        command.extend(["-b:v", f"{bitrate}k", "-bufsize", f"{buffer}k"])
        if rate_control == "cbr":
            command.extend(["-minrate", f"{bitrate}k", "-maxrate", f"{bitrate}k"])
        else:
            maximum = max(bitrate, _integer(payload.get("max_bitrate_kbps"), bitrate))
            command.extend(["-maxrate", f"{maximum}k"])
    pixel_format = str(payload.get("pixel_format") or "").strip()
    if pixel_format:
        if pixel_format not in encoder.get("pixel_formats", []):
            raise ValueError("Pixel format không được encoder hiện tại hỗ trợ.")
        command.extend(["-pix_fmt", pixel_format])
    preset = str(payload.get("preset") or "").strip()
    if preset and encoder.get("preset_supported"):
        command.extend(["-preset", preset])
    if include_audio:
        audio = _audio_encoder(payload, capabilities, container)
        if audio:
            command.extend(["-c:a", audio, "-b:a", f"{max(32, _integer(payload.get('audio_bitrate_kbps'), 192))}k"])
    else:
        command.append("-an")
    if container == "mp4":
        command.extend(["-movflags", "+faststart"])
    if extra:
        command.extend(extra)
    command.append(str(target))
    return command


def _encode_commands(payload: dict[str, Any], source: Path, target: Path) -> tuple[list[list[str]], list[Path]]:
    capabilities = encoder_capabilities()
    if not capabilities.get("available"):
        raise ValueError(str(capabilities.get("reason") or "FFmpeg encode capability chưa khả dụng."))
    container = str(payload.get("container") or target.suffix.lstrip(".") or "mp4").lower()
    if container not in capabilities.get("containers", []):
        raise ValueError("Container được chọn không có trong FFmpeg capability đã dò.")
    encoder = _select_encoder(payload, capabilities)
    if not bool(payload.get("multipass")):
        return [_encode_base(source, target, payload, encoder, capabilities, include_audio=True)], []
    if not encoder.get("multipass_supported"):
        raise ValueError("Encoder hiện tại không hỗ trợ multipass trong Hub.")
    pass_root = TEMP_ROOT / "jobs"
    pass_root.mkdir(parents=True, exist_ok=True)
    stem = pass_root / f"encode_pass_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    first_target = Path(os.devnull)
    first = _encode_base(source, first_target, payload, encoder, capabilities, include_audio=False, extra=["-pass", "1", "-passlogfile", str(stem), "-f", "null"])
    second = _encode_base(source, target, payload, encoder, capabilities, include_audio=True, extra=["-pass", "2", "-passlogfile", str(stem)])
    return [first, second], [stem]


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
    if operation == "frame_interpolate":
        if str(payload.get("backend") or "ffmpeg_minterpolate") == "practical_rife":
            return []
        fps = _interpolation_fps(payload, source)
        return [*prefix, "-vf", f"minterpolate=fps={fps:.3f}", "-c:a", "copy", str(target)]
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
    if operation == "image_levels":
        brightness = max(-1.0, min(1.0, float(payload.get("brightness", 0))))
        contrast = max(0.0, min(3.0, float(payload.get("contrast", 1))))
        saturation = max(0.0, min(3.0, float(payload.get("saturation", 1))))
        return [*prefix, "-vf", f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}", str(target)]
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
    if operation == "frame_interpolate" and str(payload.get("backend") or "ffmpeg_minterpolate") == "practical_rife":
        return unavailable("practical_rife", "Practical-RIFE chưa có CLI contract Hub được smoke; chọn FFmpeg minterpolate fallback để chạy bounded.")
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
    if operation == "encode":
        container = str(payload.get("container") or "mp4").strip().lower().lstrip(".")
        if container not in {"mp4", "mkv", "webm"}:
            return {"status": "error", "error": "Encode container chỉ hỗ trợ MP4, MKV hoặc WebM."}
        target = _output(source, operation, f".{container}")
        cleanup_prefixes: list[Path] = []
        try:
            commands, cleanup_prefixes = _encode_commands(payload, source, target)
            for index, command in enumerate(commands, start=1):
                code, output = run_command(command, label=f"ffmpeg_encode_pass{index}", owner=context, timeout_seconds=float(payload.get("timeout_seconds", 1800)))
                if code == -2:
                    return {"status": "cancelled", "reason": output}
                if code != 0:
                    return {"status": "error", "error": output or f"FFmpeg encode pass {index} kết thúc với mã {code}."}
        except (OSError, ValueError) as exc:
            return {"status": "error", "error": str(exc)}
        finally:
            for prefix in cleanup_prefixes:
                try:
                    for candidate in prefix.parent.glob(prefix.name + "*"):
                        candidate.unlink(missing_ok=True)
                except OSError:
                    continue
        if not target.is_file():
            return {"status": "error", "error": "FFmpeg không tạo output encode mong đợi."}
        return {"status": "completed", "operation": operation, "output": str(target), "container": container, "rate_control": str(payload.get("rate_control") or "quality")}
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
