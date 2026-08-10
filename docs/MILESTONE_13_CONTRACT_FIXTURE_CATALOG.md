# Milestone 13 Contract Fixture Catalog

## Purpose and strict boundary

This catalog defines reusable, sanitized, static fixtures for future contract and
conformance tests. A fixture is a bounded description of an accepted or rejected
contract shape; it is not an executable workflow, an input to a runtime provider,
or a record of a real machine.

M13C is complementary to M10B. M10B defines the release-gate lane matrix and
ordering; this document defines fixture families and oracle conventions that
future checks may consume. It does not reproduce that matrix, assert that any
test exists, or modify any existing test.

The catalog is design-only. It does not authorize a merge, a GitHub operation,
or a runtime-readiness claim. Static fixture acceptance cannot promote a
provider, model, filesystem, server, image, video, or workflow status.

## Fixture envelope conventions

Every catalog entry uses a versioned envelope:

- contract: contract-fixture.v1;
- fixture_id: an opaque, ASCII-safe, bounded identifier with no path or host
  meaning;
- family: one allowlisted contract family;
- shape: valid or adversarial;
- source: synthetic_server_owned;
- expected_oracle: a fixed static result and issue code;
- fingerprint: the digest of the canonical redacted envelope.

Fixture IDs are stable references, not filenames and not user-provided paths.
A changed semantic fixture receives a new ID; an old ID is never silently
rewritten.

Canonical encoding is UTF-8 JSON with sorted object keys, deterministic
separators, stable arrays ordered by opaque IDs, bounded strings, and no
locale-dependent formatting. Fingerprints are computed only after redaction.
Timestamps, random IDs, environment values, and process details are excluded
from the canonical projection.

Each family publishes named ceilings for raw bytes, nesting depth, collection
size, string length, and metadata entries. Catalog entries include both a
boundary-accepted shape and a limit-plus-one rejection shape where the contract
has a limit. No parser, fixture loader, or projection may allocate or parse
unbounded input.

The envelope carries only synthetic descriptors and bounded scalar metadata.
It never carries executable code, a shell line, a module name to import, a
media/blob payload, a model file, a backup, an archive, or a generated artifact.

## Valid fixture families

These are catalog families, not claims that tests or fixture files already
exist. Each family has a minimal valid shape and optional boundary-valid
variants.

| Family | Valid synthetic coverage |
| --- | --- |
| M4B extension descriptors | Minimal extension-manifest identity, compatibility, allowlisted capability/entrypoint, resource profile, and truthful deferred status. |
| M5B workflow packages | Portable package identity, typed subgraph ports, acyclic edge set, bounded metadata, canonical export, and dry-run migration summary. |
| M6C asset descriptors | Sanitized asset identity, lineage/license fields, bounded dimensions or durations as metadata, retention intent, and provider/filesystem status kept deferred when untested. |
| M7C privacy diagnostics | Fixed diagnostic issue codes, redacted finding summaries, safe reason/action values, deterministic counts, and non-operational privacy status. |
| M8 recipes | Recipe identity, safe target and variant references, lineage/usage metadata, static planning result, and image/video execution status kept deferred. |
| M10A capability gateway | Allowlisted capability and entrypoint identifiers, validated request/result summaries, fixed status reason/action, and server-owned projection metadata. |
| M12 evidence packets | Versioned immutable identity placeholders, changed-file counts, bounded command summaries, static/operational provenance fields, admission state, and canonical fingerprint placeholders. |

The valid families intentionally share opaque identifiers and fixed vocabularies
only. They do not share raw source paths, client mappings, provider handles,
model names, media bytes, or host information.

## Adversarial and rejection fixture families

Each rejection family is synthetic and carries only the fixed issue code,
safe reason, and safe remediation action expected from a validator:

- raw path and traversal values, including absolute, parent, device, URL, and
  encoded path forms;
- secrets and credentials in keys, nested metadata, URLs, or display text;
- client mapping echo, where untrusted discovery, manifest, or report fields
  are offered to a public projection;
- unsafe command, shell, executable, import, callable, module, or code fields;
- oversize byte/string/array/map/depth values and limit-plus-one boundaries;
- duplicate package, subgraph, node, edge, port, criterion, or fixture IDs;
- self-loops, cycles, recursive references, invalid edge direction, and
  incompatible typed ports;
- status inflation, such as operational or benchmark claims without an
  authorized smoke and provenance record;
- nondeterministic ordering, locale-sensitive output, transient timestamps,
  random IDs, or unstable serialization;
- TOCTOU and ref drift, including changed HEAD, base, merge-base, or owned-file
  projection after capture;
