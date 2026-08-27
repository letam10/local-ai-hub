# Post-V8 Phase 1 — Capability Graph V2

Capability Graph V2 is the first independently reviewable increment of the
Post-V8 product evolution program. It adds a server-owned, read-only graph to
the existing V5 capability registry rather than changing the meaning of a
legacy `partial`, `healthy`, or `not_run` field.

## Identity and state dimensions

Every capability has a stable `capability_id` and the following bounded public
identity fields:

- `provider`, `component`, `runtime`, `model`, `dependencies`, and `version`;
- `install_state`;
- `runtime_state`;
- `verification_state`;
- `operational_state`;
- `last_verified`, path-free `evidence`, `reason`, and `next_action`.

The operational state vocabulary is finite:

```text
DISCOVERED → REGISTERED → INSTALLED → INSTALLED_UNVERIFIED → VERIFIED
→ STARTABLE → RUNNING → OPERATIONAL

DEGRADED | UNAVAILABLE | BROKEN
```

Those words are not aliases. For example, `INSTALLED_UNVERIFIED` does not
make a worker `STARTABLE`, and `OPERATIONAL` is rejected unless the graph has
current bounded evidence with a fingerprint at `BOUNDED_SMOKE` or `PRODUCTION`
tier.

## Graph composition

The service accepts only already-owned, bounded projections:

1. component state from the API core;
2. tool-to-component relationships from the Hub tool catalog; and
3. V7 production-catalog model/runtime records.

It creates explicit `tool:`, `component:`, `runtime:`, `engine:`, `model:`,
`worker:`, and catalog-declared `resource:` capability identities where the
source relationship exists. For example, a runtime can depend on
`engine:python`, and a model that declares `minimum_vram_mb` depends on
`resource:gpu`. These Phase 1 engine/resource nodes are only `REGISTERED`:
they state the exact requirement, not that a process, GPU fit, or scheduler
reservation exists. Phase 4 will attach server-owned scheduler reservations
and GPU/CPU observations. Missing or cyclic dependencies remain graph
nodes/blockers; they are never converted into an optimistic generic status.

No graph read runs an installer, downloader, provider, model, worker, GPU
workload, recursive storage scan, or filesystem-wide discovery. The graph only
uses fixed-leaf catalog observations already permitted for the server-owned
catalog snapshot.

## Public API

All endpoints are loopback router routes and accept no path, command, URL,
credential, or client graph payload:

```text
GET /api/capabilities/v2
GET /api/capabilities/v2/{capability_id}
GET /api/capabilities/v2/{capability_id}/dependency-tree
GET /api/capabilities/v2/{capability_id}/blockers
GET /api/capabilities/v2/{capability_id}/safe-actions
GET /api/capabilities/v2/{capability_id}/verification-evidence
```

Responses have `execution: not_run` and `dry_run: true`. The graph reports
only opaque identities, finite state codes, safe action IDs, path-free reasons
and bounded evidence. An unknown or unsafe capability ID returns a fixed
`capability_not_found` error without reflecting input.

## Components UX

The Components page has a read-only Capability Graph V2 panel. It lists the
four distinct state dimensions and renders exact blocker identities such as
`worker:whisper` or `model:faster-whisper-large-v3`. It does not offer an
execution button. Refresh occurs only on page rendering or an explicit user
refresh, never through polling.

## Phase boundary

This phase is intentionally not a lifecycle executor, Model Manager V2,
Resource Scheduler, workflow runtime, plugin SDK, or updater rewrite. Those
later phases must consume these stable opaque graph identities and preserve
the existing V8 updater, external-owner, loopback, artifact and evidence
boundaries.
