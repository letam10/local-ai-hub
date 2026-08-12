# V6 Durable Admission

V6 admits one new server-validated durable JobSpec: `media.video_grade.v1`.
The body is the unwrapped `job-spec.v1` object and must contain the exact
`media.video_grade` tool, `run` operation, `reconstructable: true`, and one
opaque `artifact_[a-f0-9]{32}` VIDEO input. Accepted controls are bounded
brightness, contrast, saturation, gamma, denoise, and sharpen values; unknown
fields and raw paths, commands, callables, manifests, secrets, or executable
values are rejected without echo. The resource policy is exact:
`cpu_slots=1`, `gpu_slots=0`, `ram_mb=0`, `disk_mb=0`, and
`exclusive_group=null`.

Admission uses the code-owned production adapter registry, validates the
server-owned artifact type, and persists a queued `execution: not_run`,
`dry_run: true` durable record. It never starts an adapter. Restart recovery
and retry use the same current registry and revalidate the opaque VIDEO input;
missing, stale, tampered, or unavailable dependencies yield fixed
`CREATE_NEW_JOB` guidance. Existing hot/legacy jobs are not migrated or
converted.

Completed managed-output publication is deliberately deferred. The durable
engine's output hook is a safe no-op until a server-owned atomic cross-store
linkage primitive exists; it creates no artifact-index record, durable artifact
link, or preview URL. Existing artifact Range/HEAD transport is unchanged.
Admission and recovery are coordination evidence, not operational media
evidence.
