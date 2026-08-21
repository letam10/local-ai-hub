# V7 job-output transaction safety

The job manager creates a private, bounded `job-output-scope.v2` manifest for
output-producing jobs before the worker starts. The manifest contains only an
opaque job fingerprint, server-issued reservation records, safe relative
`Output` leaves, bounded file identity metadata, and finite reservation and
terminal states. A reservation token is the only producer authority. A path
is an internal write location and is never accepted as an ownership
credential. The manifest never enters the public job projection and never
stores an absolute path, command, log, stderr, client value, or secret.

Before a worker or helper can create an output, `JobContext.reserve_output()`
or `reserve_output_namespace()` issues an opaque token bound to the job,
producer label, expected leaf/pattern, namespace, bounded child count and
complete pre-worker snapshot. The producer receives the private path and
token only through the server-owned request. It must return token references
and call `JobContext.attest_output(token, path, producer)` after the write.
The artifact store revalidates the token, producer, expected leaf/pattern,
containment, reparse state, baseline absence, regular-file identity and size
before changing the reservation to `attested`.

When a worker returns a raw path without a matching reservation reference, or
an invalid/missing/corrupt/duplicate token, the result is
`output_scope_unavailable`/`manual_review`; no reservation is created and no
public artifact is published. Namespace membership, absence from the
baseline, or a producer-supplied path alone never grants ownership. A child
inside a claimed namespace without its own attestation is preserved and stays
`manual_review`.

Immediately before publication, the manager resolves token references to
internal paths from the private manifest. The token references and paths are
removed before the public job record is written; the existing artifact
registry returns only opaque artifact IDs and provenance.

Cancellation and publication failure resolve the manifest under the job
context lock. Successful publication marks the exact attested reservations
`published` and keeps the files in the existing opaque artifact registry.
Cancellation or publication failure adds only attested reservations to the
cleanup set; changed, missing-identity, pre-existing, foreign, unclaimed or
ambiguous files are never deleted and remain available for manual review. No
public artifact/index entry is created for a failed or cancelled job.

Persisted open/failed/cancelled scopes can be reconciled in a bounded,
idempotent pass. Reconciliation handles only explicitly attested reservation
entries and never scans arbitrary `Output` files for deletion. Direct adapter
calls without a real `JobContext` remain test-only compatibility paths; a
production Hub job cannot publish an output without a valid reservation
token. Existing staged artifact transactions, opaque IDs, native preview/Range
transport, and legacy non-producing worker behavior remain unchanged.
