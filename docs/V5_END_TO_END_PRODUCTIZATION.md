# V5-D End-to-End Productization

V5-D composes the V5-A durable job/recovery engine, V5-B capability and Module
Manager plane, and V5-C Workspace/Workflow Library into one local-first product
surface.  It owns adapters and public projections only; the underlying engines
remain the source of truth.

## Product journey

```text
Dashboard readiness
  -> server-owned capability registry + Module Manager preflight
  -> Workspace / Workflow Library revision
  -> Jobs queue and recovery state
  -> opaque Artifact Store preview and range delivery
```

`GET /api/bootstrap` carries the composed `v5-product-surface.v1` snapshot.
The snapshot contains readiness reason/next action, deterministic module
statuses, active/attention/recoverable job counts, Workflow Library recovery,
and bounded GPU/storage warnings.  The existing `GET /api/capabilities` route
remains the full server-owned capability and plan contract.

## Workflow Library API

The API delegates to the existing `WorkflowLibraryStore` and
`workflow-library.v1` schema:

- `GET /api/workflow-library` and `GET /api/workflow-library/{id}` are read-only.
- `POST /api/workflow-library` saves one validated entry with an optional
  `expected_revision`.
- `POST /api/workflow-library/import` imports a validated canonical document.
- `POST /api/workflow-library/migration/plan` is dry-run only.
- `POST /api/workflow-library/migration/confirm` requires explicit user action
  and an optional expected revision.
- `DELETE /api/workflow-library/{id}` requires the expected revision when the
  caller has one.

Conflict and recovery responses are returned as typed `409`/`503` responses;
the server never overwrites an unreadable or concurrently changed library.

## Safety and truthfulness

All composed projections are detached JSON with `execution: not_run` and
`dry_run: true`.  Client input is accepted only by the existing strict
Workflow Library validator.  No client callable, command, manifest, raw local
path, secret, model/media blob, or execution descriptor is reflected.  Job
retry/resume remains visible only when the existing public job record marks it
recoverable; interrupted or non-reconstructable records retain a next action
to create a new task.  Artifact IDs remain opaque and existing HTTP Range/HEAD
behavior is unchanged.

Provider, filesystem mutation, model, GPU, video, FFmpeg, server, browser,
download, and install evidence remains outside this static integration gate.
