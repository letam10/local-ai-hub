# Milestone 15 Runtime Smoke Authorization Contract

## Purpose and non-authority

This document is a policy contract for a future, separately authorized, bounded
runtime smoke. It does not authorize or perform any runtime operation now. It
does not start a provider, server, UI, API, model, media pipeline, workflow,
GPU job, or filesystem action.

A runtime smoke is evidence for one narrow capability and one approved
identity. It is not a benchmark, compatibility promise, production rollout,
merge approval, or runtime-readiness claim for any untested capability. Static
green evidence is a prerequisite, never a substitute for an operational smoke.

## Static prerequisite and named approval

Before any future smoke, the manager must record a named approval object after
the relevant static contracts and bounded checks have passed. The approval
must bind all of the following immutable fields:

- approval_id and approval_version;
- capability identifier and exact provider, artifact, and version;
- responsible owner and review authority;
- candidate branch/ref, exact HEAD, base identity, and source fingerprint;
- one bounded objective and the reason the smoke is needed;
- explicit start and expiration time in the manager system;
- approved input class and output class;
- maximum duration, resource budget, process count, and temporary-storage
  budget;
- device class and exact device identity when hardware is involved;
- stop conditions, abort authority, and cleanup owner.

A missing, expired, ambiguous, or changed approval is a hard stop. A branch name
without a pinned HEAD, or a provider name without an exact artifact/version,
does not authorize a smoke. The approval is detached from mutable client
metadata and must be revalidated immediately before the future smoke.

## Immutable scope and input/output boundary

The smoke accepts only synthetic data or data explicitly approved by the
manager for this exact objective. Inputs are identified by bounded, immutable
digests and safe fixture IDs. They contain no credentials, raw local paths,
user content, personal data, hidden environment values, or unreviewed model or
media bytes.

Outputs are limited to an allowlisted result shape and a bounded result size.
The smoke may not write outside its task-owned temporary root, modify a user
tree, publish an artifact, or return a raw provider response. Network,
filesystem, model, media, and provider access require their own explicit
authorization; no implied access is granted by this contract.

An input, provider, artifact, branch, or output identity that changes during
the smoke is a scope failure. The smoke is stopped and its evidence is marked
invalid rather than silently adapting.

## Preflight and truthful status

Future preflight checks confirm the approval identity, static contract
fingerprints, provider/artifact/version, input and output digests, resource
budget, device selection, and process boundary. Preflight itself is not a
functional smoke and cannot set an operational status.

Allowed truth states are:

- planned: approved but not started;
- not_run: no smoke has run or evidence is incomplete;
- partial: a bounded result exists but the capability is incomplete or only a
  subset of the approved objective passed;
- unavailable: the approved provider, artifact, device, or required resource
  cannot be used;
- operational: only when the exact approved objective completed and the
  manager accepted its bounded evidence.

A static validator passing, a package importing, or a descriptor being
discovered never justifies operational. Unsupported or untested paths remain
partial, planned, unavailable, or not_run.

## Future device selection and GPU safety

No device probe or workload is run by M15C. For a future GPU smoke, the
approval must name the required device class and exact device identity before
launch. The preflight must prove that the selected device is available and
that the approved resource budget fits.

There is no silent fallback. If the named device or backend is unavailable,
the smoke is unavailable and stops before workload launch. A different device,
backend, driver, CUDA setting, or model version requires a new approval. Device
selection is evidence, not a permission to inspect or use another task's GPU.

## Time, resource, process, and abort boundaries

The approval supplies hard ceilings for wall-clock time, CPU/GPU time, VRAM,
RAM, disk, process count, and temporary output size. A smoke never becomes a
benchmark: no throughput, latency leaderboard, stress loop, warm-up study,
parameter sweep, or comparative performance claim is allowed.

Abort immediately on timeout, budget exhaustion, unexpected child process,
network or filesystem access, unapproved input/output, device mismatch,
provider/artifact drift, scope drift, secret detection, or a stop request
from the owner or manager. Do not automatically retry or widen a limit.

Process lineage is recorded only as bounded ownership evidence. Cleanup checks
the task-created parent, children, helpers, and descendants by verified
lineage, command identity, and purpose. It stops only task-owned processes,
then verifies termination. Task-owned temporary files are enumerated by
verified scope and removed after the result is captured; unrelated processes,
files, caches, models, and environments are never touched.

## Result evidence and redaction

A result packet records only:

- approval and source fingerprints;
- capability/provider/artifact/version identifiers;
- command class label, not a raw command or argument payload;
- exit status and bounded duration class;
- a non-sensitive deterministic summary and bounded counters;
- static verdict and operational truth state;
- approved input/output digests and cleanup result;
- redacted findings, fixed issue codes, and safe reason/action text.

