# Post-V8 Phase 4 — Resource Scheduler / GPU Orchestrator V2

Resource Scheduler V2 is a server-owned capacity state machine. It is
intentionally independent from GPU probing, model loading, provider launch,
and current job dispatch. Its input is only a bounded hardware snapshot
published by server code or a test fixture.

## Resource inventory and profiles

The inventory tracks bounded CPU slots, RAM, disk, GPU identities/VRAM,
runtime slots, and provider slots. `vram_used_by_processes_mb` remains `null`
unless an independently authorized hardware observer supplies it; a scheduler
reservation is not an OS/GPU process measurement.

Each fixed server-owned profile contains:

```text
estimated_vram_mb, estimated_ram_mb, gpu_required, cpu_fallback,
exclusive, priority, interruptible, batchable, cpu_slots, disk_mb,
runtime_slot, provider_slot
```

Profiles include light CPU/FFmpeg work plus 2/4/8 GiB declarations for
vision, Whisper, video, and image workflows. They are capacity declarations,
not adapter or provider launch configurations.

## Reservation state machine

The finite public lifecycle is:

```text
QUEUED → WAITING_RESOURCE → PREPARING → RUNNING → PAUSED
                         → CANCELLING → CANCELLED
RUNNING / PAUSED → SUCCEEDED | FAILED
```

A reservation is created only when all currently known capacity constraints
fit. It binds exactly:

```text
reservation_id, job_id, worker_id, gpu_id, vram_estimate_mb, expiry
```

`RUNNING`, pause/resume, cancellation acknowledgement, and terminal result
all require the same exact `job_id`, `worker_id`, and `reservation_id`.
Mismatch never releases capacity. A pre-start reservation that expires returns
to `WAITING_RESOURCE`; it never becomes a fabricated running worker.

The default `max_heavy_gpu_jobs` is **1**. A heavy GPU profile is therefore
held in `WAITING_RESOURCE` even if raw VRAM arithmetic alone would permit a
second job. Lightweight CPU profiles can be prepared concurrently only when
the bounded CPU/RAM/disk inventory permits them. Waiting jobs are reconsidered
by priority after a reservation is released, but scheduler metadata never
starts a worker.

## Public routes and Dashboard

```text
GET /api/resource-scheduler/v2
GET /api/resource-scheduler/v2/profiles
GET /api/resource-scheduler/v2/jobs/{job_id}
```

Phase 4 exposes no browser mutation endpoint. Dashboard shows VRAM reserved,
GPU inventory (without claiming process usage), queue state, the policy limit,
and an honest unknown next-start time. The card refreshes only on dashboard
entry or explicit user refresh; there is no polling loop.

## Scope boundary

No `nvidia-smi` call, GPU workload, model/provider startup, worker dispatch,
or persistent job mutation occurs in this phase. The default installed runtime
has an `unknown` inventory unless a server-owned bounded configuration snapshot
is available. Durable Job Engine V2 is responsible for the future binding
between a validated job and `ResourceScheduler.submit`; it must not allow the
browser to fabricate a reservation.
