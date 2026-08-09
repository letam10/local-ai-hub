# Local AI Hub

Windows-native coordinator for local AI applications and engines. This
repository contains first-party source, adapters, wrappers, examples, and
documentation - not the machine installation, models, environments, caches, or
personal media.

## Safety properties

- The local installation root is supplied by `LOCAL_AI_HOME`; no personal
  absolute path is required in the repository.
- Existing AnimeSR, SAM 2, FFmpeg, Whisper/ASR, Ollama, ComfyUI, and AIRI
  installations are referenced through ignored local configuration and are not
  moved or overwritten.
- Heavy models load on demand and the default policy allows one heavy GPU job
  at a time.
- The API binds to `127.0.0.1` only. The MCP bridge uses stdio by default.
- Source media is never overwritten; outputs are written below the configured
  local output root.
- Functional smoke tests are used instead of benchmark loops or stress tests.

## Readiness model

Local AI Hub reports two independent states:

- `component_status` describes the discovered local component state, such as
  `installed`, `running`, `missing`, or `planned`.
- `tool_status` describes the readiness of a specific allowlisted tool:
  `operational`, `partial`, `queue_only`, `unavailable`, `planned`, or `error`.

An installed component never implies that every tool backed by it is
operational. For example, SAM 2 can be installed as an external GUI while its
`segment_image` and `track_video_object` tools remain unavailable. A partial
tool dynamically reports `unavailable` when its required local component is
not installed.

## Current backend readiness

The following describes the imported source contract. It does not claim that a
particular workstation has every local component configured.

### Operational

The bounded no-model control-plane smoke covers these routes when the Hub is
running:

- `GET /health`
- `GET /tools`
- `GET /models`
- `GET /components`
- `GET /jobs`

### Partial

These routes have allowlisted adapters, but a bounded functional backend smoke
result is not recorded in the repository:

- `/media/probe`
- `/vision/ui/parse`
- `/vision/detect`
- `/vision/ground`
- `/ocr/parse`
- `/speech/transcribe`
- `/voice/tts`
- `/voice/design`
- `/voice/clone`
- `/voice/convert`

### Queue-only

- `/video/upscale/anime` creates a guarded job record but does not execute
  AnimeSR until its executor has been verified.

### Unavailable / pending backend

- `/vision/segment` and `/vision/track` remain unavailable because the direct
  SAM 2 backend adapter has not been verified.
- `/video/subtitle` remains unavailable because subtitle-video output muxing
  has not been verified.

## Layout

```text
Local AI Hub/
|- Apps/
|- Adapters/
|- Cache/ (local, ignored)
|- Config/
|- Environments/ (local, ignored)
|- Hub/
|- MCP/
|- Models/ (local, ignored)
|- Output/ (local, ignored)
|- Reports/
|- Services/
`- Temp/ (local, ignored)
```

The current installation state is kept in ignored local configuration files.
Start from the tracked `Config/*.example.json` templates.

## Current local engines

- Vision: OmniParser v2, RF-DETR Nano, Grounding DINO Swin-T, and PaddleOCR-VL
  1.6 have isolated native Windows adapter paths.
- Voice: Qwen3-TTS 0.6B CustomVoice, 1.7B VoiceDesign, 1.7B Base, and Seed-VC
  tiny are represented by on-demand adapters.
- Reused external components: AnimeSR v2, SAM 2, Faster-Whisper,
  FFmpeg/FFprobe, Ollama, ComfyUI, and AIRI remain at their existing paths.

Qwen3-TTS is a voice engine. It is distinct from Qwen Image and
Qwen-Image-2512 image-generation systems; their models and directories must
never be mixed.

## API and MCP

The loopback API is `http://127.0.0.1:8765`. Read-only routes include
`/health`, `/tools`, `/models`, `/components`, and `/jobs`. `GET /tools`
returns both `component_status` and `tool_status` with the reason for the
reported capability state.

`MCP/local_ai_mcp_server.py` uses stdio and forwards only named, allowlisted
tools. It never accepts arbitrary shell commands or PowerShell.

## Checks

```powershell
python Scripts/ci_validate.py
python Scripts/diagnose.py
python Scripts/api_smoke.py --image <local-image>
python MCP/mcp_smoke.py
```

Run only the smallest applicable smoke check. Do not use these commands for
benchmarks, stress tests, or repeated model inference.

See `Reports/` for sanitized evidence and limitations. Generated local reports
may contain host-specific paths and are intentionally ignored.
