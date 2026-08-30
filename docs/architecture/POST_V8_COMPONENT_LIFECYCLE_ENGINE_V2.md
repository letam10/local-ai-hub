# Post-V8 Phase 2 — Component Lifecycle Engine V2

Component Lifecycle Engine V2 gives catalog-backed models and runtimes one
finite lifecycle contract without replacing the durable V8 component-operation
journal or creating a second executor.

## Lifecycle contract

Every `component:`, `model:`, or `runtime:` Capability Graph V2 record can be
inspected through the same ordered action vocabulary:

```text
DISCOVER → INSPECT → PLAN_INSTALL → VERIFY_SOURCE → INSTALL
→ VERIFY_INSTALL → START → HEALTH_CHECK → STOP
→ UPDATE → REPAIR → UNINSTALL → ROLLBACK
```

The action list is a contract vocabulary, not a claim that every action has a
local executor. Each action carries one of these truthful availability modes:

- `read_only`: returns only the current server-owned graph projection;
- `plan_available`: can create the existing durable V8 plan, still
  `execution: not_run` and `dry_run: true`;
- `plan_required`: no direct installation is accepted; a prior plan and the
  V8 explicit-confirmation flow are required;
- `blocked`: no registered lifecycle adapter exists, or a graph blocker keeps
  the action unavailable.

Consequently `VERIFY_SOURCE` remains blocked until a source-verification
adapter exists. `START`, `HEALTH_CHECK`, `STOP`, and `ROLLBACK` likewise stay
blocked unless a separately reviewed, server-owned adapter is introduced. The
engine never reports a component as running merely because its catalog leaf
exists.

## Existing V8 authority retained

For eligible catalog `model:` and `runtime:` records, `PLAN_INSTALL`,
`VERIFY_INSTALL`, `UPDATE`, `REPAIR`, and `UNINSTALL` delegate only to the
existing `ComponentLifecycleCoordinator` plan methods. `component:` records
remain inspectable and show their exact graph blockers, but cannot fabricate a
model/runtime plan. The public result contains a new opaque V8 `operation_id`,
`plan_id`, and fingerprint; the engine does not call confirmation, start a
worker, download a dependency, or mutate a model/runtime tree. Confirmation
remains the existing V8 route and must be explicitly authorized by the user.

`DISCOVER` and `INSPECT` are satisfied by the GET projection endpoints rather
than by creating a fake durable operation. Every POST-created lifecycle plan
therefore has a real V8 `operation_id`, `component_id`, `state`, timestamps,
and durable journal entry; read-only inspection is never presented as a
running or completed executor task.

This preserves all prior V8 transaction, cancellation, source-acceptance,
external-owner, updater, and rollback boundaries.

## API

```text
GET  /api/component-lifecycle/v2
GET  /api/component-lifecycle/v2/{capability_id}
POST /api/component-lifecycle/v2/{capability_id}/plans
```

The POST body is exactly `{ "action": "<finite lifecycle action>" }`. GET
accepts opaque `component:`, `model:`, and `runtime:` graph identities; POST
can create a plan only for eligible `model:` and `runtime:` identities.
Browser clients cannot submit a filesystem path, URL, provider command, model
bytes, or graph edge. Unknown/unsafe IDs get a fixed non-reflecting
`lifecycle_capability_not_found` response.

## Scope boundary

This phase is a unified planning and status contract for SAM2, Whisper, OCR,
AnimeSR, ComfyUI, image/video tooling, FFmpeg/helper runtimes, and future
catalog-backed records. It intentionally does not yet register provider
start/stop/health adapters, perform source downloads, load model weights,
probe GPU hardware, or replace V8 durable operation persistence. A later
lifecycle-adapter phase can activate an action only with an exact capability
binding and bounded evidence.
