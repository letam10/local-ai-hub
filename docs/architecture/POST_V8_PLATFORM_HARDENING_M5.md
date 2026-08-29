# Post-V8 Platform Hardening V2 — Milestone 5

## Purpose and owner

`src/services/platform_hardening_v2.py` is the single finite projection for
the platform-hardening boundary. It documents the existing updater, backup /
recovery, process-supervision, security and performance owners without
creating a second implementation or executing an operation.

## State, API and recovery

The projection has no new persistent state. It names the state owned by the
existing services (for example staged update/pending health, opaque backup
plans, owned process handles, sanitized diagnostics and bounded snapshot
cache) so operators know where to look. Every public result is
`execution=not_run`, `dry_run=true` and `overall_state=READ_ONLY_CONTRACT`.

- `GET /api/platform-hardening/v2` — five-area contract.
- `GET /api/platform-hardening/v2/{area_id}` — one area and its failure/recovery summary.

Updater activation, restore application and process control remain behind
their current server-owned confirmation/ownership gates. A stale or failed
operation is recovered through the owning service; this projection never
guesses a success, probes a machine, runs a benchmark or starts a child.

## Security and performance boundary

The contract preserves loopback, opaque-ID, path/secret redaction, no-generic-
process-termination and bounded-startup/polling rules. It is intentionally
not a claim that physical Windows update or restore acceptance has been run;
those remain separately bounded candidate tests.

## Tests

`tests/test_post_v8_platform_hardening_m5.py` covers all five areas, required
purpose/owner-state/API/failure/recovery metadata, path/secret-free output,
Router binding and Diagnostics/UI architecture integration.
