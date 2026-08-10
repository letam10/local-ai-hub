# Milestone 14 Schema Evolution Contract

## Purpose and boundary

This document defines a complementary design contract for evolving versioned
schemas safely. It governs identity, compatibility classification, canonical
serialization, diffing, and dry-run migration. It does not replace the M10B
release gate, the M12C evidence packet, or the M13C fixture catalog, and it
does not repeat their matrices or fixture inventory.

M14C is documentation-only. It neither implements a migration nor asserts that
a migration, validator, or conformance test already exists. The contract does
not authorize a merge, a GitHub operation, or a runtime-readiness claim.
Static schema evidence cannot promote a provider, model, filesystem, server,
image, video, or workflow status.

## Versioned schema identity

Every published contract has an immutable identity envelope:

- schema_id: a stable namespace plus major contract line;
- schema_version: semantic version in major.minor.patch form;
- contract_family: an allowlisted family name;
- schema_dialect: the declared validation dialect;
- content_fingerprint: a digest of the canonical redacted schema and policy;
- provenance: server_owned_validated, manager_qa, or not_run.

The identity is bound to the exact schema bytes and policy metadata used for a
check. A moving branch name, wall-clock value, host, user, or random identifier
is not schema identity. A changed semantic schema receives a new version and
fingerprint; an old version is not silently replaced.

## Compatibility classes

Each change receives one or more conservative classes. When classes overlap,
the strictest review and versioning rule applies.

| Class | Meaning | Default compatibility treatment |
| --- | --- | --- |
| additive | Adds an optional, representable field or enum member without changing existing meanings, limits, or required data. | Minor release only when closed-object readers have an explicit extension path; otherwise breaking. |
| breaking | Removes or renames a field, changes a type/meaning, invalidates an accepted value, or changes a required contract. | Major release, explicit migration, manual review, and revalidation. |
| restrictive | Narrows bounds, accepted values, permissions, ports, capabilities, or unknown-field policy. | Treat as consumer-breaking unless an explicit compatibility proof exists; manual review is mandatory. |
| behavioral | Changes interpretation, ordering semantics, status meaning, defaulting, or side effects without necessarily changing shape. | Requires a compatibility statement and manual review; use a major release when a consumer-visible meaning changes. |
| metadata-only | Changes descriptive text, labels, or non-semantic annotations without changing validation or interpretation. | Patch release only after proving the canonical semantic projection is unchanged. |

A purported additive change is not additive merely because a property is
optional in the producer. Closed objects and strict readers may reject that
property; the compatibility decision must consider every declared reader and
the canonical import/export rules.

## Structural validation and schema parity

Published schemas and their validators are one contract. Every object schema
must have a structurally complete properties map:

- every required name is present in properties;
- no property is required without a declared type and validation rule;
- required and additional-property policy are explicit;
- nested object schemas repeat the same parity check;
- closed objects reject unknown fields unless a named extension envelope is
  explicitly allowlisted.

A schema parity review must include one synthetic accepted shape and
representative rejected shapes for missing required data, unknown fields, wrong
types, and limit violations. No validator may accept a field that the
published schema omits, and no published schema may claim a field that the
validator ignores.

## Strict import, export, and bounds

Import accepts only bounded UTF-8 JSON in the declared top-level shape. The
parser rejects duplicate object keys, non-finite numeric tokens, invalid
UTF-8, NUL/control characters, boolean values where numbers are required, and
oversized bytes, strings, arrays, maps, or nesting depth. Limits are explicit,
tested at the boundary, and fail closed at limit-plus-one.

Import produces a detached normalized copy. It never writes, replaces, or
mutates the source representation and never loads a module, command, graph
engine, model, media asset, or user file. It rejects absolute, parent,
device, URL, or encoded path material where the contract does not explicitly
allow a safe public-source value.

Canonical export uses UTF-8 JSON, sorted object keys, deterministic separators,
stable arrays ordered by opaque IDs, and bounded scalar values. It excludes
timestamps, random IDs, locale formatting, host/user/environment data, and
transient audit fields. Reordering input keys or semantically irrelevant
metadata must not change the canonical semantic fingerprint.

