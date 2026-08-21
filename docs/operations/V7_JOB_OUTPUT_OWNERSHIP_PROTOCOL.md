# V7 Job Output Ownership Protocol

This document closes the repeated Output/Producer/Reservation failure class.
It is a source-correctness contract only; it does not claim that any model,
runtime, GPU worker or external process is operational.

## Authority

The server creates an opaque reservation before an output-producing worker is
started.  The reservation owns a private scope under the Hub-managed Temp
boundary and records the job fingerprint, adapter identity, finite output
limits and allocated relative leaves/directories.  A producer receives only an
internal reservation handle.  The browser and public API receive neither the
scope path nor a destination path.

The ownership rule is:

```text
SERVER RESERVES BEFORE WORKER
  -> PRODUCER WRITES ONLY THROUGH THE RESERVATION HANDLE
  -> ARTIFACT STORE COMMITS THE RESERVATION
```

The presence of a path in `Output` is never ownership proof.  A pre-existing,
foreign, sibling, replaced or ambiguous file is preserved and is not
published or deleted.

## Lifecycle

Reservations use the finite states `created`, `producing`, `ready_to_commit`,
`published`, `failed`, `cancelled`, `interrupted` and `manual_review`.
Creation, producer handoff, commit and cleanup are server-owned transitions.
Duplicate commit/cancel requests are harmless refusals or idempotent terminal
observations; they never create a second public artifact.

Commit validates the exact private reservation chain, no-follow regular-file
identity, bounded count/bytes, relative leaves, stable bytes and finite
provenance before copying into a server-owned managed staging scope and
registering opaque artifacts.  A failed, cancelled or uncommitted reservation
cannot create a public artifact entry.

Cleanup removes only identity-attested files allocated in the reservation
scope.  Reparse points, replacement identities, foreign children and any
uncertain file produce `manual_review`; preservation wins over cleanup.  Crash
reconciliation reads only explicit reservation manifests and never scans the
arbitrary Output tree.

## Producer inventory

The machine-readable inventory lives in
`src/services/job_manager/producer_inventory.py`.  Every known output-capable
callsite is classified as `MIGRATED_RESERVATION`, `BLOCKED_SAFE_LEGACY` or
`NO_OUTPUT_OR_BLOCKED_SAFE`; there is no `UNKNOWN` producer.

`run_media_operation` and `frame_interpolate` use the real
`JobContext.output_path()` / `output_directory()` reservation handle in the
media adapter.  Other legacy adapters remain blocked-safe: if they return a
path outside an allocated reservation, the Hub refuses publication and keeps
the uncertain file instead of inferring ownership or deleting it.

## Public truth

Public job results contain only bounded status, reason/action codes and opaque
artifact metadata.  Absolute paths, commands, URLs, secrets, object
representations and reservation roots remain private.  `NO VALID RESERVATION =
NO PUBLICATION` is enforced for both the legacy in-process Job Manager and the
durable output boundary.

## Compatibility and supersession

The refusal-safe manifest and staged-artifact checks from the earlier
`b8b436b` foundation remain useful for diagnostics and artifact integrity, but
post-hoc path snapshots are not the ownership authority for production
cleanup/publication.  The three historical lanes
`P2-V7-JOB-OUTPUT-TRANSACTION-SAFETY-001`,
`P2-V7-JOB-OUTPUT-PRODUCER-CLAIMS-001` and
`P2-V7-JOB-OUTPUT-RESERVATION-TOKEN-PROTOCOL-001` remain recorded with their
original QA failures and are superseded by
`P0-V7-JOB-OUTPUT-OWNERSHIP-PROTOCOL-FINALIZATION-001`.
