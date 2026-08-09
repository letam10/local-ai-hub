# Tyk integration boundary

Tyk is not installed in the current scope. The client and MCP bridge call only the loopback Local AI Hub coordinator at `127.0.0.1:8765`; they do not hard-code ports for individual engines.

Future routing can be inserted as:

```text
Codex -> Local AI MCP -> Tyk Gateway (optional) -> Local AI Hub -> engine adapter
```

If Tyk is detected later, record its version and endpoint in `Config/components.json` before enabling it. The default remains direct loopback access with no LAN binding, public tunnel, or port forwarding.

## Current contract

The Hub exposes the coordinator routes on `127.0.0.1:8765`; engine-specific ports are not required. The MCP server forwards the named health, model, media probe, Vision, OCR, speech, voice, and AnimeSR tools. Heavy GPU calls are serialized by the Hub's single-slot policy.

The Qwen3-TTS design and clone routes use the installed 1.7B variants on demand. The Hub still serializes heavy GPU requests so only one voice/vision job occupies the RTX 4060 slot at a time.
