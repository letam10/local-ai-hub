# V7 job-output transaction safety

The job manager creates a private, bounded `job-output-scope.v1` manifest for
output-producing jobs before the worker starts. The manifest contains only an
opaque job fingerprint, safe relative `Output` leaves, a server-owned `claims`
ledger, bounded file identity metadata, ownership (`owned` or `ambiguous`),
and a finite terminal state. It never enters the public job projection and
never stores an absolute path, command, log, stderr, client value, or secret.

Before creating a new output leaf, a server-owned worker must call
`JobContext.claim_output(path)`. The claim is accepted only for a safe,
currently absent relative leaf under the server-owned `Output` root and is
persisted atomically in the private manifest. A path that was already present
in the pre-worker bounded snapshot, appeared before an explicit claim, or
whose ownership cannot be proven, is preserved and marked `manual_review`;
absence from the baseline alone is never ownership proof. When a worker
returns output candidates, the artifact store validates every candidate as a
regular file under the same root, rejects traversal, external paths, reparse
points, directories, duplicates, and size overflow, then records the current
identity only for explicitly claimed leaves.

Cancellation and publication failure resolve the manifest under the job
context lock. Only an identity-matching candidate explicitly marked `owned`
may be removed; changed, missing-identity, pre-existing, or ambiguous files
are never deleted. Successful publication keeps the files and reaches the
existing opaque artifact registry. The existing artifact registration remains
the publication boundary, so no public artifact/index entry is created for a
failed or cancelled job.

If the required scope is missing, malformed, or cannot be read after submit,
the job fails closed before `_publish_result` or any public artifact/index
action. The result is a fixed path-free unavailable ownership outcome with
`execution=not_run` and `dry_run=true`; ambiguous files are preserved.

Persisted open/failed/cancelled scopes can be reconciled in a bounded,
idempotent pass. Reconciliation handles only the explicit manifest candidates
and never scans arbitrary `Output` files for deletion. Existing staged
artifact transactions, opaque IDs, native preview/Range transport, and legacy
worker output behavior remain unchanged. Workers that do not use the claim
seam fail closed at publication rather than receiving an inferred ownership
grant.
