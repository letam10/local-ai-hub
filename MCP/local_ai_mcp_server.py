from __future__ import annotations

import json
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP


API = "http://127.0.0.1:8765"
mcp = FastMCP("Local AI MCP", instructions="Loopback-only allowlisted tools for Local AI Hub.")


def _request(method: str, route: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(API + route, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": str(exc)}


@mcp.tool()
def get_health() -> dict:
    """Return Local AI Hub health and GPU policy."""
    return _request("GET", "/health")


@mcp.tool()
def list_models() -> dict:
    """Return the Local AI Hub model registry."""
    return _request("GET", "/models")


@mcp.tool()
def probe_media(path: str) -> dict:
    """Probe a local media file through the registered FFprobe binary."""
    return _request("POST", "/media/probe", {"path": path})


@mcp.tool()
def parse_screen(path: str) -> dict:
    """Parse a local screenshot through OmniParser when installed."""
    return _request("POST", "/vision/ui/parse", {"path": path})


@mcp.tool()
def detect_objects(path: str) -> dict:
    """Detect objects in a local image through RF-DETR when installed."""
    return _request("POST", "/vision/detect", {"path": path})


@mcp.tool()
def ground_objects(path: str, prompt: str) -> dict:
    """Ground text-described objects in a local image through Grounding DINO when installed."""
    return _request("POST", "/vision/ground", {"path": path, "prompt": prompt})


@mcp.tool()
def segment_image(path: str, boxes: list[list[float]] | None = None) -> dict:
    """Segment an image through the existing SAM 2 adapter when verified."""
    return _request("POST", "/vision/segment", {"path": path, "boxes": boxes or []})


@mcp.tool()
def ocr_document(path: str) -> dict:
    """Parse a local image or PDF through PaddleOCR-VL when installed."""
    return _request("POST", "/ocr/parse", {"path": path})


@mcp.tool()
def transcribe_media(path: str) -> dict:
    """Transcribe a local media file through the existing Whisper adapter when verified."""
    return _request("POST", "/speech/transcribe", {"path": path})


@mcp.tool()
def text_to_speech(text: str, language: str = "auto") -> dict:
    """Generate speech locally through Qwen3-TTS when installed."""
    return _request("POST", "/voice/tts", {"text": text, "language": language if language != "auto" else "English"})


@mcp.tool()
def design_voice(text: str, instruct: str, language: str = "English") -> dict:
    """Request Qwen3-TTS voice design; returns a clear unavailable status until the 1.7B model is installed."""
    return _request("POST", "/voice/design", {"text": text, "instruct": instruct, "language": language})


@mcp.tool()
def clone_voice(text: str, reference_audio: str, reference_text: str = "", language: str = "English") -> dict:
    """Request Qwen3-TTS voice cloning with a local reference audio file."""
    return _request("POST", "/voice/clone", {"text": text, "reference_audio": reference_audio, "reference_text": reference_text, "language": language})


@mcp.tool()
def convert_voice(path: str, reference_audio: str | None = None) -> dict:
    """Convert a local audio file through Seed-VC when installed."""
    return _request("POST", "/voice/convert", {"source": path, "target": reference_audio})


@mcp.tool()
def upscale_anime_video(path: str) -> dict:
    """Queue a local AnimeSR upscale without overwriting the source."""
    return _request("POST", "/video/upscale/anime", {"path": path})


if __name__ == "__main__":
    mcp.run(transport="stdio")
