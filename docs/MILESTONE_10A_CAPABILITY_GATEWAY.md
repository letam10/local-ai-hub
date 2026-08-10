# MILESTONE 10A — Server-Owned Static Capability Gateway

## Purpose

M4B, M5B, M6C, M7C and M8 provide validated static descriptors, catalogs,
preflight results, findings and dry-run plans. They do not provide one safe
public boundary for a future consumer. The gateway contract described here is
that boundary: it exposes bounded, redacted, read-only summaries produced by
the server itself.

This document defines a contract and its invariants only. It does not add a
route, execute a workload, or make any runtime/operational claim.

## Scope and explicit non-scope

In scope:

- static discovery, validation, preflight and compatibility summaries;
- opaque identity/version summaries and fixed reason/action codes;
- resource, migration, retention, remediation, generation-intent and variant
  planning summaries when they are explicitly requested as dry runs;
- deterministic, redacted JSON suitable for a later local read-only adapter.

Out of scope:

- graph, job, model, runtime, provider or media execution;
- installation, download, dependency/model acquisition or shell commands;
- OS, process, filesystem, GPU, CUDA or network probing;
- mutation of catalogs, policies, workflows, artifacts or user files;
- UI, Node Studio, job manager, artifact store and workflow-runner changes;
- arbitrary client paths, output destinations, URLs, manifests, catalogs,
  discovery objects or report mappings.

## Server-owned boundary

The gateway is a service-owned dispatcher. The caller supplies only a closed,
bounded request:

```json
{
  "schema_version": "capability-gateway.v1",
  "sections": ["extensions", "workflow_packages", "assets", "privacy", "recipes"],
  "selectors": {
    "extension_ids": ["opaque-id"],
    "package_ids": ["opaque-id@version"],
    "catalog_ids": ["opaque-id"],
    "recipe_ids": ["opaque-id"]
  },
  "include_plans": false
}
```

`sections` and selector arrays have fixed allowlists and cardinality limits.
Selectors are opaque managed identities, not paths or filenames. Unknown,
duplicate or ambiguous identities fail closed. The request must reject keys
such as `manifest`, `discovery`, `catalog`, `preflight`, `report`, `path`,
`url`, `command`, `secret` and arbitrary mapping values; rejected values are
never echoed.

The server resolves each selector against its fixed managed root and invokes
the existing validator/loader first. It then projects only an allowlisted
summary. A client-created mapping must never reach a report builder or a
provenance-sensitive function.

### Section pipeline

| Section | Server-owned pipeline | Required truth preserved |
| --- | --- | --- |
| `extensions` | M4B discovery → extension preflight → optional resource plan → compatibility summary | static descriptor status; resource plan is dry-run only |
| `workflow_packages` | M5B managed-package discovery/load → typed preflight → optional audit/diff/migration | package execution remains not run; migration is dry-run |
| `assets` | M6C catalog discovery/load → catalog/duplicate/provenance summaries → optional retention plan | no byte scan/provider execution; retention is dry-run |
| `privacy` | M7C server-owned policy/snapshot load → diagnostics/support/remediation projection | consent and policy identity are enforced; remediation is dry-run |
| `recipes` | M8 managed-catalog discovery/load → lint/compatibility/intent/variant plan | model/media execution is not run; unavailable targets stay unavailable |

All section inputs are detached, normalized service results. The gateway does
not read arbitrary paths itself, import runtime modules, execute commands,
probe hardware or pass raw source payloads through the response.

## `capability-gateway.v1` response envelope

The response is a closed object with these bounded fields:

```json
{
  "schema_version": "capability-gateway.v1",
  "status": "partial",
  "reason_code": "RUNTIME_SMOKE_NOT_AUTHORIZED",
  "reason": "Static metadata is available; runtime evidence is absent.",
  "action_code": "AUTHORIZE_BOUNDED_SMOKE",
  "action": "Use a separately authorized bounded smoke before claiming runtime readiness.",
  "execution": "not_run",
  "dry_run": true,
  "fingerprint": {"algorithm": "sha256", "value": "canonical-digest"},
  "sections": {
    "extensions": {
      "status": "partial",
      "records": [
        {"id": "opaque-id", "version": "1.0.0", "status": "partial", "reason_code": "...", "action_code": "..."}
      ],
      "counts": {"records": 1}
    }
  },
  "counts": {"operational": 0, "partial": 1, "unavailable": 0, "planned": 0},
  "errors": []
}
```

