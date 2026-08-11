# V5 Capability Control Plane

V5-B composes the existing static cores into one server-owned, deterministic
capability registry. The registry is a projection boundary: client input never
becomes a trusted manifest, and no provider, model, shell, download, or
filesystem operation is started by the registry or Module Manager.

## Contracts

- `capability-registry.v1` records an opaque id, provider/component identity,
  optional tool/workflow, version, dependency ids, observed/evidence state,
  resource hints, status, reason, and next action.
- `module-manager.v1` is a dry-run plan. It reports dependency graph findings,
  publication state, model/license metadata, source policy, and physical versus
  concurrent resource fit. Every plan has `execution: not_run` and
  `dry_run: true`.
- Future install manifests are accepted only as HTTPS metadata with a SHA-256
  digest. Local paths, shell/command payloads, credentials, and unbounded URLs
  are rejected. `not_published` remains visible when no manifest exists.

Statuses are intentionally conservative: `operational` is never inferred from
missing or stale evidence; runtime-dependent capabilities remain `partial`,
`planned`, `unavailable`, or `not_published` until separately authorized
evidence exists.

## Server-owned composition

The registry consumes fixed-root outputs from Extension Platform, Workflow
Packages, Asset Intelligence, Privacy Diagnostics, the closed Capability
Gateway projection, and release-evidence admission state. Each source is
detached, sorted, redacted, and fingerprinted before it is exposed.

The loopback-only `GET /api/capabilities` route returns the registry and a
read-only Module Manager preflight. It accepts no client manifest or execution
request. API startup and this static route do not launch a provider or inspect
runtime hardware; an optional sanitized hardware snapshot may be supplied by
server configuration for arithmetic-only resource planning.

## Resource planning

The default target profile is an NVIDIA RTX 4060 with 8 GB VRAM. Physical GPU
fit is evaluated independently from concurrent allocation. A request that is
physically too large is `unavailable`; a set that only exceeds concurrent
capacity is `partial` with a serial scheduling action. This is arithmetic over
bounded snapshots, not a GPU probe.

## Evidence boundary

Static validation proves only the shape and provenance of metadata. A release
evidence packet remains pending manager QA unless it is explicitly admitted.
Runtime smoke, provider readiness, model availability, and filesystem
operations remain outside this package and are reported truthfully.
