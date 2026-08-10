# Workflow Package Authoring Guide

This guide describes how to author a safe `workflow-package.v1` descriptor for the static Local AI Hub package catalog. A descriptor is a typed plan, not executable workflow code. Start from the managed samples in `workflow_packages/samples/` and validate with the repository CLI.

## Authoring rules

- Write ordinary UTF-8 JSON only.
- Keep the complete descriptor below the documented static size limit.
- Use only fields defined by the versioned contract; unknown fields are rejected.
- Use logical identifiers, never workstation locations, URLs, credentials, shells, modules, runners, media blobs, model weights, or checkpoints.
- Describe a graph with interfaces, declarative nodes, and typed edges. Do not embed a host workflow, generated output, model payload, or executable configuration.
- Treat `operation` values as compatibility declarations. They do not permit a package to execute a node.
- Keep the graph acyclic and package-local subgraph references bounded.

## Package skeleton

Every top-level field below is required:

```json
{
  "schema_version": "workflow-package.v1",
  "id": "publisher.safe-package",
  "version": "1.0.0",
  "title": "Short static title",
  "summary": "A bounded description of the typed package purpose.",
  "author": {"name": "Author name"},
  "license": "MIT",
  "source": {"kind": "managed-repository", "reference": "safe-package-v1"},
  "capabilities": ["image", "metadata"],
  "compatibility": {
    "hub_version": ">=4.0.0",
    "node_contract": "subgraph-blueprint.v1",
    "required_node_types": ["hub.image.inspect"]
  },
  "workflow": {"schema_version": "subgraph-blueprint.v1", "id": "entry", "title": "Entry", "description": "Typed entry blueprint.", "inputs": [], "outputs": [], "nodes": [], "edges": []},
  "subgraphs": []
}
```

The empty blueprint above demonstrates field shape only; useful packages normally include typed input/output nodes and edges. The sample `image-review.workflow-package.json` is a complete valid reference.

## Top-level fields

| Field | Requirement |
| --- | --- |
| `id` | Lowercase stable package ID, such as `publisher.safe-package` |
| `version` | SemVer, including an optional prerelease suffix |
| `title`, `summary` | Bounded plain text without locations, URLs, commands, secrets, or embedded data |
| `author.name` | Bounded plain text author label |
| `license` | Short declared license label |
| `source.kind` | `managed-repository`, `exported-package`, or `manual-authoring` |
| `source.reference` | Opaque logical source ID; it is not a path, URL, or filename |
| `capabilities` | Unique members of `audio`, `image`, `metadata`, `text`, `utility`, `video` |
| `compatibility` | Hub version constraint, fixed typed node contract, and unique operation IDs |
| `workflow` | One `subgraph-blueprint.v1` entry descriptor |
| `subgraphs` | Zero or more package-local reusable blueprints |

Avoid using titles or summaries to smuggle runtime instructions. They are descriptive only and may be omitted from public audit projections.

## Typed subgraph blueprints

Each blueprint has these required fields:

```json
{
  "schema_version": "subgraph-blueprint.v1",
  "id": "metadata-annotation",
  "title": "Metadata annotation",
  "description": "A reusable static typed blueprint.",
  "inputs": [],
  "outputs": [],
  "nodes": [],
  "edges": []
}
```

Blueprint IDs, node IDs, edge IDs, and port IDs use the same bounded identifier vocabulary. A package is limited in nodes, edges, ports, subgraphs, total graph size, and subgraph nesting; keep authored descriptors much smaller than those safety limits.

### Interfaces and ports

A port has an ID, a typed value, and optional `required`, `multi`, and `description` fields.

```json
{
  "id": "image",
  "type": "IMAGE",
  "required": true,
  "description": "An opaque host-provided image value."
}
```

Allowed port types are `AUDIO`, `BOOLEAN`, `IMAGE`, `MASK`, `METADATA`, `MODEL`, `NUMBER`, `TEXT`, and `VIDEO`.

The source and destination port types of every edge must match exactly. A non-`multi` destination port receives no more than one edge.

### Declarative nodes

Every node declares `id`, `kind`, `inputs`, and `outputs`.

| Kind | Required extra field | Interface rule |
| --- | --- | --- |
| `input` | none | It has no inputs; output ports match blueprint inputs |
| `operation` | `operation` | Value must appear in `compatibility.required_node_types` |
| `subgraph` | `ref` | Its input/output ports exactly match the referenced local blueprint |
| `output` | none | It has no outputs; input ports match blueprint outputs |

