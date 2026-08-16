# V6 video runtime integration

This package keeps the AnimeSR, Practical-RIFE, and Real-ESRGAN adapters inside
the Local AI Hub job boundary. It does not install a runtime, download a model,
run a GPU smoke, or claim that a configured leaf is operational.

## Source and runtime boundary

Each adapter accepts only an opaque Hub artifact identifier and resolves it
through the server-owned artifact store before invoking its internal worker.
Raw source paths, executable overrides, model paths, and output roots are
rejected at the adapter boundary. Runtime and model selection comes from the
server-owned local registry and fixed canonical roots.
The worker-side defense-in-depth check accepts a resolved input only under
Hub `Temp/uploads`, `Output`, or `Archive`; an arbitrary direct worker path is
not a valid artifact input.
Practical-RIFE additionally receives explicit FFmpeg and FFprobe paths and
executes with a constrained lookup path; it never relies on the interactive
process `PATH`. Worker temporary data is under the task-owned Hub `Temp/jobs`
root and final outputs are under the corresponding Hub `Output` subdirectory.
Ambient `*_HOME`, `*_ENV`, `FFMPEG_PATH`, and `FFMPEG_HOME` environment
overrides do not replace registry values. The Practical-RIFE worker accepts
only the single registry FFmpeg directory containing both `ffmpeg.exe` and
`ffprobe.exe`; a split pair is unavailable.

Missing environment, runtime, model, FFmpeg/FFprobe pair, or unsafe containment
is reported as unavailable/error. A standalone worker result is not a
capability smoke or readiness claim.

## Model and backend selection

AnimeSR is bound to the server-owned `animesr-v2` registry record.  The
adapter accepts only the fixed `AnimeSR_v2` / `animesr_v2` CLI pairing and the
canonical `Models/Video/AnimeSR/AnimeSR_v2.pth` leaf; client payloads cannot
select a model path, model ID, or experiment name (a legacy model label is
ignored).  A missing, duplicate,
placeholder, or unsafe registry record blocks the worker before launch.
Real-ESRGAN applies the same fixed-leaf rule to
`Models/Video/Real-ESRGAN/realesr-animevideov3.pth`.

The generic `run_media_operation` control-plane entry remains FFmpeg-backed,
but a selected `frame_interpolate` + `practical_rife` or `image_upscale` +
`real_esrgan` request receives a backend-specific static gate.  Missing
runtime, environment, model/script, worker, or FFmpeg/FFprobe leaves are
rejected before a Job Manager submission; present leaves remain `partial`
until a bounded smoke records the complete runtime → model → adapter → job →
artifact result.  Static leaf presence never becomes `operational` by itself.
The dedicated `upscale_anime_video` tool applies the same absent-model/runtime
gate to the AnimeSR registry binding before queueing.

## Job → artifact contract

The in-process Hub Job Manager treats a worker result as an internal hand-off.
Before a terminal result is persisted, completed `output`/`files` values are
validated by the server-owned artifact store and replaced with opaque artifact
records. Worker publication is Output-only: Hub upload roots are input-owned
and can never become a completed worker artifact. Multiple worker outputs are
validated as one batch and committed to the artifact index in one save, so a
mixed managed/unmanaged result cannot publish only its first file. Each
published record carries the terminal job ID, a bounded job fingerprint,
adapter/tool ID, attempt, and completed status. Raw workstation paths are not
persisted in `jobs.json` or returned by the public job projection.

An output outside a Hub-owned artifact root, an invalid worker status, a
malformed result, or a failed artifact publication makes the job failed with a
bounded user-facing action. Cancellation wins over a late worker completion, so
an explicitly cancelled job cannot publish a late output. Metadata-only
completed results (for example, a read-only probe) remain valid and do not need
an artifact. The Job Manager serializes cancellation with publication and the
terminal transition: cancellation before that critical section rejects the
output, while cancellation arriving inside it waits for a completed or
cancelled terminal state rather than leaving an unowned artifact.

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
