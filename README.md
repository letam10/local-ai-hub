# Local AI Hub

Windows-native coordinator for local AI applications and engines. This
repository contains first-party source, adapters, wrappers, examples and
documentation—not the machine installation.

## Design decisions

- The local installation root is supplied by `LOCAL_AI_HOME`; no personal
  absolute path is required in the repository.
- Existing AnimeSR, SAM 2, FFmpeg, Whisper/ASR, Ollama, ComfyUI and AIRI
  installations are referenced through ignored local configuration and are not
  moved or overwritten.
- Heavy models load on demand and the default policy allows one heavy GPU job
  at a time.
- The API binds to `127.0.0.1` only. The MCP bridge uses stdio by default.
- Source media is never overwritten; outputs are written below the configured
  local output root.
- Functional smoke tests are used instead of benchmark loops or stress tests.

## Layout

```text
Local AI Hub/
├── Apps\
├── Adapters\
├── Cache/ (local, ignored)
├── Config\
├── Environments/ (local, ignored)
├── Hub\
├── MCP\
├── Models/ (local, ignored)
├── Output/ (local, ignored)
├── Reports\
├── Services\
└── Temp/ (local, ignored)
```

The current installation state is kept in ignored local configuration files.
Start from the tracked `Config/*.example.json` templates.

## Current local engines

- Vision: OmniParser v2, RF-DETR Nano, Grounding DINO Swin-T, and PaddleOCR-VL 1.6 run through isolated native Windows environments.
- Voice: Qwen3-TTS 0.6B CustomVoice, 1.7B VoiceDesign, 1.7B Base, and Seed-VC tiny are installed on demand.
- Reused: AnimeSR v2, SAM 2, Faster-Whisper, FFmpeg/FFprobe, Ollama, ComfyUI, and AIRI remain at their existing paths.

## API and MCP

The loopback API is `http://127.0.0.1:8765`. Read-only routes include `/health`, `/tools`, `/models`, `/components`, and `/jobs`. Functional POST routes include `/media/probe`, `/vision/ui/parse`, `/vision/detect`, `/vision/ground`, `/ocr/parse`, `/voice/tts`, `/voice/design`, `/voice/clone`, `/voice/convert`, and `/video/upscale/anime`.

`MCP\local_ai_mcp_server.py` uses stdio and forwards only named, allowlisted tools. It never accepts arbitrary shell commands or PowerShell.

## Checks

```powershell
python Scripts/diagnose.py
python Scripts/api_smoke.py
python MCP/mcp_smoke.py
```

See the `Reports/` directory for local evidence and limitations. Generated
reports may contain host-specific paths and are intentionally ignored.
