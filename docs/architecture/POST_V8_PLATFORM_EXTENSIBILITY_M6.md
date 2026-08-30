# Post-V8 Platform Extensibility V2 — Milestone 6

## Purpose and owner

`src/services/platform_extensibility_v2.py` is the finite contract owner for
the final program milestone: Plugin SDK boundaries, explicit API versioning,
and a future remote-worker abstraction. Existing static Extension Platform
validation remains the source for declarative extension metadata; M6 does not
replace it with a dynamic loader.

## State and API

M6 introduces no persistent state and does not read a client manifest. The
public transport is:

- `GET /api/extensibility/v2` — Plugin SDK, API versioning and remote-worker
  projections.
- `GET /api/extensibility/v2/{area_id}` — one bounded area (`plugin_sdk`,
  `api_versioning` or `remote_worker`).

The Plugin SDK permission list is metadata-only and mirrors the existing
Extension Platform allowlist. API versioning names the current protocol and
the replacement/deprecation rule; a changed meaning requires a new reviewed
contract. Remote worker state is `NOT_CONFIGURED`, with endpoint and
credentials deliberately not exposed.

Every result is `execution=not_run`, `dry_run=true` and
`overall_state=READ_ONLY_CONTRACT`.

## Failure and recovery

Unknown areas are rejected. Any future plugin import, remote connection or
version migration must have an independent server-owned owner, authenticated
transport, typed capability/resource binding and contract tests. Until then,
use static descriptor validation and the existing Diagnostics route; this M6
projection never imports, connects, dispatches, installs or benchmarks.

## Tests

`tests/test_post_v8_platform_extensibility_m6.py` verifies the closed Plugin
SDK permission/forbidden lists, versioning and remote-worker state, path/secret
redaction, Router/inventory binding and Diagnostics/UI integration.
