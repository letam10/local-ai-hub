# Milestone 10B Release-Gate Contract

## Purpose and scope

This document defines the bounded, static release gate for the Local AI Hub
milestone lanes M4B, M5B, M6C, M7C, and M8. It is a contract for an isolated
integration review; it does not authorize workload execution, a merge, or a
runtime-operational claim.

The detached M10B base may not contain lane-local test files. That is a
topology fact, not evidence that a lane test is missing. The authoritative
lane inventory is:

| Lane | Branch | Existing targeted module |
| --- | --- | --- |
| M4B Extension Platform | feature/local-ai-hub-v4-extension-platform | tests/test_milestone4_extension_platform.py |
| M5B Workflow Packages | feature/local-ai-hub-v4-workflow-packages | tests/test_milestone5_workflow_packages.py |
| M6C Asset Intelligence | feature/local-ai-hub-v4-asset-intelligence-core | tests/test_milestone6_asset_intelligence.py |
| M7C Privacy Diagnostics | feature/local-ai-hub-v4-privacy-diagnostics-core | tests/test_milestone7_privacy_diagnostics.py |
| M8 Creative Recipe Intelligence | feature/local-ai-hub-v4-creative-recipe-intelligence-core | tests/test_milestone8_creative_recipe_intelligence.py |

The modules above are existing lane gates. A future cross-lane module is a
proposal only and must not be described as an existing gate:
tests/test_milestone10b_cross_lane_contracts.py (PROPOSED).

## Topology and ownership audit

Before running a gate, the integration owner records the exact base, lane
refs, worktree, and HEAD, then verifies that each lane changes only its
owned paths. The integration branch must be isolated from main and from
the lane worktrees. No worker may edit another lane's files or shared central
paths as part of this review.

Checkout, reset, rebase, force-push, and direct main operations are out of
scope. Future integration must never GitHub-merge pull requests automatically;
release and merge decisions remain with the manager.

The gate consumes only validated, server-owned normalized objects. It must
never accept client-supplied discovery, manifest, or report mappings as the
source of an audit or compatibility projection.

## Bounded static gate

Run the existing lane modules in dependency order, using python -B:

1. M4B — tests/test_milestone4_extension_platform.py
2. M5B — tests/test_milestone5_workflow_packages.py
3. M6C — tests/test_milestone6_asset_intelligence.py
4. M7C — tests/test_milestone7_privacy_diagnostics.py
5. M8 — tests/test_milestone8_creative_recipe_intelligence.py

For each lane, run its tracked validator in both JSON and Markdown modes when
provided. Then run the aggregate static checks once at the end:

    python -B scripts/ci_validate.py
    git diff --check origin/feature/local-ai-hub-v4...HEAD

The gate is intentionally bounded. It does not run the full legacy suite,
HTTP/UI smoke, a provider or filesystem operation, a benchmark, model
loading, GPU/video/FFmpeg/SAM2 work, or a server/API.

## Cross-lane probes

Cross-lane checks are pure in-memory or isolated temporary-fixture probes.
They use only server-owned, already validated outputs and verify:

- stable IDs, canonical content fingerprints, counts, and type summaries;
- deterministic ordering and repeatable JSON/Markdown projections;
- fixed issue codes with safe reason/action text;
- no client mapping, raw import payload, or unvalidated report field is echoed;
- dry-run plans do not write user trees, launch a graph/workflow, import code,
  invoke a command, access a network, or load a model/media asset.

Candidate tests for a future cross-lane module (proposal only) include:

- test_server_owned_projection_never_echoes_client_mapping;
- test_cross_lane_fingerprints_and_statuses_are_deterministic;
- test_dry_run_never_writes_or_launches;
- test_release_gate_scope_isolated.

## PASS and FAIL policy

READY_FOR_MANAGER_QA for static scope requires all five existing targeted
modules, the available JSON/Markdown validators, ci_validate, and the diff
check to pass with deterministic evidence. Ownership must be clean, outputs
must be detached and redacted, and every status must be truthful.

Static green is not a runtime-operational verdict. Provider, filesystem,
retention, model, image, video, and workflow execution remain
partial, planned, unavailable, or not_run until a separately authorized
bounded smoke has passed. No runtime status may be inferred from static
validation alone.

The release gate is FAIL on any non-zero command, unexplained skip, schema or
contract mismatch, ownership overlap, nondeterminism, unsafe path/secret/
command/media/blob/host/user/environment leakage, client-controlled
projection, false operational status, or task-owned process/artifact leak.

## Security and redaction contract

Audit and compatibility output is a whitelist projection. It may expose only
validated IDs, versions, digests, bounded counts/type summaries, fixed issue
codes, and dry-run summaries. It must omit raw paths, secrets, commands,
media/blob data, host and user identity, environment values, credentials, and
unvalidated input. Markdown display values must be escaped so that a
malicious identifier cannot create a link, HTML, or table injection.

## Ordering, cleanup, and non-overlap

The prescribed order is topology/ownership audit, syntax/schema checks, the
five existing targeted modules, JSON validators, Markdown validators, pure
cross-lane probes, ci_validate, and git diff --check, followed by cleanup.
Use only task-owned temporary fixtures; do not create tracked reports,
backups, archives, or user-workflow artifacts. Remove temporary files and
stop any process started by the task after verifying its ownership. No GPU,
video, server, download, dependency installation, or benchmark is allowed.

Each lane has one owner and one isolated worktree. The integration reviewer
must not repair lane source, widen the allowlist, or alter shared topology
during the gate. A failure is reported with minimal evidence and remains a
failure until the owning lane supplies a reviewed change.

## Safe rollout

After the topology audit, freeze the ownership map and run the bounded static
gate on the isolated integration branch. Compare canonical fingerprints,
status reason/action pairs, and redaction fixtures across all lanes. The
manager reviews the evidence and may authorize narrowly scoped runtime smokes
per capability; untested backends retain their deferred status. Only after
that review may release automation proceed, and any GitHub merge is an
explicit manager action—not an automatic step of this contract.
