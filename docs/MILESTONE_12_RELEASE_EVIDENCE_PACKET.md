# Milestone 12C Release Evidence Packet Contract

## Purpose and strict boundary

A release evidence packet is a versioned, redacted, manager-reviewable record
for one lane candidate or one isolated integration candidate. It binds the
review evidence to a specific source identity and to bounded static checks.

This packet complements M10B. M10B remains the contract for the lane gate
matrix and its ordering; M12C records the identity, bounded results, verdict
provenance, and admission decision that a manager reviews. It does not copy
the M10B matrix and it is not a replacement for any lane test module.

An evidence packet never authorizes a merge, a GitHub merge, a main-branch
operation, or a claim of runtime readiness. Runtime readiness requires a
separately authorized, bounded smoke for the named capability. Static evidence
alone cannot promote a deferred runtime status.

## Packet version and immutable identity

The packet contract is identified as release-evidence-packet.v1. These fields
are captured before checks and are immutable for the packet:

- candidate branch and candidate ref;
- base ref and its pinned base commit;
- candidate HEAD SHA;
- merge-base SHA between the candidate and the pinned base;
- lane or integration-candidate identifier and responsible owner;
- a changed-file summary containing only counts and a scope fingerprint;
- command and tool-version context using allowlisted labels and versions.

The changed-file summary must not enumerate raw local paths. It records bounded
counts for added, modified, deleted, and total files plus a digest of the
validated ownership projection. A later source change requires a new packet,
not an edit to the old identity.

The command context identifies tool families and versions (for example, Git,
Python, validator, and CI checker) without recording environment variables,
host names, user names, credentials, or an unbounded command line.

## Bounded command records

Every recorded check uses a stable command label from the manager's allowlist.
A record contains:

- label;
- exit status;
- duration class: instant, short, medium, long, or not_run;
- expected-skip codes, if any;
- a deterministic, bounded summary consisting of fixed result codes and
  numeric counters.

The record never contains raw stdout/stderr, an unbounded log, a command
payload, an environment dump, a path, a secret, or a parser exception.
Failures are represented by fixed issue codes and a safe reason/action pair.
An expected skip is admissible only when its code is explicitly allowlisted and
its reason is recorded; an unexplained skip is a rejection.

## Verdict and provenance

The packet separates static evidence from operational evidence:

- static verdict: pass, fail, or not_run;
- operational status: operational, partial, planned, unavailable, or not_run;
- provenance: server_owned_validated, manager_qa, or not_run;
- runtime-smoke status and authorization reference, when one exists.

Provider, filesystem, retention, model, image, video, workflow, and server
capabilities remain partial, planned, unavailable, or not_run until an
authorized bounded smoke passes. A static pass is never an operational pass.
The packet may say that a check was not run; it must not invent a successful
runtime result.

## Redaction and allowlist boundary

The packet projection is built only from validated, server-owned normalized
objects. Its allowlist may include immutable refs and SHAs, lane/owner
identifiers, bounded file counts, tool-version labels, fixed command result
codes, status values, reason/action codes, and canonical fingerprints.

The projection must omit:

- raw filesystem paths, path traversal material, and user-workflow locations;
- credentials, API keys, bearer material, secrets, tokens, and passwords;
- host, user, account, environment, process, and network details;
- client-supplied discovery, manifest, report, or mapping values;
- command payloads, shell text, executable paths, and arbitrary arguments;
- model, media, image, audio, video, blob, checkpoint, or weight data;
- unvalidated report data, arbitrary URLs, timestamps used as identity, and
  uncontrolled free text.

Markdown and JSON projections escape display text. Invalid input must result in
fixed codes and must never be echoed into the packet.

## Admission criteria

A packet may be admitted for manager review only when all of these conditions
hold:

1. The candidate ref, pinned base, HEAD, and merge-base are captured and
   consistent.
2. The changed-file projection is wholly inside the exact lane or integration
   ownership scope.
3. The worktree is clean, with no untracked or generated artifact in scope.
4. No process, helper, server, workload, or task-owned temporary artifact
   remains.
5. The bounded diff check and CI validation pass; expected skips are explicit.
6. The worker handoff identifies the same packet fingerprint and scope.
7. Independent manager QA has reviewed the packet and its source identity.

Fixed rejection codes include SCOPE_MISMATCH, DIRTY_WORKTREE, HEAD_DRIFT,
BASE_DRIFT, MERGE_BASE_DRIFT, PROCESS_LEAK, ARTIFACT_LEAK,
COMMAND_FAILURE, UNEXPECTED_SKIP, REDACTION_FAILURE,
CLIENT_MAPPING_ECHO, RUNTIME_CLAIM_UNAUTHORIZED, and MISSING_MANAGER_QA.
A rejection is not repaired by editing the packet; regenerate it from a new
immutable source identity after the owner addresses the cause.

