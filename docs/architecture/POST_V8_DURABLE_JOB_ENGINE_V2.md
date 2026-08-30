# Durable Job Engine V2

## Purpose

Durable Job Engine V2 persists bounded, server-owned job metadata for the
Post-V8 platform foundation. It replaces a history-only projection with an
explicit state machine while preserving the existing V5 durable-job routes and
their compatibility behavior.

The engine does not launch a process, load a model, call a provider, start a
GPU workload, or accept a filesystem path from the browser. A separately
integrated, server-owned execution owner must make an exact worker claim before
a job is publicly represented as executing.

## Owner and persistent state

| Item | Contract |
| --- | --- |
| Service owner | `src/services/durable_job_engine_v2/` |
| API owner | `src/services/api/routes/durable_job_engine_v2.py` |
| Persistent leaf | `DATA_ROOT/Config/durable_jobs_v2.sqlite3` |
| Schema | `durable-job-store.v2` + `durable-job-engine.v2` |
| Git state | Machine-local metadata; never committed |
| Data retained | Opaque job/workflow/artifact IDs, state, timestamps, bounded reason/action, retry lineage and exact reservation ID |
| Data forbidden | Raw paths, user media bytes, worker commands, process handles, provider requests, credentials, tokens and model bytes |

Reads never create the SQLite store. A store is initialized only by a
server-owned mutation such as an admitted durable job, archive, or confirmed
terminal-history deletion. The store validates the full existing parent chain
without following reparse points.

## States and execution truth

The only state vocabulary is:

`QUEUED`, `WAITING_RESOURCE`, `PREPARING`, `RUNNING`, `PAUSED`,
`CANCELLING`, `CANCELLED`, `SUCCEEDED`, `FAILED`.

`dispatchable` means a trusted server-owned owner and scheduler accepted a
future worker admission. It does **not** mean that a worker started.
`actual_execution` changes to `true` only after that exact worker claims the
matching scheduler reservation. Public projections remain:

- `execution=not_run`, `dry_run=true` for queued/preparing non-executing work;
- `execution=running|paused|cancelling` once a trusted worker has claimed it;
- a terminal execution outcome only after an actual owned execution reached a
  terminal state;
- `Đã tạo · chưa thực thi` for `RECONSTRUCT_ONLY` records.

`counts.active` includes real dispatchable scheduler work and exact owned
execution, but excludes reconstruct-only queued records. This prevents a
recreated V8 record from appearing as an executing job.

## Scheduler and worker binding

Admission binds the scheduler's exact `job_id`, `worker_id`,
`reservation_id`, resource profile and reservation capacity. `claim_running`,
progress, pause, resume, finish and cancellation acknowledgement require the
same identifiers. Browser routes cannot call these worker-only methods.

Pause/resume are only available when the registered server-owned execution
owner explicitly advertises the respective capability. The Phase 5 default
composition registers no execution owner, so an ordinary app startup cannot
mistakenly create a worker or advertise a runnable action.

## Retry and history safety

`RETRY_EXECUTION` is unavailable until a real durable dispatch bridge exists.
`RECONSTRUCT_ONLY` creates a **new** record with `retry_of` pointing at the
terminal source, `actual_execution=false`, no reservation, and no active count.
It never reopens or mutates the historical failed record.

Archive and selected-history deletion affect metadata only. They require a
terminal record; bulk deletion validates the complete selected set and uses one
SQLite transaction. Artifacts remain referenced/preserved and are never
deleted by this engine.

## Restart reconciliation

On startup the engine never promotes a queued or preparing record to `RUNNING`.
Pre-start reservations become non-dispatchable `WAITING_RESOURCE` records that
must be re-admitted by a server-owned owner. A prior `RUNNING` record becomes:

- `SUCCEEDED` only when a separately supplied bounded artifact-complete check
  confirms the existing artifact set;
- `FAILED` when the worker is gone and no complete artifacts exist;
- `FAILED` when a worker appears live but a durable V2 ownership lease cannot
  be proved.
- `FAILED` with an explicit liveness-unavailable reason when this product
  generation has no trusted worker-liveness bridge at all.

No branch deletes artifacts, kills a worker, adopts a foreign process, or
claims that a mere queued record is running.

## API

All routes are loopback application routes and accept finite JSON shapes only:

| Method | Route | Meaning |
| --- | --- | --- |
| `GET` | `/api/durable-job-engine/v2` | Bounded snapshot with optional finite status/query/archive filters |
| `GET` | `/api/durable-job-engine/v2/{job_id}` | One opaque durable record |
| `POST` | `/api/durable-job-engine/v2` | Server-owned admission request; unavailable unless an owner is registered |
| `POST` | `/api/durable-job-engine/v2/{job_id}/retry` | `RETRY_EXECUTION` or truthful `RECONSTRUCT_ONLY` |
| `POST` | `/api/durable-job-engine/v2/{job_id}/cancel` | Requests scheduler cancellation; no artifact deletion |
| `POST` | `/api/durable-job-engine/v2/{job_id}/archive` | Archives terminal metadata |
| `POST` | `/api/durable-job-engine/v2/history/delete` | Confirmed terminal metadata-only bulk deletion |

## UI

The Jobs page adds a local-only Durable Job Engine V2 panel. It provides status
groups, bounded server search, active/reconstructed/terminal counts,
metadata-only archive/delete and `Tạo lại tác vụ`. It has no polling loop, no
browser execution bridge and no `window.confirm`; metadata deletion uses an
explicit two-step in-page confirmation.

## Tests

`tests/test_post_v8_durable_job_engine_v2.py` covers read-without-creation,
opaque admission, exact reservation binding, progress/terminal artifact
preservation, pause/resume capability, reconstruct-only retry, startup
reconciliation, archive/delete atomicity, API payload rejection, default
composition and UI/route ownership checks. All tests use temporary fixture
directories and an in-memory scheduler simulation only.