Only opaque IDs/versions, bounded counts, fixed codes and safe summaries are
permitted. The envelope must not contain raw paths, host/user/environment
data, prompts, media, model payloads, secrets, private provenance carriers,
commands, or timestamps. Fingerprints use canonical key ordering, stable
entity ordering and fixed serialization; transient audit data is excluded.

`execution` is always `not_run` for this static gateway. `dry_run` is true
when a requested section includes a plan, diff or migration; it never means a
workload was started.

## Truthful status semantics

- `operational` is reserved for a contract that explicitly describes
  validated static metadata and makes no runtime claim.
- `partial` means the static result is usable while runtime, hardware,
  provider, consent or separately authorized smoke evidence is incomplete.
- `planned` means a capability is declared or not enabled but has no usable
  result; it is never promoted to `operational`.
- `unavailable` means invalid, missing, duplicate/ambiguous, incompatible,
  consent-blocked or unsupported input/identity.
- `not_run` is an execution state, not evidence of readiness. It must never be
  used to upgrade `partial`, `planned` or `unavailable` to `operational`.

Aggregate status is deterministic: an unavailable required section makes the
aggregate unavailable; otherwise any partial section makes it partial; only
fully valid static-only sections can be operational. Optional sections remain
section-scoped and report their own fixed reason/action without poisoning
unrelated results.

## Invariants and security requirements

1. **Authorization and confused-deputy resistance.** The gateway owns root
   resolution, selector lookup and section composition. A caller cannot
   supply a discovery/report mapping for the gateway to trust.
2. **Closed and bounded input.** Enforce schema/version, ASCII-safe opaque IDs,
   section/cardinality, byte, depth and string limits before parsing or
   projection. Reject paths, URLs, commands, credentials, media/blob fields,
   arbitrary runtime configuration and unknown keys.
3. **Allowlisted output.** Project only IDs, versions, fingerprints, counts,
   fixed status/reason/action codes and bounded type summaries. Never echo an
   invalid value, parser exception, raw descriptor or private ownership
   carrier.
4. **Identity and TOCTOU safety.** Use each domain's managed-root reader,
   containment/symlink/size/open-once checks and duplicate-identity handling.
   Never select by filename order. An ambiguous identity is unavailable.
5. **Provenance and consent.** M7C policy/snapshot identity and consent are
   checked server-side. Support/remediation output is unavailable without the
   required scoped consent; caller-supplied findings are not trusted.
6. **No side effects.** Validation and projection must not import executable
   extensions, call subprocess/network/GPU/model/media hooks, start jobs or
   mutate managed/user files.
7. **Determinism and redaction.** Canonical ordering, fixed messages and stable
   fingerprints make repeated output equal and prevent path/secret/prompt
   leakage. Any future Markdown projection must escape control characters and
   table/link delimiters.

## Static unit-test matrix

The smallest implementation gate is a pure unit suite using bounded fixtures
or guarded server-loader doubles. It should verify:

- a valid all-section request/response and repeat-call fingerprint equality;
- rejection of client `report`, `discovery`, `manifest`, path, URL, command or
  secret injection with fixed codes and no unsafe echo;
- duplicate, missing and ambiguous managed identities return unavailable;
- M7C missing/revoked consent and policy/snapshot mismatch fail closed;
- resource, migration, retention, remediation, generation-intent and variant
  outputs are `dry_run: true`, `execution: not_run`;
- stable section/record ordering and JSON canonicalization across input key
  order;
- response scans contain no Windows/UNC/POSIX paths, `file:`/`data:` URLs,
  token-like values, raw prompts, media or model payloads;
- monkeypatched subprocess, socket, GPU, model, media and runtime-import hooks
  are never called.

No server/API/UI/provider smoke, filesystem mutation, legacy suite, GPU/video,
FFmpeg, SAM2 or model test belongs in this documentation change. Runtime
execution, provider probing, writable remediation, job integration and route
authorization are explicit future work requiring separate approval.

