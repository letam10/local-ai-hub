# Local AI Hub V7 architecture foundation

V7 is an incremental architecture layer over the verified V6 product. V5 and
V6 remain historical branches; V7 does not rewrite their history or move the
owner's installation.

## Layers

| Layer | Canonical owner | Responsibility |
|---|---|---|
| Desktop | `src/app/` | shell, WebView, lifecycle and bootstrap composition |
| Presentation | `src/ui/` | typed loopback API rendering; no filesystem or subprocess |
| Transport | `src/services/api/` | parse/validate/authorize/serialize routes |
| Domain | `src/services/` | jobs, artifacts, projects, workflows, managers |
| AI modules | `src/modules/` | manifest, adapter, worker and capability contract |
| Platform | `src/platform/` | roots, containment, reparse/process boundaries |
| Shared | `src/shared/` | versioned schemas and low-level contracts only |

The allowed dependency direction is UI → API → services → modules → platform /
shared. Shared never imports a service, module or UI package. New modules are
registered through validated data manifests, not arbitrary Python imports.

## API route ownership (V7 Phase 3)

HTTP transport enters `HubHandler`, then the explicit server-owned
`src/services/api/router.py`, a domain route adapter under
`src/services/api/routes/`, the shared `ApiContext`, and finally the existing
application/domain service. Route adapters parse bounded request data and map
status codes; they do not own model installation, job scheduling, project
databases, archive internals or inference.

The canonical route/owner inventory is generated in
`architecture/api_routes.yaml` by `scripts/generate_api_route_inventory.py`.
Routes still in the compatibility dispatcher are marked `transport: legacy`
until their streaming or domain-specific regression suite is ready. This is a
deliberate strangler migration: only one registered router route can match a
method/path, and legacy streaming/static paths remain explicit rather than
being silently duplicated.

## Source and data roots

`src/platform/paths.py` defines `APP_ROOT` and `DATA_ROOT`. `DATA_ROOT` may be
the current legacy single-root installation (`D:\LocalAIHub`) or a separate
machine-local directory selected through environment/configuration. Existing
Models, Environments, runtime, Output, Projects, Backups and Config are reused
in place; the foundation does not migrate or duplicate them.

## Module state and evidence

The standard states are `NOT_INSTALLED`, `INSTALLING`, `INSTALLED_UNVERIFIED`,
`PARTIAL`, `OPERATIONAL`, `UNAVAILABLE`, `BROKEN`, and `UPDATE_AVAILABLE`.
Presence of a file or runtime is only an installation observation. A module is
`OPERATIONAL` only after a matching, bounded smoke receipt is recorded.

## Managers

- Model Manager owns catalog metadata, safe discovery, install plans,
  installation receipts and references. It never performs inference.
- Runtime Manager owns executable/environment metadata and bounded leaf
  inspection. It never owns model weights or changes drivers/CUDA.
- Module Manager composes manifest + runtime + model + evidence state.

Both new managers are offline/read-only at startup. Real installation is an
explicit future action with source, license, checksum, disk and user approval.

## Final V7 productization

`src/services/productization/` composes the production model/runtime catalog
and the one-click dependency plan. It does not create a second inference
implementation: fixture acceptance and future manager execution call the same
Model Manager, Runtime Manager, Component Installer, Job Manager and Artifact
Store used by the owner installation. Catalog records expose a disposition
(`AUTO_INSTALL_READY`, `AUTH_REQUIRED`, `LICENSE_REQUIRED`,
`MANUAL_IMPORT_ONLY`, or `UNSUPPORTED_SOURCE`) instead of pretending that a
metadata-only URL is downloadable.

`src/ui/features/` is the feature ownership registry and migration seam. The
legacy `pages.js`/`app.js` files are compatibility facades; new feature data is
owned by its feature namespace and arrives through typed API routes. CSS tokens,
layout and catalog styles are loaded as small feature-owned modules. The
canonical first-run command is `scripts/setup_local_ai_hub.ps1`; it is
plan-by-default and creates only absent Core state when explicitly applied.

## Compatibility

Existing `job.v2`, `node-run.v2`, opaque artifact IDs, project/backup/settings
contracts and legacy imports remain unchanged. Existing `src/shared/paths` is a
compatibility facade over the new platform registry. `Services/Whisper/` is a
first-party backend implementation; `src/modules/whisper/` is the Hub adapter
and contract boundary.

## Post-V8 foundation hardening

Post-V8 V2 surfaces add a typed capability graph, lifecycle planning facade,
model manager, resource scheduler, durable job engine and Router-bound feature
discovery without replacing the V7 productization path. The authoritative M1.1
ownership, SQLite/scheduler saga, lease, restart-readmission, digest, evidence
freshness and cache contract is
[`POST_V8_FOUNDATION_HARDENING_M1_1.md`](POST_V8_FOUNDATION_HARDENING_M1_1.md).
These additions are metadata/planning contracts only: they do not turn a
provider, model or GPU workload operational without separately authorized
runtime evidence and execution ownership.
