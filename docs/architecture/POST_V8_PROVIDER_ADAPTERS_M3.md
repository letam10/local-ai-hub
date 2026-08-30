# Post-V8 Provider Adapters & External Integrations — M3

## Purpose

Milestone 3 introduces one finite, server-owned contract for the first-party
provider seams already represented by Local AI Hub: SAM2, OmniParser, RF-DETR,
Grounding DINO, OCR, Faster-Whisper, Qwen3-TTS, Seed-VC, ComfyUI, FLUX, Qwen
Image, AnimeSR and the FFmpeg helper.  It also introduces a path-free external
application framework for AIRI, Blender and externally managed ComfyUI.

This is a contract layer, not an inference layer.  It does not import a legacy
adapter, start a worker, query a provider, load a model, inspect arbitrary
directories, reserve GPU, launch an external application or write state.

## Ownership and API

```text
WebView
  -> /api/provider-adapters/v2 (read + typed preflight only)
  -> /api/external-integrations/v2 (read + explanatory launch plan only)
  -> server-owned ProviderAdapterRegistry / ExternalIntegrationRegistry
  -> existing Capability Graph V2 + Resource Scheduler V2 / sanitized app registry
```

The browser may send only a bounded `input_types` list to provider preflight.
Paths, URLs, shell commands, provider request bodies, media bytes, model
references and credentials are rejected.  The registry accepts no dynamic
module name, import path, executable, endpoint, argument or worker identity.

| Surface | Contract | Execution truth |
|---|---|---|
| `GET /api/provider-adapters/v2` | finite first-party adapter catalog | read-only, `not_run` |
| `GET /api/provider-adapters/v2/{adapter_id}` | typed socket/dependency/resource contract | read-only, `not_run` |
| `POST /api/provider-adapters/v2/{adapter_id}/preflight` | input/dependency/resource plan | plan-only, no reservation/worker |
| `GET /api/external-integrations/v2` | installed/connection projection | read-only, `not_run` |
| `GET /api/external-integrations/v2/{integration_id}` | one external integration projection | read-only, `not_run` |
| `POST /api/external-integrations/v2/{integration_id}/launch-plan` | explanatory allowlist capability | plan-only; never launches |

## First-party adapter contract

Every adapter advertises the same stable method vocabulary:

`discover`, `availability`, `dependencies`, `start`, `stop`, `health`,
`estimate_resources`, `validate_input`, `execute`, `cancel`, and
`collect_artifacts`.

In M3, `discover`, `availability`, `dependencies`, `estimate_resources`, and
`validate_input` are metadata operations.  `start`, `stop`, `execute`,
`cancel`, and `collect_artifacts` truthfully return
`provider_adapter_execution_owner_unavailable`: there is no M3 execution
owner.  A dependency state of `READY_FOR_BINDING` means current Capability
Graph requirements are operational; it never means this registry has started
or can execute the provider.

Resource estimates use a server-owned declared scheduler profile and report
`not_reserved`.  They neither inspect GPU capacity nor accept a browser VRAM
claim.

## External integrations

External entries have these public connection states:

- `NOT_INSTALLED`
- `INSTALLED_NOT_CONNECTED`
- `CONNECTED`
- `UNSUPPORTED_API`
- `AUTH_REQUIRED`

The state is produced only from the existing sanitized allowlisted application
projection and an optional trusted enum-only observation.  It contains no
path, endpoint, cookie, token, API key or raw local configuration.  AIRI has
`official_channel: none` in M3, so even an attempted `CONNECTED` observation
cannot make it operational; an installed AIRI reports `UNSUPPORTED_API` until
an official, separately reviewed adapter exists.  The normal Hub launch button
remains the existing explicit server-owned allowlisted action.  M3's
`launch-plan` merely explains whether that action is available.

No AIRI WebView embedding, endpoint guessing, credential scraping or manual
relocation is permitted.

## Future execution binding

A future provider adapter must add all of the following before exposing an
executing action:

1. a server-owned execution owner with a stable adapter/worker identity;
2. exact capability and resource preflight, including an identity-bound lease;
3. typed opaque artifact input/output validation;
4. lifecycle/health/cancel/reconciliation behavior and bounded smoke evidence;
5. a dedicated security and operational review.

It must not repurpose this M3 projection as proof that a provider is running.

## Tests

`tests/test_post_v8_provider_adapters_m3.py` covers the closed catalog,
dependency/resource truth, typed-input rejection, no-execution defaults,
external-state truth, AIRI no-fake-connection behavior, Router contracts,
Feature Discovery binding and the AIRI route's API-only projection load.
