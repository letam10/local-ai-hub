# V7 production catalog audit

This audit records the reviewed disposition of the current 14 model records
and 15 runtime records.  It is intentionally conservative: `AUTO_INSTALL_READY`
is absent from all model records and is retained only for the reviewed FFmpeg
runtime metadata.  A source URL, revision string, local receipt, or catalog
record alone never upgrades a capability to operational.

## Closed v2 contract

`v7-production-catalog.v2` has exactly 14 fixed model IDs and 15 fixed runtime
IDs.  The top-level catalog version is bounded, every record is closed against
unknown properties, and each model `runtime_id` must reference one of the fixed
runtime IDs.  The loader rejects duplicate JSON keys, non-finite numbers,
duplicate/missing/unknown IDs, dangling references, unsafe relative leaves,
credential-bearing URLs, and mismatched primary/fallback source identities.

Every record carries explicit provider/kind/version/revision, primary source
or `null`, trusted fallback sources, source identity and verification state,
latest upstream/supported revisions, license/authentication state and update
parts.  The loader never fills these fields from `revision` or silently turns a
legacy v1 document into v2.  Legacy v1 loading remains an explicit compatibility
path for existing static callers.

Verified leaves must carry a positive `size_bytes` and lowercase 64-hex
`sha256`.  An unverified leaf must omit both fields; `size_bytes: 0` is never
used as an unknown sentinel.  Catalog loading and snapshot composition are
read-only, `execution: not_run`, `dry_run: true`, and perform no network,
process, model or runtime discovery action.

## Models

| ID | Disposition | Install path | Review result |
| --- | --- | --- | --- |
| `animesr-v2` | `MANUAL_IMPORT_ONLY` | Native import | TencentARC publishes code and model names, but the pretrained files are distributed through a Google Drive link rather than a pinned catalog digest. Existing local bytes may be reused; missing weights require import/review. |
| `sam2.1-hiera-small` | `MANUAL_IMPORT_ONLY` | Native import | Official Meta checkpoint identity, size and digest are pinned, but the managed SAM2 Python graph is not a complete auto-install bundle. |
| `faster-whisper-large-v3` | `MANUAL_IMPORT_ONLY` | Native import | The adapter/runtime contract is reviewed, but the CTranslate2 model bundle and environment are not pinned as a complete production install graph. |
| `flux-2-klein-base-4b-fp8` | `AUTH_REQUIRED` | Provider authorization | Gated provider access is explicit; no unauthenticated download is presented. |
| `qwen-image-2512-fp8` | `MANUAL_IMPORT_ONLY` | Native import | ComfyUI workflow/model file set and license/auth boundary require a reviewed bundle. |
| `practical-rife-v4` | `MANUAL_IMPORT_ONLY` | Native import | No floating weight URL is accepted; exact checkpoint identity is required. |
| `realesrgan-x4plus` | `MANUAL_IMPORT_ONLY` | Native import | Official project is known, but a pinned release asset and complete runtime graph are not yet cataloged. |
| `omniparser-v2` | `MANUAL_IMPORT_ONLY` | Native import | Tool model files and runtime dependencies need a reviewed bundle; it is not a conversational model. |
| `rfdetr-base` | `MANUAL_IMPORT_ONLY` | Native import | Upstream API/model packaging is not assumed; import remains explicit. |
| `grounding-dino-base` | `MANUAL_IMPORT_ONLY` | Native import | Checkpoint identity and runtime requirements need a reviewed immutable bundle. |
| `paddleocr-vl-0.9b` | `MANUAL_IMPORT_ONLY` | Native import | Tool model only; no chat/LLM download is implied. |
| `qwen3-tts-1.7b` | `MANUAL_IMPORT_ONLY` | Native import | Voice model is not a conversational brain; model/environment packaging remains manual. |
| `seed-vc-1` | `MANUAL_IMPORT_ONLY` | Native import | Voice-conversion asset requires exact checkpoint and environment review. |
| `comfyui-workflow-assets` | `MANUAL_IMPORT_ONLY` | Native import | Workflow metadata is not itself a model or operational engine. |

## Runtimes

| ID | Disposition | Install path | Review result |
| --- | --- | --- | --- |
| `ffmpeg` | `AUTO_INSTALL_READY` | Pinned portable archive | Officially linked Windows build, pinned release/hash/size, two required leaves, atomic archive executor, `-version` probes and tiny transform accepted. |
| `sam2` | `MANUAL_INSTALL` | Reviewed environment | Python/dependency graph is not yet an auto-installable locked environment. |
| `animesr` | `MANUAL_INSTALL` | Reviewed environment | PyTorch and upstream requirements are hardware/environment-sensitive; code/model resilience is separate from automatic environment creation. |
| `faster-whisper` | `MANUAL_INSTALL` | Reviewed environment | CTranslate2/Python package graph requires a pinned environment and model bundle. |
| `comfyui` | `REFERENCE_EXISTING` | Existing installation | Installer-managed or user-owned ComfyUI is reused only after fixed-leaf discovery; no arbitrary clone/download is performed. |
| `practical-rife` | `MANUAL_INSTALL` | Reviewed environment | Runtime and model binding are separate and not auto-installed by the current catalog. |
| `real-esrgan` | `MANUAL_INSTALL` | Reviewed environment | Runtime/model binding remains explicit; no driver/CUDA mutation. |
| `omniparser` | `MANUAL_INSTALL` | Reviewed environment | Environment and tool-model bundle require review. |
| `rfdetr` | `MANUAL_INSTALL` | Reviewed environment | No assumed upstream runtime API. |
| `grounding-dino` | `MANUAL_INSTALL` | Reviewed environment | Environment/checkpoint requirements need a locked profile. |
| `paddleocr-vl` | `MANUAL_INSTALL` | Reviewed environment | OCR tool profile is separate from any conversational model. |
| `qwen3-tts` | `MANUAL_INSTALL` | Reviewed environment | TTS environment/model profile remains manual. |
| `seed-vc` | `MANUAL_INSTALL` | Reviewed environment | Voice conversion environment/model profile remains manual. |
| `voice` | `REFERENCE_EXISTING` | Existing installation | External/legacy voice assets are discovered, not relocated or silently installed. |
| `airi` | `UNSUPPORTED` | External-managed | AIRI remains installer/cloud-managed and is outside Hub local model installation. |

## Resilience conclusions

- Source availability is cached independently from local installation.  A local
  receipt and matching runtime evidence can remain `OPERATIONAL` while source
  state is `UNAVAILABLE`, `AUTH_REQUIRED` or `DEGRADED`.
- Trusted fallback entries are identity-matched to the primary artifact; a
  different artifact is rejected.  When all reviewed sources fail, the UI
  offers Manual Import or Existing Install Reuse rather than a dead download.
- No catalog entry authorizes a conversational/chat LLM download for AIRI or
  Hub.  `AUTO_INSTALL_READY` currently has exactly one runtime (`ffmpeg`) and
  zero models; this is intentional and truthful until a complete bundle is
  reviewed.

The source references used for this review are the official TencentARC AnimeSR
repository, Meta FAIR SAM2 repository/checkpoint documentation, SYSTRAN
faster-whisper releases, and the official ComfyUI repository/documentation.
