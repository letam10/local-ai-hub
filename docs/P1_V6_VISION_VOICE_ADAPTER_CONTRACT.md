# V6 vision and voice adapter contract

OmniParser, RF-DETR, Grounding DINO, PaddleOCR-VL, Qwen3-TTS and Seed-VC
workers are bound to the local `components.json` and `model_registry.json`
records. Environment-variable path overrides and client-supplied executable,
runtime or model identifiers are not an execution authority. A missing,
ambiguous or absent model/runtime record returns `unavailable` with
`execution: not_run`.

Public job requests use opaque Hub artifact IDs. Adapters resolve those IDs
server-side through the Hub artifact store; raw paths, commands, output paths
and model selectors are refused before a worker starts. Worker timeouts are
finite and capped per adapter. Returned metadata is bounded and path-free;
internal output paths are retained only for a Job Manager context so the
existing artifact publisher can replace them with opaque artifact metadata
before jobs or browser projections are persisted.

Capability records remain `partial` until a separate bounded runtime-to-model-
to-adapter-to-job smoke writes current evidence. Static registry presence is
not an operational claim, and this package performs no runtime, provider,
model, download, GPU or smoke action.
