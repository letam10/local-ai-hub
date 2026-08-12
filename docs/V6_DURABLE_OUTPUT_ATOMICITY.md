# V6 durable output visibility

This contract applies only to a future completed `media.video_grade.v1` durable
job. Queueing, retry and reconciliation remain coordination-only:
`execution=not_run` and `dry_run=true`; this package does not run an adapter or
claim media capability readiness.

## Visibility protocol

1. The server revalidates the unchanged completed job, exact descriptor
   fingerprint, adapter, attempt, bounded output name/size and a chunked digest.
2. `stage_job_artifact` copies the output in bounded chunks and atomically adds a
   private `staged` index entry. Staged records are unavailable to resolve,
   describe, list, open, or preview, and expose no filesystem path.
3. `DurableJobStore.commit_managed_artifact` performs an exact server-owned CAS
   link for the job, transaction and provenance. Conflicts, stale attempts,
   changed records and store failures do not publish an artifact.
4. `publish_staged` changes visibility to `published` only after that durable
   link succeeds. A publish/index failure leaves the durable link private and
   the artifact unavailable until bounded reconciliation can prove an exact
   match.

Startup reconciliation aborts staged artifacts without an exact durable link,
publishes an exact linked-hidden artifact, removes stale, corrupt, replaced, or
mismatched links, and removes only unindexed regular files carrying the
Hub-owned stage marker. The transaction is idempotent and a retry uses a new
durable attempt; a linked terminal artifact is never replaced.

Existing artifact records without a visibility field retain their legacy
visibility and existing upload/Range/HEAD behavior. The durable media path is
fenced away from the older public-first generic output helper. Failed,
unavailable, interrupted, mismatched, or partial output has no public artifact
URL.

All public metadata is opaque and allowlisted: artifact ID, safe name, media
type, bounded size/digest and validated provenance. Paths, commands, logs,
client callables, secrets and raw errors are not persisted or projected.
