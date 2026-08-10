# Milestone 7C — Privacy Diagnostics and Support Core

Milestone 7C adds a static, fail-closed privacy and diagnostics core.  It is
deliberately a planning and evidence-contract layer.  It does not probe the
operating system, inspect a process or GPU, execute a command, collect a log,
open an attachment, or modify a configuration.

## Contracts

The five Draft 2020-12 contracts are exported by
`src/shared/schemas/privacy_diagnostics.py`:

- `privacy-policy.v1` — managed allowlists for components, configuration keys,
  tools and contracts, plus consent and retention ledger references.
- `diagnostic-snapshot.v1` — server-owned bounded component, config, tool and
  contract statuses.  Only opaque IDs, counts and SHA-256 metadata digests are
  accepted.
- `diagnostic-finding.v1` — one deterministic rule, severity, availability,
  fixed reason/action code and evidence digest.
- `support-bundle-manifest.v1` — a scrubbed projection containing counts,
  statuses and digests.  It is a manifest, not a file or log bundle.
- `remediation-plan.v1` — a dry-run plan with risk, owner, prerequisites and
  rollback note.  It cannot perform a remediation action.

Objects are closed and bounded.  Duplicate JSON keys, non-finite numbers,
unknown fields, unsupported IDs/statuses, paths, URLs, credentials, command
syntax, logs, attachments, media/model payloads and long blobs fail closed.
Validation returns stable issue codes and never echoes an unsafe value.

## Data ownership and status truth

Policy descriptors are discovered only below the repository-managed policy
root.  Snapshot inputs must be server-owned records and must match the
managed policy identity.  Diagnostics consume normalized validation results;
they do not accept client-built report dictionaries.  The fixed status model
is:

- `operational` means only that the supplied status says operational.  It is
  not a runtime claim made by this service.
- `partial`, `unavailable`, `planned` and `not_run` remain visible when
  evidence is incomplete.  Every projection carries `execution: not_run`.
- A missing record, stale contract, configuration digest drift or insufficient
  consent produces a deterministic finding with a fixed remediation action.

The diagnostics engine never upgrades a partial/unavailable record and never
claims that a provider or tool was executed.

## Consent and retention

Consent and retention are ledger metadata, not a command to collect data.
Diagnostics require a granted diagnostics scope.  Support projection also
requires the `support-bundle` scope (or the combined scope).  A support
manifest is unavailable when consent is absent or revoked.  Retention is
copied as a bounded classification/days/ledger reference; no deletion or
retention job is started.

## Support projection and remediation

`build_support_bundle_manifest` accepts only a list of validated findings and
returns a detached manifest containing opaque IDs, fixed statuses, counts and
digests.  `build_support_bundle_markdown` renders the same safe projection.
`plan_remediation` is always `dry_run: true`; high-risk consent and drift
actions are `manual_review`, while lower-risk review actions are `planned`.
No function writes, replaces, moves, deletes or restarts anything.

## Policy diff and migration

`diff_privacy_policies` compares every v1 field.  A label-only change is
`policy_metadata_changed`, never an unchanged result.  If canonical
fingerprints differ and no known field explains it, the closed fallback
`policy_contract_changed` is emitted.  `plan_privacy_policy_migration` is
detached and dry-run only.  Consent, retention, redaction and allowlist
changes require `manual_review`; identity changes are `unavailable` rather
than guessed replacements.

## CLI and examples

The fixed-root CLI prints scrubbed JSON or Markdown:

```text
python scripts/validate_privacy_diagnostics.py --format json
python scripts/validate_privacy_diagnostics.py --format markdown
```

The tracked example policy is under the managed policy root.  The snapshot
example is under `privacy_diagnostics/samples` and is input for bounded tests,
not an auto-discovered policy.  No download, dependency installation, model
resolution or runtime provider call is part of this milestone.
