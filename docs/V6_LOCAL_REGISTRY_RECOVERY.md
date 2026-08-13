# V6 Local Registry Recovery

The V6 local-registry recovery path is a static, fail-closed control plane for
machine-local configuration. It makes the difference between a real local
registry and a tracked example explicit; it does not make a launcher,
provider, model, or media operation operational.

## Provenance and truth

`src.services.api.config.read_local_config()` reports one of the finite
provenances `local`, `example_template`, `missing`, `malformed_local`, or
`malformed_example`. The legacy value-only readers remain for compatibility,
but recovery and launch decisions use the provenance record. Example JSON is
static documentation only: it cannot make an application launchable or make a
repair succeed.

## Offline flow

`src.services.local_registry_recovery` exposes three bounded steps:

1. `inspect_registry()` reads only fixed target names and returns provenance,
   counts, hashes, and finite error codes. It performs no network, process,
   binary, model, or configuration write.
2. `plan_registry()` builds deterministic descriptors from the trusted source
   contract and returns a dry-run plan containing only fixed target names,
   input hashes, and descriptor fingerprints. It does not expose candidate
   rows or machine-local values.
3. `apply_plan()` accepts only a module-generated plan, rechecks hashes, rejects
   unknown IDs/fields, and writes the fixed targets through same-directory
   flush/fsync/atomic replacement. A bounded journal supports
   `resume_journal()` after a multi-file interruption.

The apply result remains `execution: not_run` and generated component,
application, and model descriptors are `configured`/`not_run`. No runtime
probe or launch is implied by a successful local write.

## Safety boundary

The recovery module does not accept client rows, paths, commands, callables,
secrets, or arbitrary target names. Reparse roots/targets, malformed or stale
plans, duplicate IDs, unknown fields, secret-like values, hash races, and
journal/writer failures fail closed. Unknown existing local records are never
silently replaced.

The default `scripts/refresh_managed_registry.py` entrypoint is inspect/plan-only
and never applies a machine-local plan. Any later apply operation requires a
separately authorized caller and must use the fixed-target API under its own
disk-safety and validation boundary.

This package intentionally does not apply to the real machine-local
`Config/` tree. Any later machine recovery requires a separately authorized
plan, disk-safety review, and bounded validation. Until then, capability and
runtime state remains `partial`, `unavailable`, or `not_run` as published.
