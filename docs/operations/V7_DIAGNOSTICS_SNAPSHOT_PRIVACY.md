# V7 diagnostics snapshot privacy

The public diagnostics surface is a bounded, server-owned read-only projection. The center collects internal diagnostics for compatibility, but public snapshot, subsystem, recovery-draft, inspect-recovery, repair-verify, and export responses pass through the same fixed projection before they leave the service boundary.

The projection is limited to the fixed subsystem identifiers:

`git_integrity`, `config_registry`, `jobs_store`, `artifact_store`, `workflow_store`, `models_inventory`, `environments_inventory`, `runtime_inventory`, `storage`, `gpu`, `latest_app_errors`, and `recovery_forensic`.

Each row has a finite status, fixed reason/next-action text, a reason code, and the explicit `execution: "not_run"` / `dry_run: true` boundary. Additional values are restricted to bounded counts, booleans, fixed category flags, and an optional lowercase SHA-256 digest. Git identity, origins, branches, logs, recovery filenames, environment/model/runtime names, local paths, URLs, commands, secrets, exception text, and unknown nested objects are never public fields. Malformed or hostile input becomes `UNKNOWN` with `diagnostic_projection_unavailable` and is not echoed.

The route adapters consume the center-owned snapshot/export projection. They do not call raw subsystem or recovery callbacks for public payloads. `repair_verify` and `inspect-recovery` therefore have the same privacy and no-execution boundary as the dashboard snapshot. The existing unknown-subsystem 404 and explicit recovery-clear confirmation behavior remain intact; clearing is still an explicitly confirmed action and does not imply repair or runtime execution.

This is a privacy and data-shaping contract, not runtime evidence. It does not verify or promote a model, runtime, GPU, server, repair, installation, or recovery operation. The bounded tests use synthetic values and temporary fixtures only; they do not launch the Hub or inspect live logs, Git state, Config state, or machine inventory.
## Recovery-draft clear semantics

The clear-recovery route is truthful about the existing Node Studio draft
ownership contract.  Only a valid, server-owned, identity-attested draft may
be deleted.  Malformed, foreign, replaced, directory or reparse-backed drafts
are preserved and returned as `manual_review`; the route no longer reports
`completed` merely because a clear request was received.  Public results carry
only bounded status/reason fields and never echo a local path or exception.
