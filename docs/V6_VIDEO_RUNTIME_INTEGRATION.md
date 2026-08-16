# V6 video runtime integration

This package keeps the AnimeSR, Practical-RIFE, and Real-ESRGAN adapters inside
the Local AI Hub job boundary. It does not install a runtime, download a model,
run a GPU smoke, or claim that a configured leaf is operational.

## Source and runtime boundary

Each adapter accepts only an opaque Hub artifact identifier. The worker resolves
that identifier through the server-owned artifact store and rejects raw source
paths, executable overrides, model paths, and output roots. Runtime and model
selection comes from the server-owned local registry and fixed canonical roots.
Practical-RIFE additionally receives explicit FFmpeg and FFprobe paths and
executes with a constrained lookup path; it never relies on the interactive
process `PATH`. Worker temporary data is under the task-owned Hub `Temp/jobs`
root and final outputs are under the corresponding Hub `Output` subdirectory.

Missing environment, runtime, model, FFmpeg/FFprobe pair, or unsafe containment
is reported as unavailable/error. A standalone worker result is not a
capability smoke or readiness claim.

## Job → artifact contract

The in-process Hub Job Manager treats a worker result as an internal hand-off.
Before a terminal result is persisted, completed `output`/`files` values are
validated by the server-owned artifact store and replaced with opaque artifact
records. Each published record carries the terminal job ID, a bounded job
fingerprint, adapter/tool ID, attempt, and completed status. Raw workstation
paths are not persisted in `jobs.json` or returned by the public job projection.

An output outside a Hub-owned artifact root, an invalid worker status, a
malformed result, or a failed artifact publication makes the job failed with a
bounded user-facing action. Cancellation wins over a late worker completion, so
an explicitly cancelled job cannot publish a late output. Metadata-only
completed results (for example, a read-only probe) remain valid and do not need
an artifact.

This contract covers the legacy `job_YYYYMMDD_HHMMSS_<hex>` IDs as well as the
V5 `jobv5_<hex>` lineage format. It does not broaden artifact roots or permit
client-supplied provenance.

## Verification boundary

Static tests use temporary Hub-shaped fixtures to cover checked worker command
arguments, explicit tools, raw-path refusal, reparse/containment refusal,
registry-selected model containment, truthful unavailable results, and the
adapter → Job Manager → artifact projection. A future bounded functional smoke
must still check the active-job/GPU policy immediately before running and must
prove `runtime → model → adapter → job → artifact → preview/output` before a
tool can be reported operational.
