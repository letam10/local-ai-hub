# Post-V8 Milestone 1.1 Foundation Hardening

## Purpose

Milestone 1.1 makes the Post-V8 foundation a safe dependency boundary for a
future Workflow Runtime. It does not launch workers, models, providers or GPU
workloads. It preserves the installed application, `DATA_ROOT`, models,
environments, runtime and user output.

## Durable Job and Scheduler Saga

`DurableJobEngineV2` owns durable SQLite history. `ResourceScheduler` owns
only process-local coordination and reservations.

For an admitted or already-owned job, a state transition uses this bounded
saga:

1. Capture an internal scheduler checkpoint.
2. Apply the exact scheduler transition.
3. Persist the durable record using its expected SQLite `revision`.
4. If persistence rejects the transition, restore the full scheduler
   checkpoint only when the exact scheduler revision and epoch have not been
   superseded. Otherwise return a conflict; never report a false success.

Admission is special: the scheduler record is discarded by exact job,
worker and revision if the initial durable insert fails, because no worker can
claim a record that was not persisted.

Both the scheduler and SQLite rows advance revisions. SQLite compare-and-swap
rejects stale writes. The engine also serializes whole local sagas so a
threaded HTTP request cannot split a scheduler transition from its durable
record; it holds the scheduler's internal re-entrant coordinator across the
SQLite CAS/compensation window. Terminal scheduler records are bounded to 128; durable history remains
in SQLite and no artifact is deleted by scheduler pruning.

## Restart, Readmission and Lease Ownership

The scheduler is process-local. After restart, an unstarted durable job is
`NEEDS_READMISSION`, not a fictional waiting reservation. A trusted,
server-owned `readmit_after_restart` method may recreate a reservation only
after the registered owner, sanitized reproducible request and optional
current preflight pass. It never adopts a previous worker and it is not a
browser-owned dispatch endpoint.

A claimed worker has an opaque lease:

- `lease_id`, `worker_id`, `job_id`, `reservation_id`
- `heartbeat_at`, `expires_at`

The worker must supply the current lease for progress, pause, resume and
finish. A stale lease releases the reservation and records a failed scheduler
state. The browser never owns a lease or a resource estimate.

## Resource Admission

The scheduler accepts only server-owned hardware snapshots and profiles. When
available, an observed free-VRAM value, Hub reservations and a configurable
safety margin bound admission. With no observed free-VRAM value it remains a
declared preflight and reports that absence honestly. An exclusive reservation
blocks both a new shared GPU reservation and a new exclusive reservation; the
maximum heavy GPU reservation is one. Image/model-specific resource profiles
must be supplied by a trusted server binding rather than assuming an 8 GiB
device.

## Model Digest Semantics

`identity_digest` fingerprints catalog metadata only. It is never compared to
a file `sha256`. A strong duplicate needs a matching declared model
`content_sha256` and a matching bounded content observation. Equal model ID
and size is only a candidate. `VERIFY_CHECKSUM` reports
`checksum_not_declared` or `not_verified` unless declared content-hash evidence
exists; it does not claim a fresh byte hash merely from metadata.

## Capability Evidence and Lifecycle Scope

Engine and GPU dependency nodes can advance from `REGISTERED` to `VERIFIED`
only from a bounded server-owned observation carrying a fingerprint,
`observed_at`, source revision/fingerprint and freshness policy. Stale evidence
is retained as evidence but is downgraded from `OPERATIONAL` to `VERIFIED`.
No provider or model execution is used to build that bridge.

The lifecycle facade explicitly supports `model` and `runtime` capability
kinds. `component:*` remains inspect-only until a real server-owned component
lifecycle adapter exists; it never advertises fake START, STOP or INSTALL.

## Feature Discovery and Projection Cache

`/api/features/v2` derives its contract from the active Router registry. Each
published route includes `route_id`, HTTP method, path, contract version and
execution mode. A missing or mismatched required Post-V8 route is omitted from
the advertised features and leaves discovery unavailable.

Capability Graph, Lifecycle and Model Manager metadata projections use a
short-lived fingerprint-aware cache. Every lookup compares current bounded
input fingerprints. Live execution records and current resource evidence are
not cached, so execution state is never served from a stale projection.

## Test Evidence

The focused foundation suites cover SQLite-after-scheduler failure injection,
stale CAS rejection, concurrent finish/cancel, restart readmission, worker
lease expiry, more than 512 sequential terminal jobs, exclusive GPU admission,
content-hash semantics, evidence freshness, Router-bound discovery and cache
invalidation. They are static/simulator tests only.
