# Post-V8 Milestone 2 — Workflow & Data Foundation

## Purpose

Milestone 2 starts from the reviewed Milestone 1.1 Capability Graph, Resource
Scheduler and Durable Job Engine contracts. It does **not** introduce a second
worker, a browser-owned runner, or an unsafe replacement for the existing
Project, Artifact or Workflow Library stores.

The first M2 increment publishes four Router-bound V2 protocol surfaces:

| Surface | Contract | Authority | Execution truth |
| --- | --- | --- | --- |
| Workflow Runtime | `workflow-runtime.v2` | Server-owned typed graph/capability/resource/artifact preflight | `not_run`, `plan_only` unless a future server-owned binding is registered |
| Project Workspace | `project-workspace.v2` | Existing Creative Project Manager metadata store | read-only projection plus explicit opaque reference attachment |
| Artifact Library | `artifact-library.v2` | Existing V7/V8 opaque Artifact Store projection | read-only projection |
| Media Pipeline | `media-pipeline.v2` | Closed finite media plan vocabulary | `not_run`, `plan_only` |

No default production endpoint in this increment starts FFmpeg, AnimeSR, RIFE,
a provider, a model, a GPU workload or a background thumbnail worker. The
normal V2 dispatch path remains unavailable because it has no registered
server-owned execution binding. Project/job/workflow links and Workflow Library
favorite/recent metadata are explicit user mutations only; they do not create
a job, copy an artifact, change an artifact's bytes or start a worker.

## Workflow Runtime V2

`POST /api/workflow-runtime/v2/preflight` accepts only a bounded graph envelope
containing `graph`, optional opaque `project_id` and optional workflow
reference. It performs these steps without persistence:

1. Typed Node Studio graph validation, including input, socket and DAG checks.
2. A finite node contract projection with declared capability and resource
   requirements.
3. Capability Graph lookup. A capability is ready only when its server-owned
   operational state is `OPERATIONAL`; stale, registered, missing and unknown
   evidence are blockers rather than an execution claim.
4. A scheduler-derived dry-run resource plan. Heavy nodes require one
   exclusive heavy-GPU slot; this plan never probes a device or reserves VRAM.
5. Opaque artifact lookup for `load_*` nodes. Artifact type mismatches and
   absent artifacts remain blockers; no path is sent to the browser.

The workflow lifecycle vocabulary is `DRAFT`, `VALIDATED`, `READY`, `RUNNING`,
`FAILED`, and `COMPLETED`. In this increment only the first three are
projected. `READY` means preflight-ready **for a future registered execution
owner**, not running or dispatched.

`POST /api/workflow-runtime/v2/dispatch` is deliberately truthful. Without an
already-composed server-owned `execution_owner`, `worker_id` and approved
profile binding it returns `unavailable` and never calls durable admission. A
test-only/future composed binding may create a durable record with
`execution=not_run`; it still does not launch a worker from this layer.

Unknown node types are reported as `preserved_unexecutable`. Preflight does not
write or migrate the originating draft/library record, so it cannot discard a
future-plugin node while reporting it as unsupported.

## Existing persistent owners

- `Config/workflow_library.json` remains owned by `WorkflowLibraryStore` and
  retains its existing revision/CAS/no-follow contract. It now supports two
  explicit metadata mutations: favorite and opened-at. Both use the same
  library revision CAS and atomic write as save/delete; listing or opening by
  GET remains side-effect free. Legacy V1 records without those optional fields
  normalize as `favorite=false` and no opened timestamp.
- `Config/creative_workspace.json` remains owned by `CreativeProjectManager`.
- The Artifact Store and V8 Output Authority retain artifact/object ownership,
  publication and preview authority.

`POST /api/project-workspace/v2/{project_id}/workflows` and `/jobs` attach
only an existing opaque workflow/job identifier after source validation. The
project export route is a metadata-only manifest projection containing safe
workflow schemas, opaque artifact references and project metadata; it does not
write an archive. The V2 projection also enumerates bounded tracked templates,
saved workflows, favorites and explicit recent entries.

M2 V2 surfaces never introduce new persistent state files and never expose
absolute paths, credentials, raw subprocess arguments, or arbitrary preview
URLs. Existing artifact opening, tagging and collection policies stay with the
existing owner routes.

## Media Pipeline V2

The media contract exposes the finite plan stages `probe`, `transform`,
`upscale`, `frame_interpolation`, `encode`, `preview`, `save`, and `export`.
It permits only enumerated preflight operations. `ffmpeg_scale` is explicitly
annotated `ai_upscaler=false`; `ffmpeg_minterpolate` is explicitly annotated
`ai_interpolator=false`. `animesr` preflight stays unavailable until a
separately verified server-owned execution owner exists.

This avoids presenting a fallback as an AI operation and avoids treating a
visible control as an executed media task.

The Media and Video pages can submit a **preflight only** for an already
registered opaque VIDEO artifact. The UI has no path field, and it displays
`ffmpeg_scale`/`ffmpeg_minterpolate` as non-AI fallbacks. It never submits the
generic legacy media job form from this panel.

## Tests and next increment

Focused M2 tests cover typed graph validity, unknown-node preservation,
capability/resource/artifact blockers, opaque project/artifact projections,
workflow/job attachment, favorites/recent CAS behavior, closed media preflight
and Router registration. Future M2 work may add a trusted workflow execution
owner, artifact thumbnail jobs and real media worker integration only with
dedicated ownership, cancellation, artifact-publication and bounded
functional-smoke contracts.
