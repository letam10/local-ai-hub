# Milestone 5B — Portable Workflow Packages, Typed Subgraph Blueprints & Evaluation Core

Milestone 5B adds a static, shareable descriptor layer for Local AI Hub workflows. It is a planning and validation foundation, not a graph runner. Every public result is explicit about that boundary: a valid descriptor can be `partial`, never operational, until a separately authorized bounded runtime smoke exists.

## Delivered scope

| Deliverable | Contract |
| --- | --- |
| Portable package descriptor | `workflow-package.v1` |
| Reusable typed graph unit | `subgraph-blueprint.v1` |
| Human comparison rubric | `evaluation-scenario.v1` |
| Package tooling | Static lint, safe JSON import/export, catalog discovery, deterministic diff, dry-run migration |
| Audit output | Scrubbed JSON and Markdown provenance with descriptor fingerprint only |

The implementation owns only isolated workflow-package and evaluation-lab paths. It does not modify Node Studio, API, Job Manager, artifact storage, module adapters, existing `workflows/`, or the desktop UI.

## Safety model

Packages are declarative JSON only. The validator rejects:

- raw filesystem locations, traversal, filesystem URIs, and arbitrary URLs;
- secrets, credential-like values, environment references, executable fields, commands, shell syntax, runner/module/import fields;
- embedded media payloads, blobs, model weights, checkpoints, and weight-file references;
- unknown fields, duplicate JSON keys on external import, invalid UTF-8, non-finite numbers, oversized JSON, or excessive JSON nesting;
- duplicate package, blueprint, node, edge, or port identifiers;
- cycles, invalid edge endpoints, duplicate non-multi input connections, and port type mismatches;
- unknown subgraphs, recursive references, and nesting deeper than the bounded contract.

The typed-port vocabulary is frozen in this milestone: `AUDIO`, `BOOLEAN`, `IMAGE`, `MASK`, `METADATA`, `MODEL`, `NUMBER`, `TEXT`, and `VIDEO`. A package declares operation IDs in `compatibility.required_node_types`; operation nodes cannot introduce arbitrary runtime configuration.

No service in this milestone imports a graph module, starts a process, calls a backend, contacts the network, saves a workflow, or mutates a user workflow.

## Static lifecycle

```text
managed JSON descriptor
        |
        v
safe_import_workflow_package
        |
        +-- invalid -> unavailable with fixed validation codes
        |
        v
validated detached package
        |
        +-- lint_workflow_package
        +-- diff_workflow_packages
        +-- plan_workflow_migration (dry_run=true)
        +-- build_package_audit
        |
        v
server-owned integration preflight (still partial until smoke)
```

`safe_import_workflow_package()` accepts only UTF-8 JSON text or bytes. It deliberately has no filename, file-handle, import-hook, or persistence parameter. `export_workflow_package()` revalidates before creating canonical, sorted JSON text and a SHA-256 value. Both functions return detached data so callers cannot change the source object through the result.

`discover_managed_packages()` reads only the repository-owned `workflow_packages/` directory. It refuses symlinked or escaping descriptors and never reports a local source location. The repository samples are therefore safe examples rather than a general-purpose package loader.

## Typed blueprint contract

A package owns one entry `workflow` blueprint and zero or more reusable `subgraphs`. Each blueprint has typed interfaces, declarative nodes, and directional edges.

| Node kind | Meaning | Static rule |
| --- | --- | --- |
| `input` | Exposes a package interface value | Has outputs only; each matches an interface input |
| `operation` | Declares a host node contract | Operation must be listed in `required_node_types` |
| `subgraph` | Calls a package-local reusable blueprint | Call ports exactly match the referenced blueprint interface |
| `output` | Receives a package interface value | Has inputs only; each matches an interface output |

The descriptor represents compatibility requirements, not a request to execute them. `preflight_workflow_package()` can compare declared operation IDs to a server-owned host snapshot. A perfect static match returns `partial` with `execution: not_run`; missing requirements return `unavailable` with a remediation action.

