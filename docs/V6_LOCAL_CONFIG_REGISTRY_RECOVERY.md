# V6 Local Config Registry Recovery Safety

This package defines a static, fail-closed prerequisite for any future local
Config recovery. It does not apply machine configuration and does not make a
runtime, model, provider, launcher, or media capability operational.

## Source and consumer binding

Every plan carries a sanitized source/consumer identity pinned to the reviewed
V6 source snapshot, the fixed relative source and consumer leaves, a controller
identity, fixed target names, fixed component/model/application IDs, and a
relative-leaf attestation fingerprint. A plan is not a client payload and does
not contain rows, commands, URLs, credentials, or absolute paths.

## Target decisions

Only absent `components.json` and `model_registry.json` targets can receive an
auto-create decision, and only when the fixed relative-leaf attestation is
fresh. Any present local target—valid, malformed, unknown, or different—is
manual review and is never overwritten. `hub_config.json` and
`application_registry.local.json` are always manual review. FLUX/Qwen model
leaves, AIRI/Ollama external applications, missing engine roots, and any
reparse/junction condition remain manual review.

## Apply boundary

The default refresh script is inspect/plan-only. The explicit recovery API is
still static/offline and accepts only its own plan. Before a journal or target
write it rechecks source/consumer binding, private controller identity, the
preservation guard/isolation boundary, fixed target and journal containment,
reparse state, target hashes, zero owned-process count, and a bounded disk
margin. Journal records contain only fixed target names, hashes and status.

Writes are same-directory flush/fsync/atomic replacements and only converge
forward from originally absent files. A conflict stops and retains the journal;
there is no backup, delete, reset, restore-overwrite, or rollback-copy path.

Recovered descriptors carry `recovered_static`, `configured`, and
`execution: not_run`; the core capability projection maps them to unavailable
until a separately verified runtime source exists. Example JSON remains
documentation-only and cannot produce a running, installed, ready, launchable,
or operational component.

No machine-local Config, environment, model, runtime, server, browser,
download/install, FFmpeg/GPU/provider, or smoke action is part of this package.