## Diff contract

A schema diff compares validated canonical projections, not raw import text.
It reports stable categories in deterministic ID order:

- added;
- removed;
- changed type or constraint;
- changed requiredness or unknown-field policy;
- changed behavior or status semantics;
- metadata-only.

Diff output contains IDs, class, bounded summaries, and fixed reason/action
codes. It never echoes raw private values, command text, local paths, secrets,
model/media data, or unvalidated client mappings. The same semantic inputs
produce the same diff and fingerprint regardless of dictionary ordering,
locale, time, or process.

A diff must identify the consumer impact and the compatibility class before a
migration is considered. An ambiguous or unsupported future version is a
rejection, not an invitation to guess a downgrade.

## Migration plan contract

A migration plan is always dry-run and non-mutating:

- dry_run is true and immutable;
- source is validated against its declared version before planning;
- output is a detached plan and detached candidate, never a replacement source;
- step IDs come from a fixed allowlist and are ordered deterministically;
- the same source and target versions produce the same plan and fingerprint;
- v1 to v1 is an explicit no-op with an empty step list;
- future, unknown, or ambiguous versions fail with a safe action;
- the candidate is revalidated for required fields, closed objects, limits,
  duplicate IDs, cycles, type compatibility, and status truth.

Restrictive and breaking changes require manual review before any adoption.
Migration may not auto-repair an ambiguous value, invent an ID, silently
downgrade a version, widen a permission, execute a command, import code,
download a dependency, or write a user tree. If the candidate fails
revalidation, the plan is rejected and the source remains untouched.

## Semver, deprecation, and lifecycle

Major versions are for breaking or consumer-restrictive changes and for
consumer-visible behavioral changes that cannot be proven compatible. Minor
versions are for genuinely additive optional changes with an explicit
extension path. Patch versions are for metadata-only changes or clarifications
whose canonical semantic projection is unchanged.

Every deprecation names the replacement version, the affected fields or
behaviors, the migration step class, and the review deadline. Deprecated
fields remain accepted only for the documented window; removal is a breaking
change. Aliases and default changes are not silent compatibility repairs.
Lifecycle states are proposed, active, deprecated, retired, or rejected, and
each state transition has an immutable fingerprint and provenance.

Opaque IDs remain stable across versions whenever their semantic entity
survives. Renaming a display label does not rename an ID. A semantic change
that cannot preserve the ID must declare the replacement relation in a
reviewed migration plan rather than reusing the old fingerprint.

## Provenance and TOCTOU revalidation

A validator records the schema identity, source fingerprint, base/reference
fingerprint, and ownership projection before work begins. It rechecks the
source identity, relevant references, merge-base or parent identity, and
ownership projection after parsing, diffing, and migration planning. Any
change is a fixed REF_DRIFT, SOURCE_DRIFT, or SCOPE_DRIFT rejection.

A branch or URL is not a sufficient provenance record. Manager review fetches
the pinned identity and compares the canonical fingerprint, not a mutable
reference. A packet or report is detached from its source; changing a
returned object cannot change the input or the recorded fingerprint.

## Manager admission and rejection evidence

Admission evidence is a bounded, redacted projection containing:

- schema identity and canonical fingerprint;
- compatibility class and semver decision;
- parity result for required/properties and closed-object policy;
- canonical import/export and deterministic diff summaries;
- dry-run migration summary, step IDs, and candidate revalidation result;
- provenance and TOCTOU revalidation result;
- fixed rejection or remediation codes, if any;
- static verdict and truthful operational status.

The manager may admit a schema change only when ownership is exact, the
worktree/source identity is stable, parity and canonical checks pass, the
migration is detached and dry-run, and manual review is recorded for every
restrictive or breaking class. Rejection codes include
SCHEMA_ID_MISMATCH, REQUIRED_PROPERTY_PARITY, UNKNOWN_FIELD,
DUPLICATE_KEY, NONFINITE_NUMBER, INVALID_UTF8, LIMIT_EXCEEDED,
CANONICAL_DRIFT, UNSUPPORTED_VERSION, AMBIGUOUS_MIGRATION,
RESTRICTIVE_REVIEW_REQUIRED, BREAKING_REVIEW_REQUIRED, REF_DRIFT,
SOURCE_DRIFT, SCOPE_DRIFT, REDACTION_FAILURE, CLIENT_MAPPING_ECHO,
RUNTIME_CLAIM_UNAUTHORIZED, and MIGRATION_REVALIDATION_FAILED.