An operation is deliberately small:

```json
{
  "id": "inspect-image",
  "kind": "operation",
  "operation": "hub.image.inspect",
  "inputs": [{"id": "image", "type": "IMAGE", "required": true}],
  "outputs": [{"id": "facts", "type": "METADATA"}]
}
```

Fields that imply execution or hidden state are rejected. Do not add a command, executable, runner, module, import, data payload, media asset, file field, location, environment field, or arbitrary operation configuration.

### Edges

An edge connects one declared node output to one declared node input:

```json
{
  "id": "image-to-inspect",
  "from": {"node": "input-image", "port": "image"},
  "to": {"node": "inspect-image", "port": "image"}
}
```

The validator rejects unknown nodes/ports, duplicate edge IDs, type mismatch, duplicate non-multi connections, and any graph cycle. It does not repair a faulty graph.

### Package-local subgraphs

Use a subgraph node only for a blueprint listed in the package `subgraphs` array. The call signature must exactly equal the referenced blueprint interface. References cannot target another package and cannot create a recursive or over-deep chain.

This design intentionally keeps reuse static and local. It does not serialize a host graph runtime, a LiteGraph subgraph, a general workflow document, or an arbitrary JSON entrypoint.

## Safe validation and export

Run one of the repository-managed static reports:

```powershell
python scripts/validate_workflow_packages.py --format json
python scripts/validate_workflow_packages.py --format markdown
```

The CLI has no option to read an arbitrary location or write an arbitrary output file. Place a reviewed descriptor beneath the tracked `workflow_packages/` directory, then run the report. External integration code can use the pure Python import/export contract:

```python
from src.services.workflow_packages import export_workflow_package, safe_import_workflow_package

import_result = safe_import_workflow_package(json_text_or_bytes)
export_result = export_workflow_package(import_result)
```

An accepted import is a detached `planned` descriptor. It is not installed, registered, persisted, or run. Export uses canonical sorted JSON and returns a fingerprint, making review and package diff deterministic.

## Lint, diff, and migration dry-run

`lint_workflow_package()` reports advisory static findings such as unreachable nodes, a missing output node, an unused declared operation, or an unreferenced subgraph. It never changes the package.

`diff_workflow_packages(before, after)` returns stable change kinds and identifiers. It does not reveal the prior or new free-text content.

`plan_workflow_migration(before, after)` is always a dry run. It may return:

- `not_required` for canonical equality;
- `planned` for additive descriptor changes;
- `manual_review` for removed or changed typed contracts;
- `unavailable` for mismatched package IDs, invalid descriptors, and downgrade inference.

No migration plan writes or replaces a workflow. A consumer must apply any approved reference change separately.

## Human A/B scenario authoring

An evaluation scenario is a static review rubric, not a benchmark definition:

```json
{
  "schema_version": "evaluation-scenario.v1",
  "id": "safe-package-human-ab",
  "title": "Human descriptor comparison",
  "package": {"id": "publisher.safe-package", "version": "1.1.0"},
  "candidates": [
    {"id": "A", "label": "Baseline", "package_version": "1.0.0"},
    {"id": "B", "label": "Proposed", "package_version": "1.1.0"}
  ],
  "rubric": [
    {"id": "clarity", "label": "Clarity", "guidance": "A human compares descriptor clarity.", "weight": 1.0}
  ],
  "review_protocol": {"blind": true, "randomize_order": true, "human_review_required": true},
  "limitations": ["No benchmark or workflow is run."]
}
```

There must be exactly one `A` and one `B`, rubric IDs must be unique, and all rubric weights must total `1.0`. Do not add results, votes, score data, a runner, warmup/iteration counts, latency/throughput fields, execution settings, media, model data, or commands.

`build_human_ab_plan()` creates a `not_run` plan and can verify the referenced package ID/version using a server-owned validated package result. A human must perform the review and record any result in an approved system outside this static contract.

## Audit and provenance

Use `build_package_audit()` or `build_package_audit_markdown()` only with a descriptor or a prior service validation result. Both revalidate first, then include only safe contract IDs, fingerprints, counts, types, and static/dry-run status. They do not echo author text, imported JSON, locations, host values, secrets, commands, or runtime output.

For an API/UI integration, resolve an opaque package ID server-side and pass only a server-owned validated package result into reporting. Never accept a client-provided package/audit/discovery mapping and directly return it to another client.
