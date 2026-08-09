from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_BASE = os.environ.get("LOCAL_AI_API", "http://127.0.0.1:8765")


def call(base: str, name: str, method: str, path: str, payload: dict | None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            value = json.loads(response.read().decode("utf-8"))
            summary = {"name": name, "http": response.status, "status": value.get("status")}
            for key in ("count", "device", "api", "jobs"):
                if key in value:
                    summary[key] = value[key]
            return summary
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"name": name, "status": "error", "error": str(exc)}


def call_static(base: str, name: str, path: str) -> dict:
    request = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read()
            return {
                "name": name,
                "http": response.status,
                "status": "completed" if response.status == 200 and body else "error",
                "content_type": response.headers.get_content_type(),
            }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"name": name, "status": "error", "error": str(exc)}


def upload(base: str, path_text: str) -> tuple[str | None, dict]:
    """Stage one explicitly supplied local input and return its opaque Hub ID."""

    path = Path(path_text).expanduser()
    try:
        size = path.stat().st_size
        if not path.is_file() or size <= 0 or size > 512 * 1024 * 1024:
            return None, {"name": "upload", "status": "error", "error": "Input smoke không hợp lệ hoặc vượt 512 MiB."}
        request = urllib.request.Request(
            base + "/api/uploads",
            data=path.read_bytes(),
            method="POST",
            headers={"X-File-Name": path.name, "Content-Type": "application/octet-stream", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            value = json.loads(response.read().decode("utf-8"))
        artifact_id = value.get("artifact", {}).get("id") if isinstance(value, dict) else None
        if not isinstance(artifact_id, str):
            return None, {"name": "upload", "status": "error", "error": "Hub không trả artifact ID cho input smoke."}
        return artifact_id, {"name": "upload", "http": 201, "status": "completed"}
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, {"name": "upload", "status": "error", "error": str(exc)}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Local AI Hub smoke checks with explicitly supplied local inputs.")
    parser.add_argument("--base", default=DEFAULT_BASE, help="Local API base URL (default: %(default)s)")
    parser.add_argument("--image", help="Một ảnh local nhỏ để stage thành artifact rồi queue vision/OCR checks")
    parser.add_argument("--asr-audio", help="Một audio local ngắn để stage thành artifact rồi queue Whisper check")
    parser.add_argument("--voice-source", help="Một audio local ngắn cho optional voice clone/convert")
    parser.add_argument("--voice-target", help="Một audio local ngắn cho optional voice convert")
    parser.add_argument("--include-voice", action="store_true", help="Run bounded Qwen3-TTS and Seed-VC checks")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    requests: list[tuple[str, str, str, dict | None]] = [
        ("health", "GET", "/health", None),
        ("dashboard", "GET", "/api/dashboard", None),
        ("storage", "GET", "/api/storage", None),
        ("models", "GET", "/api/models", None),
        ("applications", "GET", "/api/applications", None),
        ("settings", "GET", "/api/settings", None),
        ("lifecycle", "GET", "/api/lifecycle", None),
    ]
    preflight: list[dict] = []
    if args.image:
        artifact_id, result = upload(base := args.base.rstrip("/"), args.image)
        preflight.append(result)
        if artifact_id:
            image_payload = {"asset_id": artifact_id}
            requests.extend(
                [
                    ("ground", "POST", "/vision/ground", {**image_payload, "prompt": "stop sign . person .", "box_threshold": 0.25, "text_threshold": 0.20}),
                    ("detect", "POST", "/vision/detect", {**image_payload, "threshold": 0.5}),
                    ("ui_parse", "POST", "/vision/ui/parse", {**image_payload, "box_threshold": 0.05}),
                    ("ocr", "POST", "/ocr/parse", image_payload),
                ]
            )
    if args.asr_audio:
        artifact_id, result = upload(base := args.base.rstrip("/"), args.asr_audio)
        preflight.append(result)
        if artifact_id:
            requests.append(("transcribe", "POST", "/speech/transcribe", {"asset_id": artifact_id, "start": 0.0, "end": 3.0, "device": "cpu"}))
    if args.include_voice:
        requests.extend(
            [
                ("tts", "POST", "/voice/tts", {"text": "Local AI Hub is ready.", "language": "English", "speaker": "Ryan"}),
                ("voice_design", "POST", "/voice/design", {"text": "Local AI Hub is ready.", "language": "English", "instruct": "Warm, clear, friendly adult voice with calm pacing.", "max_new_tokens": 256}),
            ]
        )
        if args.voice_source:
            source_id, result = upload(base := args.base.rstrip("/"), args.voice_source)
            preflight.append(result)
            if source_id:
                requests.append(("voice_clone", "POST", "/voice/clone", {"text": "This is a local cloned voice smoke test.", "language": "English", "reference_asset_id": source_id, "reference_text": "", "max_new_tokens": 256}))
        if args.voice_source and args.voice_target:
            source_id, source_result = upload(base := args.base.rstrip("/"), args.voice_source)
            target_id, target_result = upload(base, args.voice_target)
            preflight.extend([source_result, target_result])
            if source_id and target_id:
                requests.append(("voice_convert", "POST", "/voice/convert", {"source_asset_id": source_id, "target_asset_id": target_id, "diffusion_steps": 4}))
    base = args.base.rstrip("/")
    results = [*preflight, *(call(base, *item) for item in requests)]
    results.extend([
        call_static(base, "ui", "/ui/"),
        call_static(base, "ui_app", "/ui/app.js"),
    ])
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if all(result.get("status") not in {"error", "unavailable"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
