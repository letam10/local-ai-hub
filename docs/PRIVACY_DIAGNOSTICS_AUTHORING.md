# Privacy Diagnostics Authoring Guide

Author only the closed fields documented by the five `*.v1` contracts.  A
descriptor is metadata for static planning, not a plug-in, executable recipe,
or support archive.

## Privacy policy

Create a managed `privacy-policy.v1` descriptor with:

- a lowercase opaque `id`, safe human `label`, bounded integer `revision`;
- `source.kind: managed` and an opaque source reference;
- consent and retention ledger references, state/scope, retention class and
  bounded days;
- `redaction_profile: strict`;
- non-empty unique arrays selected from the fixed component, config, tool and
  contract allowlists.

The policy root is repository-owned.  Do not add a user-provided root, URL,
path, command, environment map, log, attachment or payload field.  Duplicate
policy IDs are ambiguous and are not selected by filename order.

## Diagnostic snapshot

Snapshots are server-owned.  Use `source.kind: server-owned`, the matching
`policy_id`, the same ledger references, and bounded arrays of records.  Each
record has an allowlisted ID, an allowlisted status, an evidence count and a
lowercase SHA-256 metadata digest.  Config records additionally carry expected
and observed digests.  Contract records carry expected and observed version
tokens.  Never place the config value, path, command output, host name, secret,
log or attachment in a record.

The evidence summary is also bounded and carries only counts and a digest.
Stale, partial, missing and not-run evidence should be declared honestly;
the validator and diagnostic engine will not infer a successful runtime.

## Findings, support manifests and plans

Do not hand-author a finding list for a public report.  Use the normalized
findings returned by deterministic diagnostics.  Each finding has a fixed
rule/action code, fixed text, an opaque subject, a bounded count and a digest.

A support manifest is a scrubbed index.  It may contain only opaque IDs, fixed
statuses, counts and digests.  It must have granted support consent and always
states `execution: not_run`.

A remediation plan is a review artifact.  It must have `dry_run: true` and
fixed risk, owner, prerequisite and rollback codes.  It is never permission
to edit or delete a file, change a ledger, start a provider or restart a
service.  Any separately authorized action must be implemented and tested by
the owning service.

## Validation and safe export

Use the Python validators and the Draft 2020-12 schema together.  Both reject
unknown fields and unsafe text.  JSON import rejects duplicate keys, BOM,
non-finite numbers, invalid UTF-8 and oversized input.  Canonical export sorts
keys and entity arrays and is deterministic across dict ordering.

The CLI has a fixed repository-managed root and emits only JSON/Markdown to
stdout.  It does not accept an arbitrary input or output destination.  Public
reports must be built from server-owned normalized objects and must not echo
source descriptors or validation exception text.
