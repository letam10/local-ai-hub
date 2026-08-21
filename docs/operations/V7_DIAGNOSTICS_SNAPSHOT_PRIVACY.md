# V7 Diagnostics Snapshot Privacy Boundary

The diagnostics UI and its public HTTP routes expose a bounded server-owned
projection, not the raw collector output. The projection keeps the fixed
subsystem keys, finite status values, safe reason/next-action text, bounded
counts and booleans, and `execution=not_run`/`dry_run=true`. It deliberately
does not publish Git identity, log lines, recovery filenames, environment or
model names, exception text, paths, URLs, commands, secrets, callables or
unknown nested objects.

`GET /api/diagnostics/snapshot`, the fixed subsystem route, recovery-drafts,
repair inspection and the sanitized export all consume that same center-owned
projection. A malformed collector result becomes an `UNKNOWN` row with a
fixed refusal code; it is never serialized or stringified as a diagnostic
message. The repair-clear endpoint remains an explicit confirmation boundary
and keeps its existing fixed route and input checks; file-level recovery
identities are intentionally not listed by the privacy-safe diagnostics
surface.

The projection is metadata-only. It does not make a runtime, model, GPU,
network, provider, download or install claim. A healthy diagnostic row means
only that the bounded collector returned an accepted shape; it is not evidence
that an AI backend is operational.

The route contract preserves the existing subsystem/status shape for the UI.
The separate export response retains `sanitized=true` and deterministic
bounded JSON. Any future diagnostic detail must be added as an explicitly
allowlisted scalar, count, boolean or opaque digest with a corresponding
redaction regression; free-form collector text is not a public field.
