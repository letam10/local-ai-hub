# V6 Durable Admission

V6 admits one new server-validated durable JobSpec: `media.video_grade.v1`.
The body is the unwrapped `job-spec.v1` object and must contain the exact
`media.video_grade` tool, `run` operation, `reconstructable: true`, and one
opaque `artifact_[a-f0-9]{32}` VIDEO input. Accepted controls are bounded
brightness, contrast, saturation, gamma, denoise, and sharpen values; unknown
fields and raw paths, commands, callables, manifests, secrets, or executable
values are rejected without echo.

Admission uses the code-owned production adapter registry, validates the
server-owned artifact type, and persists a queued `execution: not_run`,
`dry_run: true` durable record. It never starts an adapter. Restart recovery
and retry use the same current registry and revalidate the opaque VIDEO input;
missing, stale, tampered, or unavailable dependencies yield fixed
`CREATE_NEW_JOB` guidance. Existing hot/legacy jobs are not migrated or
converted.

The managed-output hook is reserved for a future successful server-owned
adapter result. It accepts only a completed durable record and a task-owned
managed output, computes a SHA-256 digest in bounded chunks, and registers
`video_grade.mp4` as `video/mp4` with opaque durable provenance. Failed,
unavailable, partial, mismatched, or stale outputs do not receive a preview
link. Admission and recovery are coordination evidence, not operational media
evidence.