It never records credentials, command text, executable paths, raw provider
responses, environment dumps, host/user identity, raw paths, client mappings,
model/media/blob content, or unvalidated report data. JSON and Markdown
projections escape display text and remain stable across repeated projection.
A failure is reported with minimal fixed evidence; parser exceptions and unsafe
values are not echoed.

## Failure, rollback, and containment

A failed or aborted smoke is marked FAIL or unavailable for the approved
objective. It is not converted to partial success by guessing and is never
automatically retried. The owner captures the bounded reason/action code and
notifies the manager for review.

No automatic rollback, publish, GitHub PR merge, main operation, dependency
change, model replacement, driver change, or filesystem restoration is
performed. If a separately approved smoke was allowed to make a reversible
task-owned change, rollback requires a new manager-approved action and an
independent identity check. Otherwise containment means stopping owned
processes, removing owned temporary outputs, preserving redacted evidence,
and leaving shared state untouched.

Manual manager review is required for every operational claim, every failure
with side effects, every device/backend discrepancy, and every scope or
provenance drift. The worker may hand off evidence but cannot promote status.

## Provider and workload-specific deferrals

Provider, filesystem, model, image, video, audio, and workflow smokes are
separate approvals. Approval of one capability does not authorize another
provider, artifact, modality, input class, output class, or workflow.

M15C performs and enables none of the following: GPU or video work, FFmpeg,
SAM2, model loading, server/UI/API startup, provider calls, filesystem
mutation, data download, dependency installation, driver or CUDA change,
benchmarking, or workload launch. Each remains planned, partial, unavailable,
or not_run until separately authorized and bounded.

## Placeholder-only approval and result schema

The following JSON-shaped example is documentation only. Every value is a
placeholder; it contains no real provider, path, host, user, credential, or
runtime data and is not an executable procedure:

    {
      "contract": "runtime-smoke-authorization.v1",
      "approval": {
        "approval_id": "PLACEHOLDER_APPROVAL",
        "capability": "PLACEHOLDER_CAPABILITY",
        "provider": "PLACEHOLDER_PROVIDER",
        "artifact": "PLACEHOLDER_ARTIFACT",
        "version": "PLACEHOLDER_VERSION",
        "owner": "PLACEHOLDER_OWNER",
        "candidate_head": "PLACEHOLDER_HEAD",
        "bounded_objective": "PLACEHOLDER_OBJECTIVE",
        "reason": "PLACEHOLDER_REASON",
        "expires": "PLACEHOLDER_EXPIRY",
        "input_class": "PLACEHOLDER_INPUT_CLASS",
        "output_class": "PLACEHOLDER_OUTPUT_CLASS",
        "device": "PLACEHOLDER_DEVICE_OR_NONE",
        "limits": {
          "duration_class": "PLACEHOLDER_DURATION",
          "resource_class": "PLACEHOLDER_RESOURCE"
        }
      },
      "preflight": {
        "static_status": "PLACEHOLDER_STATIC_STATUS",
        "scope_status": "PLACEHOLDER_SCOPE_STATUS",
        "provenance": "server_owned_validated"
      },
      "result": {
        "command_class": "PLACEHOLDER_COMMAND_CLASS",
        "exit_status": "PLACEHOLDER_EXIT_STATUS",
        "summary_code": "PLACEHOLDER_SUMMARY",
        "operational_status": "not_run",
        "findings": [],
        "cleanup": "PLACEHOLDER_CLEANUP_STATUS"
      }
    }

The placeholder schema does not authorize a smoke and does not turn
not_run into operational.

## Proposed future conformance checks

The following module and cases are proposals only. They are not existing
coverage and are not created or modified by M15C:

- tests/test_milestone15_runtime_smoke_authorization.py (PROPOSED);
- test_static_evidence_is_required_before_approval (PROPOSED);
- test_approval_binds_provider_artifact_version_and_head (PROPOSED);
- test_scope_drift_and_device_fallback_fail_closed (PROPOSED);
- test_result_projection_contains_no_raw_command_or_secret (PROPOSED);
- test_abort_cleans_only_task_owned_processes_and_artifacts (PROPOSED);
- test_static_and_operational_statuses_remain_distinct (PROPOSED).

Any future implementation requires a separate manager-approved scope and must
not be presented as a runtime smoke performed by this documentation task.

## Explicit task deferrals and bounded checks

M15C is documentation-only. It performs no GPU, video, FFmpeg, SAM2, model,
server, UI, API, provider, or filesystem mutation operation. It performs no
download, dependency installation, benchmark, workload launch, backup,
archive, or artifact creation.

No merge, rebase, reset, force-push, or main operation is allowed. After
writing this document, the only bounded checks are:

    git diff --check origin/feature/local-ai-hub-v4...HEAD
    python -B scripts/ci_validate.py

If both checks pass and only this document changed, stage only this document,
commit with docs: define runtime smoke authorization contract, and push the
named branch with tracking. A scope or check failure stops the flow before
commit and push.