Static verdicts use PASS or FAIL only. Operational status remains partial,
planned, unavailable, or not_run until a separately authorized runtime smoke
passes; operational is never inferred from schema validation.

## Redaction and no-client-echo rules

Only server-owned, validated normalized objects may feed a schema report.
The public projection may expose schema IDs, versions, fingerprints, bounded
counts, class names, fixed issue codes, and safe reason/action codes.

It must omit raw paths, traversal material, credentials, API keys, secrets,
command payloads, executable or import text, host/user/environment values,
client-supplied mappings, report payloads, model/media/blob/weight data, and
unvalidated free text. JSON and Markdown displays escape control characters,
brackets, angle brackets, pipes, backslashes, and line breaks so a display
value cannot become a link, HTML, table injection, or hidden command.

## Illustrative synthetic schema and plan

The following is documentation-only and uses placeholders. It is not an
executable schema or migration and contains no local data or secrets:

    {
      "schema_id": "PLACEHOLDER_FAMILY",
      "schema_version": "PLACEHOLDER_SEMVER",
      "schema_dialect": "PLACEHOLDER_DIALECT",
      "compatibility_class": "PLACEHOLDER_CLASS",
      "content_fingerprint": "PLACEHOLDER_DIGEST",
      "provenance": "server_owned_validated",
      "diff": {
        "status": "PLACEHOLDER_STATIC_STATUS",
        "categories": [],
        "review_required": "PLACEHOLDER_BOOLEAN"
      },
      "migration": {
        "dry_run": "PLACEHOLDER_TRUE",
        "source_version": "PLACEHOLDER_SOURCE",
        "target_version": "PLACEHOLDER_TARGET",
        "step_ids": [],
        "candidate_fingerprint": "PLACEHOLDER_DIGEST"
      },
      "operational_status": "not_run"
    }

Placeholder values must remain placeholders in examples and documentation.
No executable migration, real contract payload, raw import data, or runtime
result belongs in this catalog.

## Proposed conformance coverage

The following future module and cases are proposals only. They are not
existing coverage and are not created or modified by M14C:

- tests/test_milestone14_schema_evolution.py (PROPOSED);
- test_schema_required_property_parity_and_closed_objects (PROPOSED);
- test_duplicate_nonfinite_utf8_and_bounds_fail_closed (PROPOSED);
- test_canonical_import_export_is_detached_and_deterministic (PROPOSED);
- test_diff_classification_is_stable_and_redacted (PROPOSED);
- test_migration_is_dry_run_non_mutating_and_revalidated (PROPOSED);
- test_breaking_and_restrictive_changes_require_manual_review (PROPOSED);
- test_ref_drift_and_scope_drift_reject_stale_evidence (PROPOSED).

Any implementation of these proposals needs a separate scope assignment and
must not be presented as an existing M10B, M12C, or M13C gate.

## Explicit deferrals and prohibited operations

M14C is documentation-only. It does not perform or authorize GPU, video,
FFmpeg, SAM2, model, server, UI, API, provider, or filesystem mutation work.
It does not authorize downloading data, installing dependencies, changing
drivers, loading a model or media asset, benchmarking, or launching a
workload.

No automatic GitHub or main merge is allowed. No merge, rebase, reset,
force-push, backup, archive, executable fixture, script, raw local data,
secret, or artifact is allowed. After writing this document, the only bounded
checks are:

    git diff --check origin/feature/local-ai-hub-v4...HEAD
    python -B scripts/ci_validate.py

If both checks pass and only this document changed, stage only this document,
commit with docs: define schema evolution contract, and push the named branch
with tracking. A scope or check failure stops the flow before commit and push.
