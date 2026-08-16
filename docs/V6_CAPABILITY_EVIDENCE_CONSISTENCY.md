# V6 Capability Evidence Consistency

`src/services/api/core.py` separates a configured local component from a
static recovery descriptor.  `recovery_state: recovered_static` remains
`unavailable`: it is configuration evidence only and cannot become installed,
running, ready, or operational.  A normal local row with
`runtime_status: not_run` and `execution: not_run` is instead projected from
the current presence of its declared runtime, environment, and loopback port:
it may be `installed`, `partial`, or missing, but is never operational merely
because a Config row exists.

An explicit `status: not_installed` remains `not_installed` even when a
runtime leaf exists.  If a row declares a model leaf and that leaf is absent,
the component is at most `partial`; a runtime directory cannot stand in for a
missing required model.

Each public component projection contains only path-free evidence:

- `current_readiness` mirrors the current local leaf observation;
- `runtime_fingerprint` is a SHA-256 digest of the component ID, configured
  status, recovery marker, and boolean leaf observations; and
- `last_smoke` is `not_run` unless a strict
  `component-runtime-evidence.v1` record is present.

A runtime-backed tool can become `operational` only when its legacy bounded-job
receipt still exists **and** `last_smoke` names that exact tool, completed
successfully, is no more than 24 hours old, and has an exact current
`runtime_fingerprint` match.  Missing, malformed, failed, stale, or mismatched
evidence leaves the tool `partial` or `unavailable`.  This projection reads no
Config, runtime, model, or smoke state beyond the existing server-owned local
readers and never writes, executes, downloads, or installs anything.
