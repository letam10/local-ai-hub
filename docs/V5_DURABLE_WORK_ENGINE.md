# V5-A Durable Work Engine and Recovery

V5-A provides a server-owned, declarative work-engine core. It does not add a
route, provider, model runner, media worker, GPU probe, browser flow, or
background service integration. All execution claims remain `not_run`; resource
plans are `dry_run` only.

## Closed contracts

`job-spec.v1` contains exactly a safe tool ID and an
`execution-descriptor.v1`. The descriptor contains a server adapter ID, the
single `run` operation, bounded JSON arguments, a reconstructable flag, and a
declarative resource request:

- CPU slots, GPU slots, RAM and disk estimates are bounded integers.
- An optional opaque exclusive group allows mutual exclusion in the planner.
- Arguments are JSON only, depth/size bounded, and reject client execution
  objects, unsafe keys, local paths, URLs, shell-like fields, secrets, media,
  model-like values, and arbitrary mappings outside the closed shape.
- The adapter ID is resolved only from `ServerOwnedAdapterRegistry`, which is
  constructed and populated by server source. A client cannot provide a
  loader, callable, registry, manifest, executable, or runtime configuration.

Validation raises fixed `JobContractError` codes with safe actions; it never
returns the rejected value in an error or public projection.

## State and recovery model

The authoritative V5 states are:

`queued`, `starting`, `running`, `cancelling`, `completed`, `failed`,
`unavailable`, and `interrupted`.

Transitions are locked and terminal states cannot regress. A queued cancellation
is recorded as `cancelling` then `interrupted`; a running cancellation signals
only the engine-owned cooperative context. If a completion and cancellation
race, the first terminal transition wins and cannot be overwritten.

The atomic `DurableJobStore` keeps serializable records only, coalesces noisy
progress writes, flushes state transitions, and caps terminal history. At
startup, every formerly active record becomes `interrupted`; no job resumes
automatically. `retry`/`resume` creates a new attempt only when its persisted
descriptor still validates, is marked reconstructable, and names a currently
registered server adapter. Otherwise the public next action is to create a new
allowlisted descriptor.

## Capacity and artifacts

Concurrency, GPU-slot and exclusive-group controls are Python policy semaphores
only. They do not query, reserve, launch, benchmark, or otherwise use a GPU.
When a requested slot is unavailable, the record remains truthful (`queued`
with a bounded retry action, or `unavailable` when no slot is configured).

`atomic_write_job_output` writes a Hub-owned output through a temporary file and
atomic replacement, checks free disk capacity first, and registers only opaque
artifact metadata. Provenance is restricted to the V5 job ID, descriptor
fingerprint, server adapter ID, attempt, and state. Public artifact and job
views omit raw descriptors, arguments, filesystem paths, host data, runner
state, and exception text. Existing artifact streaming and HTTP Range handling
are deliberately unchanged.

Uploads use a Hub ownership prefix. Expired, unregistered partial or orphaned
upload files can be cleaned only from the managed upload root; unrelated files
and registered artifacts are retained. Disk guards fail before a write instead
of deleting user output to make room.

## V5-D integration requirement

A future, separately reviewed V5-D change must wire this core to a server-owned
adapter registry and API lifecycle. It must not accept a registry, manifest,
descriptor mapping, local path, or callable from a client. Before any adapter
is reported operational, that integration needs its own bounded functional
smoke and explicit runtime authorization. V5-A remains `partial` / `not_run`.

## Verification boundary

The V5-A unit tests cover closed descriptor rejection, deterministic detached
projection, restart reconciliation, retry eligibility, cancellation races,
concurrency/GPU policy, atomic output provenance, upload orphan cleanup, disk
guarding, bounded history, public redaction, and the existing Range parser.
They use temporary roots and in-memory adapters only. No server, UI, GPU,
video, FFmpeg, SAM2, model, provider, download, installation, or benchmark is
run by this package.