- artifact and process leaks, including generated reports, untracked files,
  task-owned helpers, servers, or temporary outputs left after a check.

Adversarial fixtures fail closed. They must not be repaired, downgraded, guessed,
executed, imported, downloaded, or echoed into an error report.

## Expected oracle vocabulary

Static fixture oracles use PASS or FAIL only. PASS means that the fixture
matches the documented static contract; it does not mean that a runtime exists.
FAIL means that the fixture is rejected with a fixed issue code and a safe
reason/action pair.

Runtime provenance is represented separately and may use only these truth
states:

- partial;
- planned;
- unavailable;
- not_run;
- operational, only after a separately authorized bounded smoke.

Suggested fixed issue families include SCHEMA_MISMATCH, UNSAFE_VALUE,
LIMIT_EXCEEDED, DUPLICATE_ID, CYCLE_DETECTED, TYPE_MISMATCH,
CLIENT_MAPPING_ECHO, STATUS_INFLATION, NONDETERMINISTIC_PROJECTION,
REF_DRIFT, REDACTION_FAILURE, ARTIFACT_LEAK, PROCESS_LEAK, and
UNAUTHORIZED_RUNTIME. The catalog may add a code only through a versioned
contract change.

Reasons and actions are short, deterministic, and allowlisted. They identify a
remediation class, never the rejected raw value. Parser exceptions, host/user
details, command payloads, local paths, credentials, and arbitrary input text
are not oracle output.

## Redaction and no-echo projections

JSON and Markdown projections are derived from validated, server-owned fixture
objects or from the catalog's fixed IDs and counts. They may expose contract
version, opaque IDs, family/type summaries, fixed result codes, bounded counts,
truth states, and canonical fingerprints.

They must omit raw paths, credentials, host/user/environment data, client
mappings, command payloads, model/media/blob content, and unvalidated report
fields. A malicious display string is escaped before Markdown rendering so it
cannot create a link, HTML, table injection, or hidden control text.

A projection check repeats the same fixture twice and compares canonical JSON,
Markdown, and fingerprint. Key order, list order, issue order, and safe
reason/action text must remain stable. The output is detached from the source
object; mutating a returned projection must not mutate the fixture envelope.

## Fixture lifecycle and ownership

Fixtures are synthetic and server-owned. A lane owner maintains the definitions
for that lane's family; an integration owner may reference opaque IDs but may
not silently change another lane's fixture semantics. Cross-lane catalog
entries contain summaries and references, not copies of private source data.

During a future check, fixtures may be materialized only under an isolated
task-owned temporary root. The loader must resolve containment, reject
symlinks or outside references, enforce byte/depth limits, and remove its
temporary outputs after the check. No real user, host, provider, model, media,
filesystem, or workflow data may enter the catalog.

Versioned catalog changes are reviewed as contract changes. Old fixtures remain
available for reproducibility; a changed oracle or redaction rule receives a
new contract/fixture version. No existing test, source, configuration, or
fixture file is asserted or modified by M13C.

## Proposed future conformance checks

The following module and cases are proposals only; none is an existing gate
and none is created by this document:

- tests/test_milestone13_contract_fixtures.py (PROPOSED);
- test_fixture_catalog_round_trips_without_source_mutation (PROPOSED);
- test_adversarial_fixtures_fail_closed_without_echo (PROPOSED);
- test_json_markdown_projections_are_redacted_and_deterministic (PROPOSED);
- test_status_oracle_never_inflates_runtime_readiness (PROPOSED);
- test_ref_drift_and_artifact_leak_are_rejected (PROPOSED);
- test_catalog_scope_contains_no_executable_payload (PROPOSED).

Any future implementation must first receive an explicit scope assignment and
must remain separate from the M10B lane gate modules.

## Explicit deferrals and prohibited operations

M13C is documentation-only. It neither performs nor authorizes GPU, video,
FFmpeg, SAM2, model, server, UI, API, provider, or filesystem mutation work.
It does not authorize downloading data, installing dependencies, changing
drivers, loading a model or media asset, benchmarking, or launching a
workload.

No merge, rebase, reset, force-push, main operation, backup, archive, script,
executable fixture, raw local data, secret, or artifact is allowed. After
writing this document, the only bounded checks are:

    git diff --check origin/feature/local-ai-hub-v4...HEAD
    python -B scripts/ci_validate.py

If both checks pass and only this document changed, stage only this document,
commit with docs: define contract fixture catalog, and push the named branch
with tracking. A scope or check failure stops the flow before commit and push.
