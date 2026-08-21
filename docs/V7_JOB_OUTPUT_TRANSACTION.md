# V7 job-output transaction safety

The job manager creates a private, bounded `job-output-scope.v1` manifest for
output-producing jobs before the worker starts. The manifest contains only an
opaque job fingerprint, safe relative `Output` leaves, exact claims, claimed
per-job namespaces, bounded file identity metadata, ownership (`owned` or
`ambiguous`), and a finite terminal state. It never enters the public job
projection and never stores an absolute path, command, log, stderr, client
value, or secret.

HubJobManager exposes three server-owned producer seams through `JobContext`:
`claim_output(path)` for a known leaf, `claim_output_namespace(label)` for
dynamic batches, and `attest_output(path)` for a returned child whose producer
has completed its internal write. A namespace is created under the private
`.job-output-scopes` directory before the producer starts; the producer
receives its path only in the internal worker request. AnimeSR,
Practical-RIFE, Real-ESRGAN, SAM2, Whisper, media/FFmpeg, ComfyUI, vision,
OCR, and voice adapters use this seam when they run under a Hub job. Direct
adapter calls without a JobContext remain compatibility/test-only paths.

When a worker returns output candidates, the artifact store validates every
candidate as a regular file under the server-owned `Output` root, rejects
traversal, external paths, reparse points, directories, duplicates, and size
overflow, then records the candidate identity in the manifest. Namespace
membership is containment only; it is never ownership proof. A candidate is
`owned` only when it is an explicitly pre-create claimed leaf or has an
explicit server-owned producer attestation recorded as an exact child claim,
and it was absent from the complete pre-worker snapshot. A path that appeared
after the snapshot without a claim, or whose ownership cannot be proven, is
preserved and marked `manual_review`; baseline absence alone is never an
ownership grant.

Cancellation and publication failure resolve the manifest under the job
context lock. Only an identity-matching candidate explicitly marked `owned`
may be removed; changed, missing-identity, pre-existing, or ambiguous files
are never deleted. Successful publication keeps the files and reaches the
existing opaque artifact registry. The existing artifact registration remains
the publication boundary, so no public artifact/index entry is created for a
failed or cancelled job.

If a required scope is missing, malformed, unreadable, or a producer cannot
obtain a manager-issued claim, publication stops with a fixed path-free
unavailable/not-run result and ambiguous files are preserved. Persisted
open/failed/cancelled scopes can be reconciled in a bounded, idempotent pass.
Reconciliation handles only the explicit manifest candidates and claimed
namespaces; it never scans arbitrary `Output` files for deletion. Existing
staged artifact transactions, opaque IDs, native preview/Range transport, and
legacy worker output behavior remain unchanged.
