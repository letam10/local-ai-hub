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

## Phase 4/5 evidence boundary

- OmniParser and Grounding DINO use a fixed 300-second worker bound. A worker
  timeout is reported with the finite `worker_timeout` / `timed_out` pair so a
  bounded timeout is not confused with a missing runtime or model.
- RF-DETR is a `detect_cli.py` worker contract, not an assumption that an
  installed upstream Python API is compatible. Its capability remains
  `partial` with `worker_contract_status: unverified_until_smoke` until one
  bounded detection receipt proves the adapter contract.
- PaddleOCR-VL requires a non-empty local tool-model payload selected from the
  local registry. Missing, empty, outside-root, or reparse model leaves return
  `tool_model_missing` and do not download anything.
- Seed-VC and Qwen3-TTS require canonical, non-reparse runtime/helper leaves
  and a non-empty canonical model payload. A missing or invalid leaf is
  `unavailable` with `execution: not_run`; no request can supply an alternate
  environment, weight path, or model selector.
