from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


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


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run bounded Local AI Hub smoke checks with explicitly supplied local inputs.")
    parser.add_argument("--base", default=DEFAULT_BASE, help="Local API base URL (default: %(default)s)")
    parser.add_argument("--image", help="Local image for the vision checks")
    parser.add_argument("--asr-audio", help="Local audio for the Whisper check")
    parser.add_argument("--voice-source", help="Local source audio for optional voice clone/convert checks")
    parser.add_argument("--voice-target", help="Local target audio for optional voice convert checks")
    parser.add_argument("--include-voice", action="store_true", help="Run bounded Qwen3-TTS and Seed-VC checks")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    requests: list[tuple[str, str, str, dict | None]] = [("health", "GET", "/health", None)]
    if args.image:
        requests.extend(
            [
                ("ground", "POST", "/vision/ground", {"path": args.image, "prompt": "stop sign . person .", "box_threshold": 0.25, "text_threshold": 0.20}),
                ("detect", "POST", "/vision/detect", {"path": args.image, "threshold": 0.5}),
                ("ui_parse", "POST", "/vision/ui/parse", {"path": args.image, "box_threshold": 0.05}),
                ("ocr", "POST", "/ocr/parse", {"path": args.image}),
            ]
        )
    if args.asr_audio:
        requests.append(("transcribe", "POST", "/speech/transcribe", {"path": args.asr_audio, "start": 0.0, "end": 3.0, "device": "cpu"}))
    if args.include_voice:
        requests.extend(
            [
                ("tts", "POST", "/voice/tts", {"text": "Local AI Hub is ready.", "language": "English", "speaker": "Ryan"}),
                ("voice_design", "POST", "/voice/design", {"text": "Local AI Hub is ready.", "language": "English", "instruct": "Warm, clear, friendly adult voice with calm pacing.", "max_new_tokens": 256}),
            ]
        )
        if args.voice_source:
            requests.append(("voice_clone", "POST", "/voice/clone", {"text": "This is a local cloned voice smoke test.", "language": "English", "reference_audio": args.voice_source, "reference_text": "", "max_new_tokens": 256}))
        if args.voice_source and args.voice_target:
            requests.append(("voice_convert", "POST", "/voice/convert", {"source": args.voice_source, "target": args.voice_target, "diffusion_steps": 4}))
    results = [call(args.base.rstrip("/"), *item) for item in requests]
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if all(result.get("status") not in {"error", "unavailable"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