## Deterministic projection, fingerprint, and TOCTOU controls

The canonical packet projection is UTF-8 JSON with sorted object keys, stable
array ordering by fixed IDs, bounded strings, and deterministic separators.
It contains no random IDs, wall-clock values, locale-dependent formatting, or
raw logs. The fingerprint is computed over this redacted canonical projection
using the declared digest algorithm.

Capture the pinned base, candidate HEAD, and merge-base before checks. Re-check
HEAD, the base pin, and the ownership projection after checks. If any ref,
merge-base, worktree, or owned-file set changes, reject the packet with a
drift code and regenerate it. A packet fingerprint is immutable after
handoff; a changed packet is a new packet version. Manager review must compare
the fingerprint and identity, not an unpinned moving branch name.

The packet is evidence, not a lock. It cannot prevent a later push or TOCTOU
change; the post-check identity revalidation and manager-side fetch are the
required controls.

## Illustrative JSON-shaped schema

The following is documentation only. All values are placeholders and contain
no local paths, credentials, secrets, host data, or runtime result:

    {
      "contract": "release-evidence-packet.v1",
      "identity": {
        "candidate_branch": "PLACEHOLDER_CANDIDATE_BRANCH",
        "candidate_ref": "PLACEHOLDER_CANDIDATE_REF",
        "base_ref": "PLACEHOLDER_BASE_REF",
        "base_sha": "PLACEHOLDER_BASE_SHA",
        "head_sha": "PLACEHOLDER_HEAD_SHA",
        "merge_base_sha": "PLACEHOLDER_MERGE_BASE_SHA",
        "lane_or_candidate": "PLACEHOLDER_LANE_ID",
        "owner": "PLACEHOLDER_OWNER_ID",
        "changed_file_summary": {
          "added": "PLACEHOLDER_COUNT",
          "modified": "PLACEHOLDER_COUNT",
          "deleted": "PLACEHOLDER_COUNT",
          "total": "PLACEHOLDER_COUNT",
          "scope_fingerprint": "PLACEHOLDER_DIGEST"
        },
        "toolchain": {
          "git": "PLACEHOLDER_VERSION",
          "python": "PLACEHOLDER_VERSION",
          "validator": "PLACEHOLDER_VERSION",
          "ci": "PLACEHOLDER_VERSION"
        }
      },
      "commands": [
        {
          "label": "PLACEHOLDER_ALLOWLISTED_CHECK",
          "exit_status": "PLACEHOLDER_STATUS",
          "duration_class": "PLACEHOLDER_DURATION",
          "expected_skips": [],
          "deterministic_summary": "PLACEHOLDER_SUMMARY_CODE"
        }
      ],
      "verdict": {
        "static": "PLACEHOLDER_STATIC_STATUS",
        "operational": "PLACEHOLDER_OPERATIONAL_STATUS",
        "provenance": "server_owned_validated",
        "runtime_smoke": "not_run"
      },
      "admission": {
        "status": "PLACEHOLDER_ADMISSION_STATUS",
        "rejection_codes": []
      },
      "fingerprint": {
        "algorithm": "PLACEHOLDER_DIGEST_ALGORITHM",
        "value": "PLACEHOLDER_PACKET_DIGEST",
        "projection": "canonical_redacted_v1"
      }
    }

Allowed operational values are operational, partial, planned, unavailable,
and not_run. The placeholder schema is not an instruction to expose the
underlying source values.

## Lifecycle and human review

The controlled lifecycle is:

1. worker creates a packet from an immutable candidate identity and hands it
   off with bounded command records;
2. an independent manager QA worktree fetches and verifies the identity,
   fingerprint, scope, redaction, and admission criteria;
3. the candidate may be published as a draft PR for human review;
4. only after explicit authorization may a narrowly scoped runtime smoke run;
5. runtime evidence is appended as a separately identified update or packet,
   never inferred from static results.

No step automatically merges a GitHub PR or writes to main. Release and merge
actions are explicit manager decisions.

## Explicit deferrals and prohibited operations

M12C is documentation-only. It does not authorize or perform GPU, video,
FFmpeg, SAM2, model, server, UI, API, provider, or filesystem mutation work.
It does not authorize downloading data, installing dependencies, changing
drivers, loading models or media, benchmarking, or launching a workload.

The worker must not merge, rebase, reset, force-push, checkout another branch,
write a backup, create an archive, or create an unrequested report artifact.
After writing this document, the bounded checks are limited to:

    git diff --check origin/feature/local-ai-hub-v4...HEAD
    python -B scripts/ci_validate.py

If both checks pass, stage only this document, commit with
docs: define release evidence packet contract, and push the named branch with
tracking. Any scope or check failure stops the flow before commit and push.
