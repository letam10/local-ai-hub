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
  -> PREPARE: COPY TO A HUB-OWNED NON-PUBLIC TRANSACTION OBJECT
  -> AUTHORIZE: DURABLY RECORD RESERVATION + TRANSACTION + OBJECT PROOFS
  -> ONE FINAL PUBLIC COMMIT: STAGED OBJECTS BECOME PUBLISHED TOGETHER
```

The presence of a path in `Output` is never ownership proof.  A pre-existing,
foreign, sibling, replaced or ambiguous file is preserved and is not
published or deleted.

## Lifecycle

Reservations use the finite states `created`, `producing`, `ready_to_commit`,
`ready_to_publish`, `commit_authorized`, `published`, `failed`, `cancelled`,
`interrupted` and `manual_review`. Creation, producer handoff, preparation,
authorization, final publication and cleanup are server-owned transitions.
After `commit_authorized`, recovery can distinguish a staged transaction from
one that already committed publicly; a later reservation-manifest
normalization write is never required for public-commit success.

Preparation is private: each candidate is copied with bounded size/SHA-256 and
identity proof into a unique Hub-owned managed object under the Artifact Store
boundary. The object is separate from both the producer reservation root and
the `.hub-reserved` input/staging path. Its index record is `staged` and is
ignored by public list/get/resolve/download projections. No prepared artifact
ID is public-capable.

Authorization persists the reservation ID, transaction ID and exact prepared
object proofs before any public visibility mutation. The final Artifact Store
commit validates every prepared object and performs one atomic managed-index
write from `staged` to `published`. There is no mandatory reservation save
after that commit. A published artifact never points to the mutable producer
reservation path; its managed object is copy-once and later identity drift
causes public resolution to fail closed.

Commit validates the exact private reservation chain, no-follow regular-file
identity, bounded count/bytes, relative leaves, stable bytes and finite
provenance before preparing the private transaction objects. Any failure
before authorization aborts transaction-owned staged records and objects, so
zero public artifacts are visible. A failure to persist authorization also
aborts before public visibility. A failure during final public commit leaves
the managed index at its prior non-public state; a successful final commit is
recoverable even if later cached reservation normalization is unavailable.

Cleanup removes only identity-attested files allocated in the reservation
scope. Reparse points, replacement identities, foreign children and any
uncertain file produce `manual_review`; preservation wins over cleanup. Crash
reconciliation reads only explicit reservation manifests and transaction
records: staged authorized work is aborted, while a transaction already
published is normalized later without another public commit. It never scans
the arbitrary Output tree.

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
