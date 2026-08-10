# Milestone 6C - Asset Intelligence and Provenance Core

Milestone 6C adds a static, integration-neutral metadata layer for assets. It is deliberately not an asset scanner, provider runtime, graph executor, retention worker, or media processor. Every output remains `partial` or `unavailable` with `execution: not_run` until a separately authorized bounded smoke proves a specific runtime integration.

## Delivered contracts

| Contract | Purpose |
| --- | --- |
| `asset-record.v1` | Bounded opaque asset metadata, SHA-256, source reference, retention declaration, and typed optional fingerprint metadata |
| `asset-catalog.v1` | A bounded collection of unique asset records |
| `provenance-lineage.v1` | A typed acyclic source asset → recipe/workflow/package → run → derivative chain |
| `smart-collection.v1` | A deterministic allowlisted metadata-query DSL |
| `asset-dataset-manifest.v1` | Canonical static dataset/collection manifest projection |

No contract accepts a filesystem location, URL, secret, command, embedded media/data, model weight, arbitrary provider config, executable callback, runtime handle, or workflow payload.

## Static intelligence boundary

Duplicate groups mean only `declared_sha256_match`: equal, validated SHA-256 metadata already present in a server-owned catalog. The service neither opens nor hashes an asset, so it does not independently prove the declared values match asset bytes. Perceptual and embedding entries are typed metadata only and require `status: not_run`; they never include a perceptual scan result, embedding vector, provider output, or similarity score.

Caption and tag provider cards are `unavailable`. Embedding is `partial` only for validating opaque metadata shape. None of those cards discovers a local provider, loads a model, fetches a network resource, or executes a backend.

## Provenance and retention

Lineage nodes use only opaque references and these directional relation types:

```text
asset -> recipe -> workflow -> package -> run -> asset
```

The contract also permits a source asset to begin at a workflow. Edge/node IDs must be unique, relation endpoint kinds must match, every reference must resolve to a declared node, cycles are rejected, and every node retention value must be within the lineage retention bound.

`preflight_provenance_lineage()` can compare asset-node references *and their declared retention days* to a server-owned validated catalog. It does not resolve recipes, packages, workflows, or runs. `plan_asset_retention()` is always `dry_run: true`; it records static retention declarations and never deletes, moves, opens, or schedules an asset.

## Smart Collections

The query language is data, not code. Its only fields are:

- `asset.bytes` with `eq`, `gte`, or `lte` and a bounded integer;
- `asset.kind`, `asset.media_type`, and `retention.classification` with `eq` or bounded unique `in` values;
- `fingerprints.has_perceptual`, `fingerprints.has_embedding`, and `exact_duplicate` with boolean `eq`.

Expressions use bounded nested `all` or `any` groups, with both per-group and total-expression limits. Unknown fields/operators, arbitrary expression code, callbacks, paths, URLs, query strings, or command fields are rejected. Evaluation sorts asset IDs deterministically and performs no I/O beyond explicitly supplied in-memory descriptors.

## Discovery, reporting, and migration

`discover_managed_asset_catalogs()` reads only static JSON under fixed repository-managed `asset_catalog/`. It refuses symlinked/escaping descriptors, verifies descriptor size before whole-file reads and again after the read, rejects duplicate managed catalog, lineage, and collection identities as unavailable/ambiguous, and never returns raw filesystem paths or descriptor payloads.

`build_asset_qa_report()`/Markdown project only safe IDs, fingerprints, counts, capability-card state, and `not_run` truth. `diff_asset_catalogs()` compares canonical validated records and declared-SHA duplicate-group changes without exposing labels or private data. `plan_asset_catalog_migration()` is an immutable dry run; removed/changed asset metadata or removed duplicate groups require `manual_review`.

Draft 2020-12 schemas are closed and cover the field shapes, primitive bounds, labels, and typed Smart Collection alternatives. Runtime validation additionally enforces cross-record identity uniqueness, lineage relation endpoints, DAG acyclicity, retention comparisons, the total query-node bound, and managed-identity ambiguity; these relational invariants cannot be expressed reliably in standalone JSON Schema.

## Integration boundary

An API or UI integration must resolve opaque managed catalog IDs server-side and pass only validated service results into projection functions. It must not accept a client-provided discovery, report, catalog, lineage, collection, or provider-card mapping and echo it publicly. Shared UI/API/job/node/project-manager/runtime code is intentionally untouched by this milestone.

## Verification boundary

The focused M6C suite covers schema/property parity, safe samples, raw-path/secret/command rejection, duplicate SHA projection, perceptual/embedding `not_run` state, lineage cycle/relation/retention bounds, typed query validation/evaluation, canonical export, fixed-root discovery/size/symlink behavior, dry-run plans, scrubbed reports, and no-execution guard checks.

The only aggregate gate is the targeted M6C suite, static schema/CLI checks, `ci_validate` secret/large-file validation, and `git diff --check`. It excludes broad legacy suites, HTTP/UI smoke, asset scans, video/GPU work, benchmarks, provider calls, downloads, and retention mutations.