## Deterministic diff and migration

`diff_workflow_packages(before, after)` compares canonicalized, revalidated descriptors. It reports only safe identifiers and change kinds, never copies prior free-text values into output. Reordering JSON keys or unordered IDs does not create a false change.

`plan_workflow_migration(before, after)` always returns `dry_run: true` and `execution: not_run`. It never edits either descriptor or a consumer workflow.

| Change | Planner status |
| --- | --- |
| Identical canonical descriptor | `not_required` |
| Additive static change | `planned` |
| Removed or changed typed interface/node requirement | `manual_review` |
| Different package ID, invalid input, or downgrade request | `unavailable` |

The plan describes required human actions only. Applying a consumer update remains the responsibility of a separately reviewed integration lane.

## Human A/B evaluation core

`evaluation-scenario.v1` is a human-review rubric. It requires exactly two candidates, `A` and `B`, bounded unique criteria whose weights sum to `1.0`, and a protocol declaring `human_review_required: true`.

The scenario does not accept benchmark data, runners, latency/throughput fields, media payloads, scores, votes, or execution settings. `build_human_ab_plan()` always returns `execution: not_run`; it can verify that the scenario references a supplied server-owned validated package but cannot run either candidate.

## Scrubbed audit and provenance

`build_package_audit()` accepts a descriptor or prior validated result and revalidates it before projection. Its JSON/Markdown output contains only:

- package ID, version, static fingerprint, capability IDs, and declared node requirement IDs;
- node/edge counts and typed input/output summaries;
- static validation provenance and `execution: not_run`;
- optional evaluation criterion count and optional dry-run migration summary.

It excludes title/summary free text, raw imported payloads, local locations, user/host metadata, commands, artifact references, secrets, and any runtime claim. `build_package_audit_markdown()` renders fixed labels plus validated identifiers rather than injecting package text into Markdown.

## Proposed integration surface for LOCAL AI HUB (2)

This branch intentionally provides pure Python contracts only. A central integration should call only the owned service outputs below:

```python
from src.services.workflow_packages import (
    discover_managed_packages,
    load_managed_package,
    preflight_workflow_package,
)

catalog = discover_managed_packages()
package = load_managed_package("local-ai-hub.image-review", "1.1.0")
preflight = preflight_workflow_package(package, server_owned_node_type_snapshot)
```

An API/UI integration must not expose a route that accepts a client-supplied package, discovery record, audit mapping, or manifest-like mapping and passes it to a public projection. Resolve an opaque managed package ID server-side, validate the descriptor, and project only the resulting server-owned record. Runtime-dependent extensions and packages remain `partial` until their own bounded functional smoke is authorized and passes.

## CLI and managed samples

The static CLI intentionally has no arbitrary input/output path arguments:

```powershell
python scripts/validate_workflow_packages.py --format json
python scripts/validate_workflow_packages.py --format markdown --package-id local-ai-hub.image-review
```

It reads only tracked JSON below `workflow_packages/` and writes a JSON or Markdown report to stdout. It does not install, download, vendor, launch, execute, or persist anything.

Tracked samples include two versions of `local-ai-hub.image-review` and one human A/B scenario. `Config/workflow_packages.example.json` documents a conservative default: static validation is required and import/execution/network/model-download/workflow-mutation controls are all disabled.

## Verification

The targeted Milestone 5B suite checks schema requirement/property parity, valid samples, unsafe data rejection, graph cycles and type mismatches, subgraph recursion/depth, detached canonical import/export, static discovery/preflight, lint, deterministic dry-run migration, human-only evaluation, and scrubbed audit output.

The milestone gate is limited to this targeted suite, schema/CLI static checks, the repository secret/large-file validation, and whitespace diff validation. It intentionally excludes HTTP/UI smoke, legacy full-suite repetition, video processing, GPU work, and benchmarks.
