# V7 Runtime Evidence and Catalog Binding

The production catalog snapshot is a server-owned, read-only projection. It may
show fixed runtime leaves and catalog metadata, but it does not run a runtime,
probe a provider, download a package, or publish operational readiness by
itself.

## Evidence contract

- `component-runtime-evidence.v2` is the only evidence family eligible for an
  operational projection.
- A v2 record binds the named `runtime_id` to the current catalog schema,
  catalog version and revision, catalog fingerprint, normalized source
  identity (or explicit `null`), record revision, install strategy, and a
  bounded runtime leaf fingerprint containing presence, size and mtime
  observations.
- The catalog inspector supplies this binding from the current loaded
  server-owned catalog. Every binding field and every required leaf must match
  before `OPERATIONAL` can be projected.
- Evidence is fresh, completed, execution-completed evidence only. A missing,
  malformed, duplicate-key, unknown-field, stale, reparse, leaf-drift or
  identity-drift record remains `INSTALLED_UNVERIFIED` with
  `execution=not_run` and `dry_run=true`.
- Legacy `component-runtime-evidence.v1` remains readable for diagnostics, but
  it is never upgraded or accepted for operational promotion.

## Static boundary

Writing evidence remains a separate bounded operation. This package does not
generate smoke evidence, launch a runtime, call a provider, inspect a model,
download or install anything. No catalog metadata or evidence record promotes
a model, runtime, component, release or provider to operational readiness
without a separately authorized bounded smoke contract.
